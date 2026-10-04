"""Link-timing table and listen-only capture controls for the Health page."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui.theme import COLORS


def _fmt_ms(ms) -> str:
    if ms is None:
        return "—"
    value = float(ms)
    if value >= 1000:
        return f"{value / 1000:.2f} s"
    if value >= 100:
        return f"{value:.0f} ms"
    return f"{value:.1f} ms"


class LinkHealthPanel(QWidget):
    """Per-deck status timing, plus a button that records received packets."""

    capture_requested = Signal(int)

    def __init__(self, i18n, parent=None):
        super().__init__(parent)
        self._i18n = i18n
        self._notice = ""
        self._sized_rows = -1

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self._title = QLabel(i18n.t("health_timing"))
        self._title.setObjectName("SectionTitle")
        root.addWidget(self._title)

        self._hint = QLabel(i18n.t("health_timing_hint"))
        self._hint.setWordWrap(True)
        self._hint.setObjectName("Dim")
        self._hint.setStyleSheet(f"color:{COLORS['dim']}; font-size:12px;")
        root.addWidget(self._hint)

        self.table = QTableWidget(0, 7)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 7):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self._apply_headers()
        root.addWidget(self.table)

        self._empty = QLabel(i18n.t("health_no_timing"))
        self._empty.setObjectName("Dim")
        root.addWidget(self._empty)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self._duration_lbl = QLabel(i18n.t("health_capture_duration"))
        self._duration_lbl.setObjectName("Dim")
        self.seconds = QSpinBox()
        self.seconds.setRange(5, 60)
        self.seconds.setValue(10)
        self.seconds.setSuffix(" s")
        self.seconds.setFixedWidth(88)
        self.capture_btn = QPushButton(i18n.t("health_capture"))
        self.capture_btn.setCursor(Qt.PointingHandCursor)
        self.capture_btn.clicked.connect(self._emit_capture)
        self._status = QLabel(i18n.t("health_capture_idle"))
        self._status.setWordWrap(True)
        self._status.setObjectName("Dim")
        self._status.setStyleSheet(f"color:{COLORS['dim']}; font-size:12px;")
        controls.addWidget(self._duration_lbl)
        controls.addWidget(self.seconds)
        controls.addWidget(self.capture_btn)
        controls.addWidget(self._status, 1)
        root.addLayout(controls)

    def retranslate(self) -> None:
        self._title.setText(self._i18n.t("health_timing"))
        self._hint.setText(self._i18n.t("health_timing_hint"))
        self._empty.setText(self._i18n.t("health_no_timing"))
        self._duration_lbl.setText(self._i18n.t("health_capture_duration"))
        self.capture_btn.setText(self._i18n.t("health_capture"))
        self._apply_headers()

    def note(self, text: str) -> None:
        """Local message, kept until the next capture status replaces it."""
        self._notice = text or ""
        if self._notice:
            self._status.setText(self._notice)
            self._status.setStyleSheet(f"color:{COLORS['warn']}; font-size:12px;")

    def update_state(self, state: dict) -> None:
        rows = list(state.get("link_timing") or [])
        self._empty.setVisible(not rows)
        self.table.setVisible(bool(rows))
        self.table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            name = str(entry.get("name") or "").strip()
            deck = str(entry.get("device") if entry.get("device") is not None else "")
            label = f"{deck}  {name}".strip() if name else deck
            holes = entry.get("seq_holes")
            counter = entry.get("seq_counter") or "unused"
            hole_text = "—" if holes is None or counter != "live" else f"{int(holes):,}"
            vals = [
                label,
                _fmt_ms(entry.get("age_ms")),
                _fmt_ms(entry.get("interval_ms")),
                _fmt_ms(entry.get("jitter_ms")),
                _fmt_ms(entry.get("max_gap_ms")),
                f"{int(entry.get('late') or 0):,}",
                hole_text,
            ]
            age = entry.get("age_ms")
            late = int(entry.get("late") or 0)
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                color = _cell_color(col, age, late, holes, counter)
                if color:
                    item.setForeground(QColor(color))
                self.table.setItem(row, col, item)
        self._fit_table(len(rows))

        cap = state.get("capture")
        if not isinstance(cap, dict):
            return
        active = bool(cap.get("active"))
        saving = bool(cap.get("saving"))
        self.capture_btn.setEnabled(not active and not saving)
        self.seconds.setEnabled(not active and not saving)
        if active or saving:
            self._notice = ""
        if active:
            left = cap.get("remaining_s")
            left_s = f"{float(left):.0f}" if left is not None else "…"
            self._status.setText(self._i18n.t("health_capture_running").format(
                left=left_s, packets=int(cap.get("packets") or 0)))
            self._status.setStyleSheet(f"color:{COLORS['accent']}; font-size:12px;")
        elif saving:
            self._status.setText(self._i18n.t("health_capture_saving").format(
                packets=int(cap.get("packets") or 0)))
            self._status.setStyleSheet(f"color:{COLORS['accent']}; font-size:12px;")
        elif cap.get("error"):
            self._status.setText(self._i18n.t("health_capture_failed").format(
                error=cap.get("error")))
            self._status.setStyleSheet(f"color:{COLORS['danger']}; font-size:12px;")
        elif cap.get("last_path"):
            key = "health_capture_truncated" if cap.get("last_truncated") else "health_capture_saved"
            self._status.setText(self._i18n.t(key).format(
                packets=int(cap.get("last_packets") or 0),
                path=cap.get("last_path") or ""))
            self._status.setStyleSheet(f"color:{COLORS['ok']}; font-size:12px;")
        elif self._notice:
            self._status.setText(self._notice)
            self._status.setStyleSheet(f"color:{COLORS['warn']}; font-size:12px;")
        else:
            self._status.setText(self._i18n.t("health_capture_idle"))
            self._status.setStyleSheet(f"color:{COLORS['dim']}; font-size:12px;")

    def _fit_table(self, count: int) -> None:
        """Keep the table to its rows. Skip the reflow once the size has stuck."""
        if count <= 0:
            self._sized_rows = 0
            return
        if count == self._sized_rows and self.table.height() > 48:
            return
        self.table.resizeRowsToContents()
        shown = min(count, 6)
        if count > shown:
            self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        else:
            self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        header_h = max(self.table.horizontalHeader().sizeHint().height(), 28)
        rows_h = sum(max(self.table.rowHeight(i), 24) for i in range(shown))
        extra = 8 if count > shown else 2
        height = header_h + rows_h + extra
        if height != self.table.height():
            self.table.setFixedHeight(height)
        self._sized_rows = count

    def _emit_capture(self) -> None:
        self.capture_requested.emit(int(self.seconds.value()))

    def _apply_headers(self) -> None:
        self.table.setHorizontalHeaderLabels([
            self._i18n.t("health_col_deck"),
            self._i18n.t("health_col_last"),
            self._i18n.t("health_col_interval"),
            self._i18n.t("health_col_jitter"),
            self._i18n.t("health_col_gap"),
            self._i18n.t("health_col_late"),
            self._i18n.t("health_col_holes"),
        ])


def _cell_color(col: int, age, late: int, holes, counter: str) -> str:
    """Warn when a deck looks stalled, late, or missing sequence numbers."""
    if col == 1 and age is not None:
        if float(age) >= 1000:
            return COLORS["danger"]
        if float(age) >= 400:
            return COLORS["warn"]
    if col == 5 and late > 0:
        return COLORS["warn"]
    if col == 6 and counter == "live" and holes:
        return COLORS["warn"]
    return ""
