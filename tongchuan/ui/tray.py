"""Tray icon (Windows) / menu-bar icon (macOS) and the macOS Dock menu: 开始/停止 · 暂停/继续 · 退出.

Also toggles the two floating windows and brings the control panel back. The same QActions feed
the tray menu and the Dock menu, so their labels and check marks stay in sync from refresh().
"""
from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..config import IS_MAC
from .icons import tray_icon


class Tray(QObject):
    def __init__(self, win) -> None:
        super().__init__(win)
        self.win = win
        self.act_start = QAction("开始", self, triggered=win._toggle)
        self.act_mute = QAction("暂停（静音）", self, triggered=win.toggle_mute)
        self.act_convo = QAction("对话悬浮窗", self, checkable=True, triggered=win.toggle_convo)
        self.act_bar = QAction("对方字幕条", self, checkable=True, triggered=win.toggle_overlay_pin)
        self.act_show = QAction("显示主窗口", self, triggered=win.show_main)
        self.act_quit = QAction("退出", self, triggered=win.quit_app)

        self.menu = self._build_menu()
        self.icon: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.icon = QSystemTrayIcon(tray_icon("stopped"), self)
            self.icon.setToolTip("同传助手")
            self.icon.setContextMenu(self.menu)
            self.icon.activated.connect(self._activated)
            self.icon.show()
        self.dock_menu: QMenu | None = None
        if IS_MAC and hasattr(QMenu, "setAsDockMenu"):  # Qt 6 exposes this on macOS only
            self.dock_menu = self._build_menu(dock=True)
            try:
                self.dock_menu.setAsDockMenu()
            except Exception:  # noqa: BLE001 — older PySide6 without the binding
                self.dock_menu = None
        self.refresh()

    def _build_menu(self, dock: bool = False) -> QMenu:
        m = QMenu()
        m.addAction(self.act_start)
        m.addAction(self.act_mute)
        m.addSeparator()
        m.addAction(self.act_convo)
        m.addAction(self.act_bar)
        if not dock:  # the Dock icon itself shows the window; the tray needs an explicit entry
            m.addSeparator()
            m.addAction(self.act_show)
        m.addSeparator()
        m.addAction(self.act_quit)
        return m

    def _activated(self, reason) -> None:
        # Windows: left click opens the control panel (the menu is on right click; macOS shows the
        # menu for any click on a menu-bar icon that has one)
        if reason == QSystemTrayIcon.ActivationReason.Trigger and not IS_MAC:
            self.win.show_main()

    def refresh(self) -> None:
        eng, s = self.win.engine, self.win.s
        running = eng.running
        self.act_start.setText("停止" if running else "开始")
        self.act_mute.setEnabled(running)
        self.act_mute.setText("继续（取消静音）" if eng.muted else "暂停（静音）")
        self.act_convo.setChecked(self.win.convo.isVisible())
        self.act_bar.setChecked(s.overlay_pinned)
        state = "muted" if running and eng.muted else ("running" if running else "stopped")
        if self.icon:
            self.icon.setIcon(tray_icon(state))
            self.icon.setToolTip("同传助手 · " + {"running": "运行中", "muted": "已静音", "stopped": "已停止"}[state])
