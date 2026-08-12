"""
ui/plot_widget.py

Stacked, distance-aligned telemetry plot (speed / delta / throttle /
brake / RPM / gear), styled after the reference CLI script's PNG
output but interactive:

  * drag a rectangle on any panel -> all panels zoom to that distance
    range together (they share a linked X/distance axis, mirroring
    how the reference script resamples every channel onto one
    distance grid)
  * double left-click any panel -> reset zoom

Single-lap views get per-channel accent colors (dashboard style);
multi-lap comparisons keep one consistent color per lap across every
panel, with a delta-vs-first-lap panel, exactly like the CLI script.
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
    "rpm": "#f5a623",
    "gear": "#c77dff",
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
        self._build_layout(multi=False)

    # -- layout ---------------------------------------------------------
    def _build_layout(self, multi: bool):
        self.clear()
        self._plots.clear()
        self._viewboxes.clear()

        panels = ["speed", "delta", "throttle", "brake", "rpm", "gear"] if multi else \
                 ["speed", "throttle", "brake", "rpm", "gear"]
        row_stretch = {"speed": 3, "delta": 1, "throttle": 1, "brake": 1, "rpm": 1, "gear": 1}
        labels = {
            "speed": "Speed (km/h)", "delta": "Delta (s)", "throttle": "Throttle (%)",
            "brake": "Brake (%)", "rpm": "RPM", "gear": "Gear",
        }

        master_plot = None
        for row, name in enumerate(panels):
            vb = ZoomViewBox()
            plot = self.addPlot(row=row, col=0, viewBox=vb)
            plot.setLabel("left", labels[name], color=FG)
            plot.showGrid(x=True, y=True, alpha=0.25)
            plot.getAxis("bottom").setPen(pg.mkPen(GRID))
            plot.getAxis("left").setPen(pg.mkPen(GRID))
            plot.getAxis("bottom").setTextPen(pg.mkPen(MUTED))
            plot.getAxis("left").setTextPen(pg.mkPen(MUTED))
            if row < len(panels) - 1:
                plot.getAxis("bottom").setStyle(showValues=False)
            else:
                plot.setLabel("bottom", "Distance (m)", color=FG)
            self.ci.layout.setRowStretchFactor(row, row_stretch[name])

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
        self._build_layout(multi=multi)
        if not laps:
            return

        ref = laps[0]

        def color(l: LapData, channel: str) -> str:
            return l.color if multi else CHANNEL_COLORS[channel]

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

        for l in laps:
            brake_color = QColor(color(l, "brake"))
            curve = self._plots["brake"].plot(l.dist, l.brake, pen=pg.mkPen(brake_color, width=1.6))
            zero_curve = self._plots["brake"].plot(l.dist, np.zeros_like(l.dist), pen=None)
            fill_color = QColor(brake_color)
            fill_color.setAlpha(60)
            self._plots["brake"].addItem(pg.FillBetweenItem(curve, zero_curve, brush=pg.mkBrush(fill_color)))
        self._plots["brake"].setYRange(-5, 105)

        for l in laps:
            self._plots["rpm"].plot(l.dist, l.rpm, pen=pg.mkPen(color(l, "rpm"), width=1.4))

        for l in laps:
            self._plots["gear"].plot(l.dist, l.gear, pen=pg.mkPen(color(l, "gear"), width=1.8))
        self._plots["gear"].getAxis("left").setTicks([[(i, str(i)) for i in range(1, 9)]])

        for plot in self._plots.values():
            plot.enableAutoRange(x=True, y=True)

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
