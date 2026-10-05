import base64
import configparser
import tempfile
from pathlib import Path

from app.services.platformio import (
  PlatformIOService,
  Target,
  _EXTRA_CORES,
  _extra_core_for_fqbn,
  _has_prelude,
  _looks_like_missing_header,
  _strip_comments,
  annotate_build_stderr,
  humanize_cli_error,
  register_extra_core,
)


class ArduinoCLIService(PlatformIOService):
  """Compatibility name for the PlatformIO compiler service.

  Legacy Velxio projects without platformio.ini still use the generated
  PlatformIO project from PlatformIOService. New workspaces carry a real
  PlatformIO tree and are built with their src/include/lib layout preserved.
  """

  @staticmethod
  def _project_ini(files: list[dict]) -> str | None:
    for f in files:
      name = str(f.get("name") or "").replace("\\", "/").lstrip("/")
      if name == "platformio.ini":
        return str(f.get("content") or "")
    return None

  @staticmethod
  def _safe_project_name(raw: str) -> str:
    name = raw.replace("\\", "/").lstrip("/")
    path = Path(name)
    if not name or path.is_absolute() or ".." in path.parts:
      raise ValueError(f"Invalid PlatformIO project path: {raw}")
    if path.parts and path.parts[0] in (".pio", ".git"):
      raise ValueError(f"Reserved PlatformIO project path: {raw}")
    return name

  def _write_project_files(self, root: Path, files: list[dict], t: Target) -> None:
    for i, f in enumerate(files):
      name = self._safe_project_name(str(f.get("name") or f"src/file{i}.cpp"))
      if name == "platformio.ini":
        continue
      content = str(f.get("content") or "")
      suffix = Path(name).suffix.lower()
      if (
        t.family == "rp2040"
        and name.startswith("src/")
        and suffix in (".ino", ".c", ".cc", ".cpp", ".cxx")
        and "#define Serial Serial1" not in content
      ):
        content = "#define Serial Serial1\n" + content
      path = root / name
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_text(content)

  @staticmethod
  def _first_env(cfg: configparser.ConfigParser) -> str:
    if cfg.has_section("platformio"):
      raw = cfg.get("platformio", "default_envs", fallback="")
      envs = [x.strip() for chunk in raw.splitlines() for x in chunk.split(",") if x.strip()]
      if envs:
        return envs[0]
    if cfg.has_section("env:velxio"):
      return "velxio"
    for section in cfg.sections():
      if section.startswith("env:") and len(section) > 4:
        return section[4:]
    return "velxio"

  def _prepare_project_ini(
    self,
    root: Path,
    raw_ini: str,
    t: Target,
    allowed_libraries: set[str] | None,
    scope_dir: str | None,
  ) -> str:
    cfg = configparser.ConfigParser(interpolation=None, strict=False)
    cfg.optionxform = str
    try:
      cfg.read_string(raw_ini or "")
    except configparser.Error as exc:
      raise ValueError(f"Invalid platformio.ini: {exc}") from exc

    env = self._first_env(cfg)
    if not cfg.has_section("platformio"):
      cfg.add_section("platformio")
    cfg.set("platformio", "default_envs", env)

    section = f"env:{env}"
    if not cfg.has_section(section):
      cfg.add_section(section)

    # The selected canvas board is authoritative for the MCU which executes
    # the result. Preserve flags/dependencies but prevent a copied stale ini
    # from compiling for another chip and feeding that image to this emulator.
    cfg.set(section, "platform", t.platform)
    cfg.set(section, "board", t.board)
    cfg.set(section, "framework", "arduino")
    if not cfg.has_option(section, "lib_ldf_mode"):
      cfg.set(section, "lib_ldf_mode", "deep+")

    for extra in t.extra:
      if "=" not in extra:
        continue
      key, value = extra.split("=", 1)
      cfg.set(section, key.strip(), value.strip())

    if scope_dir:
      existing = cfg.get(section, "lib_extra_dirs", fallback="").strip()
      values = [x.strip() for x in existing.splitlines() if x.strip()]
      if scope_dir not in values:
        values.append(scope_dir)
      cfg.set(section, "lib_extra_dirs", "\n".join(values))
    elif allowed_libraries:
      existing = cfg.get(section, "lib_deps", fallback="")
      deps = [x.strip() for x in existing.splitlines() if x.strip()]
      seen = set(deps)
      for lib in sorted(allowed_libraries):
        if lib not in seen:
          deps.append(lib)
          seen.add(lib)
      cfg.set(section, "lib_deps", "\n".join(deps))

    with (root / "platformio.ini").open("w") as fp:
      cfg.write(fp)
    return env

  @staticmethod
  def _artifact_result(root: Path, env: str, t: Target, stdout: str, stderr: str) -> dict:
    out = root / ".pio" / "build" / env
    if t.family == "avr":
      p = out / "firmware.hex"
      if not p.exists():
        return {"success": False, "stdout": stdout, "stderr": stderr, "error": "PlatformIO did not produce firmware.hex"}
      return {"success": True, "hex_content": p.read_text(), "stdout": stdout, "stderr": stderr, "error": None}

    if t.family == "rp2040":
      bin_p = out / "firmware.bin"
      uf2_p = out / "firmware.uf2"
      target = bin_p if bin_p.exists() else uf2_p
      if not target.exists():
        return {"success": False, "stdout": stdout, "stderr": stderr, "error": "PlatformIO did not produce RP2040 firmware"}
      return {
        "success": True,
        "binary_content": base64.b64encode(target.read_bytes()).decode(),
        "binary_type": "bin" if target == bin_p else "uf2",
        "uf2_content": base64.b64encode(uf2_p.read_bytes()).decode() if uf2_p.exists() else None,
        "stdout": stdout,
        "stderr": stderr,
        "error": None,
      }

    if t.family == "stm32":
      elf_p = out / "firmware.elf"
      bin_p = out / "firmware.bin"
      target = elf_p if elf_p.exists() else bin_p
      if not target.exists():
        return {"success": False, "stdout": stdout, "stderr": stderr, "error": "PlatformIO did not produce STM32 firmware"}
      return {
        "success": True,
        "binary_content": base64.b64encode(target.read_bytes()).decode(),
        "binary_type": "elf" if target == elf_p else "bin",
        "stdout": stdout,
        "stderr": stderr,
        "error": None,
      }

    bin_p = out / "firmware.bin"
    if not bin_p.exists():
      return {"success": False, "stdout": stdout, "stderr": stderr, "error": "PlatformIO did not produce firmware.bin"}
    return {
      "success": True,
      "binary_content": base64.b64encode(bin_p.read_bytes()).decode(),
      "binary_type": "bin",
      "stdout": stdout,
      "stderr": stderr,
      "error": None,
    }

  async def _compile_once(
    self,
    files: list[dict],
    board_fqbn: str,
    allowed_libraries: set[str] | None,
    owner_id: str | None,
  ) -> dict:
    raw_ini = self._project_ini(files)
    if raw_ini is None:
      return await super()._compile_once(files, board_fqbn, allowed_libraries, owner_id)

    t = self._target(board_fqbn)
    if not t:
      return {"success": False, "stdout": "", "stderr": "", "error": f"Unsupported PlatformIO target: {board_fqbn}"}

    try:
      with tempfile.TemporaryDirectory(prefix="velxio-pio-project-") as td:
        root = Path(td)
        self._write_project_files(root, files, t)
        scope_dir = self._scope_dir(allowed_libraries, owner_id)
        env = self._prepare_project_ini(root, raw_ini, t, allowed_libraries, scope_dir)
        result = await self._run([self.cli_path, "run", "-e", env], cwd=root)
        stderr = annotate_build_stderr(result.stderr or "") or ""
        stdout = result.stdout or ""
        if result.returncode != 0:
          return {"success": False, "stdout": stdout, "stderr": stderr, "error": "PlatformIO compilation failed"}
        return self._artifact_result(root, env, t, stdout, stderr)
    except (OSError, ValueError) as exc:
      return {"success": False, "stdout": "", "stderr": "", "error": str(exc)}
