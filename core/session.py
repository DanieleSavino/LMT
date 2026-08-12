"""
core/session.py

DuckDB telemetry session loading, adapted from the reference
telemetry_plot.py script. Handles the WAL-replay quirk, dense
(channelsList) vs sparse (event) channel tables, and lap boundary /
lap-time reconstruction.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd


def find_sibling_wal(db_path: Path) -> Optional[Path]:
    """Look for a WAL file next to db_path under any naming convention
    seen in LMU-style exports."""
    candidates = [
        db_path.with_name(db_path.name + ".wal"),          # standard duckdb convention
        db_path.with_name(db_path.stem + "_duckdb.wal"),    # observed export quirk
        db_path.with_name(db_path.name.replace(".", "_") + ".wal"),
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


class TelemetrySession:
    """Wraps one imported duckdb telemetry file.

    A private temp dir is used (like the CLI script) so a sibling WAL
    file gets copied alongside and replayed automatically on connect,
    without ever touching the user's original file. The temp dir is
    kept alive for the lifetime of the session (not a `with` block)
    since the UI needs to keep running queries against it (lap lists,
    lap extraction, etc.) - call .close() when the file is removed
    from the app, or on app exit.
    """

    def __init__(self, db_path: str, wal_path: Optional[str] = None, label: Optional[str] = None):
        self.path = Path(db_path)
        if not self.path.exists():
            raise FileNotFoundError(f"duckdb file not found: {db_path}")

        self.label = label or self.path.stem
        self._tmpdir = tempfile.TemporaryDirectory(prefix="lmu_tel_")
        workdir = Path(self._tmpdir.name)

        dest = workdir / self.path.name
        shutil.copy(self.path, dest)

        wal_src = Path(wal_path) if wal_path else find_sibling_wal(self.path)
        self.wal_used: Optional[Path] = None
        if wal_src and wal_src.exists():
            shutil.copy(wal_src, workdir / (dest.name + ".wal"))
            self.wal_used = wal_src

        self.con = duckdb.connect(str(dest), read_only=False)
        self._freqs: Optional[dict] = None
        self._units: Optional[dict] = None
        self._tables: Optional[set] = None

    # -- schema ---------------------------------------------------------
    def frequencies(self) -> dict:
        if self._freqs is None:
            rows = self.con.execute("SELECT channelName, frequency FROM channelsList").fetchall()
            self._freqs = {name: freq for name, freq in rows}
        return self._freqs

    def units(self) -> dict:
        """channelName -> unit string (e.g. 'km/h', 'm/s', '%') for
        every dense channel, straight from channelsList. Different
        dense channels are not guaranteed to share units even when
        they're physically comparable - e.g. a real LMU export has
        Ground Speed in km/h but Wheel Speed in m/s - so anything
        comparing two channels numerically (like the lockup/
        loss-of-traction slip calc) needs to check this first.
        Returns {} if this file's channelsList has no unit column at
        all (e.g. older exports or synthetic test data) rather than
        raising - callers should treat a missing/empty unit as
        "unknown", not "no conversion needed"."""
        if self._units is None:
            cols = {row[0] for row in self.con.execute("DESCRIBE channelsList").fetchall()}
            if "unit" in cols:
                rows = self.con.execute("SELECT channelName, unit FROM channelsList").fetchall()
                self._units = {name: unit for name, unit in rows}
            else:
                self._units = {}
        return self._units

    def table_names(self) -> set:
        """All table names present in this session's duckdb file (dense
        channel tables, sparse event tables, and the bookkeeping tables
        like channelsList/Lap/Lap Time). Used to check whether an
        optional channel (TC, ABS, Wheel Speed, ...) is present at all
        before trying to query it - some cars/exports simply don't have
        every channel."""
        if self._tables is None:
            rows = self.con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
            self._tables = {name for (name,) in rows}
        return self._tables

    def has_channel(self, name: str) -> bool:
        """Whether channel `name` exists in this file at all, dense or
        sparse - does not say anything about whether it has meaningful
        (non-empty / non-zero) data for any particular lap."""
        return name in self.frequencies() or name in self.table_names()

    def get_dense_channel(self, name: str, file_t0: float) -> pd.DataFrame:
        """Single-value view of a dense channel. Most dense channels
        have one `value` column, but some (Wheel Speed, TyresPressure,
        Susp Pos, ...) are per-wheel with four columns (`value1..4` =
        FL/FR/RL/RR, the standard rF2/LMU wheel order) instead - for
        those, this returns the four-wheel average so generic callers
        that just want "a number" still get something sane. Callers
        that need per-wheel resolution (lockup/loss-of-traction) should
        use get_wheel_channel() instead."""
        freq = self.frequencies()[name]
        df = self.con.execute(f'SELECT * FROM "{name}"').fetchdf().reset_index().rename(columns={"index": "idx"})
        df["ts"] = file_t0 + df["idx"] / freq
        if "value" in df.columns:
            return df[["ts", "value"]]
        wheel_cols = [c for c in ("value1", "value2", "value3", "value4") if c in df.columns]
        df["value"] = df[wheel_cols].mean(axis=1)
        return df[["ts", "value"]]

    def get_wheel_channel(self, name: str, file_t0: float) -> pd.DataFrame:
        """Per-wheel view of a dense channel stored as `value1..4`
        (FL/FR/RL/RR). Raises KeyError if `name` isn't a per-wheel
        channel - callers should check via has_channel()/a try block,
        same pattern as the other optional channels."""
        freq = self.frequencies()[name]
        df = self.con.execute(f'SELECT * FROM "{name}"').fetchdf().reset_index().rename(columns={"index": "idx"})
        df["ts"] = file_t0 + df["idx"] / freq
        return df[["ts", "value1", "value2", "value3", "value4"]].rename(
            columns={"value1": "fl", "value2": "fr", "value3": "rl", "value4": "rr"}
        )

    def get_sparse_channel(self, name: str) -> pd.DataFrame:
        df = self.con.execute(f'SELECT * FROM "{name}"').fetchdf()
        return df[["ts", "value"]]

    def get_series(self, name: str, file_t0: float):
        """Returns (df[ts, value], is_sparse) regardless of whether the
        channel lives in the dense channelsList table or a sparse
        event table."""
        if name in self.frequencies():
            return self.get_dense_channel(name, file_t0), False
        return self.get_sparse_channel(name), True

    # -- laps -------------------------------------------------------------
    def get_lap_boundaries(self) -> pd.DataFrame:
        df = self.con.execute('SELECT * FROM "Lap" ORDER BY ts').fetchdf()
        return df.rename(columns={"value": "lap"})

    def get_lap_times(self) -> pd.DataFrame:
        """Reconstruct completed lap times from the sparse 'Lap Time'
        event table: the new value (a just-completed lap's time)
        appears at the same ts as the *next* lap's boundary, so
        completed_lap = (lap number active at this ts) - 1."""
        laptime = self.con.execute('SELECT * FROM "Lap Time" ORDER BY ts').fetchdf().rename(columns={"value": "laptime"})
        laps = self.get_lap_boundaries()
        laptime = laptime[laptime["laptime"] > 0]
        out = []
        for _, row in laptime.iterrows():
            prior = laps[laps["ts"] <= row["ts"]]
            if prior.empty:
                continue
            new_lap = int(prior.iloc[-1]["lap"])
            out.append({"lap": new_lap - 1, "laptime": float(row["laptime"]), "ts": row["ts"]})
        return pd.DataFrame(out, columns=["lap", "laptime", "ts"])

    def list_laps(self) -> list:
        """Every lap in this file with a best-effort time, for the sidebar."""
        laps = self.get_lap_boundaries().sort_values("ts").reset_index(drop=True)
        laptimes = self.get_lap_times()
        out = []
        for i in range(len(laps) - 1):
            lap_no = int(laps.loc[i, "lap"])
            row = laptimes[laptimes["lap"] == lap_no]
            if not row.empty:
                t = float(row.iloc[0]["laptime"])
            else:
                t = float(laps.loc[i + 1, "ts"] - laps.loc[i, "ts"])
            out.append({"lap": lap_no, "laptime": t})
        return out

    def resolve_lap_number(self, lap_spec) -> int:
        if isinstance(lap_spec, str) and lap_spec.lower() == "best":
            lt = self.get_lap_times()
            if not lt.empty:
                return int(lt.loc[lt["laptime"].idxmin(), "lap"])
            laps = self.get_lap_boundaries().sort_values("ts").reset_index(drop=True)
            completed = laps.iloc[:-1].copy()
            completed["duration"] = laps["ts"].diff().shift(-1).iloc[:-1]
            if completed.empty:
                raise ValueError("no completed laps found in this file")
            return int(completed.loc[completed["duration"].idxmin(), "lap"])
        return int(lap_spec)

    def close(self):
        try:
            self.con.close()
        finally:
            self._tmpdir.cleanup()
