#!/usr/bin/env python3
"""Logs steps of the wall clock (CLOCK_REALTIME) against CLOCK_MONOTONIC_RAW: each time the offset
between the two changes by more than --min-ms between two samples, one line with the local time,
the uptime and the step (negative: the wall clock went back). File mtimes come from the wall clock,
so a backward step makes a later write older than an earlier one.

usage: clock-steps.py [--seconds N] [--interval-ms M] [--min-ms S]
"""
import argparse
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seconds', type=float, default=600)
    ap.add_argument('--interval-ms', type=float, default=5)
    ap.add_argument('--min-ms', type=float, default=5)
    a = ap.parse_args()
    raw = time.CLOCK_MONOTONIC_RAW

    def offset():
        return time.clock_gettime_ns(time.CLOCK_REALTIME) - time.clock_gettime_ns(raw)

    end = time.monotonic() + a.seconds
    last = offset()
    steps = 0
    worst = 0
    while time.monotonic() < end:
        time.sleep(a.interval_ms / 1000)
        now = offset()
        d = (now - last) / 1e6
        if abs(d) >= a.min_ms:
            steps += 1
            worst = min(worst, d)
            print(f'{time.strftime("%H:%M:%S")} uptime {time.clock_gettime(time.CLOCK_BOOTTIME):.3f} '
                  f'wall clock step {d:+.3f} ms', flush=True)
        last = now
    print(f'{steps} steps of at least {a.min_ms} ms in {a.seconds:.0f} s; largest backward {worst:.3f} ms', flush=True)


if __name__ == '__main__':
    main()
