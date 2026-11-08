import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.platformio_compiler import (
  PlatformIOBoard,
  compile_with_platformio,
  firmware_result,
  make_flash_image,
  prepare_project,
  select_board,
)


class PlatformIOCompilerTests(unittest.TestCase):
  def test_families_have_board_mappings(self):
    cases = {
      "arduino:avr:uno": ("atmelavr", "uno"),
      "arduino:avr:nano:cpu=atmega328": ("atmelavr", "nanoatmega328"),
      "rp2040:rp2040:rpipico": ("raspberrypi", "pico"),
      "esp32:esp32:esp32": ("espressif32", "esp32dev"),
      "esp32:esp32:esp32c3": ("espressif32", "esp32-c3-devkitm-1"),
      "STMicroelectronics:stm32:GenF4:pnum=BLACKPILL_F401CE": ("ststm32", "blackpill_f401ce"),
    }
    for fqbn, expected in cases.items():
      with self.subTest(fqbn=fqbn):
        board = select_board(fqbn)
        self.assertIsNotNone(board)
        self.assertEqual((board.platform, board.board), expected)
    self.assertIsNone(select_board("unsupported:board:id"))

  def test_project_writes_platformio_config_and_ino(self):
    with tempfile.TemporaryDirectory() as root:
      prepare_project(root, [
        {"name": "sketch.ino", "content": "void setup() {}"},
        {"name": "lib/helper.cpp", "content": "int helper() {return 1;}"},
        {"name": "pins.h", "content": "#pragma once"},
      ], PlatformIOBoard("atmelavr", "uno", "avr"))
      project = Path(root)
      self.assertTrue((project / "src/main.ino").exists())
      self.assertTrue((project / "lib/helper.cpp").exists())
      self.assertTrue((project / "include/pins.h").exists())
      self.assertIn("platform = atmelavr", (project / "platformio.ini").read_text())

  def test_existing_platformio_config_and_tree_survive(self):
    original = (
      "[platformio]\n"
      "default_envs = target\n\n"
      "[env:target]\n"
      "platform = ststm32\n"
      "board = blackpill_f401ce\n"
      "framework = arduino\n"
      "lib_deps =\n"
      "  adafruit/Adafruit BusIO\n"
      "build_flags = -DPRODUCT_REV=2\n"
    )
    with tempfile.TemporaryDirectory() as root:
      environment = prepare_project(root, [
        {"name": "platformio.ini", "content": original},
        {"name": "src/main.cpp", "content": "#include <Arduino.h>\nvoid setup() {}\nvoid loop() {}"},
        {"name": "include/board.h", "content": "#define BOARD_ID 401"},
        {"name": "lib/signal/src/signal.cpp", "content": "int signal() { return 1; }"},
        {"name": "lib/signal/include/signal.h", "content": "int signal();"},
      ], PlatformIOBoard("ststm32", "blackpill_f401ce", "stm32"))
      project = Path(root)
      self.assertEqual(environment, "target")
      self.assertEqual((project / "platformio.ini").read_text(), original)
      self.assertTrue((project / "src/main.cpp").exists())
      self.assertTrue((project / "include/board.h").exists())
      self.assertTrue((project / "lib/signal/src/signal.cpp").exists())
      self.assertTrue((project / "lib/signal/include/signal.h").exists())

  def test_configuration_fails_if_board_does_not_match_workspace(self):
    config = "[env:uno]\nplatform = atmelavr\nboard = uno\nframework = arduino\n"
    with tempfile.TemporaryDirectory() as root:
      with self.assertRaisesRegex(ValueError, "no environment targeting"):
        prepare_project(root, [
          {"name": "platformio.ini", "content": config},
          {"name": "src/main.cpp", "content": "int main() { return 0; }"},
        ], PlatformIOBoard("ststm32", "blackpill_f401ce", "stm32"))

  def test_imported_extra_scripts_are_rejected(self):
    config = (
      "[env:controller]\nplatform = ststm32\nboard = blackpill_f401ce\n"
      "framework = arduino\nextra_scripts = post:custom.py\n"
    )
    with tempfile.TemporaryDirectory() as root:
      with self.assertRaisesRegex(ValueError, "Unsupported executable PlatformIO setting"):
        prepare_project(root, [
          {"name": "platformio.ini", "content": config},
          {"name": "src/main.cpp", "content": "void setup() {}"},
        ], PlatformIOBoard("ststm32", "blackpill_f401ce", "stm32"))

  def test_imported_board_requires_correct_platform(self):
    config = "[env:controller]\nplatform = espressif32\nboard = blackpill_f401ce\n"
    with tempfile.TemporaryDirectory() as root:
      with self.assertRaisesRegex(ValueError, "must use platform ststm32"):
        prepare_project(root, [
          {"name": "platformio.ini", "content": config},
          {"name": "src/main.cpp", "content": "void setup() {}"},
        ], PlatformIOBoard("ststm32", "blackpill_f401ce", "stm32"))

  def test_invalid_filename_is_rejected(self):
    with tempfile.TemporaryDirectory() as root:
      with self.assertRaisesRegex(ValueError, "Unsafe"):
        prepare_project(root, [{"name": "../config.ini", "content": "bad"}], PlatformIOBoard("atmelavr", "uno", "avr"))

  def test_firmware_outputs_for_avr_rp2040_and_stm32(self):
    with tempfile.TemporaryDirectory() as root:
      build = Path(root)
      (build / "firmware.hex").write_text(":00000001FF")
      self.assertEqual(firmware_result(build, PlatformIOBoard("atmelavr", "uno", "avr"))["hex_content"], ":00000001FF")
      (build / "firmware.bin").write_bytes(b"bin")
      (build / "firmware.uf2").write_bytes(b"uf2")
      rp = firmware_result(build, PlatformIOBoard("raspberrypi", "pico", "rp2040"))
      self.assertEqual(base64.b64decode(rp["binary_content"]), b"bin")
      self.assertEqual(base64.b64decode(rp["uf2_content"]), b"uf2")
      (build / "firmware.elf").write_bytes(b"\x7fELF")
      stm = firmware_result(build, PlatformIOBoard("ststm32", "blackpill_f401ce", "stm32"))
      self.assertEqual(stm["binary_type"], "elf")
      self.assertEqual(base64.b64decode(stm["binary_content"]), b"\x7fELF")

  def test_esp32_merge_outputs_four_megabyte_image(self):
    with tempfile.TemporaryDirectory() as root:
      directory = Path(root)
      (directory / "bootloader.bin").write_bytes(b"boot")
      (directory / "partitions.bin").write_bytes(b"part")
      (directory / "firmware.bin").write_bytes(b"app")
      image = make_flash_image(directory, "esp32dev")
      self.assertEqual(len(image), 4 * 1024 * 1024)
      self.assertEqual(image[0x1000:0x1004], b"boot")
      self.assertEqual(image[0x8000:0x8004], b"part")
      self.assertEqual(image[0x10000:0x10003], b"app")
      c3image = make_flash_image(directory, "esp32-c3-devkitm-1")
      self.assertEqual(c3image[:4], b"boot")
      self.assertEqual(c3image[0x1000:0x1004], b"\xff" * 4)

  def test_missing_esp32_bootloader_gives_error(self):
    with tempfile.TemporaryDirectory() as root:
      path = Path(root)
      (path / "partitions.bin").write_bytes(b"x")
      (path / "firmware.bin").write_bytes(b"x")
      with self.assertRaisesRegex(ValueError, "bootloader"):
        make_flash_image(path, "esp32dev")


class PlatformIOCompilerAsyncTests(unittest.IsolatedAsyncioTestCase):
  async def test_compilation_uses_pio_not_arduino(self):
    def fake_run(command, **kwargs):
      project = Path(command[command.index("--project-dir") + 1])
      out = project / ".pio/build/uno"
      out.mkdir(parents=True)
      (out / "firmware.hex").write_text(":00000001FF")
      return SimpleNamespace(stdout="Success", stderr="", returncode=0)

    with (
      patch("app.services.platformio_compiler.find_platformio", return_value="/tmp/pio"),
      patch("app.services.platformio_compiler.subprocess.run", side_effect=fake_run) as run,
    ):
      result = await compile_with_platformio([{"name": "sketch.ino", "content": "void setup() {}"}], "arduino:avr:uno")
      self.assertTrue(result["success"])
      self.assertEqual(result["hex_content"], ":00000001FF")
      self.assertEqual(run.call_count, 1)
      self.assertEqual(run.call_args.args[0][1], "run")

  async def test_missing_compiler_is_reported(self):
    with patch("app.services.platformio_compiler.find_platformio", return_value=""):
      result = await compile_with_platformio([], "arduino:avr:uno")
    self.assertFalse(result["success"])
    self.assertIn("not installed", result["error"])

  async def test_unmapped_boards_do_not_fall_back_silently(self):
    result = await compile_with_platformio([], "unknown:unknown:unknown")
    self.assertFalse(result["success"])
    self.assertIn("mapping", result["error"])


if __name__ == "__main__":
  unittest.main()
