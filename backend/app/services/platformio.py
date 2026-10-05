import asyncio
import base64
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.core.hooks import materialize_library_scope, scope_retry_allowed


_MISSING_HEADER_RE = re.compile(
  r"fatal error:\s*\S+\.h(?:pp)?:\s*No such file or directory",
  re.IGNORECASE,
)

_EXTRA_CORES: dict[str, dict] = {}


@dataclass(frozen=True)
class Target:
  platform: str
  board: str
  family: str
  extra: tuple[str, ...] = ()


def _looks_like_missing_header(stderr: str | None) -> bool:
  return bool(stderr and _MISSING_HEADER_RE.search(stderr))


def _strip_comments(source: str) -> str:
  out: list[str] = []
  i = 0
  n = len(source)
  while i < n:
    two = source[i:i + 2]
    if two == "//":
      j = source.find("\n", i)
      i = n if j < 0 else j
    elif two == "/*":
      j = source.find("*/", i + 2)
      i = n if j < 0 else j + 2
    else:
      out.append(source[i])
      i += 1
  return "".join(out)


def _has_prelude(content: str, prelude: str) -> bool:
  lines = [x.strip() for x in prelude.splitlines() if x.strip()]
  code = _strip_comments(content)
  return all(x in code for x in lines)


def annotate_build_stderr(stderr: str | None) -> str | None:
  if not stderr:
    return stderr
  if "_needsbt.h" in stderr or "This library needs Bluetooth enabled" in stderr:
    return stderr.rstrip() + (
      "\nVelxio: Bluetooth is not emulated on this board. WiFi can work, but "
      "the CYW43439 Bluetooth controller is not modelled.\n"
    )
  return stderr


def humanize_cli_error(raw: str | None, *, action: str = "run PlatformIO") -> str:
  text = (raw or "").strip()
  if not text:
    return f"Could not {action}: PlatformIO returned no output."
  first = next((x.strip() for x in text.splitlines() if x.strip()), text)
  return f"Could not {action}: {first[:300]}"


def register_extra_core(
  core_id: str,
  index_url: str,
  *,
  version: str | None = None,
  match: str | None = None,
  sketch_prelude: str | None = None,
) -> None:
  _EXTRA_CORES[core_id] = {
    "index_url": index_url,
    "version": version,
    "match": match or core_id,
    "sketch_prelude": sketch_prelude,
  }


def _extra_core_for_fqbn(fqbn: str) -> dict | None:
  for core_id, entry in _EXTRA_CORES.items():
    if entry["match"] in fqbn:
      return {"core_id": core_id, **entry}
  return None


class PlatformIOService:
  CORE_URLS = {
    "rp2040:rp2040": "https://github.com/earlephilhower/arduino-pico/releases/download/global/package_rp2040_index.json",
    "esp32:esp32": "https://espressif.github.io/arduino-esp32/package_esp32_index.json",
    "ATTinyCore:avr": "http://drazzy.com/package_drazzy.com_index.json",
  }
  REQUIRED_CORES = ["arduino:avr"]
  ON_DEMAND_CORES = {
    "ATTinyCore:avr": "ATTinyCore:avr",
    "rp2040": "rp2040:rp2040",
    "mbed_rp2040": "arduino:mbed_rp2040",
    "esp32": "esp32:esp32",
  }
  CORE_INSTALL_VERSIONS = {"ATTinyCore:avr": "1.4.1"}

  def __init__(self, cli_path: str = "pio"):
    self.cli_path = shutil.which(cli_path) or cli_path
    self._ready: set[str] = set()

  def _target(self, fqbn: str) -> Target | None:
    if fqbn.startswith("arduino:avr:uno"):
      return Target("platformio/atmelavr", "uno", "avr")
    if fqbn.startswith("arduino:avr:nano"):
      return Target("platformio/atmelavr", "nanoatmega328", "avr")
    if fqbn.startswith("arduino:avr:mega"):
      return Target("platformio/atmelavr", "megaatmega2560", "avr")
    if fqbn.startswith("ATTinyCore:avr:attinyx5"):
      return Target("platformio/atmelavr", "digispark-tiny", "avr")
    if fqbn.startswith("rp2040:rp2040:rpipicow"):
      return Target(
        "https://github.com/maxgerhardt/platform-raspberrypi.git",
        "rpipicow",
        "rp2040",
        ("board_build.core = earlephilhower",),
      )
    if fqbn.startswith("rp2040:rp2040:rpipico"):
      return Target(
        "https://github.com/maxgerhardt/platform-raspberrypi.git",
        "rpipico",
        "rp2040",
        ("board_build.core = earlephilhower",),
      )
    if fqbn.startswith("esp32:esp32:esp32cam"):
      return Target("platformio/espressif32", "esp32cam", "esp32")
    if fqbn.startswith("esp32:esp32:lolin32-lite"):
      return Target("platformio/espressif32", "lolin32_lite", "esp32")
    if fqbn.startswith("esp32:esp32:XIAO_ESP32S3"):
      return Target("platformio/espressif32", "seeed_xiao_esp32s3", "esp32")
    if fqbn.startswith("esp32:esp32:XIAO_ESP32C3"):
      return Target("platformio/espressif32", "seeed_xiao_esp32c3", "esp32")
    if fqbn.startswith("esp32:esp32:nano_nora"):
      return Target("platformio/espressif32", "arduino_nano_esp32", "esp32")
    if fqbn.startswith("esp32:esp32:esp32s3"):
      return Target("platformio/espressif32", "esp32-s3-devkitc-1", "esp32")
    if fqbn.startswith("esp32:esp32:esp32c3"):
      return Target("platformio/espressif32", "esp32-c3-devkitm-1", "esp32")
    if fqbn.startswith("esp32:esp32:esp32"):
      return Target("platformio/espressif32", "esp32dev", "esp32")
    if fqbn.startswith("STMicroelectronics:stm32"):
      f = fqbn.upper()
      if "BLUEPILL_F103C8" in f:
        return Target("platformio/ststm32", "bluepill_f103c8", "stm32")
      if "BLUEPILL_F103CB" in f:
        return Target("platformio/ststm32", "genericSTM32F103CB", "stm32")
      if "BLACKPILL_F411CE" in f:
        return Target("platformio/ststm32", "blackpill_f411ce", "stm32")
      if "BLACKPILL_F401CE" in f:
        return Target("platformio/ststm32", "blackpill_f401ce", "stm32")
      if "DISCO_F407VG" in f:
        return Target("platformio/ststm32", "disco_f407vg", "stm32")
      if "GENERIC_F405RGTX" in f:
        return Target("platformio/ststm32", "genericSTM32F405RG", "stm32")
      if "GENERIC_F205RGTX" in f:
        return Target("platformio/ststm32", "genericSTM32F205RG", "stm32")
    return None

  async def _run(self, args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PLATFORMIO_DISABLE_UPGRADE_CHECK"] = "true"
    return await asyncio.to_thread(
      subprocess.run,
      args,
      cwd=str(cwd) if cwd else None,
      env=env,
      capture_output=True,
      text=True,
    )

  async def ensure_core_for_board(self, board_fqbn: str) -> dict:
    t = self._target(board_fqbn)
    if not t:
      return {
        "needed": True,
        "installed": False,
        "core_id": board_fqbn,
        "log": f"No PlatformIO target mapping for {board_fqbn}",
      }
    if t.platform in self._ready:
      return {"needed": False, "installed": True, "core_id": t.platform, "log": ""}
    r = await self._run([
      self.cli_path,
      "pkg",
      "install",
      "--global",
      "--platform",
      t.platform,
    ])
    ok = r.returncode == 0
    if ok:
      self._ready.add(t.platform)
    return {
      "needed": True,
      "installed": ok,
      "core_id": t.platform,
      "log": (r.stdout or "") + (r.stderr or ""),
    }

  def _ini(
    self,
    t: Target,
    allowed_libraries: set[str] | None,
    scope_dir: str | None,
  ) -> str:
    lines = [
      "[platformio]",
      "default_envs = velxio",
      "",
      "[env:velxio]",
      f"platform = {t.platform}",
      f"board = {t.board}",
      "framework = arduino",
      "lib_ldf_mode = deep+",
      *t.extra,
    ]
    if scope_dir:
      lines.append(f"lib_extra_dirs = {scope_dir}")
    elif allowed_libraries:
      lines.append("lib_deps =")
      lines.extend(f"  {x}" for x in sorted(allowed_libraries))
    return "\n".join(lines) + "\n"

  def _write_files(self, root: Path, files: list[dict], t: Target) -> None:
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(files):
      name = str(f.get("name") or f"file{i}.ino").replace("\\", "/").lstrip("/")
      if ".." in Path(name).parts:
        raise ValueError(f"Invalid file name: {name}")
      content = str(f.get("content") or "")
      if t.family == "rp2040" and name.endswith(".ino") and "#define Serial Serial1" not in content:
        content = "#define Serial Serial1\n" + content
      path = src / name
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_text(content)

  def _scope_dir(
    self,
    allowed_libraries: set[str] | None,
    owner_id: str | None,
  ) -> str | None:
    if allowed_libraries is None:
      return None
    scope = materialize_library_scope(allowed_libraries, owner_id)
    if not scope:
      return None
    return str(scope[0])

  async def _compile_once(
    self,
    files: list[dict],
    board_fqbn: str,
    allowed_libraries: set[str] | None,
    owner_id: str | None,
  ) -> dict:
    t = self._target(board_fqbn)
    if not t:
      return {
        "success": False,
        "stdout": "",
        "stderr": "",
        "error": f"Unsupported PlatformIO target: {board_fqbn}",
      }
    with tempfile.TemporaryDirectory(prefix="velxio-pio-") as td:
      root = Path(td)
      self._write_files(root, files, t)
      scope_dir = self._scope_dir(allowed_libraries, owner_id)
      (root / "platformio.ini").write_text(self._ini(t, allowed_libraries, scope_dir))
      r = await self._run([self.cli_path, "run", "-e", "velxio"], cwd=root)
      stderr = annotate_build_stderr(r.stderr or "") or ""
      if r.returncode != 0:
        return {
          "success": False,
          "stdout": r.stdout or "",
          "stderr": stderr,
          "error": "PlatformIO compilation failed",
        }

      out = root / ".pio" / "build" / "velxio"
      if t.family == "avr":
        p = out / "firmware.hex"
        if not p.exists():
          return {
            "success": False,
            "stdout": r.stdout or "",
            "stderr": stderr,
            "error": "PlatformIO did not produce firmware.hex",
          }
        return {
          "success": True,
          "hex_content": p.read_text(),
          "stdout": r.stdout or "",
          "stderr": stderr,
          "error": None,
        }

      if t.family == "rp2040":
        bin_p = out / "firmware.bin"
        uf2_p = out / "firmware.uf2"
        target = bin_p if bin_p.exists() else uf2_p
        if not target.exists():
          return {
            "success": False,
            "stdout": r.stdout or "",
            "stderr": stderr,
            "error": "PlatformIO did not produce RP2040 firmware",
          }
        return {
          "success": True,
          "binary_content": base64.b64encode(target.read_bytes()).decode(),
          "binary_type": "bin" if target == bin_p else "uf2",
          "uf2_content": base64.b64encode(uf2_p.read_bytes()).decode() if uf2_p.exists() else None,
          "stdout": r.stdout or "",
          "stderr": stderr,
          "error": None,
        }

      if t.family == "stm32":
        elf_p = out / "firmware.elf"
        bin_p = out / "firmware.bin"
        target = elf_p if elf_p.exists() else bin_p
        if not target.exists():
          return {
            "success": False,
            "stdout": r.stdout or "",
            "stderr": stderr,
            "error": "PlatformIO did not produce STM32 firmware",
          }
        return {
          "success": True,
          "binary_content": base64.b64encode(target.read_bytes()).decode(),
          "binary_type": "elf" if target == elf_p else "bin",
          "stdout": r.stdout or "",
          "stderr": stderr,
          "error": None,
        }

      bin_p = out / "firmware.bin"
      if not bin_p.exists():
        return {
          "success": False,
          "stdout": r.stdout or "",
          "stderr": stderr,
          "error": "PlatformIO did not produce firmware.bin",
        }
      return {
        "success": True,
        "binary_content": base64.b64encode(bin_p.read_bytes()).decode(),
        "binary_type": "bin",
        "stdout": r.stdout or "",
        "stderr": stderr,
        "error": None,
      }

  async def compile(
    self,
    files: list[dict],
    board_fqbn: str = "arduino:avr:uno",
    board_options: dict | None = None,
    allowed_libraries: set[str] | None = None,
    owner_id: str | None = None,
  ) -> dict:
    result = await self._compile_once(files, board_fqbn, allowed_libraries, owner_id)
    if (
      not result.get("success")
      and allowed_libraries is not None
      and _looks_like_missing_header(result.get("stderr"))
      and scope_retry_allowed.get()
    ):
      retry = await self._compile_once(files, board_fqbn, None, owner_id)
      if retry.get("success"):
        retry["manifest_incomplete"] = True
      return retry
    return result

  async def search_libraries(self, query: str) -> dict:
    r = await self._run([
      self.cli_path,
      "pkg",
      "search",
      f"type:library {query}",
    ])
    if r.returncode != 0:
      return {"success": False, "libraries": [], "error": humanize_cli_error(r.stderr, action="search libraries")}
    libs = []
    seen = set()
    for line in (r.stdout or "").splitlines():
      s = line.strip()
      if not s or "/" not in s or s.startswith(("Found ", "Library ", "Registry ")):
        continue
      name = s.split()[0]
      if "/" not in name or name in seen:
        continue
      seen.add(name)
      libs.append({"name": name, "version": "", "sentence": "PlatformIO Registry library"})
    return {"success": True, "libraries": libs, "error": None}

  async def install_library(self, spec: str) -> dict:
    r = await self._run([
      self.cli_path,
      "pkg",
      "install",
      "--global",
      "--library",
      spec,
    ])
    out = (r.stdout or "") + (r.stderr or "")
    return {
      "success": r.returncode == 0,
      "stdout": out,
      "error": None if r.returncode == 0 else humanize_cli_error(out, action=f"install {spec}"),
      "fallback": False,
      "requested_version": None,
    }

  async def uninstall_library(self, name: str) -> dict:
    r = await self._run([
      self.cli_path,
      "pkg",
      "uninstall",
      "--global",
      "--library",
      name,
    ])
    out = (r.stdout or "") + (r.stderr or "")
    return {
      "success": r.returncode == 0,
      "stdout": out,
      "error": None if r.returncode == 0 else humanize_cli_error(out, action=f"uninstall {name}"),
    }

  async def list_installed_libraries(self) -> dict:
    r = await self._run([
      self.cli_path,
      "pkg",
      "list",
      "--global",
      "--only-libraries",
    ])
    if r.returncode != 0:
      return {"success": False, "libraries": [], "error": humanize_cli_error(r.stderr, action="list libraries")}
    libs = []
    for line in (r.stdout or "").splitlines():
      s = line.strip()
      if not s or s.startswith(("Platform", "Packages", "Library", "---")):
        continue
      name = s.split()[0]
      if name:
        libs.append({"name": name, "version": ""})
    return {"success": True, "libraries": libs, "error": None}

  async def list_boards(self) -> list:
    r = await self._run([self.cli_path, "boards"])
    if r.returncode != 0:
      return []
    return [x for x in (r.stdout or "").splitlines() if x.strip()]
