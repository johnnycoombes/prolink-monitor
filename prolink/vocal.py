"""Vocal regions from rekordbox PWVC thresholds and the PWV6/PWV7 bands.

Parsing and the mid-dominant test follow chrisle/alphatheta-connect (MIT),
adapted here. PWVC stores three thresholds. A column is vocal when the mid
band is above its threshold and the low and high bands stay under theirs.
Our ANLZ files store each column as mid, high, low (see ``decode_3band``).
"""

from __future__ import annotations

from dataclasses import dataclass

VOCAL_STRIP_H = 8


@dataclass
class VocalThresholds:
    low: int
    mid: int
    high: int


def thresholds_from_body(body: bytes) -> VocalThresholds | None:
    """PWVC body: unknown u16, then low, mid, high as u16 big-endian."""
    if len(body) < 8:
        return None
    import struct
    _unknown, low, mid, high = struct.unpack_from(">HHHH", body, 0)
    return VocalThresholds(int(low), int(mid), int(high))


def vocal_regions(
    band3: bytes,
    thresholds: VocalThresholds | None,
    duration_ms: float,
) -> list[dict]:
    """Contiguous vocal spans as ``{t, end}`` milliseconds across the track."""
    if not band3 or thresholds is None or duration_ms <= 0 or len(band3) < 3:
        return []
    n = len(band3) // 3
    if n <= 0:
        return []
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for i in range(n):
        mid = band3[i * 3]
        high = band3[i * 3 + 1]
        low = band3[i * 3 + 2]
        vocal = (
            mid > thresholds.mid
            and low < thresholds.low
            and high < thresholds.high
        )
        if vocal and start is None:
            start = i
        elif not vocal and start is not None:
            spans.append((start, i))
            start = None
    if start is not None:
        spans.append((start, n))
    out = []
    for a, b in spans:
        if b <= a:
            continue
        t0 = (a / n) * duration_ms
        t1 = (b / n) * duration_ms
        out.append({"t": round(t0, 1), "end": round(t1, 1)})
    return out
