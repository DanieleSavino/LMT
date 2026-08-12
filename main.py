#!/usr/bin/env python3
"""
LMU Telemetry - a small desktop app for browsing and comparing
Le Mans Ultimate (and other iRacing/rFactor2/ACC-style) duckdb
telemetry exports.

Run:
    pip install -r requirements.txt
    python main.py
"""
import sys

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    # OrganizationName + ApplicationName together determine where
    # QStandardPaths.AppDataLocation points (see core/persistence.py) -
    # set both up front so imported-session history lands in a stable,
    # OS-appropriate folder rather than drifting if the app name ever
    # changes casing/spacing.
    app.setOrganizationName("lmt-telemetry")
    app.setApplicationName("LMT")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
