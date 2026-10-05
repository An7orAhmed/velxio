from velxio.config import load_config
from velxio.renode import Event, parse_events, prepare, summarize


def test_prepare_generates_f401_run(tmp_path):
  fw = tmp_path / "fw.hex"
  fw.write_text(":00000001FF\n")
  cfg = load_config(seconds=0.001)
  out = tmp_path / "run"
  prep = prepare(fw, cfg, out)
  text = prep.script.read_text()
  assert "stm32f401.repl" in text
  assert "LoadHEX @firmware.hex" in text
  assert "gpioPortB OnGPIO 12 True" in text
  assert "adc1 FeedSample" in text
  assert "0x40010034" in text


def test_event_parser():
  text = "x VELXIO_EVT|00:00:00.001000|0x40010034|0x00000300 y"
  ev = parse_events(text)
  assert len(ev) == 1
  assert ev[0].name == "TIM1_CCR1"
  assert ev[0].value == 0x300


def test_summary_decodes_inverter():
  cfg = load_config()
  ev = [
    Event("0", 0x40010000, 0x60),
    Event("0", 0x40010028, 0),
    Event("0", 0x4001002C, 1379),
    Event("0", 0x40010020, 0x55),
    Event("0", 0x40010044, 0x8000 | 0xC6),
    Event("0", 0x40010034, 100),
    Event("0", 0x40010034, 1200),
    Event("0", 0x40010038, 90),
    Event("0", 0x40010038, 1180),
  ]
  s = summarize(ev, cfg)
  assert s["tim1"]["mode"] == "inverter"
  assert s["tim1"]["center_aligned"] is True
  assert 30000 < s["tim1"]["carrier_hz"] < 31000
  assert s["tim1"]["ccr1_min"] == 100
  assert s["tim1"]["ccr1_max"] == 1200
