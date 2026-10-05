from pathlib import Path
import sys


_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "backend"))

from app.services.arduino_cli import ArduinoCLIService  # noqa: E402


def test_real_platformio_tree_is_preserved(tmp_path):
  svc = ArduinoCLIService()
  target = svc._target("arduino:avr:uno")
  assert target is not None

  files = [
    {"name": "platformio.ini", "content": "[env:velxio]\nplatform = old\nboard = old\nframework = arduino\nbuild_flags = -DHELLO=1\n"},
    {"name": "src/main.cpp", "content": "#include <Arduino.h>\nvoid setup(){}\nvoid loop(){}\n"},
    {"name": "include/pins.h", "content": "#pragma once\n"},
    {"name": "lib/demo/demo.cpp", "content": "int demo = 1;\n"},
  ]

  svc._write_project_files(tmp_path, files, target)
  env = svc._prepare_project_ini(
    tmp_path,
    files[0]["content"],
    target,
    allowed_libraries=None,
    scope_dir=None,
  )

  assert env == "velxio"
  assert (tmp_path / "src/main.cpp").exists()
  assert (tmp_path / "include/pins.h").exists()
  assert (tmp_path / "lib/demo/demo.cpp").exists()
  assert not (tmp_path / "src/src/main.cpp").exists()

  ini = (tmp_path / "platformio.ini").read_text()
  assert "board = uno" in ini
  assert "framework = arduino" in ini
  assert "build_flags = -DHELLO=1" in ini


def test_legacy_sketch_still_uses_legacy_platformio_path():
  svc = ArduinoCLIService()
  assert svc._project_ini([
    {"name": "sketch.ino", "content": "void setup(){}\nvoid loop(){}\n"},
  ]) is None


def test_stm32_project_keeps_elf_target_family(tmp_path):
  svc = ArduinoCLIService()
  target = svc._target("STMicroelectronics:stm32:GenF4:pnum=BLACKPILL_F401CE")
  assert target is not None
  assert target.family == "stm32"
  assert target.board == "blackpill_f401ce"

  raw = "[platformio]\ndefault_envs = custom\n\n[env:custom]\nplatform = ststm32\nboard = wrong\nframework = arduino\n"
  env = svc._prepare_project_ini(tmp_path, raw, target, None, None)
  assert env == "custom"
  ini = (tmp_path / "platformio.ini").read_text()
  assert "board = blackpill_f401ce" in ini
