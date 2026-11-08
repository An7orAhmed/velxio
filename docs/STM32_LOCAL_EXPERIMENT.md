# Local STM32 QEMU experiment

This fork contains an opt-in STM32 test backend built around the independent, open-source QEMU ARM system emulator. It does not include Velxio's private STM32 manager, licensed binaries, or hosted authentication.

## Scope

The first experiment accepts the existing \`stm32-blackpill-f401\` board and boots ELF or raw BIN firmware using QEMU's \`olimex-stm32-h405\` machine. This is an STM32F405 approximation, not an STM32F401 emulator. Serial output is forwarded to Velxio's Serial Monitor. GPIO, ADC, I2C, SPI, TIM1, DMA, PWM, and analog interactions are not connected to the circuit canvas.

STM32F401RCT6 is not the STM32F401CE on the Black Pill. Its pin count, Flash size and peripheral setup can differ. Inverter firmware relying on precise complementary TIM1 PWM, dead time, DMA and clock configuration cannot be validated with this approximation.

## Local setup

On macOS:

~~~sh
brew install qemu
qemu-system-arm -machine help | grep olimex-stm32-h405
~~~

On Ubuntu:

~~~sh
sudo apt-get update
sudo apt-get install qemu-system-arm
qemu-system-arm -machine help | grep olimex-stm32-h405
~~~

The QEMU package must provide the \`olimex-stm32-h405\` machine. If it does not, use an independently built QEMU version that does. The runtime executable is resolved from \`QEMU_STM32_BIN\`, or from \`qemu-system-arm\` in \`PATH\`.

Start the backend from the repository:

~~~sh
git checkout experiment/stm32-local-qemu
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
VELXIO_LOCAL_STM32=1 uvicorn app.main:app --host 127.0.0.1 --port 8001
~~~

In another terminal:

~~~sh
cd frontend
npm install
npm run dev
~~~

Open the local Vite URL, add the STM32F401 Black Pill, load firmware, and run it.

If \`qemu-system-arm\` is outside \`PATH\`, start the backend with \`QEMU_STM32_BIN=/absolute/path/to/qemu-system-arm\`.

For a local firmware test, prefer an ARM ELF file. A raw BIN also works when it is linked for the flash address expected by the emulated board. Intel HEX is currently rejected with an explicit error. You can convert an Intel HEX file using:

~~~sh
arm-none-eabi-objcopy -I ihex -O binary firmware.hex firmware.bin
~~~

## Tests

~~~sh
cd backend
python -m unittest tests.test_local_stm32 -v
~~~

The unit tests exercise the backend protocol and process lifecycle with a mock QEMU process. They do not prove a real STM32 firmware boots, or that peripheral timings match hardware.

## Follow-up experiments

1. Run a Cortex-M4 ELF with simple UART output and inspect QEMU startup logs.
2. Check firmware clock initialization against the F405 model.
3. Implement a freely licensed GPIO event bridge and test PA0, PB0 and PC13.
4. Add timing and peripheral fidelity tests before making claims about inverter PWM.

The code is covered by Velxio's AGPLv3 terms. This branch is intended for local experimentation and has no connection to the upstream commercial runtime.
