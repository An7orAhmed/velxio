from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

StatusCallback = Callable[[str, dict], Awaitable[None]]


@dataclass(frozen=True)
class RuntimeInfo:
  lib_path: Path
  managed: bool
  cleanup_available: bool


class Stm32RuntimeBuilder:
  """Resolve or build the PICSimLab-compatible STM32 QEMU shared library.

  A prebuilt library can be supplied with VELXIO_STM32_LIB. Managed builds are
  kept under VELXIO_STM32_RUNTIME_DIR (default: /tmp/velxio-stm32-runtime).
  Temporary source/build files are separate so they can be removed after the
  user approves cleanup without deleting the finished runtime.
  """

  def __init__(self) -> None:
    root = Path(os.getenv("VELXIO_STM32_RUNTIME_DIR", "/tmp/velxio-stm32-runtime"))
    self.root = root
    self.runtime_dir = root / "runtime"
    self.work_dir = root / "build"
    self.manifest_path = root / "build-manifest.json"
    self._lock = asyncio.Lock()

  @property
  def library_name(self) -> str:
    if sys.platform == "darwin":
      return "libqemu-stm32.dylib"
    if os.name == "nt":
      return "libqemu-stm32.dll"
    return "libqemu-stm32.so"

  def configured_library(self) -> Optional[Path]:
    value = os.getenv("VELXIO_STM32_LIB", "").strip()
    if not value:
      return None
    path = Path(value).expanduser().resolve()
    return path if path.is_file() else None

  def managed_library(self) -> Optional[Path]:
    candidates = [
      self.runtime_dir / self.library_name,
      self.runtime_dir / "libqemu-arm.so",
      self.runtime_dir / "libqemu-stm32.so",
    ]
    return next((p for p in candidates if p.is_file()), None)

  def is_available(self) -> bool:
    return self.configured_library() is not None or self.managed_library() is not None

  async def ensure_runtime(self, callback: StatusCallback) -> RuntimeInfo:
    configured = self.configured_library()
    if configured:
      return RuntimeInfo(configured, managed=False, cleanup_available=False)

    managed = self.managed_library()
    if managed:
      return RuntimeInfo(managed, managed=True, cleanup_available=self.work_dir.exists())

    async with self._lock:
      managed = self.managed_library()
      if managed:
        return RuntimeInfo(managed, managed=True, cleanup_available=self.work_dir.exists())

      await callback("system", {
        "event": "stm32_runtime_build_started",
        "message": "Preparing the open STM32 emulator runtime…",
      })
      lib_path = await self._build(callback)
      cleanup_bytes = self.cleanup_size()
      await callback("system", {
        "event": "stm32_runtime_built",
        "message": "STM32 emulator runtime is ready.",
        "cleanup_available": cleanup_bytes > 0,
        "cleanup_bytes": cleanup_bytes,
      })
      return RuntimeInfo(lib_path, managed=True, cleanup_available=cleanup_bytes > 0)

  async def _build(self, callback: StatusCallback) -> Path:
    if os.name == "nt":
      raise RuntimeError(
        "Automatic STM32 runtime build is currently supported on Unix-like hosts. "
        "Set VELXIO_STM32_LIB to a compatible libqemu-stm32 library on Windows."
      )

    required = ["git", "bash", "make"]
    missing = [name for name in required if shutil.which(name) is None]
    if missing:
      raise RuntimeError(
        "Missing STM32 build tools: " + ", ".join(missing) +
        ". Install them or set VELXIO_STM32_LIB to a compatible prebuilt runtime."
      )

    repo = os.getenv("VELXIO_STM32_QEMU_REPO", "https://github.com/lcgamboa/qemu_stm32.git")
    ref = os.getenv("VELXIO_STM32_QEMU_REF", "picsimlab")
    src = self.work_dir / "qemu_stm32"
    self.runtime_dir.mkdir(parents=True, exist_ok=True)
    self.work_dir.mkdir(parents=True, exist_ok=True)

    if not src.exists():
      await callback("system", {
        "event": "stm32_runtime_build_progress",
        "stage": "download",
        "message": "Downloading the open STM32 QEMU source…",
      })
      await self._run(["git", "clone", "--depth", "1", "--branch", ref, repo, str(src)])

    build_script = src / "build_libqemu-stm32.sh"
    if not build_script.is_file():
      raise RuntimeError(
        "The selected STM32 QEMU source does not contain build_libqemu-stm32.sh. "
        "Set VELXIO_STM32_QEMU_REPO/REF to a compatible PICSimLab fork or provide "
        "VELXIO_STM32_LIB directly."
      )

    await callback("system", {
      "event": "stm32_runtime_build_progress",
      "stage": "compile",
      "message": "Building the STM32 QEMU shared library…",
    })
    await self._run(["bash", str(build_script)], cwd=src)

    built_candidates = [
      src / "build" / "libqemu-stm32.so",
      src / "build" / "libqemu-stm32.dylib",
      src / "build" / "libqemu-stm32.dll",
      src / "build" / "libqemu-arm.so",
    ]
    built = next((p for p in built_candidates if p.is_file()), None)
    if built is None:
      raise RuntimeError(
        "STM32 QEMU build completed but no supported shared library was produced."
      )

    destination = self.runtime_dir / built.name
    shutil.copy2(built, destination)
    self.manifest_path.write_text(json.dumps({
      "repo": repo,
      "ref": ref,
      "runtime": str(destination),
      "cleanup_paths": [str(self.work_dir)],
    }, indent=2), encoding="utf-8")
    return destination

  async def _run(self, command: list[str], cwd: Optional[Path] = None) -> None:
    process = await asyncio.create_subprocess_exec(
      *command,
      cwd=str(cwd) if cwd else None,
      stdout=asyncio.subprocess.PIPE,
      stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
      detail = stderr.decode(errors="replace").strip() or stdout.decode(errors="replace").strip()
      raise RuntimeError(f"Command failed ({process.returncode}): {' '.join(command)}\n{detail[-4000:]}")

  def cleanup_size(self) -> int:
    if not self.work_dir.exists():
      return 0
    total = 0
    for path in self.work_dir.rglob("*"):
      try:
        if path.is_file() and not path.is_symlink():
          total += path.stat().st_size
      except OSError:
        pass
    return total

  async def cleanup_build_files(self) -> dict:
    """Remove only build-owned temporary files, never the finished runtime."""
    before = self.cleanup_size()
    if self.work_dir.exists():
      await asyncio.to_thread(shutil.rmtree, self.work_dir, True)
    try:
      if self.manifest_path.exists():
        self.manifest_path.unlink()
    except OSError:
      pass
    return {"removed_bytes": before, "runtime_kept": True}


stm32_runtime_builder = Stm32RuntimeBuilder()
