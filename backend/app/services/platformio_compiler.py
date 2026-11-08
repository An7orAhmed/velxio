import asyncio
import base64
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PlatformIOBoard:
  platform: str
  board: str
  family: str


BOARD_IDS = {
  "arduino:avr:uno": PlatformIOBoard("atmelavr", "uno", "avr"),
  "arduino:avr:nano:cpu=atmega328": PlatformIOBoard("atmelavr", "nanoatmega328", "avr"),
  "arduino:avr:mega": PlatformIOBoard("atmelavr", "megaatmega2560", "avr"),
  "ATTinyCore:avr:attinyx5:chip=85,clock=16pll": PlatformIOBoard("atmelavr", "attiny85", "avr"),
  "rp2040:rp2040:rpipico": PlatformIOBoard("raspberrypi", "pico", "rp2040"),
  "rp2040:rp2040:rpipicow": PlatformIOBoard("raspberrypi", "pico", "rp2040"),
  "esp32:esp32:esp32": PlatformIOBoard("espressif32", "esp32dev", "esp32"),
  "esp32:esp32:esp32cam": PlatformIOBoard("espressif32", "esp32cam", "esp32"),
  "esp32:esp32:lolin32-lite": PlatformIOBoard("espressif32", "lolin32_lite", "esp32"),
  "esp32:esp32:esp32s3": PlatformIOBoard("espressif32", "esp32-s3-devkitc-1", "esp32"),
  "esp32:esp32:XIAO_ESP32S3": PlatformIOBoard("espressif32", "seeed_xiao_esp32s3", "esp32"),
  "esp32:esp32:nano_nora": PlatformIOBoard("espressif32", "arduino_nano_esp32", "esp32"),
  "esp32:esp32:esp32c3": PlatformIOBoard("espressif32", "esp32-c3-devkitm-1", "esp32"),
  "esp32:esp32:XIAO_ESP32C3": PlatformIOBoard("espressif32", "seeed_xiao_esp32c3", "esp32"),
  "STMicroelectronics:stm32:GenF1:pnum=BLUEPILL_F103C8": PlatformIOBoard("ststm32", "bluepill_f103c8", "stm32"),
  "STMicroelectronics:stm32:GenF1:pnum=BLUEPILL_F103CB": PlatformIOBoard("ststm32", "bluepill_f103c8_128k", "stm32"),
  "STMicroelectronics:stm32:GenF4:pnum=BLACKPILL_F401CE": PlatformIOBoard("ststm32", "blackpill_f401ce", "stm32"),
  "STMicroelectronics:stm32:GenF4:pnum=BLACKPILL_F411CE": PlatformIOBoard("ststm32", "blackpill_f411ce", "stm32"),
  "STMicroelectronics:stm32:Disco:pnum=DISCO_F407VG": PlatformIOBoard("ststm32", "disco_f407vg", "stm32"),
  "STMicroelectronics:stm32:GenF4:pnum=GENERIC_F405RGTX": PlatformIOBoard("ststm32", "genericSTM32F405RG", "stm32"),
  "STMicroelectronics:stm32:GenF2:pnum=GENERIC_F205RGTX": PlatformIOBoard("ststm32", "genericSTM32F205RG", "stm32"),
}


def select_board(fqbn):
  if fqbn in BOARD_IDS:
    return BOARD_IDS[fqbn]
  for name, board in BOARD_IDS.items():
    if fqbn.startswith(name + ":"):
      return board
  return None


def find_platformio():
  configured = os.environ.get("VELXIO_PLATFORMIO_BIN")
  if configured:
    return configured if Path(configured).is_file() else ""
  return shutil.which("pio") or shutil.which("platformio") or ""


def prepare_project(directory, files, board):
  project = Path(directory)
  src = project / "src"
  include = project / "include"
  src.mkdir(parents=True, exist_ok=True)
  include.mkdir(parents=True, exist_ok=True)

  (project / "platformio.ini").write_text(
    f"[env:{board.board}]\n"
    f"platform = {board.platform}\n"
    f"board = {board.board}\n"
    "framework = arduino\n"
    "build_type = release\n",
    encoding="utf-8",
  )

  has_sources = False
  for entry in files:
    name = str(entry.get("name") or "").replace("\\", "/")
    if not name or name.startswith("/") or ":" in name or ".." in name.split("/"):
      raise ValueError(f"Unsafe source filename: {name!r}")
    original = Path(name)
    suffix = original.suffix.lower()
    if suffix not in (".ino", ".cpp", ".cc", ".c", ".h", ".hpp"):
      continue

    parts = original.parts
    if parts[0] in ("src", "include"):
      parts = parts[1:]
    if not parts:
      raise ValueError("A source filename is required")

    filename = Path(*parts)
    if filename.suffix.lower() in (".h", ".hpp"):
      destination = include / filename
    else:
      destination = src / filename
      has_sources = True
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(str(entry.get("content") or ""), encoding="utf-8")

  if not has_sources:
    raise ValueError("No compilable Arduino or C/C++ source files")

  sketch = src / "sketch.ino"
  if sketch.is_file() and not (src / "main.ino").exists():
    sketch.rename(src / "main.ino")


def make_flash_image(build_dir, board_id):
  firmware = build_dir / "firmware.bin"
  partitions = build_dir / "partitions.bin"
  bootloader = build_dir / "bootloader.bin"
  if not firmware.is_file() or not partitions.is_file():
    raise ValueError("PlatformIO ESP32 build needs firmware.bin and partitions.bin")

  if not bootloader.is_file():
    candidates = list(build_dir.glob("*bootloader*.bin"))
    if candidates:
      bootloader = candidates[0]

  if not bootloader.is_file():
    root = Path(os.environ.get("PLATFORMIO_CORE_DIR", "~/.platformio")).expanduser()
    framework = root / "packages" / "framework-arduinoespressif32"
    chip = "esp32c3" if "c3" in board_id else ("esp32s3" if "s3" in board_id else "esp32")
    names = (
      f"tools/sdk/{chip}/bin/bootloader_dio_40m.bin",
      f"tools/sdk/{chip}/bin/bootloader_qio_80m.bin",
      f"tools/sdk/{chip}/bin/bootloader_qio_40m.bin",
    )
    for name in names:
      candidate = framework / name
      if candidate.is_file():
        bootloader = candidate
        break

  if not bootloader.is_file():
    raise ValueError(
      "PlatformIO did not provide a compatible ESP32 bootloader. "
      "A complete flash image is required by the Velxio simulator"
    )

  boot_offset = 0 if "c3" in board_id else 0x1000
  image = bytearray(b"\xff" * (4 * 1024 * 1024))
  for offset, path in (
    (boot_offset, bootloader),
    (0x8000, partitions),
    (0x10000, firmware),
  ):
    payload = path.read_bytes()
    if offset + len(payload) > len(image):
      raise ValueError("ESP32 firmware exceeds the 4 MB emulator image")
    image[offset:offset + len(payload)] = payload
  return bytes(image)


def firmware_result(build_dir, board):
  if board.family == "avr":
    path = build_dir / "firmware.hex"
    if not path.is_file():
      raise ValueError("PlatformIO did not produce firmware.hex")
    return {"hex_content": path.read_text(encoding="utf-8")}

  if board.family == "esp32":
    content = make_flash_image(build_dir, board.board)
    return {
      "binary_content": base64.b64encode(content).decode("ascii"),
      "binary_type": "bin",
    }

  if board.family == "rp2040":
    binary = build_dir / "firmware.bin"
    uf2 = build_dir / "firmware.uf2"
    if not binary.is_file():
      raise ValueError("PlatformIO did not produce firmware.bin for RP2040")
    return {
      "binary_content": base64.b64encode(binary.read_bytes()).decode("ascii"),
      "binary_type": "bin",
      "uf2_content": base64.b64encode(uf2.read_bytes()).decode("ascii") if uf2.is_file() else None,
    }

  elf = build_dir / "firmware.elf"
  binary = build_dir / "firmware.bin"
  target = elf if elf.is_file() else binary
  if not target.is_file():
    raise ValueError("PlatformIO did not produce an STM32 ELF or BIN")
  return {
    "binary_content": base64.b64encode(target.read_bytes()).decode("ascii"),
    "binary_type": "elf" if target == elf else "bin",
  }


async def compile_with_platformio(files, board_fqbn):
  board = select_board(board_fqbn)
  if not board:
    return {
      "success": False,
      "error": f"PlatformIO board mapping is unavailable for {board_fqbn}. Switch to Arduino CLI.",
      "stdout": "",
      "stderr": "",
    }

  executable = find_platformio()
  if not executable:
    return {
      "success": False,
      "error": "PlatformIO is not installed. Launch ./run.sh --compiler platformio.",
      "stdout": "",
      "stderr": "",
    }

  try:
    with tempfile.TemporaryDirectory(prefix="velxio-pio-") as directory:
      prepare_project(directory, files, board)
      result = await asyncio.to_thread(
        subprocess.run,
        [executable, "run", "--project-dir", directory, "--environment", board.board],
        capture_output=True,
        text=True,
        timeout=900,
      )
      stdout = result.stdout or ""
      stderr = result.stderr or ""
      if result.returncode:
        return {
          "success": False,
          "stdout": stdout,
          "stderr": stderr,
          "error": f"PlatformIO build failed for {board.board}",
        }

      artifact_dir = Path(directory) / ".pio" / "build" / board.board
      try:
        firmware = firmware_result(artifact_dir, board)
      except ValueError as error:
        return {
          "success": False,
          "stdout": stdout,
          "stderr": str(error),
          "error": str(error),
        }

      return {
        "success": True,
        "stdout": stdout,
        "stderr": stderr,
        **firmware,
      }
  except (OSError, ValueError, subprocess.TimeoutExpired) as error:
    return {
      "success": False,
      "stdout": "",
      "stderr": str(error),
      "error": f"PlatformIO build failed: {error}",
    }
