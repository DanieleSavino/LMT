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

    # -- schema ---------------------------------------------------------
    def frequencies(self) -> dict:
        if self._freqs is None:
            rows = self.con.execute("SELECT channelName, frequency FROM channelsList").fetchall()
            self._freqs = {name: freq for name, freq in rows}
        return self._freqs

    def get_dense_channel(self, name: str, file_t0: float) -> pd.DataFrame:
        freq = self.frequencies()[name]
        df = self.con.execute(f'SELECT * FROM "{name}"').fetchdf().reset_index().rename(columns={"index": "idx"})
        df["ts"] = file_t0 + df["idx"] / freq
        return df[["ts", "value"]]

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
