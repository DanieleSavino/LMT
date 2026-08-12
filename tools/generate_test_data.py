"""
tools/generate_test_data.py

Builds a small synthetic .duckdb telemetry file with the same table
shapes a real LMU export has: a channelsList table plus one dense
table per fixed-frequency channel (including dense "TC", single
`value` column), a sparse "ABS" event table, sparse "Gear"/"Lap"/"Lap
Time" event tables, and a dense per-wheel "Wheel Speed" channel with
four columns (value1..4 = FL/FR/RL/RR, matching the real export and
core/session.get_wheel_channel()) - used to derive the lockup /
loss-of-traction panels. Handy for trying the app out without a real
LMU export.

By default ABS never activates (permanently False), matching most of
the LMU field (Hypercar/LMP2/GTE don't have ABS) - this is what
exercises the app's "hide the ABS panel, show lockup instead" path.
Pass --gt3 to simulate a GT3 car that actually has (and occasionally
triggers) ABS, which exercises the opposite path (ABS panel shown,
lockup hidden except where ABS wasn't intervening).

Lockup is generated mostly on the front wheels (brake bias is
front-heavy) and wheelspin mostly on the rears (RWD), each wheel with
its own noise, so a single-corner event doesn't get averaged away -
exercising the per-wheel min/max slip logic in core/lapdata.py.

Wheel Speed is generated in m/s while Ground Speed is in km/h and
both get a real `unit` column in channelsList - matching a real LMU
export's actual (and easy to miss) unit mismatch between the two
channels, so this generator regression-tests the unit-conversion
logic in core/lapdata._wheel_speed_scale() instead of silently using
same-unit values that would never have caught that bug.

Usage:
    python tools/generate_test_data.py session1.duckdb --laps 3 --seed 1
    python tools/generate_test_data.py session2.duckdb --laps 2 --seed 2 --gt3
"""
import argparse
from pathlib import Path

import duckdb
import numpy as np


def make_lap(lap_len_m=3000.0, freq=20.0, base_laptime=95.0, seed=0, gt3=False):
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
    # Gain of 10 (not 4) so the heaviest braking zones actually reach
    # the 65-70% range the lockup/ABS generation below gates on -
    # otherwise (as before) brake never exceeds ~35% and lockup/ABS
    # never fire in the generated file.
    brake = np.clip(-d_speed * 10, 0, 100)
    rpm = 1000 + speed * 55 + rng.normal(0, 50, n)
    gear = np.clip(np.round(speed / 40) + 1, 1, 7)

    # TC as a boolean-ish intervention flag: kicks in on hard throttle
    # at low-ish speed (traction limited), plus a little noise so it's
    # not perfectly correlated with throttle alone.
    tc = ((throttle > 80) & (speed < 160) & (rng.random(n) < 0.5)).astype(float)

    # ABS: only GT3 cars have it in LMU. Non-GT3 laps get a channel
    # that's present but permanently zero, same as a real export from
    # a car with no ABS system - which is exactly the case the app's
    # "hide the ABS panel if it never activates" logic needs to handle.
    if gt3:
        abs_ = ((brake > 70) & (rng.random(n) < 0.5)).astype(float)
    else:
        abs_ = np.zeros(n)

    # Wheel Speed, per corner (FL, FR, RL, RR) - independent slip noise
    # layered on top of Ground Speed. This is deliberately *not* the
    # same mask as TC/ABS above, since the lockup/loss-of-traction
    # panels are derived independently from slip, not from the game's
    # own TC/ABS decisions. Front wheels get most of the lockup
    # (front-biased brakes), rear wheels get most of the wheelspin
    # (RWD), each with its own random gate so events land on one
    # corner at a time rather than symmetrically on the axle.
    wheel_speed = {}
    for corner, lockup_share, spin_share in (
        ("fl", 1.0, 0.1), ("fr", 1.0, 0.1), ("rl", 0.2, 1.0), ("rr", 0.2, 1.0)
    ):
        wheelspin_amt = np.where(
            (throttle > 75) & (speed < 170), rng.uniform(0.05, 0.35, n) * spin_share, 0.0
        ) * (rng.random(n) < 0.4)
        lockup_amt = np.where(
            brake > 65, rng.uniform(0.05, 0.35, n) * lockup_share, 0.0
        ) * (rng.random(n) < 0.3)
        wheel_speed[corner] = np.clip(
            speed * (1 + wheelspin_amt - lockup_amt) + rng.normal(0, 1.5, n), 0, None
        )
        # Store in m/s (real export's Wheel Speed unit), not km/h -
        # see module docstring: this is what exercises the
        # unit-conversion path rather than accidentally matching units
        # with Ground Speed.
        wheel_speed[corner] = wheel_speed[corner] / 3.6

    return t, dist, speed, throttle, brake, tc, abs_, wheel_speed, rpm, gear


def build(out_path: Path, n_laps: int, seed: int, gt3: bool):
    if out_path.exists():
        out_path.unlink()
    con = duckdb.connect(str(out_path))

    freq = 20.0
    dense_specs = {
        "Ground Speed": [], "Throttle Pos": [], "Brake Pos": [],
        "Engine RPM": [], "Lap Dist": [], "TC": [],
    }
    wheel_speed_cols = {"fl": [], "fr": [], "rl": [], "rr": []}
    gear_rows, abs_rows, lap_rows, laptime_rows = [], [], [], []

    t0 = 0.0
    lap_boundaries = [0.0]
    for lap in range(n_laps):
        t, dist, speed, throttle, brake, tc, abs_flag, wheel_speed, rpm, gear = make_lap(
            seed=seed * 100 + lap, gt3=gt3
        )
        dense_specs["Ground Speed"].extend(speed.tolist())
        dense_specs["Throttle Pos"].extend(throttle.tolist())
        dense_specs["Brake Pos"].extend(brake.tolist())
        dense_specs["Engine RPM"].extend(rpm.tolist())
        dense_specs["Lap Dist"].extend(dist.tolist())
        dense_specs["TC"].extend(tc.tolist())
        for corner, values in wheel_speed_cols.items():
            values.extend(wheel_speed[corner].tolist())

        lap_t = t0 + t

        def step_rows(ts_arr, values):
            """Sparse event encoding: only emit a row when the value
            changes, same convention as the real 'Gear'/'ABS' event
            tables (log on change, hold last value otherwise)."""
            rows, prev = [], None
            for ts, v in zip(ts_arr, values):
                if v != prev:
                    rows.append((float(ts), float(v)))
                    prev = v
            return rows

        gear_rows.extend(step_rows(lap_t, gear))
        abs_rows.extend(step_rows(lap_t, abs_flag))

        t0 += t[-1] + 1.0 / freq
        lap_boundaries.append(t0)

    for lap, ts in enumerate(lap_boundaries[:-1]):
        lap_rows.append((ts, lap))
    lap_rows.append((lap_boundaries[-1], n_laps))  # boundary closing the last lap

    for lap in range(n_laps):
        completed_time = lap_boundaries[lap + 1] - lap_boundaries[lap]
        laptime_rows.append((lap_boundaries[lap + 1], completed_time))

    con.execute("CREATE TABLE channelsList (channelName VARCHAR, frequency DOUBLE, unit VARCHAR)")
    channel_units = {
        "Ground Speed": "km/h", "Throttle Pos": "%", "Brake Pos": "%",
        "Engine RPM": "RPM", "Lap Dist": "m", "TC": "", "Wheel Speed": "m/s",
    }
    for name in list(dense_specs) + ["Wheel Speed"]:
        con.execute("INSERT INTO channelsList VALUES (?, ?, ?)", [name, freq, channel_units.get(name, "")])

    for name, values in dense_specs.items():
        con.execute(f'CREATE TABLE "{name}" (value DOUBLE)')
        con.executemany(f'INSERT INTO "{name}" VALUES (?)', [(v,) for v in values])

    # Wheel Speed: dense, per-wheel (value1..4 = FL/FR/RL/RR), matching
    # the real export's schema - not a single `value` column.
    con.execute('CREATE TABLE "Wheel Speed" (value1 DOUBLE, value2 DOUBLE, value3 DOUBLE, value4 DOUBLE)')
    con.executemany(
        'INSERT INTO "Wheel Speed" VALUES (?, ?, ?, ?)',
        list(zip(
            wheel_speed_cols["fl"], wheel_speed_cols["fr"],
            wheel_speed_cols["rl"], wheel_speed_cols["rr"],
        )),
    )

    con.execute('CREATE TABLE "Gear" (ts DOUBLE, value DOUBLE)')
    con.executemany('INSERT INTO "Gear" VALUES (?, ?)', gear_rows)

    con.execute('CREATE TABLE "ABS" (ts DOUBLE, value DOUBLE)')
    con.executemany('INSERT INTO "ABS" VALUES (?, ?)', abs_rows)

    con.execute('CREATE TABLE "Lap" (ts DOUBLE, value INTEGER)')
    con.executemany('INSERT INTO "Lap" VALUES (?, ?)', lap_rows)

    con.execute('CREATE TABLE "Lap Time" (ts DOUBLE, value DOUBLE)')
    con.executemany('INSERT INTO "Lap Time" VALUES (?, ?)', laptime_rows)

    con.close()
    kind = "GT3 (with ABS)" if gt3 else "non-GT3 (no ABS)"
    print(f"wrote {out_path} with {n_laps} laps [{kind}]")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("output", type=Path)
    ap.add_argument("--laps", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--gt3", action="store_true", help="simulate a car with a working ABS system")
    args = ap.parse_args()
    build(args.output, args.laps, args.seed, args.gt3)
