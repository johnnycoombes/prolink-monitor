"""Waveform Current Position: status-packet parsing and Auto fallback."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from prolink import proto


def _status(*, name: bytes = b"CDJ-3000", color: int | None = None,
            position: int | None = None, marker: bool = True,
            length: int = 0xE0) -> bytes:
    """Minimal CDJ status packet, optionally with settings block 1."""
    pkt = bytearray(length)
    pkt[0:10] = proto.MAGIC
    pkt[10] = proto.TYPE_CDJ_STATUS
    pkt[0x0B:0x0B + len(name)] = name[:20]
    pkt[0x21] = 1
    if marker and length >= proto.SETTINGS_BLOCK_OFFSET + len(proto.SETTINGS_BLOCK_MARKER):
        off = proto.SETTINGS_BLOCK_OFFSET
        pkt[off:off + 4] = proto.SETTINGS_BLOCK_MARKER
        if length >= off + 8:
            pkt[off + 4:off + 8] = bytes.fromhex("00000001")
    if color is not None and length > proto.WAVEFORM_COLOR_OFFSET:
        pkt[proto.WAVEFORM_COLOR_OFFSET] = color
    if position is not None and length > proto.WAVEFORM_POSITION_OFFSET:
        pkt[proto.WAVEFORM_POSITION_OFFSET] = position
    return bytes(pkt)


class WaveformSettingsParseTests(unittest.TestCase):
    def test_centre_and_rgb(self):
        parsed = proto.parse(_status(color=0x03, position=0x01, name=b"CDJ-3000"))
        self.assertIsInstance(parsed, proto.Status)
        assert isinstance(parsed, proto.Status)
        self.assertEqual(parsed.waveform_position, "centre")
        self.assertEqual(parsed.waveform_color, "rgb")
        self.assertEqual(parsed.name, "CDJ-3000")

    def test_left_and_three_band(self):
        parsed = proto.parse(_status(color=0x04, position=0x02, name=b"XDJ-AZ"))
        assert isinstance(parsed, proto.Status)
        self.assertEqual(parsed.waveform_position, "left")
        self.assertEqual(parsed.waveform_color, "3band")

    def test_blue(self):
        parsed = proto.parse(_status(color=0x01, position=0x01))
        assert isinstance(parsed, proto.Status)
        self.assertEqual(parsed.waveform_color, "blue")

    def test_unknown_bytes_are_none(self):
        parsed = proto.parse(_status(color=0x02, position=0x09))
        assert isinstance(parsed, proto.Status)
        self.assertIsNone(parsed.waveform_color)
        self.assertIsNone(parsed.waveform_position)

    def test_missing_marker_ignored(self):
        # Long enough, and 0xDD even says "left", but the marker is absent.
        parsed = proto.parse(_status(position=0x02, marker=False, length=0x124))
        assert isinstance(parsed, proto.Status)
        self.assertIsNone(parsed.waveform_position)
        self.assertIsNone(parsed.waveform_color)

    def test_short_packet(self):
        parsed = proto.parse(_status(position=0x02, length=0xA7))
        assert isinstance(parsed, proto.Status)
        self.assertIsNone(parsed.waveform_position)
        self.assertIsNone(parsed.waveform_color)

    def test_marker_without_position_byte(self):
        # Colour byte is in range; position byte is not.
        parsed = proto.parse(_status(color=0x03, position=0x02, length=0xDB))
        assert isinstance(parsed, proto.Status)
        self.assertEqual(parsed.waveform_color, "rgb")
        self.assertIsNone(parsed.waveform_position)


class PlayheadFallbackTests(unittest.TestCase):
    def test_auto_uses_reported_value(self):
        self.assertEqual(
            proto.resolve_playhead_position("auto", "left", "CDJ-3000"), "left")
        self.assertEqual(
            proto.resolve_playhead_position("auto", "centre", "XDJ-AZ"), "centre")
        self.assertEqual(
            proto.resolve_playhead_position("auto", "center", "XDJ-AZ"), "centre")

    def test_auto_model_fallback_when_unreported(self):
        self.assertEqual(proto.resolve_playhead_position("auto", None, "XDJ-AZ"), "left")
        self.assertEqual(proto.resolve_playhead_position("auto", None, "OPUS-QUAD"), "left")
        self.assertEqual(proto.resolve_playhead_position("auto", None, "Opus Quad"), "left")
        self.assertEqual(proto.resolve_playhead_position("auto", None, "CDJ-3000"), "centre")
        self.assertEqual(
            proto.resolve_playhead_position("auto", None, "CDJ-2000NXS2"), "centre")
        self.assertEqual(proto.resolve_playhead_position("auto", None, "XDJ-XZ"), "centre")
        self.assertEqual(proto.resolve_playhead_position("auto", None, ""), "centre")

    def test_manual_override_wins(self):
        self.assertEqual(
            proto.resolve_playhead_position("left", "centre", "CDJ-3000"), "left")
        self.assertEqual(
            proto.resolve_playhead_position("centre", "left", "XDJ-AZ"), "centre")
        self.assertEqual(
            proto.resolve_playhead_position("center", "left", "OPUS-QUAD"), "centre")

    def test_unknown_mode_is_auto(self):
        self.assertEqual(proto.normalize_playhead_mode("nope"), "auto")
        self.assertEqual(
            proto.resolve_playhead_position("nope", None, "XDJ-AZ"), "left")

    def test_fraction(self):
        self.assertEqual(proto.playhead_fraction("centre"), proto.PLAYHEAD_CENTRE_FRACTION)
        self.assertEqual(proto.playhead_fraction("left"), 0.25)
        self.assertEqual(proto.playhead_fraction(None), 0.5)


class PlayheadSettingsTests(unittest.TestCase):
    def test_sanitize(self):
        from gui.settings import _sanitize
        self.assertEqual(_sanitize({})["playhead_position"], "auto")
        self.assertEqual(_sanitize({"playhead_position": "left"})["playhead_position"], "left")
        self.assertEqual(
            _sanitize({"playhead_position": "center"})["playhead_position"], "centre")
        self.assertEqual(_sanitize({"playhead_position": "bogus"})["playhead_position"], "auto")


class ProbeWaveformFieldsTests(unittest.TestCase):
    def test_named_offsets(self):
        import probe
        color = probe.field(0x0A, 0xDA)
        pos = probe.field(0x0A, 0xDD)
        self.assertIsNotNone(color)
        self.assertIsNotNone(pos)
        assert color is not None and pos is not None
        self.assertEqual(color[0], 0xDA)
        self.assertEqual(pos[0], 0xDD)
        self.assertEqual(pos[1], 1)
        self.assertIn("centre", pos[2])
        self.assertEqual(probe.format_value(pos[2], 2), "left")
        self.assertEqual(probe.format_value(color[2], 3), "RGB")


class WaveformViewAnchorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def test_left_quarter_keeps_the_needle_on_the_playhead(self):
        from gui.widgets import WaveformView
        view = WaveformView()
        view.resize(400, 200)
        view.set_playhead_position("left")
        self.assertEqual(view._playhead_frac, 0.25)
        self.assertEqual(view._window_start(1000, 400), 900)
        view._pos_ms = 5000.0
        view._detail = {"n": 10, "cps": 150.0}
        ms = view._ms_at(int(round(view.width() * 0.25)), 80)
        self.assertAlmostEqual(ms, 5000.0, delta=1.0)

        view.set_playhead_position("centre")
        self.assertEqual(view._playhead_frac, 0.5)
        self.assertEqual(view._window_start(1000, 400), 800)
        ms = view._ms_at(view.width() // 2, 80)
        self.assertAlmostEqual(ms, 5000.0, delta=1.0)

    def test_card_follows_reported_value_and_override(self):
        from gui.i18n import I18n
        from gui.widgets import DeckCard
        card = DeckCard(1, I18n())
        quiet = {
            "number": 1, "track_id": 0, "bpm": 0, "pitch": 0,
            "state": "no_track", "playing": False, "bar": 0,
        }
        card.set_playhead_mode("auto")
        card.update_deck({**quiet, "name": "XDJ-AZ", "waveform_position": None}, 0)
        self.assertEqual(card.wave._playhead_frac, 0.25)
        card.set_playhead_mode("centre")
        self.assertEqual(card.wave._playhead_frac, 0.5)
        card.update_deck({**quiet, "name": "CDJ-3000", "waveform_position": "left"}, 0)
        self.assertEqual(card.wave._playhead_frac, 0.5)
        card.set_playhead_mode("auto")
        self.assertEqual(card.wave._playhead_frac, 0.25)


class PaintStateWaveformTests(unittest.TestCase):
    def test_paint_state_carries_reported_position(self):
        from app import Monitor

        parsed = proto.parse(_status(name=b"XDJ-AZ", color=0x03, position=0x02))
        assert isinstance(parsed, proto.Status)
        mon = Monitor.__new__(Monitor)
        mon.engine = MagicMock()
        deck = MagicMock()
        deck.number = 1
        deck.is_playing = False
        deck.position_ms = 0.0
        deck.track_length_ms = 0
        deck.position_source = "beat_grid"
        deck.status = parsed
        mon.engine.active_decks.return_value = [deck]

        out = Monitor.paint_state(mon)
        snap = out["decks"][0]
        self.assertEqual(snap["waveform_position"], "left")
        self.assertEqual(snap["waveform_color"], "rgb")


if __name__ == "__main__":
    unittest.main()
