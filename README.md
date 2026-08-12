# LMU Telemetry

A small desktop app for browsing and comparing Le Mans Ultimate (and
other iRacing/rFactor2/ACC-style) `.duckdb` telemetry exports.

Built with **Python + Qt (PySide6) + pyqtgraph** - no Electron, no
browser runtime. pyqtgraph gives GPU-friendly, real-time-interactive
plots, which is what makes the drag-to-zoom feel instant even with
long laps.

## Features

- **Import** one or more `.duckdb` telemetry files. If a session was
  exported mid-recording, a sibling WAL file (`session.duckdb.wal` or
  the `session_duckdb.wal` naming quirk some export tools use) is
  auto-detected and replayed, exactly like the reference CLI script -
  your original files are never modified (everything happens on a
  copy in a temp dir).
- **Watch** a single lap as a dashboard: speed / throttle / brake /
  RPM / gear stacked and distance-aligned, each channel with its own
  accent color.
- **Compare** two or more laps (from the same file or different
  files): the first lap you check becomes the reference (red) and
  every other lap is overlaid, plus a delta-vs-reference panel, same
  idea as the CLI script's multi-`--lap` mode.
- **Zoom by dragging**: click-drag a rectangle on any panel and every
  stacked panel zooms to that distance range together (they share a
  linked distance axis). Double-click any panel to reset the zoom.

## Install & run

```bash
pip install -r requirements.txt
python main.py
```

## Try it without a real LMU export

```bash
python tools/generate_test_data.py session1.duckdb --laps 3 --seed 1
python tools/generate_test_data.py session2.duckdb --laps 2 --seed 2
python main.py
```

Then in the app: **Import telemetry…**, select both files, check a
lap under each session in the sidebar, and hit **Plot selected**.

## Project layout

```
main.py                  entry point
core/
  session.py              duckdb loading, WAL replay, lap boundary/time
                           reconstruction (ported from the reference script)
  lapdata.py               per-lap channel extraction + distance resampling
ui/
  main_window.py            toolbar + sidebar + plot layout
  session_tree.py           sidebar: sessions -> checkable laps
  plot_widget.py             stacked pyqtgraph plot + drag-to-zoom
tools/
  generate_test_data.py     synthetic .duckdb generator for trying the app out
```

## Notes on the data format

Sessions are duckdb files with:
- `channelsList(channelName, frequency)` - lists every **dense**
  channel (fixed-frequency samples, one row per sample, no timestamp
  column - time is `row_index / frequency`).
- One table per dense channel (e.g. `"Ground Speed"`, `"Throttle Pos"`,
  `"Brake Pos"`, `"Engine RPM"`, `"Lap Dist"`), each with a `value` column.
- **Sparse** event tables that only log on change: `"Gear"`, `"Lap"`
  (lap-boundary markers), `"Lap Time"` (completed lap time, logged at
  the *next* lap boundary) - each with `(ts, value)` columns.
