import asyncio
import logging
import os
import pathlib
import re
import shutil
import tempfile

from app.local_stm32 import (
  LocalSession,
  LocalStm32Qemu,
  decode_firmware,
)


logger = logging.getLogger(__name__)
GPIO_OUTPUT_RE = re.compile(r"VELXIO_GPIO_WRITE ([A-I]) (0x[0-9a-fA-F]+|\d+) (0x[0-9a-fA-F]+|\d+)")
PORTS = "ABCDEFGHI"


def find_renode():
  configured = os.environ.get("VELXIO_RENODE_BIN")
  if configured:
    path = pathlib.Path(configured).expanduser()
    return str(path) if path.is_file() else ""
  return shutil.which("renode") or ""


def build_renode_script(firmware_path):
  lines = [
    "mach create",
    "machine LoadPlatformDescription @platforms/cpus/stm32f4.repl",
    f"sysbus LoadELF @{firmware_path}",
  ]
  for port in PORTS:
    code = (
      "import System; "
      f"System.Console.WriteLine('VELXIO_GPIO_WRITE {port} {{0}} {{1}}'.format(offset, value))"
    )
    lines.append(f'sysbus SetHookBeforePeripheralWrite gpioPort{port} "{code}"')
  lines.append("start")
  return "\n".join(lines) + "\n"


def parse_gpio_write(line, outputs):
  match = GPIO_OUTPUT_RE.search(line)
  if not match:
    return []
  port, offset_raw, value_raw = match.groups()
  offset = int(offset_raw, 0)
  value = int(value_raw, 0)
  previous = outputs.get(port, 0)

  if offset == 0x14:
    state = value & 0xFFFF
  elif offset == 0x18:
    state = (previous | (value & 0xFFFF)) & ~((value >> 16) & 0xFFFF)
  elif offset == 0x28:
    state = previous & ~(value & 0xFFFF)
  else:
    return []

  state &= 0xFFFF
  outputs[port] = state
  changed = previous ^ state
  events = []
  for bit in range(16):
    if changed & (1 << bit):
      events.append({
        "pin": (ord(port) - ord("A")) * 16 + bit,
        "state": int(bool(state & (1 << bit))),
      })
  return events


class LocalStm32Renode(LocalStm32Qemu):
  async def start(self, client_id, firmware_b64, callback):
    await self.stop(client_id)
    executable = find_renode()
    if not executable:
      await callback("error", {
        "message": "Renode is not installed. Start Velxio with ./run.sh --compiler platformio "
                   "--stm32-engine renode on macOS, or install Renode and restart"
      })
      return

    try:
      firmware, extension = decode_firmware(firmware_b64)
      if extension != ".elf":
        raise ValueError("Renode STM32 emulation requires an ELF firmware. Build with PlatformIO first")
    except ValueError as error:
      await callback("error", {"message": str(error)})
      return

    firmware_path = None
    script_path = None
    try:
      with tempfile.NamedTemporaryFile(prefix="velxio-stm32-", suffix=".elf", delete=False) as stream:
        stream.write(firmware)
        firmware_path = pathlib.Path(stream.name)
      with tempfile.NamedTemporaryFile(
        prefix="velxio-stm32-", suffix=".resc", mode="w", encoding="utf-8", delete=False
      ) as stream:
        stream.write(build_renode_script(firmware_path))
        script_path = pathlib.Path(stream.name)

      await callback("system", {"event": "booting"})
      process = await asyncio.create_subprocess_exec(
        executable, "--disable-gui", "--console", "-p",
        "-e", f"include @{script_path}",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
      )
    except OSError as error:
      if firmware_path:
        firmware_path.unlink(missing_ok=True)
      if script_path:
        script_path.unlink(missing_ok=True)
      await callback("error", {"message": f"Unable to launch STM32 Renode: {error}"})
      return

    session = LocalSession(process, firmware_path, callback, script_path=script_path)
    self.sessions[client_id] = session
    session.tasks = [
      asyncio.create_task(self._read_stdout(session)),
      asyncio.create_task(self._read_stderr(session)),
    ]
    session.tasks.append(asyncio.create_task(self._watch(client_id, session)))
    await callback("system", {
      "event": "booted",
      "emulator": "renode",
      "machine": "stm32f4",
      "approximate": True,
    })
    await callback("system", {
      "event": "peripherals_limited",
      "message": "Renode STM32F4 GPIO changes are connected to the canvas. "
                 "STM32F401 clock, interrupt and peripheral timing is approximate",
    })

  async def _read_stdout(self, session):
    assert session.process.stdout is not None
    gpio_state = {}
    while line := await session.process.stdout.readline():
      decoded = line.decode("utf-8", errors="replace")
      for event in parse_gpio_write(decoded, gpio_state):
        try:
          await session.callback("gpio_change", event)
        except Exception:
          logger.exception("Failed to forward Renode GPIO change")
      if "Error" in decoded or "error" in decoded:
        session.stderr_tail = (session.stderr_tail + decoded)[-4096:]
