# Self-hosted STM32 backend

This branch adds an open extension path for STM32 simulation in a self-hosted Velxio backend. The first target is **STM32F401CE / Black Pill** (`stm32-blackpill-f401`).

## Status

The Velxio integration is implemented around the existing public STM32 WebSocket protocol and `stm32_worker.py` bridge. The open PICSimLab QEMU fork currently does **not** provide an STM32F401 machine/SoC model, so exact F401 execution still requires a compatible runtime that actually implements STM32F401CE.

Do not map the F401 board to an F405 machine just because both are Cortex-M4. Their memory/peripheral details differ and STM32 HAL/register-level firmware can depend on those differences.

## Architecture

```text
Stm32Bridge.ts
  -> /api/simulation/ws/<client-id>
  -> simulation.py
  -> dispatch_ws_sim_message()
  -> app.services.stm32_oss.handler
  -> Stm32OssManager
  -> app.services.stm32_worker
  -> PICSimLab-compatible libqemu-stm32
```

The extension registers through Velxio's existing simulation hook seam and only claims `stm32-blackpill-f401`. Other board kinds remain available to other installed backends.

A WebSocket session can be reserved before firmware is compiled. When `stm32_load_firmware` arrives, the manager starts/restarts the worker with the new firmware.

## Runtime selection

If you already have a compatible shared library, point Velxio at it:

```bash
export VELXIO_STM32_LIB=/absolute/path/to/libqemu-stm32.so
export VELXIO_STM32_F401_MACHINE=<real-f401-machine-name>
```

On macOS the library can be a `.dylib`.

`VELXIO_STM32_F401_MACHINE` must identify a genuine STM32F401-compatible machine in the selected runtime. It intentionally has no unsafe F405 fallback.

## Automatic runtime build

If `VELXIO_STM32_LIB` is not set and no managed runtime exists, the backend can build the PICSimLab-compatible STM32 QEMU library on first use.

Defaults:

```text
VELXIO_STM32_QEMU_REPO=https://github.com/lcgamboa/qemu.git
VELXIO_STM32_QEMU_REF=picsimlab-stm32
VELXIO_STM32_RUNTIME_DIR=/tmp/velxio-stm32-runtime
```

Override any of them with environment variables.

Required host tools are checked before starting the build. Common requirements are Git, Bash, Make, Ninja, Perl and standard shell utilities. The macOS build script also requires `uv`.

Velxio does **not** install system packages automatically.

## Post-build cleanup

After a managed runtime build succeeds, the frontend displays a permission dialog showing the approximate temporary build size.

If the user chooses **Clean up**:

- the downloaded QEMU source/build directory managed by this feature is removed;
- the compiled emulator shared library is kept in the runtime directory;
- no system-wide compiler, package, Homebrew/apt package, Ninja, Perl, Git or other host tool is removed.

If the user chooses **Keep files**, nothing is deleted.

This cleanup request is sent through the existing STM32 WebSocket lane using a reserved `stm32_bus_attrs` owner; no additional HTTP endpoint is required.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `VELXIO_STM32_LIB` | Use an existing compatible QEMU shared library instead of the managed builder. |
| `VELXIO_STM32_RUNTIME_DIR` | Root for the managed final runtime and temporary build directory. |
| `VELXIO_STM32_QEMU_REPO` | Source repository cloned by the managed builder. |
| `VELXIO_STM32_QEMU_REF` | Branch/tag/ref cloned by the managed builder. |
| `VELXIO_STM32_F401_MACHINE` | Exact F401 machine name exposed by the selected runtime. Required before firmware execution. |

## Current F401 emulator work

The maintained `lcgamboa/qemu` `picsimlab-stm32` branch contains STM32F1/F2/F405-family models but no STM32F401 SoC/machine implementation. The next emulator-side milestone is therefore to add an actual F401 model, preferably by sharing reusable STM32F4 peripheral implementations while declaring the correct F401-specific memory map, capacities, interrupt map, clocks and available peripherals.

Initial validation should progress through:

1. reset/vector-table boot;
2. bare-metal loop;
3. RCC + GPIO blink;
4. USART output;
5. SysTick and interrupt entry/return;
6. STM32 HAL clock initialization;
7. timers/PWM;
8. I2C/SPI external devices;
9. ADC/DMA;
10. FreeRTOS context switching.

## Licensing

The Velxio fork remains under its existing **GNU AGPLv3** terms. The QEMU/PICSimLab runtime and any emulator-side changes are also subject to their own upstream licenses. This document does not change or replace those licenses.
