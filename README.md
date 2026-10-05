# Velxio

Velxio is now a small STM32F401xx firmware simulator for the Powerbox sine wave inverter.

The old browser IDE, compilers, ESP32 emulation, generic circuit editor, MCP server, news, library manager and other unrelated features are intentionally gone from this branch. The simulator has one job: boot the real STM32 firmware, give it repeatable hardware inputs and capture what the inverter firmware does.

## What it simulates

The built in board is STM32F401RCT6 with 256 KB flash, 64 KB SRAM, 25 MHz HSE assumptions and an 84 MHz core. The local Renode platform includes Cortex M4, NVIC and SysTick, RCC, PWR, flash, GPIO A/B, TIM1, TIM2, ADC1 and DMA2.

The Powerbox profile uses these signals:

| Signal | Pin |
| --- | --- |
| battery ADC | PA0 |
| mains ADC | PA1 |
| output feedback ADC | PA2 |
| current ADC | PA3 |
| NTC ADC | PA4 |
| front panel ladder ADC | PA5 |
| bridge CH1 | PA8 / PB13 |
| bridge CH2 | PA9 / PB14 |
| transfer relay | PB7 |
| buzzer | PB8 |
| fan | PB9 |
| microswitch | PB10 |
| hardware break | PB12 |

The default ADC profile gives mains and output feedback a 50 Hz biased waveform around 2048 counts. Everything is in raw 12 bit ADC counts so there is no hidden voltage calibration.

## Install

Python 3.10 or newer is enough for Velxio itself.

```sh
pip install -e .
```

For the MCU execution backend, install Renode or Docker. With Docker installed, Velxio can use `antmicro/renode:latest` automatically.

## Run firmware

```sh
velxio firmware.hex
```

Or use an ELF:

```sh
velxio firmware.elf
```

Run a longer virtual test:

```sh
velxio firmware.hex --seconds 1
```

Force a backend:

```sh
velxio firmware.hex --backend native
velxio firmware.hex --backend docker
```

A run directory is created automatically. It contains `hardware.json`, `run.resc`, `renode.log`, `trace.csv` and `summary.json`.

## JSON hardware override

You do not need JSON for the normal Powerbox setup. Use it when you want a different battery level, mains amplitude, frequency, panel code or runtime.

```json
{
  "simulation": {
    "seconds": 0.5
  },
  "adc": {
    "PA0": {
      "mode": "constant",
      "value": 3000
    },
    "PA1": {
      "mode": "sine",
      "offset": 2048,
      "amplitude": 850,
      "frequency_hz": 50
    },
    "PA3": {
      "mode": "constant",
      "value": 2200
    }
  },
  "gpio": {
    "PB10": 1,
    "PB12": 1
  }
}
```

Run it with:

```sh
velxio firmware.hex --config hardware.json
```

Only values present in your file override the built in profile.

## Trace

The runner watches the registers that matter to this firmware instead of trying to pretend it is a SPICE simulator.

`trace.csv` records TIM1 mode, enable, prescaler, ARR, CCR1, CCR2 and BDTR writes, TIM2 timing writes and GPIOB output writes. `summary.json` decodes the final TIM1 mode, center aligned carrier frequency, dead time, CCR range and TIM2 tick.

That makes it useful for checking firmware behavior like:

* TIM1 carrier setup
* center aligned mode
* SPWM duty updates
* inverter versus charge output enables
* dead time register value
* TIM2 control tick
* relay, buzzer and fan changes

## Important limit

This is a firmware simulator, not a MOSFET, transformer or mains power model. Renode's current STM32 timer model does not fully model TIM1 complementary outputs and break/dead time behavior as real pins. Velxio therefore treats the TIM1 register writes as the source of truth for those checks. It can tell you what the firmware configured and how CCR1/CCR2 moved, but it is not proof that a real bridge is electrically safe.

The ADC side is stimulus based for now. PA2 does not yet close a transformer feedback loop from the generated PWM. That is the next useful model to add if the basic firmware run works.

## Prepare without running

This is useful on a machine that does not have Renode or Docker yet:

```sh
velxio firmware.hex --prepare-only --out build/sim
```

You can inspect or run `build/sim/run.resc` manually later.

## Test

```sh
pip install pytest
pytest
```
