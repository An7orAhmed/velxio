# macOS development setup

Run the automated manual setup from the repository root:

```bash
./scripts/macos-dev.sh
```

If the executable bit was lost after downloading the source as an archive, use:

```bash
bash scripts/macos-dev.sh
```

The script installs or checks Homebrew, Python 3.12 and Node.js 18+, creates the backend virtual environment, installs backend dependencies including PlatformIO, prepares AVR, RP2040, STM32 and ESP32 PlatformIO platforms, installs frontend packages, then starts the FastAPI backend on port 8001 and Vite frontend on port 5173.

Use `--setup-only` to install everything without starting the servers. Use `--no-open` to keep the browser from opening automatically.

Velxio's Arduino build API now uses PlatformIO. The existing `ArduinoCLIService` import name is kept only as a compatibility shim for the current API and MCP code while the project is migrated.
