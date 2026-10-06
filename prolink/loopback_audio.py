"""Optional system-audio bars for the panel visualiser.

The default visualiser never needs this. When the desktop setting is on and
the MIT ``soundcard`` package is installed, a background thread reads the
default output (WASAPI loopback on Windows) and publishes bar levels.

Pro DJ Link itself still carries no audio. If loopback is off, missing, or
quiet, the overlay keeps using the waveform.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Sequence


def soundcard_available() -> bool:
    try:
        import soundcard  # noqa: F401
    except Exception:
        return False
    return True


def bars_from_samples(samples: Sequence[float], n_bars: int = 48, rate: int = 44100) -> list[float]:
    """Log-spaced magnitude bars from one mono buffer. Pure Python, no FFT lib."""
    n = len(samples)
    if n < 8 or n_bars < 1:
        return [0.0] * max(1, n_bars)
    # Hann window, then Goertzel at the centre of each log band.
    windowed = []
    for i, sample in enumerate(samples):
        w = 0.5 - 0.5 * math.cos((2 * math.pi * i) / (n - 1))
        windowed.append(float(sample) * w)
    nyquist = rate / 2
    low_hz = 40.0
    high_hz = min(16000.0, nyquist * 0.9)
    bars: list[float] = []
    for i in range(n_bars):
        u0 = i / n_bars
        u1 = (i + 1) / n_bars
        f0 = low_hz * (high_hz / low_hz) ** u0
        f1 = low_hz * (high_hz / low_hz) ** u1
        freq = (f0 + f1) / 2
        omega = (2 * math.pi * freq) / rate
        coeff = 2 * math.cos(omega)
        s0 = 0.0
        s1 = 0.0
        s2 = 0.0
        for x in windowed:
            s0 = x + coeff * s1 - s2
            s2 = s1
            s1 = s0
        power = s1 * s1 + s2 * s2 - coeff * s1 * s2
        if power < 0:
            power = 0.0
        bars.append(power)
    peak = max(bars) or 1.0
    # Compress so a single tone does not pin every neighbour to the ceiling.
    return [min(1.0, math.sqrt(b / peak)) for b in bars]


class LoopbackCapturer:
    """Background WASAPI/loopback reader. Safe to start when soundcard is absent."""

    def __init__(self, n_bars: int = 48) -> None:
        self.n_bars = n_bars
        self.levels: list[float] = []
        self.updated = 0.0
        self.error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if self.running:
            return True
        if not soundcard_available():
            self.error = "soundcard is not installed"
            return False
        self._stop.clear()
        self.error = ""
        self._thread = threading.Thread(target=self._run, name="spectrum-loopback", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def snapshot(self, *, max_age: float = 0.5) -> list[float] | None:
        with self._lock:
            if not self.levels:
                return None
            if time.time() - self.updated > max_age:
                return None
            return list(self.levels)

    def _run(self) -> None:
        try:
            import soundcard as sc

            speaker = sc.default_speaker()
            mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        except Exception as exc:
            self.error = str(exc)
            return
        rate = 44100
        frames = 2048
        try:
            with mic.recorder(samplerate=rate, channels=1) as rec:
                while not self._stop.is_set():
                    data = rec.record(numframes=frames)
                    try:
                        samples = [float(row[0] if hasattr(row, "__getitem__") else row) for row in data]
                    except Exception:
                        samples = [float(x) for x in data]
                    levels = bars_from_samples(samples, self.n_bars, rate)
                    with self._lock:
                        self.levels = levels
                        self.updated = time.time()
        except Exception as exc:
            self.error = str(exc)
