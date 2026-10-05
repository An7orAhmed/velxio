from __future__ import annotations

import argparse
import json
import sys

from .config import load_config
from .renode import prepare, run, summarize


def parser() -> argparse.ArgumentParser:
  p = argparse.ArgumentParser(prog="velxio", description="STM32F401 inverter firmware simulator")
  p.add_argument("firmware", help="STM32 firmware .hex or .elf")
  p.add_argument("-c", "--config", help="JSON hardware override")
  p.add_argument("--seconds", type=float, help="virtual seconds to run")
  p.add_argument("--backend", choices=["auto", "native", "docker"], default="auto")
  p.add_argument("--out", help="run directory")
  p.add_argument("--timeout", type=int, default=120, help="host side timeout in seconds")
  p.add_argument("--prepare-only", action="store_true", help="generate Renode files but do not run")
  return p


def main() -> None:
  a = parser().parse_args()
  try:
    cfg = load_config(a.config, a.seconds)
    prep = prepare(a.firmware, cfg, a.out)
    if a.prepare_only:
      print(prep.root)
      return
    proc, events = run(prep, a.backend, a.timeout)
    summary = summarize(events, cfg)
    print(json.dumps(summary, indent=2))
    print(f"run files: {prep.root}", file=sys.stderr)
    if proc.returncode:
      print("Renode exited with an error. See renode.log in the run directory.", file=sys.stderr)
      raise SystemExit(proc.returncode)
    if not events:
      print("No traced peripheral writes were captured. Check renode.log before trusting the run.", file=sys.stderr)
  except Exception as e:
    print(f"velxio: {e}", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
  main()
