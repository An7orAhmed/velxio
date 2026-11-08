import unittest
from pathlib import Path
from unittest.mock import patch

from app.local_stm32 import LocalSession
from app.local_stm32_renode import (
  GPIO_OUTPUT_RE,
  build_renode_script,
  parse_gpio_write,
)


class RenodeGpioTests(unittest.TestCase):
  def test_script_loads_stm32f4_platform_and_installs_gpio_hooks(self):
    script = build_renode_script(Path("/tmp/test.elf"))
    self.assertIn("machine LoadPlatformDescription @platforms/cpus/stm32f4.repl", script)
    self.assertIn("sysbus LoadELF @/tmp/test.elf", script)
    self.assertIn('SetHookBeforePeripheralWrite gpioPortC "', script)
    self.assertIn("VELXIO_GPIO_WRITE C", script)
    self.assertTrue(script.rstrip().endswith("start"))

  def test_onboard_active_low_led_follows_pc13_bit_set_and_reset(self):
    outputs = {}
    high = parse_gpio_write("VELXIO_GPIO_WRITE C 24 8192", outputs)
    self.assertEqual(high, [{"pin": 45, "state": 1}])
    low = parse_gpio_write("VELXIO_GPIO_WRITE C 24 536870912", outputs)
    self.assertEqual(low, [{"pin": 45, "state": 0}])
    self.assertEqual(parse_gpio_write("VELXIO_GPIO_WRITE C 24 536870912", outputs), [])

  def test_odr_register_write_changes_led(self):
    outputs = {}
    self.assertEqual(parse_gpio_write("VELXIO_GPIO_WRITE C 20 8192", outputs), [{"pin": 45, "state": 1}])
    self.assertEqual(parse_gpio_write("VELXIO_GPIO_WRITE C 20 0", outputs), [{"pin": 45, "state": 0}])

  def test_ignores_gpio_configuration_writes(self):
    outputs = {}
    self.assertEqual(parse_gpio_write("VELXIO_GPIO_WRITE C 0 1073741824", outputs), [])
    self.assertEqual(outputs, {})

  def test_other_ports_map_to_linear_pin_numbers(self):
    outputs = {}
    self.assertEqual(parse_gpio_write("VELXIO_GPIO_WRITE A 24 2", outputs), [{"pin": 1, "state": 1}])
    self.assertEqual(parse_gpio_write("VELXIO_GPIO_WRITE D 24 4096", outputs), [{"pin": 60, "state": 1}])

  def test_prefix_output_does_not_confuse_parser(self):
    outputs = {}
    self.assertIsNotNone(GPIO_OUTPUT_RE.search("log > VELXIO_GPIO_WRITE C 24 8192"))
    self.assertEqual(parse_gpio_write("log > VELXIO_GPIO_WRITE C 24 8192", outputs)[0]["pin"], 45)


if __name__ == "__main__":
  unittest.main()
