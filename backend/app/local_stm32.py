import asyncio
import base64
import binascii
import logging
import os
import pathlib
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from app.core.hooks import register_ws_sim_disconnect, register_ws_sim_handler


logger = logging.getLogger(__name__)

EventCallback = Callable[[str, dict], Awaitable[None]]

STM32_MACHINE = "olimex-stm32-h405"
SUPPORTED_BOARDS = {"stm32-blackpill-f401"}
HANDLED_MESSAGES = {
  "start_stm32",
  "stop_stm32",
  "stm32_load_firmware",
  "stm32_gpio_in",
  "stm32_serial_input",
  "stm32_sensor_attach",
  "stm32_sensor_update",
  "stm32_sensor_detach",
  "stm32_bus_map",
  "stm32_bus_attrs",
}


def find_qemu() -> str:
  configured = os.environ.get("QEMU_STM32_BIN")
  if configured:
    path = pathlib.Path(configured).expanduser()
    return str(path) if path.is_file() else ""
  return shutil.which("qemu-system-arm") or ""


def build_command(qemu: str, firmware: str) -> list[str]:
  return [
    qemu,
    "-M", STM32_MACHINE,
    "-nographic",
    "-monitor", "none",
    "-serial", "stdio",
    "-kernel", firmware,
  ]


def decode_intel_hex(firmware: bytes) -> bytes:
  addresses: dict[int, int] = {}
  base_address = 0

  for raw_line in firmware.splitlines():
    line = raw_line.strip()
    if not line:
      continue
    if not line.startswith(b":"):
      raise ValueError("Invalid Intel HEX record")

    try:
      record = bytes.fromhex(line[1:].decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
      raise ValueError("Invalid Intel HEX encoding") from exc

    if len(record) < 5 or len(record) != record[0] + 5 or sum(record) & 0xFF:
      raise ValueError("Invalid Intel HEX checksum or record size")

    address = int.from_bytes(record[1:3], "big")
    record_type = record[3]
    payload = record[4:-1]

    if record_type == 0:
      for index, value in enumerate(payload):
        addresses[base_address + address + index] = value
    elif record_type == 1:
      break
    elif record_type == 2 and len(payload) == 2:
      base_address = int.from_bytes(payload, "big") << 4
    elif record_type == 4 and len(payload) == 2:
      base_address = int.from_bytes(payload, "big") << 16
    elif record_type not in (3, 5):
      raise ValueError("Unsupported Intel HEX record type")

  if not addresses:
    raise ValueError("Intel HEX contains no firmware data")

  first_address = min(addresses)
  flash_start = 0x08000000 if 0x08000000 <= first_address < 0x08100000 else 0
  size = max(addresses) - flash_start + 1

  if first_address < flash_start or size > 2 * 1024 * 1024:
    raise ValueError("Intel HEX address range is outside the supported flash image")

  image = bytearray(b"\xFF" * size)
  for address, value in addresses.items():
    image[address - flash_start] = value
  return bytes(image)


def decode_firmware(firmware_b64: str) -> tuple[bytes, str]:
  try:
    firmware = base64.b64decode(firmware_b64, validate=True)
  except (binascii.Error, ValueError) as exc:
    raise ValueError("Invalid base64 firmware") from exc
  if not firmware:
    raise ValueError("Firmware is empty")
  if firmware.lstrip().startswith(b":"):
    return decode_intel_hex(firmware), ".bin"
  return firmware, ".elf" if firmware.startswith(b"\x7fELF") else ".bin"

@dataclass
class LocalSession:
  process: asyncio.subprocess.Process
  firmware_path: pathlib.Path
  callback: EventCallback
  tasks: list[asyncio.Task] = field(default_factory=list)
  stderr_tail: str = ""


class LocalStm32Qemu:
  def __init__(self):
    self.sessions: dict[str, LocalSession] = {}
    self.boards: dict[str, str] = {}

  async def handle(
    self,
    websocket: object,
    client_id: str,
    message_type: str,
    data: dict,
    callback: EventCallback,
  ) -> bool:
    if message_type not in HANDLED_MESSAGES:
      return False

    if message_type == "start_stm32":
      board = str(data.get("board", ""))
      if board not in SUPPORTED_BOARDS:
        await callback("error", {
          "message": f"Local QEMU experiment currently supports stm32-blackpill-f401, not {board}"
        })
        return True
      self.boards[client_id] = board
      firmware_b64 = data.get("firmware_b64")
      if firmware_b64:
        await self.start(client_id, str(firmware_b64), callback)
    elif message_type == "stm32_load_firmware":
      if client_id not in self.boards:
        await callback("error", {"message": "Start an STM32F401 board before loading firmware"})
      else:
        await self.start(client_id, str(data.get("firmware_b64") or ""), callback)
    elif message_type == "stop_stm32":
      await self.stop(client_id)
      self.boards.pop(client_id, None)

    return True

  async def start(self, client_id: str, firmware_b64: str, callback: EventCallback) -> None:
    await self.stop(client_id)

    qemu = find_qemu()
    if not qemu:
      await callback("error", {
        "message": "Local STM32 QEMU not found. Install qemu-system-arm or set QEMU_STM32_BIN"
      })
      return

    try:
      firmware, extension = decode_firmware(firmware_b64)
    except ValueError as exc:
      await callback("error", {"message": str(exc)})
      return

    firmware_path: pathlib.Path | None = None
    try:
      with tempfile.NamedTemporaryFile(prefix="velxio-stm32-", suffix=extension, delete=False) as file:
        file.write(firmware)
        firmware_path = pathlib.Path(file.name)

      await callback("system", {"event": "booting"})
      process = await asyncio.create_subprocess_exec(
        *build_command(qemu, str(firmware_path)),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
      )
    except (OSError, ValueError) as exc:
      if firmware_path:
        firmware_path.unlink(missing_ok=True)
      await callback("error", {"message": f"Unable to launch local QEMU: {exc}"})
      return

    session = LocalSession(process, firmware_path, callback)
    self.sessions[client_id] = session
    session.tasks = [
      asyncio.create_task(self._read_stdout(session)),
      asyncio.create_task(self._read_stderr(session)),
    ]
    session.tasks.append(asyncio.create_task(self._watch(client_id, session)))

    await callback("system", {
      "event": "booted",
      "emulator": "qemu-system-arm",
      "machine": STM32_MACHINE,
      "approximate": True,
    })
    await callback("system", {
      "event": "peripherals_limited",
      "message": "F405 approximation: serial output only. GPIO, ADC, DMA and TIM1 are not connected to the canvas",
    })

  async def _read_stdout(self, session: LocalSession) -> None:
    assert session.process.stdout is not None
    while chunk := await session.process.stdout.read(1024):
      try:
        await session.callback("serial_output", {
          "data": chunk.decode("utf-8", errors="replace"),
          "uart": 0,
          "b64": base64.b64encode(chunk).decode("ascii"),
        })
      except Exception:
        logger.exception("STM32 serial event delivery failed")

  async def _read_stderr(self, session: LocalSession) -> None:
    assert session.process.stderr is not None
    while chunk := await session.process.stderr.read(1024):
      session.stderr_tail = (session.stderr_tail + chunk.decode("utf-8", errors="replace"))[-4096:]

  async def _watch(self, client_id: str, session: LocalSession) -> None:
    status = await session.process.wait()
    await asyncio.gather(*session.tasks[:2], return_exceptions=True)
    session.firmware_path.unlink(missing_ok=True)
    if self.sessions.get(client_id) is session:
      self.sessions.pop(client_id, None)
      reason = session.stderr_tail.strip() or f"QEMU exited with code {status}"
      try:
        await session.callback("system", {
          "event": "crash",
          "reason": reason,
          "code": status,
        })
      except Exception:
        logger.exception("STM32 QEMU exit event delivery failed")

  async def stop(self, client_id: str) -> None:
    session = self.sessions.pop(client_id, None)
    if not session:
      return
    if session.process.returncode is None:
      session.process.terminate()
      try:
        await asyncio.wait_for(session.process.wait(), timeout=3)
      except asyncio.TimeoutError:
        session.process.kill()
        await session.process.wait()
    await asyncio.gather(*session.tasks, return_exceptions=True)
    session.firmware_path.unlink(missing_ok=True)

  async def disconnect(self, client_id: str) -> None:
    await self.stop(client_id)
    self.boards.pop(client_id, None)


def register_local_stm32() -> LocalStm32Qemu:
  runner = LocalStm32Qemu()
  register_ws_sim_handler(runner.handle)
  register_ws_sim_disconnect(runner.disconnect)
  return runner
