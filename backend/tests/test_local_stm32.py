import asyncio
import base64
import pathlib
import unittest
from unittest.mock import AsyncMock, patch

from app.local_stm32 import (
  LocalStm32Qemu,
  build_command,
  decode_firmware,
)


class FakeProcess:
  def __init__(self):
    self.stdout = asyncio.StreamReader()
    self.stderr = asyncio.StreamReader()
    self.stdout.feed_data(b"STM32 ready\r\n")
    self.stdout.feed_eof()
    self.stderr.feed_eof()
    self.returncode = None
    self.finished = asyncio.Event()

  async def wait(self):
    await self.finished.wait()
    return self.returncode

  def terminate(self):
    self.returncode = -15
    self.finished.set()

  def kill(self):
    self.returncode = -9
    self.finished.set()


class LocalStm32Tests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.runner = LocalStm32Qemu()
    self.events = []

    async def callback(event_type, data):
      self.events.append((event_type, data))

    self.callback = callback

  async def asyncTearDown(self):
    await self.runner.disconnect("board-1")

  def test_command_uses_independent_qemu_machine(self):
    command = build_command("/usr/bin/qemu-system-arm", "/tmp/firmware.elf")
    self.assertEqual(command[:3], ["/usr/bin/qemu-system-arm", "-M", "olimex-stm32-h405"])
    self.assertEqual(command[-2:], ["-kernel", "/tmp/firmware.elf"])

  def test_firmware_formats(self):
    elf = b"\x7fELF" + b"\x00" * 16
    self.assertEqual(decode_firmware(base64.b64encode(elf).decode()), (elf, ".elf"))
    self.assertEqual(decode_firmware(base64.b64encode(b"\x01\x02").decode()), (b"\x01\x02", ".bin"))
    hex_image = b":020000040800F2\n:0400000000100020CC\n:00000001FF\n"
    self.assertEqual(
      decode_firmware(base64.b64encode(hex_image).decode()),
      (b"\x00\x10\x00\x20", ".bin"),
    )
    with self.assertRaisesRegex(ValueError, "checksum"):
      decode_firmware(base64.b64encode(hex_image.replace(b"20CC", b"20CD")).decode())
    with self.assertRaises(ValueError):
      decode_firmware("invalid==base64")

  async def test_unsupported_board_is_reported(self):
    handled = await self.runner.handle(
      None, "board-1", "start_stm32",
      {"board": "stm32-blackpill", "firmware_b64": "AA=="},
      self.callback,
    )
    self.assertTrue(handled)
    self.assertIn("stm32-blackpill-f401", self.events[-1][1]["message"])

  async def test_no_local_qemu_is_reported(self):
    with patch("app.local_stm32.find_qemu", return_value=""):
      await self.runner.handle(
        None, "board-1", "start_stm32",
        {"board": "stm32-blackpill-f401", "firmware_b64": "AQI="},
        self.callback,
      )
    self.assertIn("QEMU not found", self.events[-1][1]["message"])

  async def test_firmware_can_arrive_after_board_start(self):
    process = FakeProcess()
    with (
      patch("app.local_stm32.find_qemu", return_value="/usr/bin/qemu-system-arm"),
      patch("app.local_stm32.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=process) as spawn,
    ):
      await self.runner.handle(
        None, "board-1", "start_stm32",
        {"board": "stm32-blackpill-f401"},
        self.callback,
      )
      self.assertFalse(spawn.called)
      await self.runner.handle(
        None, "board-1", "stm32_load_firmware",
        {"firmware_b64": base64.b64encode(b"\x7fELFtest").decode()},
        self.callback,
      )
      self.assertTrue(spawn.called)
      await asyncio.sleep(0)
      self.assertTrue(any(kind == "serial_output" and "STM32 ready" in data["data"] for kind, data in self.events))
      firmware_path = pathlib.Path(spawn.call_args.args[-1])
      self.assertTrue(firmware_path.exists())
      await self.runner.handle(None, "board-1", "stop_stm32", {}, self.callback)
      self.assertFalse(firmware_path.exists())
      self.assertNotIn("board-1", self.runner.sessions)

  async def test_unrelated_messages_are_not_consumed(self):
    handled = await self.runner.handle(None, "board-1", "start_pi", {}, self.callback)
    self.assertFalse(handled)


if __name__ == "__main__":
  unittest.main()
