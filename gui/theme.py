"""Dark Qt stylesheet and color tokens for Prolink Listener desktop."""

from __future__ import annotations

COLORS = {
    "bg": "#07080a",
    "panel": "#0e1014",
    "panel_high": "#14171d",
    "panel_hover": "#1a1e27",
    "line": "#22262f",
    "line_bright": "#333945",
    "text": "#e8eaf0",
    "dim": "#7b8291",
    "dimmest": "#4a505c",
    "accent": "#22d3ee",
    "danger": "#ff3b30",
    "ok": "#4ade80",
    "warn": "#ffb020",
    "input_bg": "#0b0d11",
    "sidebar": "#0a0c10",
    "sidebar_active": "#161a22",
}

DECK_COLORS = {
    1: "#ffb020",
    2: "#22d3ee",
    3: "#f472b6",
    4: "#4ade80",
    5: "#ffb020",
    6: "#22d3ee",
}


STYLESHEET = f"""
* {{
    font-family: "Segoe UI", "DejaVu Sans", "Helvetica Neue", sans-serif;
    color: {COLORS["text"]};
}}

QWidget {{
    background-color: {COLORS["bg"]};
    color: {COLORS["text"]};
}}

QMainWindow, QDialog {{
    background-color: {COLORS["bg"]};
}}

QFrame#Sidebar {{
    background-color: {COLORS["sidebar"]};
    border-right: 1px solid {COLORS["line"]};
}}

QFrame#Content, QFrame#Page {{
    background-color: {COLORS["bg"]};
}}

QFrame#TopBar {{
    background-color: {COLORS["panel"]};
    border-bottom: 1px solid {COLORS["line"]};
}}

QFrame#StatusBar {{
    background-color: {COLORS["panel"]};
    border-top: 1px solid {COLORS["line"]};
}}

QFrame#DeckCard {{
    background-color: {COLORS["panel"]};
    border: 1px solid {COLORS["line"]};
    border-radius: 6px;
}}

QFrame#DeckCard[offair="true"] {{
    background-color: #0a0c0f;
}}

QFrame#Card {{
    background-color: {COLORS["panel"]};
    border: 1px solid {COLORS["line"]};
    border-radius: 8px;
}}

QLabel#Brand {{
    font-family: "Cascadia Mono", "DejaVu Sans Mono", "Consolas", monospace;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 2px;
    color: {COLORS["text"]};
}}

QLabel#BrandSub {{
    font-family: "Cascadia Mono", "DejaVu Sans Mono", "Consolas", monospace;
    font-size: 11px;
    color: {COLORS["dimmest"]};
}}

QLabel#PageTitle {{
    font-size: 22px;
    font-weight: 700;
}}

QLabel#PageSubtitle {{
    color: {COLORS["dim"]};
    font-size: 13px;
}}

QLabel#SectionTitle {{
    font-family: "Cascadia Mono", "DejaVu Sans Mono", "Consolas", monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    color: {COLORS["dimmest"]};
    text-transform: uppercase;
}}

QLabel#Dim {{
    color: {COLORS["dim"]};
}}

QLabel#Mono {{
    font-family: "Cascadia Mono", "DejaVu Sans Mono", "Consolas", monospace;
}}

QPushButton {{
    background-color: {COLORS["panel_high"]};
    border: 1px solid {COLORS["line"]};
    border-radius: 4px;
    padding: 8px 14px;
    font-size: 12px;
    font-weight: 600;
}}

QPushButton:hover {{
    background-color: {COLORS["panel_hover"]};
    border-color: {COLORS["line_bright"]};
}}

QPushButton:pressed {{
    background-color: {COLORS["line"]};
}}

QPushButton:disabled {{
    color: {COLORS["dimmest"]};
    background-color: {COLORS["panel"]};
}}

QPushButton#Primary {{
    background-color: {COLORS["accent"]};
    color: {COLORS["bg"]};
    border: none;
}}

QPushButton#Primary:hover {{
    background-color: #67e8f9;
}}

QPushButton#Danger {{
    background-color: rgba(255, 59, 48, 0.15);
    color: {COLORS["danger"]};
    border: 1px solid rgba(255, 59, 48, 0.35);
}}

QPushButton#NavButton {{
    text-align: left;
    padding: 12px 16px;
    border: none;
    border-radius: 6px;
    background-color: transparent;
    font-family: "Cascadia Mono", "DejaVu Sans Mono", "Consolas", monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    color: {COLORS["dim"]};
}}

QPushButton#NavButton:hover {{
    background-color: {COLORS["panel_hover"]};
    color: {COLORS["text"]};
}}

QPushButton#NavButton[active="true"] {{
    background-color: {COLORS["sidebar_active"]};
    color: {COLORS["text"]};
    border-left: 3px solid {COLORS["accent"]};
}}

QPushButton#Chip {{
    padding: 5px 10px;
    border-radius: 3px;
    font-family: "Cascadia Mono", "DejaVu Sans Mono", "Consolas", monospace;
    font-size: 11px;
    font-weight: 600;
}}

QPushButton#Chip[active="true"] {{
    background-color: {COLORS["text"]};
    color: {COLORS["bg"]};
    border: none;
}}

QToolButton#Chip {{
    background-color: {COLORS["panel_high"]};
    border: 1px solid {COLORS["line"]};
    border-radius: 3px;
    padding: 5px 10px;
    font-family: "Cascadia Mono", "DejaVu Sans Mono", "Consolas", monospace;
    font-size: 11px;
    font-weight: 600;
}}

QToolButton#Chip:hover {{
    background-color: {COLORS["panel_hover"]};
    border-color: {COLORS["line_bright"]};
}}

QToolButton#Chip::menu-indicator {{
    image: none;
    width: 0;
}}

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {{
    background-color: {COLORS["input_bg"]};
    border: 1px solid {COLORS["line"]};
    border-radius: 4px;
    padding: 8px 10px;
    selection-background-color: {COLORS["accent"]};
    selection-color: {COLORS["bg"]};
}}

QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border-color: {COLORS["accent"]};
}}

QComboBox::drop-down {{
    border: none;
    width: 24px;
}}

QComboBox QAbstractItemView {{
    background-color: {COLORS["panel_high"]};
    border: 1px solid {COLORS["line"]};
    selection-background-color: {COLORS["accent"]};
    selection-color: {COLORS["bg"]};
}}

QCheckBox {{
    spacing: 8px;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {COLORS["line_bright"]};
    border-radius: 3px;
    background: {COLORS["input_bg"]};
}}

QCheckBox::indicator:checked {{
    background: {COLORS["accent"]};
    border-color: {COLORS["accent"]};
}}

QScrollArea {{
    border: none;
    background: transparent;
}}

QScrollBar:vertical {{
    background: {COLORS["bg"]};
    width: 10px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {COLORS["line_bright"]};
    border-radius: 4px;
    min-height: 30px;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QTableWidget {{
    background-color: {COLORS["panel"]};
    border: 1px solid {COLORS["line"]};
    border-radius: 6px;
    gridline-color: {COLORS["line"]};
    selection-background-color: {COLORS["panel_hover"]};
}}

QHeaderView::section {{
    background-color: {COLORS["panel_high"]};
    color: {COLORS["dim"]};
    border: none;
    border-bottom: 1px solid {COLORS["line"]};
    border-right: 1px solid {COLORS["line"]};
    padding: 8px;
    font-family: "Cascadia Mono", "DejaVu Sans Mono", "Consolas", monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}}

QMessageBox {{
    background-color: {COLORS["panel"]};
}}

QToolTip {{
    background-color: {COLORS["panel_high"]};
    color: {COLORS["text"]};
    border: 1px solid {COLORS["line"]};
    padding: 4px 8px;
}}
"""
