import hashlib
import json
import platform
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


def host_name(system=None, machine=None):
  system = (system or platform.system()).lower()
  machine = (machine or platform.machine()).lower()
  if machine in ("arm64", "aarch64"):
    if system == "darwin":
      return "arm64-apple-darwin"
    if system == "linux":
      return "aarch64-linux-gnu"
  if machine in ("x86_64", "amd64"):
    if system == "darwin":
      return "x86_64-apple-darwin"
    if system == "linux":
      return "x86_64-pc-linux-gnu"
  raise ValueError(f"Unsupported toolchain host: {system}/{machine}")


def select_archive(index, host):
  package = next(
    item for item in index["packages"]
    if item["name"] == "STMicroelectronics"
  )
  platforms = [
    item for item in package["platforms"]
    if item["architecture"] == "stm32"
  ]
  if not platforms:
    raise ValueError("No STM32 platform found in the Arduino package index")
  latest = max(
    platforms,
    key=lambda item: tuple(int(part) for part in re.findall(r"\d+", item["version"]))
  )
  dependency = next(
    item for item in latest["toolsDependencies"]
    if item["name"] == "xpack-arm-none-eabi-gcc"
  )
  tool = next(
    item for item in package["tools"]
    if item["name"] == dependency["name"]
    and item["version"] == dependency["version"]
  )
  archive = next(item for item in tool["systems"] if item["host"] == host)
  url = archive["url"]
  parsed = urlparse(url)
  if (
    parsed.scheme != "https"
    or parsed.hostname != "github.com"
    or not parsed.path.startswith("/xpack-dev-tools/arm-none-eabi-gcc-xpack/releases/download/")
  ):
    raise ValueError("Toolchain URL is not an official xPack release")

  filename = archive["archiveFileName"]
  if Path(filename).name != filename or not filename.endswith(".tar.gz"):
    raise ValueError("Unexpected toolchain archive filename")
  algorithm, separator, checksum = archive["checksum"].partition(":")
  if algorithm != "SHA-256" or not separator or not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
    raise ValueError("Unexpected toolchain checksum format")

  return {
    "url": url,
    "filename": filename,
    "checksum": checksum.lower(),
    "size": int(archive["size"]),
    "version": tool["version"],
  }


def matches_archive(path, archive):
  if not path.is_file() or path.stat().st_size != archive["size"]:
    return False
  digest = hashlib.sha256()
  with path.open("rb") as source:
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
      digest.update(chunk)
  return digest.hexdigest() == archive["checksum"]


def arduino_directories():
  result = subprocess.run(
    ["arduino-cli", "config", "dump", "--json"],
    check=True,
    capture_output=True,
    text=True,
  )
  settings = json.loads(result.stdout)
  directories = settings.get("directories", {})
  data = directories.get("data")
  if not data:
    data = "~/Library/Arduino15" if platform.system() == "Darwin" else "~/.arduino15"
  data_directory = Path(data).expanduser()
  downloads = directories.get("downloads")
  downloads_directory = Path(downloads).expanduser() if downloads else data_directory / "staging"
  return data_directory, downloads_directory


def stage_toolchain():
  data_directory, downloads_directory = arduino_directories()
  index_path = data_directory / "package_stmicroelectronics_index.json"
  if not index_path.is_file():
    raise ValueError(f"Missing STM32 package index: {index_path}")

  index = json.loads(index_path.read_text(encoding="utf-8"))
  archive = select_archive(index, host_name())
  destination = downloads_directory / "packages" / archive["filename"]
  destination.parent.mkdir(parents=True, exist_ok=True)

  if matches_archive(destination, archive):
    print(f"[velxio] Verified existing Arduino toolchain archive: {destination}", flush=True)
    return

  if destination.exists():
    destination.unlink()

  partial = destination.with_name(destination.name + ".partial")
  print(
    f"[velxio] Downloading official xPack GCC {archive['version']} to Arduino cache "
    f"({archive['size'] // 1000000} MB)",
    flush=True,
  )
  command = [
    "curl",
    "--fail",
    "--location",
    "--retry", "8",
    "--retry-all-errors",
    "--retry-delay", "5",
    "--connect-timeout", "30",
    "--speed-limit", "1024",
    "--speed-time", "120",
    "--continue-at", "-",
    "--output", str(partial),
    archive["url"],
  ]
  subprocess.run(command, check=True)

  if not matches_archive(partial, archive):
    partial.unlink(missing_ok=True)
    raise ValueError("The toolchain download failed size or SHA256 verification")

  partial.replace(destination)
  print(f"[velxio] Verified and cached: {destination}", flush=True)


def main():
  try:
    stage_toolchain()
  except (ValueError, OSError, KeyError, StopIteration, subprocess.CalledProcessError) as error:
    print(f"[velxio] Official xPack download recovery failed: {error}", file=sys.stderr)
    return 1
  return 0


if __name__ == "__main__":
  sys.exit(main())
