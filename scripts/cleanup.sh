#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${1:?Repository path is required}"
MODE="${2:?Cleanup mode is required}"
DRY_RUN="${3:-0}"

if [[ "$MODE" != "tools" ]] && [[ "$MODE" != "all" ]]; then
  printf '[velxio] Unknown cleanup mode: %s\n' "$MODE" >&2
  exit 2
fi

if [[ "$DRY_RUN" != "0" ]] && [[ "$DRY_RUN" != "1" ]]; then
  printf '[velxio] Invalid dry run option\n' >&2
  exit 2
fi

if [[ ! -f "$ROOT/run.sh" ]] ||
  [[ ! -f "$ROOT/backend/app/main.py" ]] ||
  [[ ! -f "$ROOT/frontend/package.json" ]]; then
  printf '[velxio] Refusing cleanup: this is not a Velxio checkout\n' >&2
  exit 1
fi

if [[ "$MODE" == "all" ]]; then
  if [[ ! -e "$ROOT/.git" ]] || [[ "$(basename "$ROOT")" != "velxio" ]]; then
    printf '[velxio] Refusing full removal: expected a Git checkout named velxio\n' >&2
    exit 1
  fi
fi

if [[ -z "${HOME:-}" ]] || [[ "$HOME" == "/" ]]; then
  printf '[velxio] Refusing cleanup: HOME is not a valid user directory\n' >&2
  exit 1
fi

HOME_DIR="$(cd "$HOME" && pwd -P)"
if [[ "$HOME_DIR" == "/" ]] || [[ "$HOME_DIR" == "$ROOT" ]]; then
  printf '[velxio] Refusing cleanup with an unsafe home directory\n' >&2
  exit 1
fi

PROJECT_PATHS=(
  "$ROOT/.run"
  "$ROOT/.cache"
  "$ROOT/node_modules"
  "$ROOT/frontend/node_modules"
  "$ROOT/frontend/dist"
  "$ROOT/frontend/.vite"
  "$ROOT/backend/.venv"
  "$ROOT/backend/venv"
  "$ROOT/backend/dist"
  "$ROOT/backend/.pytest_cache"
)

SHARED_PATHS=(
  "$HOME_DIR/Library/Arduino15"
  "$HOME_DIR/.arduino15"
  "$HOME_DIR/.platformio"
  "$HOME_DIR/.cache/arduino"
  "$HOME_DIR/.cache/platformio"
  "$HOME_DIR/Library/Caches/arduino"
  "$HOME_DIR/Library/Caches/arduino-cli"
  "$HOME_DIR/Library/Caches/platformio"
)

for path in "${SHARED_PATHS[@]}"; do
  if [[ "$ROOT/" == "$path/"* ]]; then
    printf '[velxio] Refusing cleanup: the checkout is inside a toolchain directory (%s)\n' "$path" >&2
    exit 1
  fi
done

printf '\n[velxio] Cleanup mode: %s\n' "$MODE"
printf '[velxio] Repository: %s\n' "$ROOT"
printf '\nLocal build environments and generated files:\n'

found=0
for path in "${PROJECT_PATHS[@]}"; do
  if [[ -e "$path" ]] || [[ -L "$path" ]]; then
    printf '  %s\n' "$path"
    found=1
  fi
done
if [[ "$found" == "0" ]]; then
  printf '  No generated project directories found\n'
fi

printf '\nShared Arduino and PlatformIO toolchains and download caches:\n'
found=0
for path in "${SHARED_PATHS[@]}"; do
  if [[ -e "$path" ]] || [[ -L "$path" ]]; then
    printf '  %s\n' "$path"
    found=1
  fi
done
if [[ "$found" == "0" ]]; then
  printf '  No shared toolchain directories found\n'
fi

if [[ "$MODE" == "all" ]]; then
  printf '\nFull removal also deletes the entire Git repository, its source files, changes, and untracked files:\n'
  printf '  %s\n' "$ROOT"
  if command -v git >/dev/null 2>&1; then
    changes="$(git -C "$ROOT" status --porcelain --untracked-files=normal 2>/dev/null || true)"
    if [[ -n "$changes" ]]; then
      printf '\nUncommitted or untracked files will be lost:\n'
      printf '%s\n' "$changes"
    fi
  fi
fi

printf '\nThese toolchain directories may be shared by other Arduino and PlatformIO projects.\n'
printf 'The cleanup does not delete your Arduino sketches, system-wide Python, Node.js, QEMU, Homebrew, or shared CLI installations.\n'

if [[ "$DRY_RUN" == "1" ]]; then
  printf '\n[velxio] Dry run complete. Nothing was deleted.\n'
  exit 0
fi

if [[ ! -t 0 ]]; then
  printf '\n[velxio] Refusing to delete files without an interactive terminal. Use --dry-run to preview.\n' >&2
  exit 1
fi

if [[ "$MODE" == "all" ]]; then
  phrase="DELETE VELXIO"
else
  phrase="CLEAN TOOLCHAINS"
fi

printf '\nType %s to confirm: ' "$phrase"
IFS= read -r confirmation || true
if [[ "$confirmation" != "$phrase" ]]; then
  printf '\n[velxio] Cleanup cancelled. No files were deleted.\n'
  exit 0
fi

remove_path() {
  local path="$1"
  if [[ ! -e "$path" ]] && [[ ! -L "$path" ]]; then
    return
  fi

  if [[ "$path" == "/" ]] || [[ "$path" == "$HOME_DIR" ]] || [[ "$path" == "$ROOT" ]]; then
    printf '[velxio] Refusing unsafe deletion: %s\n' "$path" >&2
    exit 1
  fi

  printf '[velxio] Removing %s\n' "$path"
  rm -rf -- "$path"
}

for path in "${PROJECT_PATHS[@]}"; do
  remove_path "$path"
done

for path in "${SHARED_PATHS[@]}"; do
  remove_path "$path"
done

if [[ "$MODE" == "all" ]]; then
  printf '\n[velxio] Removing the entire checkout: %s\n' "$ROOT"
  cd "$HOME_DIR"
  rm -rf -- "$ROOT"
  printf '[velxio] Velxio checkout and selected toolchain caches removed.\n'
else
  printf '\n[velxio] Arduino/PlatformIO toolchain caches and local build dependencies removed.\n'
  printf '[velxio] Your Velxio source files and Git history are preserved.\n'
fi
