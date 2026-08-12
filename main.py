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
import threading

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow
from core import liveslip


def start_telemetry_daemon():
    """
    Spawns the liveslip runtime capture in a background thread.
    daemon=True ensures it closes automatically when the UI exits.
    """
    capture_thread = threading.Thread(target=liveslip.main, daemon=True)
    capture_thread.start()


def main():
    app = QApplication(sys.argv)
    
    # OrganizationName + ApplicationName together determine where
    # QStandardPaths.AppDataLocation points (see core/persistence.py) -
    # set both up front so imported-session history lands in a stable,
    # OS-appropriate folder rather than drifting if the app name ever
    # changes casing/spacing.
    app.setOrganizationName("lmt-telemetry")
    app.setApplicationName("LMT")
    
    # Start the background telemetry capture
    start_telemetry_daemon()
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
