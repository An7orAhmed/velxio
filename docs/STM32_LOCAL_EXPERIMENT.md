# Local STM32 simulation

Velxio has two independent, opt-in STM32 firmware runners. Neither uses the original commercial STM32 service.

## The onboard LED and why a successful build was not blinking

The original QEMU runner uses the `olimex-stm32-h405` machine. It models an STM32F405 rather than the selected STM32F401CE, and QEMU has no STM32 GPIO controller. The firmware may compile and start without providing any pin-state events. The onboard LED therefore stays unchanged, even when `digitalWrite(LED_BUILTIN, ...)` executes in the firmware.

The new experimental Renode runner loads the PlatformIO-generated ELF on Renode's STM32F4 model. It observes GPIO ODR and BSRR register writes, converts them into linear pin numbers and publishes `gpio_change` events through the existing WebSocket bridge. The Black Pill's onboard LED is on PC13, active low, mapped to linear pin 45.

This uses real MCU register writes from emulated firmware, not a hardcoded timer animation. The user LED can only blink if the firmware boots, writes PC13 and Renode runs its supported peripherals successfully.

## Run

On macOS, the following command selects the Renode engine automatically, and installs Renode using its official Homebrew tap if necessary:

~~~sh
git switch dev
git pull origin dev
./run.sh --compiler platformio
~~~

The explicit engine selection is:

~~~sh
./run.sh --compiler platformio --stm32-engine renode
~~~

To use the old UART-only QEMU approximation:

~~~sh
./run.sh --compiler platformio --stm32-engine qemu
~~~

On Linux, Renode must be installed separately; the default STM32 runner remains QEMU. Set `VELXIO_RENODE_BIN` to an executable path if Renode is not on your PATH. PlatformIO compilation can work without Renode, but the STM32 canvas LED cannot be driven by QEMU.

## Supported firmware and accuracy

Renode currently supports the `stm32-blackpill-f401` Velxio board only. Use the matching PlatformIO `blackpill_f401ce` environment. It expects an ARM ELF with Flash segments linked at the normal STM32 Flash address; PlatformIO's `firmware.elf` is used automatically when you click Compile.

Renode's STM32F4 CPU, SysTick and GPIO models are not an exact simulation of the STM32F401CE. UART data and input peripherals are not yet wired to this experimental engine. ADC, timers, DMA, PWM, interrupt timing, frequency accuracy and external circuits need separate tests.

The STM32F401RCT6 is a different package/board from the STM32F401CEU6. In particular, this simulator is NOT suitable for validating high power inverter PWM complementary outputs, dead time, shoot-through protection or current limiting.

## Tests

~~~sh
cd backend
python -m unittest tests.test_local_stm32 tests.test_local_stm32_renode -v
~~~

The unit tests verify firmware decoding, runner lifecycle, Renode script generation and GPIO output event parsing for PC13. They do not replace a real end-to-end firmware test in Renode.

Project source is covered by Velxio's AGPLv3 terms.
