"""
core/lapdata.py

Per-lap extraction: pulls the six telemetry channels for one lap out
of a TelemetrySession, resamples them onto a common distance grid, and
returns a LapData ready to plot. Ported from telemetry_plot.py's
extract_lap().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.interpolate import interp1d

from core.session import TelemetrySession

CHANNELS = {
    "speed": "Ground Speed",
    "throttle": "Throttle Pos",
    "brake": "Brake Pos",
    "rpm": "Engine RPM",
    "gear": "Gear",
    "lapdist": "Lap Dist",
}

DIST_STEP = 2.0  # meters, resampling grid resolution


def fmt_laptime(t: Optional[float]) -> str:
    if t is None:
        return "n/a"
    m = int(t // 60)
    s = t - m * 60
    return f"{m}:{s:06.3f}" if m else f"{s:.3f}s"


@dataclass
class LapData:
    session_label: str
    file_label: str
    lap_number: int
    laptime: Optional[float]
    color: str = "#ffffff"
    dist: np.ndarray = field(default_factory=lambda: np.array([]))
    speed: np.ndarray = field(default_factory=lambda: np.array([]))
    throttle: np.ndarray = field(default_factory=lambda: np.array([]))
    brake: np.ndarray = field(default_factory=lambda: np.array([]))
    rpm: np.ndarray = field(default_factory=lambda: np.array([]))
    gear: np.ndarray = field(default_factory=lambda: np.array([]))
    time_at_dist: np.ndarray = field(default_factory=lambda: np.array([]))
    max_dist: float = 0.0

    @property
    def display_label(self) -> str:
        return f"{self.session_label} - L{self.lap_number}"


def extract_lap(sess: TelemetrySession, lap_number: int, color: str = "#ffffff") -> LapData:
    laps = sess.get_lap_boundaries()
    laptimes = sess.get_lap_times()

    if lap_number not in laps["lap"].values:
        available = sorted(laps["lap"].unique().tolist())
        raise ValueError(f"lap {lap_number} not found in {sess.label}. Available laps: {available}")

    lap_start_ts = float(laps.loc[laps["lap"] == lap_number, "ts"].iloc[0])
    later = laps[laps["ts"] > lap_start_ts]
    lap_end_ts = float(later["ts"].iloc[0]) if not later.empty else None
    file_t0 = float(laps["ts"].min())

    raw = {}
    for key, chan_name in CHANNELS.items():
        df, is_sparse = sess.get_series(chan_name, file_t0)
        if is_sparse:
            # Sparse (step) channels: keep everything up to the lap end
            # so the last known value (e.g. gear selected right before
            # the line) carries forward into the lap, then clip.
            sub = df[df["ts"] <= lap_end_ts] if lap_end_ts is not None else df
        else:
            if lap_end_ts is not None:
                sub = df[(df["ts"] >= lap_start_ts) & (df["ts"] <= lap_end_ts)]
            else:
                sub = df[df["ts"] >= lap_start_ts]
        if sub.empty:
            raise ValueError(
                f"no data for channel '{chan_name}' in lap {lap_number} of {sess.label} - "
                "the recording may have been cut off before this lap completed"
            )
        sub = sub.copy()
        sub["t_rel"] = sub["ts"] - lap_start_ts
        raw[key] = sub

    lt_row = laptimes[laptimes["lap"] == lap_number]
    laptime_val = float(lt_row.iloc[0]["laptime"]) if not lt_row.empty else (
        (lap_end_ts - lap_start_ts) if lap_end_ts is not None else None
    )

    # Build distance -> time mapping from Lap Dist, then resample every
    # other channel onto a common distance grid via a two-step
    # interpolation (dist -> time -> channel value). Only clamp true
    # wraparounds (large negative jump); small dips right at the s/f
    # line are left alone.
    ld = raw["lapdist"].sort_values("t_rel")
    dist_vals = ld["value"].to_numpy()
    dist_t = ld["t_rel"].to_numpy()
    track_len_guess = float(np.nanmax(dist_vals))
    dist_vals = np.where(dist_vals < -100, dist_vals + track_len_guess, dist_vals)

    order = np.argsort(dist_t)
    dist_t, dist_vals = dist_t[order], dist_vals[order]
    keep = np.concatenate([[True], np.diff(dist_vals) > 0])
    dist_t, dist_vals = dist_t[keep], dist_vals[keep]

    if len(dist_t) < 2:
        raise ValueError(f"not enough distance samples to build a grid for lap {lap_number} of {sess.label}")

    max_dist = float(dist_vals.max())
    time_of_dist = interp1d(dist_vals, dist_t, bounds_error=False, fill_value=(dist_t[0], dist_t[-1]))

    grid = np.arange(0, max_dist, DIST_STEP)
    t_on_grid = time_of_dist(grid)

    def resample(key, kind="linear"):
        df = raw[key].sort_values("t_rel").drop_duplicates(subset="t_rel")
        x = df["t_rel"].to_numpy()
        y = df["value"].to_numpy()
        if len(x) < 2:
            return np.full_like(t_on_grid, float(y[0]) if len(y) else np.nan, dtype=float)
        fill = "extrapolate" if kind == "linear" else (y[0], y[-1])
        f = interp1d(x, y, kind=kind, bounds_error=False, fill_value=fill)
        return f(t_on_grid)

    return LapData(
        session_label=sess.label,
        file_label=str(sess.path),
        lap_number=lap_number,
        laptime=laptime_val,
        color=color,
        dist=grid,
        speed=resample("speed"),
        throttle=resample("throttle"),
        brake=resample("brake"),
        rpm=resample("rpm"),
        gear=np.round(resample("gear", kind="previous")),
        time_at_dist=t_on_grid,
        max_dist=max_dist,
    )
