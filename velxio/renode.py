from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from .config import adc_samples

TIM1 = {
  0x40010000: "TIM1_CR1",
  0x40010020: "TIM1_CCER",
  0x40010028: "TIM1_PSC",
  0x4001002C: "TIM1_ARR",
  0x40010034: "TIM1_CCR1",
  0x40010038: "TIM1_CCR2",
  0x40010044: "TIM1_BDTR",
}
TIM2 = {
  0x40000000: "TIM2_CR1",
  0x40000028: "TIM2_PSC",
  0x4000002C: "TIM2_ARR",
}
GPIOB = {
  0x40020414: "GPIOB_ODR",
  0x40020418: "GPIOB_BSRR",
}
EVENT_RE = re.compile(r"VELXIO_EVT\|([^|]+)\|(0x[0-9A-Fa-f]+)\|(0x[0-9A-Fa-f]+)")


@dataclass
class Event:
  time: str
  address: int
  value: int

  @property
  def name(self) -> str:
    return TIM1.get(self.address) or TIM2.get(self.address) or GPIOB.get(self.address) or hex(self.address)


@dataclass
class PreparedRun:
  root: Path
  script: Path
  firmware: Path
  config: dict[str, Any]


def _esc(path: Path) -> str:
  return str(path).replace("\\", "/")


def _sample_lines(samples: dict[int, list[int]]) -> list[str]:
  out: list[str] = []
  for ch, vals in samples.items():
    if len(set(vals)) == 1:
      out.append(f"adc1 FeedSample {vals[0]} {ch} {len(vals)}")
      continue
    for v in vals:
      out.append(f"adc1 FeedSample {v} {ch}")
  return out


def _hook_lines(cfg: dict[str, Any]) -> list[str]:
  addrs: list[int] = []
  trace = cfg.get("trace", {})
  if trace.get("tim1", True):
    addrs += list(TIM1)
  if trace.get("tim2", True):
    addrs += list(TIM2)
  if trace.get("gpiob", True):
    addrs += list(GPIOB)
  py = "print 'VELXIO_EVT|%s|0x%08X|0x%08X' % (machine.ElapsedVirtualTime.TimeElapsed, address, value)"
  return [f'sysbus AddWatchpointHook 0x{a:08X} DoubleWord Write "{py}"' for a in addrs]


def prepare(firmware: str | Path, cfg: dict[str, Any], root: str | Path | None = None) -> PreparedRun:
  src = Path(firmware).resolve()
  if not src.is_file():
    raise FileNotFoundError(src)
  if src.suffix.lower() not in {".hex", ".elf"}:
    raise ValueError("firmware must be .hex or .elf")
  work = Path(root).resolve() if root else Path(tempfile.mkdtemp(prefix="velxio-run-"))
  work.mkdir(parents=True, exist_ok=True)
  fw = work / f"firmware{src.suffix.lower()}"
  shutil.copy2(src, fw)
  platform = work / "stm32f401.repl"
  platform.write_text(files("velxio").joinpath("platforms/stm32f401.repl").read_text(encoding="utf-8"), encoding="utf-8")
  (work / "hardware.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
  load = f"sysbus LoadHEX @{_esc(fw.name)}" if fw.suffix == ".hex" else f"sysbus LoadELF @{_esc(fw.name)}"
  seconds = float(cfg["simulation"]["seconds"])
  lines = [
    'mach create "powerbox-f401"',
    f"machine LoadPlatformDescription @{_esc(platform.name)}",
    "using sysbus",
    load,
    "cpu VectorTableOffset 0x08000000",
  ]
  for pin, val in sorted(cfg.get("gpio", {}).items()):
    m = re.fullmatch(r"P([AB])(\d{1,2})", pin.upper())
    if not m:
      continue
    port, num = m.groups()
    lines.append(f"gpioPort{port} OnGPIO {int(num)} {'True' if int(val) else 'False'}")
  lines += _sample_lines(adc_samples(cfg))
  lines += _hook_lines(cfg)
  lines += [f'emulation RunFor "{seconds:.9f}"', "quit"]
  script = work / "run.resc"
  script.write_text("\n".join(lines) + "\n", encoding="utf-8")
  return PreparedRun(work, script, fw, cfg)


def choose_backend(name: str) -> str:
  if name not in {"auto", "native", "docker"}:
    raise ValueError("backend must be auto, native or docker")
  if name == "native":
    if not shutil.which("renode"):
      raise RuntimeError("renode was not found in PATH")
    return "native"
  if name == "docker":
    if not shutil.which("docker"):
      raise RuntimeError("docker was not found in PATH")
    return "docker"
  if shutil.which("renode"):
    return "native"
  if shutil.which("docker"):
    return "docker"
  raise RuntimeError("install Renode or Docker, or use --prepare-only")


def run(prepared: PreparedRun, backend: str = "auto", timeout: int = 120) -> tuple[subprocess.CompletedProcess[str], list[Event]]:
  mode = choose_backend(backend)
  if mode == "native":
    cmd = ["renode", "--console", "--disable-gui", "-p", prepared.script.name]
  else:
    mount = f"{prepared.root}:/work"
    cmd = ["docker", "run", "--rm", "-v", mount, "-w", "/work", "antmicro/renode:latest", "renode", "--console", "--disable-gui", "-p", prepared.script.name]
  proc = subprocess.run(cmd, cwd=prepared.root, text=True, capture_output=True, timeout=timeout)
  text = proc.stdout + "\n" + proc.stderr
  events = parse_events(text)
  (prepared.root / "renode.log").write_text(text, encoding="utf-8")
  write_trace(prepared.root / "trace.csv", events)
  summary = summarize(events, prepared.config)
  summary["backend"] = mode
  summary["returncode"] = proc.returncode
  (prepared.root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
  return proc, events


def parse_events(text: str) -> list[Event]:
  out: list[Event] = []
  for m in EVENT_RE.finditer(text):
    out.append(Event(m.group(1).strip(), int(m.group(2), 16), int(m.group(3), 16)))
  return out


def write_trace(path: Path, events: list[Event]) -> None:
  with path.open("w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["time", "name", "address", "value"])
    for e in events:
      w.writerow([e.time, e.name, f"0x{e.address:08X}", f"0x{e.value:08X}"])


def _last(events: list[Event], addr: int, default: int = 0) -> int:
  for e in reversed(events):
    if e.address == addr:
      return e.value
  return default


def _deadtime_seconds(dtg: int, timer_hz: float, ckd: int) -> float:
  scale = (1, 2, 4, 4)[ckd & 3]
  t = scale / timer_hz
  if dtg < 0x80:
    ticks = dtg
  elif dtg < 0xC0:
    ticks = (64 + (dtg & 0x3F)) * 2
  elif dtg < 0xE0:
    ticks = (32 + (dtg & 0x1F)) * 8
  else:
    ticks = (32 + (dtg & 0x1F)) * 16
  return ticks * t


def summarize(events: list[Event], cfg: dict[str, Any]) -> dict[str, Any]:
  timer_hz = float(cfg["mcu"]["core_hz"])
  cr1 = _last(events, 0x40010000)
  psc = _last(events, 0x40010028)
  arr = _last(events, 0x4001002C)
  ccer = _last(events, 0x40010020)
  bdtr = _last(events, 0x40010044)
  cms = (cr1 >> 5) & 0x3
  divisor = (psc + 1) * (arr + 1) * (2 if cms else 1) if arr or psc else 0
  carrier = timer_hz / divisor if divisor else None
  dt = _deadtime_seconds(bdtr & 0xFF, timer_hz, (cr1 >> 8) & 0x3)
  ccr1 = [e.value for e in events if e.address == 0x40010034]
  ccr2 = [e.value for e in events if e.address == 0x40010038]
  mode = "off"
  enabled = ccer & 0x55
  if bdtr & (1 << 15):
    if enabled == 0x44:
      mode = "charge"
    elif enabled == 0x55:
      mode = "inverter"
    elif enabled:
      mode = "partial"
  tim2_psc = _last(events, 0x40000028)
  tim2_arr = _last(events, 0x4000002C)
  tick = timer_hz / ((tim2_psc + 1) * (tim2_arr + 1)) if tim2_arr or tim2_psc else None
  return {
    "events": len(events),
    "tim1": {
      "mode": mode,
      "cr1": f"0x{cr1:08X}",
      "ccer": f"0x{ccer:08X}",
      "bdtr": f"0x{bdtr:08X}",
      "psc": psc,
      "arr": arr,
      "center_aligned": bool(cms),
      "carrier_hz": carrier,
      "deadtime_us": dt * 1e6,
      "ccr1_updates": len(ccr1),
      "ccr2_updates": len(ccr2),
      "ccr1_min": min(ccr1) if ccr1 else None,
      "ccr1_max": max(ccr1) if ccr1 else None,
      "ccr2_min": min(ccr2) if ccr2 else None,
      "ccr2_max": max(ccr2) if ccr2 else None
    },
    "tim2": {
      "psc": tim2_psc,
      "arr": tim2_arr,
      "tick_hz": tick
    }
  }
