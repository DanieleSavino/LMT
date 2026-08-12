"""
tools/generate_test_data.py

Builds a small synthetic .duckdb telemetry file with the same table
shapes the app (and the reference CLI script) expect: a channelsList
table plus one dense table per fixed-frequency channel, and sparse
"Gear" / "Lap" / "Lap Time" event tables. Handy for trying the app out
without a real LMU export.

Usage:
    python tools/generate_test_data.py session1.duckdb --laps 3 --seed 1
    python tools/generate_test_data.py session2.duckdb --laps 2 --seed 2
"""
import argparse
from pathlib import Path

import duckdb
import numpy as np


def make_lap(lap_len_m=3000.0, freq=20.0, base_laptime=95.0, seed=0):
    rng = np.random.default_rng(seed)
    n = int(base_laptime * freq)
    t = np.arange(n) / freq

    dist = np.linspace(0, lap_len_m, n)
    dist = np.clip(dist + rng.normal(0, 0.4, n).cumsum() * 0.01, 0, None)

    # rough speed profile: a couple of braking zones + straights, so the
    # channels aren't just flat lines
    phase = (dist / lap_len_m) * 2 * np.pi
    speed = 140 + 90 * np.sin(phase * 2) - 40 * np.sin(phase * 5)
    speed = np.clip(speed + rng.normal(0, 2, n), 40, 300)

    d_speed = np.diff(speed, prepend=speed[0])
    throttle = np.clip(d_speed * 3 + 55, 0, 100)
    brake = np.clip(-d_speed * 4, 0, 100)
    rpm = 1000 + speed * 55 + rng.normal(0, 50, n)
    gear = np.clip(np.round(speed / 40) + 1, 1, 7)

    return t, dist, speed, throttle, brake, rpm, gear


def build(out_path: Path, n_laps: int, seed: int):
    if out_path.exists():
        out_path.unlink()
    con = duckdb.connect(str(out_path))

    freq = 20.0
    dense_specs = {"Ground Speed": [], "Throttle Pos": [], "Brake Pos": [], "Engine RPM": [], "Lap Dist": []}
    gear_rows, lap_rows, laptime_rows = [], [], []

    t0 = 0.0
    lap_boundaries = [0.0]
    for lap in range(n_laps):
        t, dist, speed, throttle, brake, rpm, gear = make_lap(seed=seed * 100 + lap)
        dense_specs["Ground Speed"].extend(speed.tolist())
        dense_specs["Throttle Pos"].extend(throttle.tolist())
        dense_specs["Brake Pos"].extend(brake.tolist())
        dense_specs["Engine RPM"].extend(rpm.tolist())
        dense_specs["Lap Dist"].extend(dist.tolist())

        abs_t = t0 + t
        prev_g = None
        for ts, g in zip(abs_t, gear):
            if g != prev_g:
                gear_rows.append((float(ts), float(g)))
                prev_g = g

        t0 += t[-1] + 1.0 / freq
        lap_boundaries.append(t0)

    for lap, ts in enumerate(lap_boundaries[:-1]):
        lap_rows.append((ts, lap))
    lap_rows.append((lap_boundaries[-1], n_laps))  # boundary closing the last lap

    for lap in range(n_laps):
        completed_time = lap_boundaries[lap + 1] - lap_boundaries[lap]
        laptime_rows.append((lap_boundaries[lap + 1], completed_time))

    con.execute("CREATE TABLE channelsList (channelName VARCHAR, frequency DOUBLE)")
    for name in dense_specs:
        con.execute("INSERT INTO channelsList VALUES (?, ?)", [name, freq])

    for name, values in dense_specs.items():
        con.execute(f'CREATE TABLE "{name}" (value DOUBLE)')
        con.executemany(f'INSERT INTO "{name}" VALUES (?)', [(v,) for v in values])

    con.execute('CREATE TABLE "Gear" (ts DOUBLE, value DOUBLE)')
    con.executemany('INSERT INTO "Gear" VALUES (?, ?)', gear_rows)

    con.execute('CREATE TABLE "Lap" (ts DOUBLE, value INTEGER)')
    con.executemany('INSERT INTO "Lap" VALUES (?, ?)', lap_rows)

    con.execute('CREATE TABLE "Lap Time" (ts DOUBLE, value DOUBLE)')
    con.executemany('INSERT INTO "Lap Time" VALUES (?, ?)', laptime_rows)

    con.close()
    print(f"wrote {out_path} with {n_laps} laps")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("output", type=Path)
    ap.add_argument("--laps", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    build(args.output, args.laps, args.seed)
