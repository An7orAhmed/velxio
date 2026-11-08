import asyncio
import base64
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


BOARD_IDS = {
  "STMicroelectronics:stm32:GenF4:pnum=BLACKPILL_F401CE": "blackpill_f401ce",
  "STMicroelectronics:stm32:GenF4:pnum=BLACKPILL_F411CE": "blackpill_f411ce",
  "STMicroelectronics:stm32:Disco:pnum=DISCO_F407VG": "disco_f407vg",
}


def platformio_executable():
  configured = os.environ.get("VELXIO_PLATFORMIO_BIN")
  if configured:
    return configured if Path(configured).is_file() else ""
  return shutil.which("pio") or shutil.which("platformio") or ""


def platformio_board(fqbn):
  return BOARD_IDS.get(fqbn)


def prepare_sources(project_dir, files, board_id):
  project = Path(project_dir)
  source_dir = project / "src"
  include_dir = project / "include"
  source_dir.mkdir(parents=True, exist_ok=True)
  include_dir.mkdir(parents=True, exist_ok=True)

  config = (
    f"[env:{board_id}]\n"
    "platform = ststm32\n"
    f"board = {board_id}\n"
    "framework = arduino\n"
    "build_type = release\n"
  )
  (project / "platformio.ini").write_text(config, encoding="utf-8")

  ino_files = []
  other_sources = []
  for entry in files:
    name = str(entry.get("name") or "").replace("\\", "/")
    content = str(entry.get("content") or "")
    if not name or name.startswith("/") or ".." in name.split("/") or ":" in name:
      raise ValueError(f"Unsafe source filename: {name}")
    suffix = Path(name).suffix.lower()
    if suffix == ".ino":
      ino_files.append((name, content))
    elif suffix in {".cpp", ".c", ".cc", ".h", ".hpp"}:
      other_sources.append((name, content))

  if not ino_files and not other_sources:
    raise ValueError("No Arduino or C/C++ source files provided")

  for name, content in other_sources:
    relative = Path(name)
    if relative.parts[0] in {"src", "include"}:
      relative = Path(*relative.parts[1:])
    target_root = include_dir if relative.suffix.lower() in {".h", ".hpp"} else source_dir
    destination = target_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")

  if ino_files:
    if (source_dir / "main.cpp").exists():
      raise ValueError("Cannot compile both main.cpp and an .ino sketch using PlatformIO")
    ino_files.sort(key=lambda item: (Path(item[0]).name != "sketch.ino", item[0]))
    sketch = "#include <Arduino.h>\n\n" + "\n\n".join(content for _, content in ino_files) + "\n"
    (source_dir / "main.cpp").write_text(sketch, encoding="utf-8")


async def compile_stm32(files, board_fqbn):
  board_id = platformio_board(board_fqbn)
  if not board_id:
    return {
      "success": False,
      "stdout": "",
      "stderr": "",
      "error": f"PlatformIO STM32 support is not yet mapped for {board_fqbn}. Switch to Arduino CLI for this board.",
    }

  executable = platformio_executable()
  if not executable:
    return {
      "success": False,
      "stdout": "",
      "stderr": "",
      "error": "PlatformIO CLI is missing. Run ./run.sh --compiler platformio to install it.",
    }

  try:
    with tempfile.TemporaryDirectory(prefix="velxio-pio-stm32-") as directory:
      project = Path(directory)
      prepare_sources(project, files, board_id)

      process = await asyncio.to_thread(
        subprocess.run,
        [executable, "run", "--project-dir", str(project), "--environment", board_id],
        capture_output=True,
        text=True,
        timeout=900,
      )
      stdout = process.stdout or ""
      stderr = process.stderr or ""
      if process.returncode:
        return {
          "success": False,
          "stdout": stdout,
          "stderr": stderr,
          "error": f"PlatformIO failed to build {board_id}",
        }

      artifact_dir = project / ".pio" / "build" / board_id
      elf_file = artifact_dir / "firmware.elf"
      bin_file = artifact_dir / "firmware.bin"
      target = elf_file if elf_file.is_file() else bin_file
      if not target.is_file():
        return {
          "success": False,
          "stdout": stdout,
          "stderr": stderr,
          "error": f"PlatformIO completed but no ELF or BIN was found under {artifact_dir}",
        }

      return {
        "success": True,
        "binary_content": base64.b64encode(target.read_bytes()).decode("ascii"),
        "binary_type": "elf" if target == elf_file else "bin",
        "stdout": stdout,
        "stderr": stderr,
      }
  except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
    return {
      "success": False,
      "stdout": "",
      "stderr": str(exc),
      "error": f"PlatformIO STM32 build could not finish: {exc}",
    }
