import json

from velxio.config import adc_samples, load_config, samples_for


def test_default_profile_is_f401():
  cfg = load_config()
  assert cfg["mcu"]["name"] == "STM32F401RCT6"
  assert cfg["mcu"]["core_hz"] == 84000000
  assert set(cfg["adc"]) == {"PA0", "PA1", "PA2", "PA3", "PA4", "PA5"}


def test_override_merges(tmp_path):
  p = tmp_path / "hw.json"
  p.write_text(json.dumps({"adc": {"PA0": {"mode": "constant", "value": 3100}}}))
  cfg = load_config(p, 0.1)
  assert cfg["adc"]["PA0"]["value"] == 3100
  assert cfg["adc"]["PA1"]["frequency_hz"] == 50.0
  assert cfg["simulation"]["seconds"] == 0.1


def test_sine_is_12_bit():
  vals = samples_for({"mode": "sine", "offset": 2048, "amplitude": 5000, "frequency_hz": 50}, 100, 18000)
  assert min(vals) >= 0
  assert max(vals) <= 4095


def test_adc_count_matches_duration():
  cfg = load_config(seconds=0.02)
  samples = adc_samples(cfg)
  assert len(samples[0]) >= 360
  assert len(samples[0]) == len(samples[5])
