"""
ui/plot_widget.py

Stacked, distance-aligned telemetry plot (speed / delta / throttle /
TC / loss-of-traction / brake / ABS / lockup / RPM / gear), styled
after the reference CLI script's PNG output but interactive:

  * drag a rectangle on any panel -> all panels zoom to that distance
    range together (they share a linked X/distance axis, mirroring
    how the reference script resamples every channel onto one
    distance grid)
  * double left-click any panel -> reset zoom

Single-lap views get per-channel accent colors (dashboard style);
multi-lap comparisons keep one consistent color per lap across every
panel, with a delta-vs-first-lap panel, exactly like the CLI script.

TC / ABS / lockup / loss-of-traction panels are conditional, decided
fresh each time set_laps() is called: TC only appears if any plotted
lap actually has TC data; ABS only appears if it intervened at least
once (most of the field has no ABS at all, so a permanently-flat-zero
panel isn't worth the row - only GT3 cars use it); lockup is a
stand-in for cars without ABS, so it's only shown when the ABS panel
isn't; loss-of-traction is independent of TC and shown whenever
wheel-speed-derived slip data is available.

Distance-stall shading: since every panel is indexed by distance, an
event that covers almost no net track distance over real seconds
(spinning, stalling, a long off-track recovery) has no x-axis room to
be shown - the resampled channels there are a straight interpolation
between "before" and "after", not a record of what happened. Those
stretches (LapData.gap, see core/lapdata.py) get a translucent shaded
band in each lap's color across every panel, so a flat trace through
one reads as "can't be shown here" rather than "nothing happened".
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from scipy.interpolate import interp1d

from core.lapdata import LapData, fmt_laptime

BG = "#0b0b0d"
FG = "#d8d8d8"
GRID = "#2a2a2e"
MUTED = "#8a8a8e"

CHANNEL_COLORS = {
    "speed": "#3aa0ff",
    "throttle": "#59d16c",
    "brake": "#e6002b",
    "tc": "#ffd60a",
    "abs": "#ff6b9d",
    "lockup": "#ff3b3b",
    "traction_loss": "#00e5ff",
    "rpm": "#f5a623",
    "gear": "#c77dff",
}

PANEL_LABELS = {
    "speed": "Speed (km/h)", "delta": "Delta (s)", "throttle": "Throttle (%)",
    "tc": "TC", "traction_loss": "Loss of Traction", "brake": "Brake (%)",
    "abs": "ABS", "lockup": "Lockup", "rpm": "RPM", "gear": "Gear",
}

PANEL_ROW_STRETCH = {
    "speed": 3, "delta": 1, "throttle": 1, "tc": 1, "traction_loss": 1,
    "brake": 1, "abs": 1, "lockup": 1, "rpm": 1, "gear": 1,
}

pg.setConfigOption("background", BG)
pg.setConfigOption("foreground", FG)
pg.setConfigOption("antialias", True)


class ZoomViewBox(pg.ViewBox):
    """A ViewBox with a two-gesture navigation scheme:

      * left-click-drag draws a rubber band and, on release, reports
        the X range it spans (in data/distance coordinates) via
        `rangeSelected` - unambiguously a "zoom to this range" gesture.
      * right-click-drag pans left/right along the (shared, linked)
        distance axis - this is what lets you scroll sideways once
        you've zoomed in, which a left-drag alone can't do.
      * scroll wheel zooms in/out on X; holding Shift while scrolling
        pans X instead, for touchpad/mouse users who'd rather not
        right-drag.
      * double left-click asks the plot to reset its zoom.

    Built-in ViewBox mouse handling is left off (setMouseEnabled(False))
    so every gesture above is handled explicitly here rather than
    fighting pyqtgraph's default left-drag-pans-by-default behaviour.
    """

    rangeSelected = Signal(float, float)
    resetRequested = Signal()
    manualNavigation = Signal()  # fired on any pan/zoom the user drives by hand

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMouseEnabled(x=False, y=False)
        self._rubber = pg.LinearRegionItem(brush=(120, 170, 255, 60), pen=pg.mkPen("#7fb3ff", width=1))
        self._rubber.setZValue(1000)
        self._rubber.setMovable(False)
        self._rubber.hide()
        self.addItem(self._rubber, ignoreBounds=True)

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() == Qt.RightButton:
            ev.accept()
            # Same delta-in-view-coordinates approach pyqtgraph's own
            # PanMode uses: map last/current scene positions into data
            # space and translate by the difference, so the point under
            # the cursor stays under the cursor while dragging.
            p_last = self.mapToView(ev.lastPos())
            p_now = self.mapToView(ev.pos())
            dx = p_now.x() - p_last.x()
            if dx != 0:
                self.manualNavigation.emit()
                self.translateBy(x=-dx, y=0)
            if ev.isFinish():
                self.setCursor(Qt.ArrowCursor)
            else:
                self.setCursor(Qt.ClosedHandCursor)
            return

        if ev.button() != Qt.LeftButton:
            ev.ignore()
            return
        ev.accept()
        p0 = self.mapToView(ev.buttonDownPos())
        p1 = self.mapToView(ev.pos())
        x0, x1 = sorted((p0.x(), p1.x()))

        if not ev.isFinish():
            self._rubber.setRegion((x0, x1))
            self._rubber.show()
            return

        self._rubber.hide()
        if abs(x1 - x0) < 1e-9:
            return
        self.manualNavigation.emit()
        self.rangeSelected.emit(x0, x1)

    def mouseClickEvent(self, ev):
        if ev.double() and ev.button() == Qt.LeftButton:
            self.resetRequested.emit()
            ev.accept()
        else:
            ev.ignore()

    def wheelEvent(self, ev, axis=None):
        try:
            delta = ev.delta()
        except Exception:
            delta = 0

        self.manualNavigation.emit()

        if ev.modifiers() & Qt.ShiftModifier:
            # Shift+scroll: pan X instead of zooming, same direction
            # convention as most apps (scroll down/right -> move right).
            (x0, x1), _ = self.viewRange()
            span = x1 - x0
            step = -0.15 * span if delta > 0 else 0.15 * span
            self.setXRange(x0 + step, x1 + step, padding=0)
            ev.accept()
            return

        s = 0.9 if delta > 0 else 1.1
        self.scaleBy(x=s, y=1.0, center=self.mapToView(ev.pos()))
        ev.accept()


class TelemetryPlotWidget(pg.GraphicsLayoutWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackground(BG)
        self._plots: dict = {}
        self._viewboxes: list = []
        self._laps: list = []
        self._build_layout(["speed", "throttle", "brake", "rpm", "gear"])

    # -- layout ---------------------------------------------------------
    def _build_layout(self, panels: list):
        """(Re)builds the stacked panel layout for an explicit, ordered
        list of panel keys - which panels appear (and in what order) is
        decided by the caller in set_laps(), since that depends on which
        optional channels (TC / ABS / lockup / loss-of-traction) are
        actually present/relevant for the laps being plotted."""
        self.clear()
        self._plots.clear()
        self._viewboxes.clear()

        master_plot = None
        for row, name in enumerate(panels):
            vb = ZoomViewBox()
            plot = self.addPlot(row=row, col=0, viewBox=vb)
            plot.setLabel("left", PANEL_LABELS[name], color=FG)
            plot.showGrid(x=True, y=True, alpha=0.25)
            plot.getAxis("bottom").setPen(pg.mkPen(GRID))
            plot.getAxis("left").setPen(pg.mkPen(GRID))
            plot.getAxis("bottom").setTextPen(pg.mkPen(MUTED))
            plot.getAxis("left").setTextPen(pg.mkPen(MUTED))
            if row < len(panels) - 1:
                plot.getAxis("bottom").setStyle(showValues=False)
            else:
                plot.setLabel("bottom", "Distance (m)", color=FG)
            self.ci.layout.setRowStretchFactor(row, PANEL_ROW_STRETCH[name])

            # Link every panel's X axis directly to one master (the
            # first panel) rather than chaining plot->plot->plot: a
            # chain compounds each link's own padding/rounding at every
            # hop, so panels several rows down could drift away from an
            # exact zoom range. A single shared master keeps every
            # panel byte-for-byte in sync.
            if master_plot is None:
                master_plot = plot
            else:
                plot.setXLink(master_plot)

            vb.rangeSelected.connect(self._on_range_selected)
            vb.resetRequested.connect(self.reset_zoom)
            vb.manualNavigation.connect(self._on_manual_navigation)
            self._plots[name] = plot
            self._viewboxes.append(vb)

    # -- data -------------------------------------------------------------
    def set_laps(self, laps: list):
        self._laps = laps
        multi = len(laps) > 1

        # Which optional panels make sense for *this* set of laps:
        #  - TC: shown whenever at least one lap actually has TC data
        #    (some cars/classes don't run TC and the channel may be
        #    entirely absent from the export).
        #  - ABS: shown only if it actually intervened at least once in
        #    any of the plotted laps - most of the LMU field (Hypercar,
        #    LMP2, GTE...) has no ABS at all, so the channel is either
        #    missing or permanently zero for them; only GT3 cars use it.
        #    A panel that's always flat at zero isn't worth the row.
        #  - Lockup / loss-of-traction are derived from wheel-speed vs
        #    ground-speed slip (see core/lapdata.py) and only exist if
        #    the export includes a "Wheel Speed" channel. Lockup is a
        #    stand-in for ABS on cars that don't have it, so it's only
        #    shown when the ABS panel isn't; loss-of-traction is useful
        #    regardless of TC and is shown whenever slip data exists.
        has_tc = any(l.tc is not None for l in laps)
        has_abs_activation = any(l.abs is not None and np.any(l.abs > 0.5) for l in laps)
        has_slip_data = any(l.lockup is not None for l in laps)

        show_tc = has_tc
        show_abs = has_abs_activation
        show_traction_loss = has_slip_data
        show_lockup = has_slip_data and not show_abs

        panels = ["speed"]
        if multi:
            panels.append("delta")
        panels.append("throttle")
        if show_tc:
            panels.append("tc")
        if show_traction_loss:
            panels.append("traction_loss")
        panels.append("brake")
        if show_abs:
            panels.append("abs")
        if show_lockup:
            panels.append("lockup")
        panels += ["rpm", "gear"]

        self._build_layout(panels)
        if not laps:
            return

        ref = laps[0]

        def color(l: LapData, channel: str) -> str:
            return l.color if multi else CHANNEL_COLORS[channel]

        def plot_filled_events(panel: str, values_attr: str):
            """Shared style for step/event traces (TC, ABS, lockup,
            loss-of-traction): filled from zero so intervention periods
            read as solid blocks rather than thin spike lines. Laps
            missing this particular channel are skipped rather than
            erroring, so mixed sessions (e.g. one file has TC data,
            another doesn't) still render everything they can."""
            for l in laps:
                values = getattr(l, values_attr)
                if values is None:
                    continue
                c = QColor(color(l, panel))
                curve = self._plots[panel].plot(l.dist, values, pen=pg.mkPen(c, width=1.6))
                zero_curve = self._plots[panel].plot(l.dist, np.zeros_like(l.dist), pen=None)
                fill_color = QColor(c)
                fill_color.setAlpha(80)
                self._plots[panel].addItem(pg.FillBetweenItem(curve, zero_curve, brush=pg.mkBrush(fill_color)))

        for l in laps:
            self._plots["speed"].plot(
                l.dist, l.speed, pen=pg.mkPen(color(l, "speed"), width=2),
                name=f"{l.display_label}  ({fmt_laptime(l.laptime)})",
            )
        legend = self._plots["speed"].addLegend(offset=(-10, 10))
        legend.setBrush(pg.mkBrush(20, 20, 22, 210))
        legend.setLabelTextColor(FG)

        if multi and "delta" in self._plots:
            common_max = min(l.max_dist for l in laps)
            ref_time_at = interp1d(ref.dist, ref.time_at_dist, bounds_error=False, fill_value="extrapolate")
            for l in laps[1:]:
                grid = l.dist[l.dist <= common_max]
                delta = l.time_at_dist[: len(grid)] - ref_time_at(grid)
                self._plots["delta"].plot(grid, delta, pen=pg.mkPen(l.color, width=1.6))
            zero_line = pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(ref.color, width=1))
            self._plots["delta"].addItem(zero_line)

        for l in laps:
            self._plots["throttle"].plot(l.dist, l.throttle, pen=pg.mkPen(color(l, "throttle"), width=1.6))
        self._plots["throttle"].setYRange(-5, 105)

        if "tc" in self._plots:
            plot_filled_events("tc", "tc")

        if "traction_loss" in self._plots:
            plot_filled_events("traction_loss", "traction_loss")

        for l in laps:
            brake_color = QColor(color(l, "brake"))
            curve = self._plots["brake"].plot(l.dist, l.brake, pen=pg.mkPen(brake_color, width=1.6))
            zero_curve = self._plots["brake"].plot(l.dist, np.zeros_like(l.dist), pen=None)
            fill_color = QColor(brake_color)
            fill_color.setAlpha(60)
            self._plots["brake"].addItem(pg.FillBetweenItem(curve, zero_curve, brush=pg.mkBrush(fill_color)))
        self._plots["brake"].setYRange(-5, 105)

        if "abs" in self._plots:
            plot_filled_events("abs", "abs")

        if "lockup" in self._plots:
            plot_filled_events("lockup", "lockup")

        for l in laps:
            self._plots["rpm"].plot(l.dist, l.rpm, pen=pg.mkPen(color(l, "rpm"), width=1.4))

        for l in laps:
            self._plots["gear"].plot(l.dist, l.gear, pen=pg.mkPen(color(l, "gear"), width=1.8))
        self._plots["gear"].getAxis("left").setTicks([[(i, str(i)) for i in range(1, 9)]])

        self._plot_gaps(laps, color)

        for plot in self._plots.values():
            plot.enableAutoRange(x=True, y=True)

    def _plot_gaps(self, laps: list, color):
        """Shade distance ranges where a lap's gap[] flag is set - a
        spin/off/stall that covered almost no net track distance over
        real seconds of driving (see core/lapdata.GAP_TIME_PER_STEP).
        Every channel through that stretch is a straight-line
        interpolation between "before" and "after", not a record of
        what happened, since there's no distance x-axis room to show
        it - this makes that explicit instead of silently drawing a
        smooth (and misleading) line through the event."""
        for l in laps:
            if l.gap is None or not np.any(l.gap):
                continue
            c = QColor(color(l, "speed"))
            c.setAlpha(60)
            edge = QColor(c)
            edge.setAlpha(140)
            # Contiguous runs of True in l.gap -> (start_dist, end_dist)
            idx = np.flatnonzero(l.gap)
            breaks = np.flatnonzero(np.diff(idx) > 1)
            starts = np.concatenate([[0], breaks + 1])
            ends = np.concatenate([breaks, [len(idx) - 1]])
            for s, e in zip(starts, ends):
                # gap[i] marks the grid point *after* the jump, so the
                # stalled stretch runs from the previous point to here.
                lo = l.dist[max(idx[s] - 1, 0)]
                hi = l.dist[idx[e]]
                for plot in self._plots.values():
                    region = pg.LinearRegionItem(
                        values=(lo, hi), movable=False,
                        brush=pg.mkBrush(c), pen=pg.mkPen(edge, width=1),
                    )
                    region.setZValue(-10)
                    plot.addItem(region, ignoreBounds=True)

    # -- zoom / pan -----------------------------------------------------------
    def _on_manual_navigation(self):
        # Any hand-driven pan or zoom (drag-pan, shift+wheel, wheel-zoom)
        # should "stick" rather than get silently overridden the next
        # time autorange recomputes - so X autorange comes off as soon
        # as the user touches navigation at all, same as the existing
        # drag-to-zoom behaviour already did.
        for plot in self._plots.values():
            plot.enableAutoRange(x=False)

    def _on_range_selected(self, x0: float, x1: float):
        # Driving the master panel's X range is enough - every other
        # panel is X-linked to it and pyqtgraph aligns them pixel-for-
        # pixel (accounting for each panel's own left-margin/label
        # width), which is the correct behaviour for a shared distance
        # axis across panels with differently-sized Y tick labels.
        for plot in self._plots.values():
            plot.enableAutoRange(x=False)  # else Y-autorange below re-triggers an X autoscale too

        master = next(iter(self._plots.values()))
        master.setXRange(x0, x1, padding=0)

        for plot in self._plots.values():
            plot.enableAutoRange(y=True)
            plot.vb.updateAutoRange()

    def reset_zoom(self):
        for plot in self._plots.values():
            plot.enableAutoRange(x=True, y=True)
            plot.vb.updateAutoRange()
