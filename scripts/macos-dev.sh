#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
setup_only=0
no_open=0

for arg in "$@"; do
  case "$arg" in
    --setup-only) setup_only=1 ;;
    --no-open) no_open=1 ;;
    *)
      echo "Unknown option: $arg"
      echo "Usage: bash scripts/macos-dev.sh [--setup-only] [--no-open]"
      exit 1
      ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script is for macOS."
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "Installing Homebrew..."
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
fi

if ! command -v python3.12 >/dev/null 2>&1; then
  echo "Installing Python 3.12..."
  brew install python@3.12
fi

need_node=1
if command -v node >/dev/null 2>&1; then
  major="$(node -p 'process.versions.node.split(".")[0]')"
  if [[ "$major" -ge 18 ]]; then
    need_node=0
  fi
fi

if [[ "$need_node" -eq 1 ]]; then
  echo "Installing Node.js..."
  brew install node
fi

py="$(command -v python3.12)"
venv="$root/backend/.venv"

if [[ ! -x "$venv/bin/python" ]]; then
  echo "Creating backend virtual environment..."
  "$py" -m venv "$venv"
fi

vpy="$venv/bin/python"
pip="$venv/bin/pip"
pio="$venv/bin/pio"

"$vpy" -m pip install --upgrade pip wheel
"$pip" install -r "$root/backend/requirements.txt"

# Keep the backend subprocess environment aligned with the virtualenv even
# though this script launches executables by absolute path instead of sourcing
# activate. PlatformIOService and any child process can therefore resolve pio.
export PATH="$venv/bin:$PATH"
export VELXIO_PIO_PATH="$pio"
export PLATFORMIO_DISABLE_UPGRADE_CHECK=true

if [[ ! -x "$pio" ]]; then
  echo "PlatformIO executable was not installed at: $pio"
  echo "Try: $pip install 'platformio>=6.2.0,<7'"
  exit 1
fi

platforms=(
  "platformio/atmelavr"
  "https://github.com/maxgerhardt/platform-raspberrypi.git"
  "platformio/ststm32"
  "platformio/espressif32"
)

for p in "${platforms[@]}"; do
  echo "Preparing PlatformIO platform: $p"
  "$pio" pkg install --global --platform "$p"
done

cd "$root/frontend"
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi

# Server/Docker installs use /var/lib/velxio-build by default. A normal macOS
# user cannot create that path, so manual development keeps artifacts inside
# the checkout unless the caller explicitly supplies another writable path.
export VELXIO_ARTIFACT_CACHE="${VELXIO_ARTIFACT_CACHE:-$root/.cache/velxio-build/artifacts}"
mkdir -p "$VELXIO_ARTIFACT_CACHE"

# The open STM32 runtime is also kept in the repo-local cache. The backend will
# build it on demand when an STM32F401xx target is selected.
export VELXIO_STM32_AUTO_BUILD="${VELXIO_STM32_AUTO_BUILD:-1}"
export VELXIO_STM32_RUNTIME_DIR="${VELXIO_STM32_RUNTIME_DIR:-$root/.cache/stm32-runtime}"
export VELXIO_STM32_CLEANUP_PROMPT="${VELXIO_STM32_CLEANUP_PROMPT:-1}"
mkdir -p "$VELXIO_STM32_RUNTIME_DIR"

if [[ "$setup_only" -eq 1 ]]; then
  echo
  echo "Setup complete."
  echo "PlatformIO: $pio"
  echo "Artifact cache: $VELXIO_ARTIFACT_CACHE"
  echo "STM32 runtime: $VELXIO_STM32_RUNTIME_DIR"
  echo "Run: bash scripts/macos-dev.sh"
  exit 0
fi

for port in 8001 5173; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is already in use. Stop the existing process and run again."
    exit 1
  fi
done

bp=""
fp=""

cleanup() {
  [[ -n "$fp" ]] && kill "$fp" >/dev/null 2>&1 || true
  [[ -n "$bp" ]] && kill "$bp" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

(
  cd "$root/backend"
  exec "$venv/bin/uvicorn" app.main:app --reload --port 8001
) &
bp=$!

(
  cd "$root/frontend"
  export VITE_API_BASE="http://127.0.0.1:8001/api"
  exec npm run dev -- --host 127.0.0.1 --port 5173
) &
fp=$!

wait_port() {
  local port="$1"
  local i
  for i in $(seq 1 60); do
    if nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

if ! wait_port 8001; then
  echo "Backend did not start on port 8001."
  exit 1
fi

if ! wait_port 5173; then
  echo "Frontend did not start on port 5173."
  exit 1
fi

echo
echo "Velxio is running."
echo "Frontend: http://127.0.0.1:5173"
echo "Backend:  http://127.0.0.1:8001"
echo "Compiler: PlatformIO ($pio)"
echo "Artifact cache: $VELXIO_ARTIFACT_CACHE"
echo "STM32 runtime: $VELXIO_STM32_RUNTIME_DIR"
echo "Press Ctrl+C to stop both servers."

if [[ "$no_open" -eq 0 ]]; then
  open "http://127.0.0.1:5173"
fi

wait "$fp" "$bp"