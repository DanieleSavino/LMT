"""
ui/session_tree.py

Left sidebar: one top-level item per imported duckdb file, each with a
checkable child item per lap. Checking laps (in click order) builds
the list sent to the plot widget - the first checked lap becomes the
reference lap for the delta panel and gets the reference red color,
mirroring the CLI script's "first --lap given is the reference".
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from core.lapdata import fmt_laptime
from core.session import TelemetrySession

DEFAULT_COLORS = ["#e6002b", "#ffffff", "#3aa0ff", "#f5c518", "#7fdc7f", "#c77dff"]

SESSION_ROLE = Qt.UserRole + 1
LAP_ROLE = Qt.UserRole + 2


class SessionTree(QTreeWidget):
    selectionChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["Session / Lap", "Time"])
        self.setColumnWidth(0, 220)
        self._sessions: dict = {}
        self._checked_order: list = []  # [(session key, lap number), ...] in check order
        self.itemChanged.connect(self._on_item_changed)

    def add_session(self, sess: TelemetrySession) -> str:
        key = str(sess.path)
        self._sessions[key] = sess

        top = QTreeWidgetItem([sess.label, ""])
        top.setData(0, SESSION_ROLE, key)
        top.setFlags(top.flags() & ~Qt.ItemIsUserCheckable)
        self.addTopLevelItem(top)

        for lap in sess.list_laps():
            child = QTreeWidgetItem([f"Lap {lap['lap']}", fmt_laptime(lap["laptime"])])
            child.setData(0, SESSION_ROLE, key)
            child.setData(0, LAP_ROLE, lap["lap"])
            child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
            child.setCheckState(0, Qt.Unchecked)
            top.addChild(child)
        top.setExpanded(True)
        return key

    def remove_session(self, key: str):
        sess = self._sessions.pop(key, None)
        if sess:
            sess.close()
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            if item.data(0, SESSION_ROLE) == key:
                self.takeTopLevelItem(i)
                break
        self._checked_order = [(k, l) for k, l in self._checked_order if k != key]
        self.selectionChanged.emit()

    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        if item.data(0, LAP_ROLE) is None:
            return
        key = item.data(0, SESSION_ROLE)
        lap = item.data(0, LAP_ROLE)
        entry = (key, lap)
        if item.checkState(0) == Qt.Checked:
            if entry not in self._checked_order:
                self._checked_order.append(entry)
        else:
            if entry in self._checked_order:
                self._checked_order.remove(entry)
        self.selectionChanged.emit()

    def selected_laps(self):
        """Returns [(session, lap_number, color), ...] in check order."""
        out = []
        for i, (key, lap) in enumerate(self._checked_order):
            sess = self._sessions[key]
            color = DEFAULT_COLORS[i % len(DEFAULT_COLORS)]
            out.append((sess, lap, color))
        return out

    def clear_selection(self):
        self.blockSignals(True)
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            for j in range(top.childCount()):
                top.child(j).setCheckState(0, Qt.Unchecked)
        self.blockSignals(False)
        self._checked_order.clear()
        self.selectionChanged.emit()

    def sessions(self):
        return list(self._sessions.values())
