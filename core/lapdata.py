"""
core/lapdata.py

Per-lap extraction: pulls the telemetry channels (speed / throttle /
brake / RPM / gear, plus the optional TC / ABS / lockup /
loss-of-traction channels where the data supports them) for one lap
out of a TelemetrySession, resamples them onto a common distance
grid, and returns a LapData ready to plot. Ported from
telemetry_plot.py's extract_lap().

TC comes straight from LMU's own dense "TC" channel; ABS from its
sparse "ABS" event table. Lockup and loss-of-traction are *derived*
here (not raw LMU channels, LMU doesn't export either directly -
TinyPedals derives the same kind of signal itself for the same
reason): a relative-slip comparison between "Wheel Speed" and "Ground
Speed", per wheel -

    slip = (wheel_speed - ground_speed) / max(|ground_speed|, 1)

"Wheel Speed" is a per-wheel channel (four columns, FL/FR/RL/RR - see
core/session.get_wheel_channel()), so slip is computed per corner and
the most extreme wheel at each sample drives the flag: the most
negative slip (most-locked wheel) for lockup, the most positive slip
(most-spinning wheel) for loss-of-traction. Averaging all four first
would wash out a single-corner lockup/spin under the other three
wheels' normal readings.

Lockup is additionally gated on ABS being inactive at that sample:
if the car has ABS and it's currently modulating the brake, a slip
dip is the system doing its job, not a lockup the driver needs to
know about, so it's excluded. Loss-of-traction isn't ABS-gated (ABS
only affects braking) - it's independent of TC too, since TC cutting
in and wheelspin still happening are both worth seeing.

Both are skipped entirely, gracefully, if the export doesn't include
a "Wheel Speed" channel.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.interpolate import interp1d

from core.session import TelemetrySession

# Channels every lap must have to be extractable at all.
REQUIRED_CHANNELS = {
    "speed": "Ground Speed",
    "throttle": "Throttle Pos",
    "brake": "Brake Pos",
    "rpm": "Engine RPM",
    "gear": "Gear",
    "lapdist": "Lap Dist",
}

# Channels that may or may not exist depending on the car/export - a
# missing optional channel just means the derived field stays None,
# it never fails the whole lap extraction.
OPTIONAL_CHANNELS = {
    "tc": "TC",
    "abs": "ABS",
}

# "Wheel Speed" is handled separately from OPTIONAL_CHANNELS since
# it's a per-wheel channel (4 columns), not a single value - see
# core/session.get_wheel_channel().
WHEEL_SPEED_CHANNEL = "Wheel Speed"
WHEEL_COLS = ("fl", "fr", "rl", "rr")

DIST_STEP = 2.0  # meters, resampling grid resolution

# A single DIST_STEP grid step normally takes tens of milliseconds
# even at low speed - anything this long means real time passed with
# almost no net distance progress (spin, stall, off-track recovery).
GAP_TIME_PER_STEP = 1.0  # seconds

# Thresholds for the derived lockup / loss-of-traction flags. Slip is
# relative ((wheel - ground) / ground) once both speeds are in the
# same unit, so the thresholds are unitless - see
# _speed_unit_factor()/_wheel_speed_scale() below for why they can't
# just be compared as raw channel values.
LOCKUP_SLIP = -0.15       # wheel this much slower than the car ...
LOCKUP_BRAKE_MIN = 10.0   # ... while brake is at least this much (%)
TRACTION_LOSS_SLIP = 0.15  # wheel this much faster than the car ...
TRACTION_LOSS_THROTTLE_MIN = 10.0  # ... while throttle is at least this much (%)

# Conversion factor to m/s for the speed units LMU exports use. A real
# export has been observed with Ground Speed in km/h and Wheel Speed
# in m/s *in the same file* - comparing them without converting first
# gives a bogus, roughly-constant slip offset (~-0.72 for km/h vs
# m/s), which reads as near-permanent lockup on every brake touch.
UNIT_TO_MPS = {
    "m/s": 1.0,
    "km/h": 1000.0 / 3600.0,
    "mph": 0.44704,
}


def _wheel_speed_scale(sess: TelemetrySession) -> float:
    """Factor to multiply Wheel Speed values by so they land in the
    same unit as Ground Speed. Falls back to 1.0 (no conversion) if
    either channel's unit is missing or unrecognized - best effort
    rather than failing lockup/loss-of-traction detection outright,
    though the caller should still sanity-check results in that case."""
    units = sess.units()
    ground_unit = (units.get("Ground Speed") or "").strip().lower()
    wheel_unit = (units.get(WHEEL_SPEED_CHANNEL) or "").strip().lower()
    if not ground_unit or not wheel_unit or ground_unit == wheel_unit:
        return 1.0
    ground_factor = UNIT_TO_MPS.get(ground_unit)
    wheel_factor = UNIT_TO_MPS.get(wheel_unit)
    if not ground_factor or not wheel_factor:
        return 1.0
    return wheel_factor / ground_factor


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
    # Optional / derived - None means "not available for this lap",
    # which is distinct from an all-zero array (channel present, just
    # never triggered).
    tc: Optional[np.ndarray] = None
    abs: Optional[np.ndarray] = None
    lockup: Optional[np.ndarray] = None
    traction_loss: Optional[np.ndarray] = None
    time_at_dist: np.ndarray = field(default_factory=lambda: np.array([]))
    max_dist: float = 0.0
    # True at grid points reached after an anomalously long real-time
    # gap (see GAP_TIME_PER_STEP) - a spin, stall, or off-track
    # excursion that covered almost no net track distance. Every
    # resampled channel is a straight-line interpolation across that
    # gap, not a record of what actually happened, since a
    # distance-indexed plot has no x-axis room for "lots of time,
    # little distance". Used to shade those stretches in the UI
    # instead of silently drawing a smooth (and misleading) line
    # through them.
    gap: np.ndarray = field(default_factory=lambda: np.array([]))

    @property
    def display_label(self) -> str:
        return f"{self.session_label} - L{self.lap_number}"


def _clip_to_lap(df, lap_start_ts, lap_end_ts, is_sparse):
    if is_sparse:
        # Sparse (step) channels: keep everything up to the lap end so
        # the last known value (e.g. gear selected right before the
        # line) carries forward into the lap, then clip.
        sub = df[df["ts"] <= lap_end_ts] if lap_end_ts is not None else df
    else:
        if lap_end_ts is not None:
            sub = df[(df["ts"] >= lap_start_ts) & (df["ts"] <= lap_end_ts)]
        else:
            sub = df[df["ts"] >= lap_start_ts]
    return sub


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
    for key, chan_name in REQUIRED_CHANNELS.items():
        df, is_sparse = sess.get_series(chan_name, file_t0)
        sub = _clip_to_lap(df, lap_start_ts, lap_end_ts, is_sparse)
        if sub.empty:
            raise ValueError(
                f"no data for channel '{chan_name}' in lap {lap_number} of {sess.label} - "
                "the recording may have been cut off before this lap completed"
            )
        sub = sub.copy()
        sub["t_rel"] = sub["ts"] - lap_start_ts
        raw[key] = sub

    # Optional channels: skip quietly (leave the key out of `raw`) on
    # anything that goes wrong - missing table, empty result, whatever
    # - rather than failing the whole lap over a channel the car/export
    # might legitimately not have.
    for key, chan_name in OPTIONAL_CHANNELS.items():
        if not sess.has_channel(chan_name):
            continue
        try:
            df, is_sparse = sess.get_series(chan_name, file_t0)
            sub = _clip_to_lap(df, lap_start_ts, lap_end_ts, is_sparse)
            if sub.empty:
                continue
            sub = sub.copy()
            sub["t_rel"] = sub["ts"] - lap_start_ts
            raw[key] = sub
        except Exception:
            continue

    wheel_raw = None
    if sess.has_channel(WHEEL_SPEED_CHANNEL):
        try:
            df = sess.get_wheel_channel(WHEEL_SPEED_CHANNEL, file_t0)
            sub = _clip_to_lap(df, lap_start_ts, lap_end_ts, is_sparse=False)
            if not sub.empty:
                sub = sub.copy()
                sub["t_rel"] = sub["ts"] - lap_start_ts
                wheel_raw = sub
        except Exception:
            wheel_raw = None

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
    # Enforce *global* monotonicity, not just consecutive-sample: a
    # spin/off can leave raw distance oscillating or briefly running
    # backward for several real seconds while barely progressing
    # along the track (e.g. Lap Dist only updates at 10Hz, coarser
    # than everything else). A plain "diff > 0" filter only compares
    # each sample to its immediate predecessor, so it can still let
    # through a value lower than an earlier *kept* sample, leaving an
    # array that looks sorted locally but isn't globally increasing.
    # interp1d doesn't validate that and silently returns garbage on
    # such an input (not an error) - concretely, the reconstructed
    # time for the distance grid can jump backwards mid-lap, which
    # scrambles every resampled channel through that stretch (not
    # just lockup/loss-of-traction) into non-physical noise instead
    # of showing what actually happened.
    dist_vals = np.maximum.accumulate(dist_vals)
    keep = np.concatenate([[True], np.diff(dist_vals) > 0])
    dist_t, dist_vals = dist_t[keep], dist_vals[keep]

    if len(dist_t) < 2:
        raise ValueError(f"not enough distance samples to build a grid for lap {lap_number} of {sess.label}")

    max_dist = float(dist_vals.max())
    time_of_dist = interp1d(dist_vals, dist_t, bounds_error=False, fill_value=(dist_t[0], dist_t[-1]))

    grid = np.arange(0, max_dist, DIST_STEP)
    t_on_grid = time_of_dist(grid)
    gap_mask = np.diff(t_on_grid, prepend=t_on_grid[0] if len(t_on_grid) else 0.0) > GAP_TIME_PER_STEP

    def resample(key, kind="linear"):
        df = raw[key].sort_values("t_rel").drop_duplicates(subset="t_rel")
        x = df["t_rel"].to_numpy()
        y = df["value"].to_numpy()
        if len(x) < 2:
            return np.full_like(t_on_grid, float(y[0]) if len(y) else np.nan, dtype=float)
        fill = "extrapolate" if kind == "linear" else (y[0], y[-1])
        f = interp1d(x, y, kind=kind, bounds_error=False, fill_value=fill)
        return f(t_on_grid)

    speed = resample("speed")
    throttle = resample("throttle")
    brake = resample("brake")

    # TC/ABS are sparse "Events" tables (like Gear) that only log on
    # change, so they need the same step-hold (previous-value)
    # resampling rather than linear interpolation between events.
    tc = np.round(resample("tc", kind="previous")) if "tc" in raw else None
    abs_ = np.round(resample("abs", kind="previous")) if "abs" in raw else None

    lockup = traction_loss = None
    if wheel_raw is not None:
        # Detect lockup/loss-of-traction at the wheel channel's native
        # time resolution (100Hz), *then* pool "did this happen at
        # all" onto the distance grid - not the other way around.
        # Point-sampling the interpolated slip curve only at the
        # sparse grid times misses real, brief events: at 200+ km/h a
        # single 2m grid step is ~36ms, wider than the 10ms native
        # sample spacing, so a lockup/spin lasting a few samples can
        # fall entirely between two grid points and never get
        # evaluated at all - the resampled channels still look smooth
        # even though the raw data clearly shows it happened.
        wd = wheel_raw.sort_values("t_rel").drop_duplicates(subset="t_rel")
        wt = wd["t_rel"].to_numpy()

        def interp_onto_wheel_t(key, kind="linear"):
            d = raw[key].sort_values("t_rel").drop_duplicates(subset="t_rel")
            x, y = d["t_rel"].to_numpy(), d["value"].to_numpy()
            if len(x) < 2:
                return np.full_like(wt, float(y[0]) if len(y) else np.nan, dtype=float)
            fill = "extrapolate" if kind == "linear" else (y[0], y[-1])
            return interp1d(x, y, kind=kind, bounds_error=False, fill_value=fill)(wt)

        speed_at_wt = interp_onto_wheel_t("speed")
        brake_at_wt = interp_onto_wheel_t("brake")
        throttle_at_wt = interp_onto_wheel_t("throttle")
        abs_at_wt = np.round(interp_onto_wheel_t("abs", kind="previous")) if "abs" in raw else None

        wheel_scale = _wheel_speed_scale(sess)
        slips = [
            (wd[c].to_numpy() * wheel_scale - speed_at_wt) / np.maximum(np.abs(speed_at_wt), 1.0)
            for c in WHEEL_COLS
        ]
        # Most-locked wheel drives lockup, most-spinning wheel drives
        # loss-of-traction - a real lockup/spin is rarely all four
        # corners at once, so averaging across wheels would hide a
        # single-corner event under the other three's normal slip.
        min_slip = np.minimum.reduce(slips)
        max_slip = np.maximum.reduce(slips)

        raw_lockup = (min_slip < LOCKUP_SLIP) & (brake_at_wt > LOCKUP_BRAKE_MIN)
        if abs_at_wt is not None:
            # Only if no ABS: exclude samples where ABS is actively
            # modulating the brake, since that slip dip is the system
            # working as intended, not a lockup worth flagging.
            raw_lockup = raw_lockup & (abs_at_wt < 0.5)
        raw_traction_loss = (max_slip > TRACTION_LOSS_SLIP) & (throttle_at_wt > TRACTION_LOSS_THROTTLE_MIN)

        def pool_any(raw_flag: np.ndarray) -> np.ndarray:
            """result[i] = did raw_flag go True anywhere in
            (t_on_grid[i-1], t_on_grid[i]] - an "any" downsample, not
            a point sample, so a spike that falls between two grid
            times still shows up at the grid point after it."""
            cum = np.concatenate([[0], np.cumsum(raw_flag.astype(np.int64))])
            edges = np.searchsorted(wt, t_on_grid, side="right")
            prev_edges = np.concatenate([[0], edges[:-1]])
            return ((cum[edges] - cum[prev_edges]) > 0).astype(float)

        lockup = pool_any(raw_lockup)
        traction_loss = pool_any(raw_traction_loss)

    return LapData(
        session_label=sess.label,
        file_label=str(sess.path),
        lap_number=lap_number,
        laptime=laptime_val,
        color=color,
        dist=grid,
        speed=speed,
        throttle=throttle,
        brake=brake,
        tc=tc,
        abs=abs_,
        lockup=lockup,
        traction_loss=traction_loss,
        rpm=resample("rpm"),
        gear=np.round(resample("gear", kind="previous")),
        time_at_dist=t_on_grid,
        gap=gap_mask,
        max_dist=max_dist,
    )
