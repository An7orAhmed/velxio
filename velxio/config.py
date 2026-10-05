from __future__ import annotations

import json
import math
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Any

PIN_TO_ADC = {f"PA{i}": i for i in range(6)}


def _merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
  out = deepcopy(a)
  for k, v in b.items():
    if isinstance(v, dict) and isinstance(out.get(k), dict):
      out[k] = _merge(out[k], v)
    else:
      out[k] = deepcopy(v)
  return out


def default_config() -> dict[str, Any]:
  p = files("velxio").joinpath("defaults/powerbox.json")
  return json.loads(p.read_text(encoding="utf-8"))


def load_config(path: str | Path | None = None, seconds: float | None = None) -> dict[str, Any]:
  cfg = default_config()
  if path:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
      raise ValueError("hardware config must be a JSON object")
    cfg = _merge(cfg, raw)
  if seconds is not None:
    cfg["simulation"]["seconds"] = seconds
  validate_config(cfg)
  return cfg


def validate_config(cfg: dict[str, Any]) -> None:
  mcu = str(cfg.get("mcu", {}).get("name", ""))
  if not mcu.startswith("STM32F401"):
    raise ValueError("this focused simulator only supports STM32F401xx")
  seconds = float(cfg["simulation"]["seconds"])
  hz = float(cfg["simulation"]["control_hz"])
  if seconds <= 0 or seconds > 60:
    raise ValueError("simulation.seconds must be > 0 and <= 60")
  if hz <= 0:
    raise ValueError("simulation.control_hz must be > 0")
  for pin in PIN_TO_ADC:
    if pin not in cfg.get("adc", {}):
      raise ValueError(f"missing ADC source for {pin}")
    src = cfg["adc"][pin]
    if src.get("mode") not in {"constant", "sine"}:
      raise ValueError(f"{pin}.mode must be constant or sine")


def _clip(v: float) -> int:
  return max(0, min(4095, int(round(v))))


def samples_for(src: dict[str, Any], count: int, hz: float) -> list[int]:
  if src["mode"] == "constant":
    return [_clip(float(src["value"]))] * count
  off = float(src.get("offset", 2048))
  amp = float(src.get("amplitude", 0))
  freq = float(src.get("frequency_hz", 50))
  phase = math.radians(float(src.get("phase_deg", 0)))
  return [_clip(off + amp * math.sin(2 * math.pi * freq * (i / hz) + phase)) for i in range(count)]


def adc_samples(cfg: dict[str, Any]) -> dict[int, list[int]]:
  seconds = float(cfg["simulation"]["seconds"])
  hz = float(cfg["simulation"]["control_hz"])
  count = max(1, int(math.ceil(seconds * hz)) + 32)
  return {PIN_TO_ADC[pin]: samples_for(cfg["adc"][pin], count, hz) for pin in PIN_TO_ADC}
