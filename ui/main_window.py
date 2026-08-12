"""
ui/main_window.py

Top-level window: toolbar (import / plot / reset / remove), a
SessionTree sidebar of imported files & laps, and the stacked
TelemetryPlotWidget on the right.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog, QLabel, QMainWindow, QMessageBox, QSplitter, QStatusBar,
    QToolBar, QVBoxLayout, QWidget,
)

from core import persistence
from core.lapdata import extract_lap
from core.session import TelemetrySession
from ui.plot_widget import TelemetryPlotWidget
from ui.session_tree import SESSION_ROLE, SessionTree


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LMT")
        self.resize(1500, 950)
        self.setStyleSheet(
            "QMainWindow, QWidget { background-color: #0b0b0d; color: #d8d8d8; }"
            "QTreeWidget { background-color: #131315; border: 1px solid #2a2a2e; }"
            "QTreeWidget::item { padding: 3px; }"
            "QHeaderView::section { background-color: #17171a; color: #8a8a8e; border: none; padding: 4px; }"
            "QToolBar { background-color: #17171a; border: none; spacing: 6px; padding: 4px; }"
            "QStatusBar { background-color: #17171a; color: #8a8a8e; }"
            "QToolButton { background-color: #232327; border: 1px solid #33333a; "
            "padding: 5px 10px; border-radius: 4px; }"
            "QToolButton:hover { background-color: #2d2d33; }"
            "QLabel { color: #8a8a8e; }"
        )

        self.tree = SessionTree()
        self.tree.selectionChanged.connect(self._on_selection_changed)

        self.plot_widget = TelemetryPlotWidget()

        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(6, 6, 6, 6)
        left_layout.addWidget(QLabel("Imported sessions - check laps to plot / compare:"))
        left_layout.addWidget(self.tree)
        splitter.addWidget(left)
        splitter.addWidget(self.plot_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 1180])
        self.setCentralWidget(splitter)

        self._build_toolbar()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage(
            "Import a .duckdb telemetry export, check one or more laps, then Plot. "
            "Left-drag to zoom, right-drag (or Shift+scroll) to pan, "
            "scroll to zoom, double-click to reset."
        )

        self._restore_sessions()

    # -- persistence ----------------------------------------------------
    def _restore_sessions(self):
        """Re-imports whatever was in the sidebar last time the app
        closed. A remembered file that's since been moved/deleted is
        just dropped from the list, quietly - no error dialog, since
        that's expected background drift (exports get cleaned up,
        drives get unmounted, etc.), not something the user did wrong.
        """
        remembered = persistence.load_paths()
        if not remembered:
            return

        restored = 0
        still_present = []
        for path in remembered:
            try:
                sess = TelemetrySession(path)
                self.tree.add_session(sess)
                restored += 1
                still_present.append(path)
            except Exception:
                pass  # gone/unreadable - silently drop it, per above

        if len(still_present) != len(remembered):
            persistence.save_paths(still_present)

        if restored:
            self.statusBar().showMessage(f"Restored {restored} session(s) from last time.", 5000)

    def _persist_current_sessions(self):
        persistence.save_paths([str(sess.path) for sess in self.tree.sessions()])

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)

        act_import = QAction("Import telemetry…", self)
        act_import.triggered.connect(self.import_telemetry)
        tb.addAction(act_import)

        tb.addSeparator()

        act_plot = QAction("Plot selected", self)
        act_plot.triggered.connect(self.plot_selected)
        tb.addAction(act_plot)

        act_clear_sel = QAction("Clear selection", self)
        act_clear_sel.triggered.connect(self.tree.clear_selection)
        tb.addAction(act_clear_sel)

        act_reset_zoom = QAction("Reset zoom", self)
        act_reset_zoom.triggered.connect(self.plot_widget.reset_zoom)
        tb.addAction(act_reset_zoom)

        tb.addSeparator()

        act_remove = QAction("Remove session…", self)
        act_remove.triggered.connect(self.remove_session)
        tb.addAction(act_remove)

    # -- actions ------------------------------------------------------------
    def import_telemetry(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import telemetry (.duckdb)", str(Path.home()), "DuckDB telemetry (*.duckdb);;All files (*)"
        )
        imported = 0
        for path in paths:
            try:
                sess = TelemetrySession(path)
                self.tree.add_session(sess)
                imported += 1
            except Exception as exc:
                QMessageBox.warning(self, "Import failed", f"{Path(path).name}:\n{exc}")
        if imported:
            self.statusBar().showMessage(f"Imported {imported} file(s).", 5000)
            self._persist_current_sessions()

    def remove_session(self):
        item = self.tree.currentItem()
        if item is None:
            QMessageBox.information(self, "Remove session", "Select a session (or one of its laps) in the tree first.")
            return
        while item.parent() is not None:
            item = item.parent()
        key = item.data(0, SESSION_ROLE)
        self.tree.remove_session(key)
        self._persist_current_sessions()

    def plot_selected(self):
        selected = self.tree.selected_laps()
        if not selected:
            QMessageBox.information(self, "Plot selected", "Check at least one lap in the sidebar first.")
            return
        laps = []
        for sess, lap_number, color in selected:
            try:
                laps.append(extract_lap(sess, lap_number, color=color))
            except Exception as exc:
                QMessageBox.warning(self, "Plot failed", f"{sess.label} lap {lap_number}:\n{exc}")
        self.plot_widget.set_laps(laps)
        self.statusBar().showMessage(f"Plotted {len(laps)} lap(s).", 5000)

    def _on_selection_changed(self):
        n = len(self.tree.selected_laps())
        self.statusBar().showMessage(f"{n} lap(s) selected." if n else "No laps selected.")

    def closeEvent(self, event):
        for sess in self.tree.sessions():
            sess.close()
        super().closeEvent(event)
