from __future__ import annotations

APP_BG = "#14110D"
TERMINAL_BG = "#0D0B08"
CONTROL_BG = "#120F0B"
STATUS_BLOCK = "#191510"
PANEL_BG = "#1D1913"
PANEL_ALT = "#241F17"
PROGRESS_TRACK = "#17130D"

BORDER = "#332B20"
CONTROL_BORDER = "#473A28"
STATUS_BORDER = "#2A2318"
TERMINAL_BORDER = "#3E2F1A"
ACCENT_BORDER = "#4A3A1E"

TEXT = "#F2ECE0"
MUTED = "#A4967E"
ON_ACCENT = "#14110D"
ON_RED = "#FFFFFF"

ACCENT = "#F5A524"
ACCENT_DARK = "#D4880F"
ACCENT_BRIGHT = "#FFC55C"
ACCENT_SOFT = "#2A2011"
TERMINAL_LINE = "#FFC55C"

RED = "#F5544E"
RED_DARK = "#FF7A72"
RED_SOFT = "#2A1712"

DISABLED_BG = "#241F17"
DISABLED_TEXT = "#6B5E49"
DISABLED_BORDER = "#3A3020"

RAIN_RGB = (245, 165, 36)
RAIN_BG = (13, 11, 8)

FONT_SANS = '"Microsoft YaHei UI", "Noto Sans SC", "Segoe UI"'
FONT_MONO = '"Consolas", "JetBrains Mono", monospace'

RADIUS_LG = 8
RADIUS_MD = 7
RADIUS_SM = 6
RADIUS_XS = 4


def build_stylesheet() -> str:
    return f"""
    QWidget {{ color: {TEXT}; font-family: {FONT_SANS}; font-size: 13px; }}
    QWidget#RootWindow {{ background: {APP_BG}; }}

    QFrame#Sidebar, QFrame#Panel {{ background: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: {RADIUS_LG}px; }}
    QFrame#SoftPanel, QFrame#FormatPanel {{ background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: {RADIUS_LG}px; }}
    QFrame#StatusBlock {{ background: {STATUS_BLOCK}; border: 1px solid {STATUS_BORDER}; border-radius: {RADIUS_LG}px; }}
    QFrame#ProcessingTerminal {{ background: {TERMINAL_BG}; border: 1px solid {TERMINAL_BORDER}; border-radius: {RADIUS_LG}px; }}
    QStackedWidget#Stack, QScrollArea#FormScroll, QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: 0; }}

    QLabel, QCheckBox {{ background: transparent; }}
    QLabel#Brand {{ font-size: 22px; font-weight: 700; color: {ACCENT}; padding: 6px 6px; }}
    QLabel#PageTitle {{ font-size: 21px; font-weight: 700; }}
    QLabel#Muted, QLabel#FieldLabel {{ color: {MUTED}; }}
    QLabel#StatusMessage {{ color: {ACCENT}; font-family: {FONT_MONO}; font-weight: 600; }}
    QLabel#DecryptProgressLabel, QLabel#TranscodeProgressLabel {{ color: {TEXT}; font-weight: 700; }}
    QLabel#AppVersion {{ color: {MUTED}; padding: 6px 4px; }}
    QLabel#StatusOk {{ background: {ACCENT_SOFT}; color: {ACCENT_DARK}; border: 1px solid {ACCENT}; border-radius: {RADIUS_LG}px; padding: 6px 10px; font-weight: 600; }}
    QLabel#StatusOff {{ background: {RED_SOFT}; color: {RED_DARK}; border: 1px solid {RED}; border-radius: {RADIUS_LG}px; padding: 6px 10px; font-weight: 600; }}
    QLabel#Stat {{ background: {ACCENT_SOFT}; border: 1px solid {BORDER}; border-radius: {RADIUS_LG}px; padding: 6px 10px; color: {ACCENT}; font-weight: 600; }}
    QLabel#DecodeSuccessRate, QLabel#TranscodeSuccessRate, QLabel#SuccessRate {{ background: {RED_SOFT}; border: 1px solid {BORDER}; border-radius: {RADIUS_LG}px; padding: 6px 10px; color: {RED_DARK}; font-weight: 600; }}
    QLabel#TerminalTitle {{ color: {ACCENT}; font-family: {FONT_MONO}; font-size: 11px; font-weight: 700; }}
    QLabel#TerminalLine {{ color: {TERMINAL_LINE}; font-family: {FONT_MONO}; font-size: 10px; }}

    QLineEdit#Input, QComboBox#Combo, QComboBox#QQOutputFormat, QSpinBox#Spin, QSpinBox#TranscodeWorkers {{ background: {CONTROL_BG}; border: 1px solid {CONTROL_BORDER}; border-radius: {RADIUS_MD}px; padding: 5px 8px; min-height: 22px; color: {TEXT}; }}
    QLineEdit#Input:hover, QComboBox#Combo:hover, QComboBox#QQOutputFormat:hover, QSpinBox#Spin:hover, QSpinBox#TranscodeWorkers:hover {{ border: 1px solid {ACCENT}; }}
    QLineEdit#Input:focus, QComboBox#Combo:focus, QComboBox#QQOutputFormat:focus, QSpinBox#Spin:focus, QSpinBox#TranscodeWorkers:focus {{ border: 1px solid {ACCENT_DARK}; }}
    QComboBox#Combo:on, QComboBox#QQOutputFormat:on {{ background: {ACCENT_SOFT}; border: 1px solid {ACCENT}; color: {ACCENT_BRIGHT}; }}
    QComboBox#Combo::drop-down, QComboBox#QQOutputFormat::drop-down {{ border-left: 1px solid {CONTROL_BORDER}; width: 24px; background: {ACCENT_SOFT}; border-top-right-radius: {RADIUS_MD}px; border-bottom-right-radius: {RADIUS_MD}px; }}
    QComboBox#Combo::drop-down:on, QComboBox#QQOutputFormat::drop-down:on {{ background: {ACCENT}; border-left: 1px solid {ACCENT_BRIGHT}; }}
    QComboBox#Combo QAbstractItemView, QComboBox#QQOutputFormat QAbstractItemView {{ background: {PANEL_BG}; color: {TEXT}; selection-background-color: {ACCENT_SOFT}; border: 1px solid {BORDER}; }}
    QListView#ComboPopup {{ background: {PANEL_BG}; border: 1px solid {ACCENT_BORDER}; border-radius: {RADIUS_LG}px; padding: 6px; outline: 0; color: {TEXT}; }}
    QListView#ComboPopup::item {{ min-height: 30px; padding: 7px 10px; margin: 2px; border: 1px solid transparent; border-radius: {RADIUS_SM}px; color: {TEXT}; background: transparent; }}
    QListView#ComboPopup::item:hover {{ background: {ACCENT_SOFT}; border: 1px solid {ACCENT_BORDER}; color: {ACCENT}; }}
    QListView#ComboPopup::item:selected {{ background: {ACCENT}; border: 1px solid {ACCENT_BRIGHT}; color: {ON_ACCENT}; }}
    QListView#ComboPopup::item:selected:hover {{ background: {ACCENT_BRIGHT}; border: 1px solid {ACCENT_BRIGHT}; color: {ON_ACCENT}; }}

    QPushButton {{ border: 1px solid {ACCENT_BORDER}; border-radius: {RADIUS_LG}px; padding: 7px 14px; background: {ACCENT_SOFT}; color: {ACCENT}; font-weight: 600; }}
    QPushButton:hover {{ border: 1px solid {ACCENT}; }}
    QPushButton#PrimaryButton {{ background: {ACCENT}; color: {ON_ACCENT}; min-width: 112px; border: 1px solid {ACCENT_BRIGHT}; }}
    QPushButton#PrimaryButton:hover {{ background: {ACCENT_DARK}; }}
    QPushButton#DangerButton {{ background: {RED_SOFT}; color: {RED_DARK}; border: 1px solid {RED_SOFT}; }}
    QPushButton#DangerButton:hover {{ border: 1px solid {RED}; }}
    QPushButton#DangerButton:disabled {{ background: {DISABLED_BG}; color: {DISABLED_TEXT}; border: 1px solid {DISABLED_BORDER}; }}
    QPushButton#UpdateButton {{ background: {RED_SOFT}; color: {RED_DARK}; border: 1px solid {RED_SOFT}; }}
    QPushButton#UpdateButton:hover {{ background: {RED}; color: {ON_RED}; }}
    QPushButton#SmallButton, QPushButton#OpenOutputButton {{ background: {ACCENT_SOFT}; color: {ACCENT_DARK}; padding: 6px 10px; }}
    QPushButton#DisabledButton, QPushButton:disabled {{ background: {DISABLED_BG}; color: {DISABLED_TEXT}; border: 1px solid {DISABLED_BORDER}; }}

    QCheckBox {{ spacing: 10px; padding: 3px 2px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {CONTROL_BORDER}; border-radius: {RADIUS_XS}px; background: {CONTROL_BG}; }}
    QCheckBox::indicator:hover {{ border: 1px solid {ACCENT_DARK}; background: {ACCENT_SOFT}; }}
    QCheckBox::indicator:checked {{ border: 1px solid {ACCENT}; background: {ACCENT}; }}

    QListWidget#PlatformList {{ background: transparent; border: 0; outline: 0; }}
    QListWidget#PlatformList::item {{ padding: 12px 10px; border-radius: {RADIUS_LG}px; margin: 2px 0; }}
    QListWidget#PlatformList::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT_DARK}; }}
    QListWidget#PlatformList::item:hover {{ background: {RED_SOFT}; }}

    QProgressBar#DecryptProgress, QProgressBar#TranscodeProgress {{ background: {PROGRESS_TRACK}; border: 0; border-radius: {RADIUS_SM}px; height: 12px; }}
    QProgressBar#DecryptProgress::chunk {{ background: {ACCENT}; border-radius: {RADIUS_SM}px; }}
    QProgressBar#TranscodeProgress::chunk {{ background: {RED}; border-radius: {RADIUS_SM}px; }}

    QScrollBar:vertical {{ background: {APP_BG}; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {CONTROL_BORDER}; border-radius: 5px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: {ACCENT_DARK}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    """
