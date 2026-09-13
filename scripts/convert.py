#!/usr/bin/env python3
"""Unit/time conversions. Never do these in your head or inline.

Usage:
  convert.py epoch <value>          # auto-detects s / ms / us, prints all plausible readings
  convert.py bytes <value>          # B/KB/MB/GB
  convert.py duration <seconds>     # human duration
"""
import sys, datetime

def epoch(v):
    n = float(v); now = datetime.datetime.now(datetime.timezone.utc)
    print(f"input: {v}\nnow:   {now.isoformat()}\n")
    for unit, div in (("seconds", 1), ("milliseconds", 1e3), ("microseconds", 1e6)):
        try:
            dt = datetime.datetime.fromtimestamp(n / div, datetime.timezone.utc)
        except (ValueError, OSError, OverflowError):
            print(f"  {unit:14} out of range"); continue
        plausible = 2000 < dt.year < 2100
        d = dt - now
        rel = ("EXPIRED " + str(-d).split('.')[0] + " ago") if d.total_seconds() < 0 \
              else f"valid {d.days}d {d.seconds//3600}h {(d.seconds//60)%60}m"
        print(f"  {unit:14} {dt.isoformat()}  {rel}{'   <-- PLAUSIBLE' if plausible else '   (implausible year)'}")

def human_bytes(v):
    n = float(v)
    for u in ("B","KB","MB","GB","TB"):
        if n < 1024 or u == "TB": print(f"  {n:.2f} {u}"); break
        n /= 1024

def duration(v):
    s = int(float(v)); print(f"  {s//86400}d {(s%86400)//3600}h {(s%3600)//60}m {s%60}s")

if __name__ == "__main__":
    if len(sys.argv) < 3: print(__doc__); sys.exit(2)
    {"epoch": epoch, "bytes": human_bytes, "duration": duration}[sys.argv[1]](sys.argv[2])
