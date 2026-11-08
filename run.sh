#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd -P)"
STATE="$ROOT/.run"
BIN="$STATE/bin"
LOGS="$STATE/logs"
VENV="$ROOT/backend/.venv"
BACKEND_PID=""
FRONTEND_PID=""
mkdir -p "$BIN" "$LOGS"
export PATH="$BIN:/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$ROOT"

COMPILER="$(printenv VELXIO_COMPILER 2>/dev/null || printf 'arduino')"
PIO=""
REPAIR=0
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --compiler)
      if [[ "$#" -lt 2 ]]; then
        printf 'Missing compiler name. Choose arduino or platformio\n' >&2
        exit 2
      fi
      COMPILER="$2"
      shift 2
      ;;
    --repair)
      REPAIR=1
      shift
      ;;
    *)
      printf 'Usage: ./run.sh [--compiler arduino|platformio] [--repair]\n' >&2
      exit 2
      ;;
  esac
done

if [[ "$COMPILER" != "arduino" ]] && [[ "$COMPILER" != "platformio" ]]; then
  printf 'Invalid compiler: %s. Choose arduino or platformio\n' "$COMPILER" >&2
  exit 2
fi

if [[ "$REPAIR" == "1" ]]; then
  printf '[velxio] Rebuilding local development dependencies\n'
  rm -rf "$VENV" "$ROOT/node_modules" "$ROOT/frontend/node_modules" "$STATE/tooling"
  rm -f "$STATE/python.sha256" "$STATE/root.sha256" "$STATE/frontend.sha256"
fi

log() { printf '\n[velxio] %s\n' "$*"; }
warn() { printf '\n[velxio] WARNING: %s\n' "$*" >&2; }
fail() { printf '\n[velxio] ERROR: %s\n' "$*" >&2; exit 1; }

kill_tree() {
  local pid="$1" child=""
  if command -v pgrep >/dev/null 2>&1; then
    while IFS= read -r child; do
      [[ -n "$child" ]] && kill_tree "$child"
    done < <(pgrep -P "$pid" || true)
  fi
  kill "$pid" 2>/dev/null || true
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  [[ -z "$FRONTEND_PID" ]] || kill_tree "$FRONTEND_PID"
  [[ -z "$BACKEND_PID" ]] || kill_tree "$BACKEND_PID"
  if [[ "$status" -ne 0 ]]; then warn "Server logs: $LOGS"; fi
  exit "$status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then "$@"
  elif command -v sudo >/dev/null 2>&1; then sudo "$@"
  else fail "Administrator access is required for $1"
  fi
}

ensure_package_manager() {
  case "$(uname -s)" in
    Darwin)
      if ! command -v brew >/dev/null 2>&1; then
        log "Installing Homebrew using its official installer"
        command -v curl >/dev/null 2>&1 || fail "curl is required to install Homebrew"
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
      fi
      command -v brew >/dev/null 2>&1 || fail "Homebrew installation failed"
      ;;
    Linux)
      command -v apt-get >/dev/null 2>&1 || fail "Automatic Linux setup supports apt-based distributions"
      ;;
    *) fail "Use run.sh in macOS or an apt-based Linux terminal" ;;
  esac
}

install_package() {
  if [[ "$(uname -s)" == "Darwin" ]]; then
    brew install "$1"
  else
    if [[ ! -f "$STATE/apt-updated" ]]; then
      as_root apt-get update
      touch "$STATE/apt-updated"
    fi
    as_root apt-get install -y "$2"
  fi
}

ensure_tools() {
  command -v curl >/dev/null 2>&1 || install_package curl curl
  command -v git >/dev/null 2>&1 || install_package git git
}

python_ok() {
  "$1" -c 'import sys; sys.exit(sys.version_info < (3, 12))' >/dev/null 2>&1
}

ensure_python() {
  local candidate=""
  for candidate in python3.12 python3.13 python3.14 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && python_ok "$candidate"; then
      PYTHON="$(command -v "$candidate")"
      return
    fi
  done
  log "Installing Python 3.12"
  install_package python@3.12 python3.12
  if [[ "$(uname -s)" == "Darwin" ]]; then
    PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
  else
    PYTHON="$(command -v python3.12 || true)"
  fi
  [[ -n "$PYTHON" ]] && python_ok "$PYTHON" || fail "Python 3.12+ is unavailable from the package manager"
}

node_ok() {
  command -v node >/dev/null 2>&1 &&
    node -e 'const [a,b]=process.versions.node.split(".").map(Number);process.exit(a>22||(a===22&&b>=12)?0:1)' >/dev/null 2>&1
}

ensure_node() {
  if ! node_ok; then
    log "Installing Node.js 22"
    if [[ "$(uname -s)" == "Darwin" ]]; then
      install_package node@22 nodejs
      export PATH="$(brew --prefix node@22)/bin:$PATH"
    else
      command -v npm >/dev/null 2>&1 || install_package node nodejs
      command -v npm >/dev/null 2>&1 || install_package npm npm
      if ! node_ok; then
        as_root npm install -g n
        as_root n 22
        export PATH="/usr/local/bin:$PATH"
        hash -r
      fi
    fi
  fi
  node_ok || fail "Node.js 22.12+ could not be installed"
  command -v npm >/dev/null 2>&1 || fail "npm is missing"

  local npm_major="$(npm --version | cut -d. -f1)"
  if [[ "$npm_major" =~ ^[0-9]+$ ]] && [[ "$npm_major" -ge 11 ]]; then
    NPM="$(command -v npm)"
  else
    log "Installing npm 11 locally"
    if [[ ! -x "$STATE/tooling/node_modules/.bin/npm" ]] ||
      [[ "$("$STATE/tooling/node_modules/.bin/npm" --version | cut -d. -f1)" -lt 11 ]]; then
      npm install --prefix "$STATE/tooling" --no-audit --no-fund npm@11
    fi
    NPM="$STATE/tooling/node_modules/.bin/npm"
  fi
}

ensure_arduino() {
  if ! command -v arduino-cli >/dev/null 2>&1; then
    log "Installing Arduino CLI"
    if [[ "$(uname -s)" == "Darwin" ]]; then
      brew install arduino-cli
    else
      curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh |
        env BINDIR="$BIN" bash
    fi
  fi
  command -v arduino-cli >/dev/null 2>&1 || fail "arduino-cli installation failed"
}

ensure_qemu() {
  if ! command -v qemu-system-arm >/dev/null 2>&1; then
    log "Installing QEMU ARM emulator"
    install_package qemu qemu-system-arm
  fi
  command -v qemu-system-arm >/dev/null 2>&1 || fail "QEMU installation failed"
  QEMU="$(command -v qemu-system-arm)"
  if ! "$QEMU" -machine help | grep -qF olimex-stm32-h405; then
    warn "This QEMU build lacks the olimex-stm32-h405 machine; STM32 firmware may not boot"
  fi
}

ensure_cores() {
  if [[ "$COMPILER" == "platformio" ]]; then
    log "PlatformIO selected. Skipping Arduino CLI board indexes and core downloads"
    return
  fi
  if [[ "$VELXIO_SKIP_CORES" == "1" ]]; then
    warn "Arduino core setup was skipped. STM32 compilation requires STMicroelectronics:stm32"
    return
  fi

  local stm32_url="https://github.com/stm32duino/BoardManagerFiles/raw/main/package_stmicroelectronics_index.json"
  local pico_url="https://github.com/earlephilhower/arduino-pico/releases/download/global/package_rp2040_index.json"
  local core=""
  local pico_enabled="$VELXIO_INSTALL_PICO"

  log "Repairing Arduino package indexes without changing your global Arduino configuration"
  export ARDUINO_BOARD_MANAGER_ADDITIONAL_URLS="$stm32_url"
  if [[ "$pico_enabled" == "1" ]]; then
    export ARDUINO_BOARD_MANAGER_ADDITIONAL_URLS="$stm32_url $pico_url"
  fi

  if ! arduino-cli core update-index; then
    if [[ "$pico_enabled" == "1" ]]; then
      warn "The RP2040 package index failed. Retrying with the STM32 index only"
      export ARDUINO_BOARD_MANAGER_ADDITIONAL_URLS="$stm32_url"
      pico_enabled=0
      arduino-cli core update-index ||
        fail "STM32 index download failed. Check network access to stm32duino/BoardManagerFiles"
    else
      fail "Arduino package indexes could not be updated. Check the network or run ./run.sh again"
    fi
  fi

  for core in arduino:avr STMicroelectronics:stm32; do
    if ! arduino-cli core list | grep -q "^$core[[:space:]]"; then
      log "Installing missing Arduino core $core"
      if ! arduino-cli core install "$core"; then
        if [[ "$core" != "STMicroelectronics:stm32" ]]; then
          fail "Failed to install $core. Check the Arduino CLI output and retry"
        fi

        warn "STM32 installation failed. Retrying the Arduino CLI download"
        sleep 5
        if ! arduino-cli core install "$core"; then
          log "Attempting to recover the STM32 GCC download from the official xPack release"
          "$PYTHON" "$ROOT/scripts/stage_stm32_toolchain.py" ||
            fail "Unable to cache the official STM32 GCC archive. The upstream download may still be unavailable"
          arduino-cli core install "$core" ||
            fail "STM32 installation failed after verified toolchain recovery. Check the remaining dependency errors"
        fi
      fi
    fi
    if ! arduino-cli core list | grep -q "^$core[[:space:]]"; then
      fail "$core is still missing after setup. Firmware compilation would fail"
    fi
  done

  if [[ "$pico_enabled" == "1" ]]; then
    if ! arduino-cli core list | grep -q '^rp2040:rp2040[[:space:]]'; then
      log "Installing RP2040 board core"
      arduino-cli core install rp2040:rp2040 ||
        warn "RP2040 installation failed. STM32 and AVR are available"
    fi
  fi

  log "Installed Arduino cores:"
  arduino-cli core list
}

fingerprint() {
  "$PYTHON" -c 'import hashlib,pathlib,sys;print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$1"
}

ensure_python_deps() {
  local requirements="$ROOT/backend/requirements.txt"
  local marker="$STATE/python.sha256"
  local expected="$(fingerprint "$requirements")"
  if [[ ! -x "$VENV/bin/python" ]] ||
    ! "$VENV/bin/python" -c 'import fastapi,uvicorn,pydantic_settings,mcp,esptool,wasmtime,zstandard' >/dev/null 2>&1; then
    rm -f "$marker"
  fi
  if [[ -d "$VENV" ]] && [[ ! -x "$VENV/bin/python" ]]; then
    rm -rf "$VENV"
  fi
  if [[ ! -d "$VENV" ]]; then
    log "Creating Python virtual environment"
    if ! "$PYTHON" -m venv "$VENV"; then
      if [[ "$(uname -s)" == "Linux" ]]; then
        install_package python@3.12 python3.12-venv
        "$PYTHON" -m venv "$VENV"
      else
        fail "Unable to create Python virtual environment"
      fi
    fi
  fi
  if [[ ! -f "$marker" ]] || [[ "$(cat "$marker")" != "$expected" ]]; then
    log "Installing and repairing backend dependencies"
    "$VENV/bin/python" -m pip install --upgrade pip
    "$VENV/bin/python" -m pip install -r "$requirements"
    printf '%s\n' "$expected" > "$marker"
  fi
}

ensure_platformio() {
  if [[ "$COMPILER" != "platformio" ]]; then
    return
  fi

  local candidate=""
  for candidate in "$(command -v pio || true)" "$(command -v platformio || true)" "$VENV/bin/pio"; do
    if [[ -n "$candidate" ]] && [[ -x "$candidate" ]] &&
      "$candidate" --version >/dev/null 2>&1; then
      PIO="$candidate"
      log "Found PlatformIO: $PIO"
      return
    fi
  done

  log "Installing PlatformIO CLI in the backend virtual environment"
  "$VENV/bin/python" -m pip install --upgrade platformio
  PIO="$VENV/bin/pio"
  [[ -x "$PIO" ]] && "$PIO" --version >/dev/null 2>&1 ||
    fail "PlatformIO installation failed"
}

ensure_npm_deps() {
  local directory="$1"
  local binary="$2"
  local marker="$3"
  local expected="$(fingerprint "$directory/package.json")"
  if [[ ! -x "$directory/node_modules/.bin/$binary" ]] ||
    [[ ! -f "$marker" ]] || [[ "$(cat "$marker")" != "$expected" ]]; then
    log "Installing and repairing npm dependencies in $directory"
    (cd "$directory" && "$NPM" install --include=optional --no-audit --no-fund)
    printf '%s\n' "$expected" > "$marker"
  fi
}

check_port() {
  "$PYTHON" -c 'import socket,sys;s=socket.socket();s.bind(("127.0.0.1",int(sys.argv[1])));s.close()' "$1" ||
    fail "Port $1 is in use. Close the existing server and retry"
}

wait_for_url() {
  local url="$1" pid="$2" attempt=0
  while [[ "$attempt" -lt 120 ]]; do
    if curl --noproxy '*' --max-time 2 -fsS -o /dev/null "$url" 2>/dev/null; then return 0; fi
    if ! kill -0 "$pid" 2>/dev/null; then return 1; fi
    attempt=$((attempt + 1))
    sleep 1
  done
  return 1
}

VELXIO_SKIP_CORES="$(printenv VELXIO_SKIP_CORES 2>/dev/null || printf '0')"
VELXIO_INSTALL_PICO="$(printenv VELXIO_INSTALL_PICO 2>/dev/null || printf '0')"

log "Checking system dependencies"
ensure_package_manager
ensure_tools
ensure_python
ensure_node
if [[ "$COMPILER" == "arduino" ]]; then
  ensure_arduino
fi
ensure_qemu
ensure_cores
ensure_python_deps
ensure_platformio
ensure_npm_deps "$ROOT" tsx "$STATE/root.sha256"
ensure_npm_deps "$ROOT/frontend" vite "$STATE/frontend.sha256"
check_port 8001
check_port 5173

log "Starting backend"
(
  cd "$ROOT/backend"
  exec env VELXIO_LOCAL_STM32=1 VELXIO_COMPILER="$COMPILER" VELXIO_PLATFORMIO_BIN="$PIO" QEMU_STM32_BIN="$QEMU" \
    "$VENV/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8001
) > "$LOGS/backend.log" 2>&1 &
BACKEND_PID=$!

if ! wait_for_url http://127.0.0.1:8001/health "$BACKEND_PID"; then
  tail -n 40 "$LOGS/backend.log" >&2 || true
  fail "Backend startup failed"
fi

log "Starting frontend"
(
  cd "$ROOT/frontend"
  exec "$NPM" run dev -- --host 127.0.0.1 --port 5173 --strictPort
) > "$LOGS/frontend.log" 2>&1 &
FRONTEND_PID=$!

if ! wait_for_url http://127.0.0.1:5173 "$FRONTEND_PID"; then
  tail -n 40 "$LOGS/frontend.log" >&2 || true
  fail "Frontend startup failed"
fi

printf '\nVelxio is running\n'
printf '  Application: http://localhost:5173\n'
printf '  Backend API: http://localhost:8001/api/docs\n'
printf '  API health:  http://localhost:8001/health\n'
printf '  Logs:        %s\n' "$LOGS"
printf '  STM32 QEMU:  %s\n' "$QEMU"
printf '  Compiler:    %s\n' "$COMPILER"
printf '  Stop:        Ctrl+C\n\n'

while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 2
done
fail "A server stopped unexpectedly"
