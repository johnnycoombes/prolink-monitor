"""1.0.11: hi-res cover from the audio file (mutagen, ranged reads, cache)."""

import io
import os
import tempfile
import threading
import unittest

from mutagen.flac import Picture
from mutagen.id3 import APIC, ID3

from prolink import hires_art

JPEG = b"\xff\xd8\xff\xe0" + b"J" * 900_000 + b"\xff\xd9"  # bigger than the old 512 KB limit


def _ranged(blob, log):
    def read(offset, length):
        log.append((offset, length))
        return blob[offset:offset + length]
    return read


def _mp3():
    tags = ID3()
    tags.add(APIC(encoding=3, mime="image/png", type=0, desc="back", data=b"\x89PNG" + b"p" * 10))
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="front", data=JPEG))
    buf = io.BytesIO()
    tags.save(buf)
    return buf.getvalue() + b"\xff\xfb\x90\x00" * 50_000  # audio we must not read


def _flac():
    pic = Picture()
    pic.type = 3
    pic.mime = "image/jpeg"
    pic.data = JPEG
    pic.width = pic.height = 1400
    pic.depth = 24
    streaminfo = bytes(34)
    body = pic.write()
    blocks = (bytes([0]) + len(streaminfo).to_bytes(3, "big") + streaminfo
              + bytes([0x80 | 6]) + len(body).to_bytes(3, "big") + body)
    return b"fLaC" + blocks + b"\xff\xf8" + b"a" * 200_000


class ReadCoverTests(unittest.TestCase):
    def test_mp3_front_cover_without_reading_audio(self):
        blob = _mp3()
        log = []
        out = hires_art.read_cover(_ranged(blob, log), len(blob), cap_mb=4)
        self.assertEqual(out, JPEG)
        audio_start = blob.index(b"\xff\xfb\x90\x00")
        self.assertTrue(all(o + n <= audio_start for o, n in log))

    def test_flac_picture(self):
        blob = _flac()
        out = hires_art.read_cover(_ranged(blob, []), len(blob), cap_mb=4)
        self.assertEqual(out, JPEG)

    def test_size_cap(self):
        blob = _mp3()
        log = []
        # cap clamps to 1 MB minimum; the tag is < 1 MB so this one still works,
        # but a tag larger than the cap is never pulled.
        big = hires_art.gather_metadata(_ranged(blob, []), len(blob), cap=1000)
        self.assertIsNone(big)

    def test_unknown_format(self):
        self.assertIsNone(hires_art.read_cover(lambda o, n: b"\x00" * n, 100))

    def test_clamp(self):
        self.assertEqual(hires_art.clamp_cap_mb("x"), hires_art.DEFAULT_CAP_MB)
        self.assertEqual(hires_art.clamp_cap_mb(999), hires_art.MAX_CAP_MB)


class CacheTests(unittest.TestCase):
    def test_async_load_memory_and_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = hires_art.HiResArtCache(tmp)
            key = cache.key("h", "Contents/a.mp3")
            done = threading.Event()
            got = []
            self.assertTrue(cache.request(key, lambda: b"IMG", lambda d: (got.append(d), done.set())))
            self.assertTrue(done.wait(5))
            self.assertEqual(got, [b"IMG"])
            self.assertEqual(cache.get(key), b"IMG")
            self.assertTrue(os.path.isfile(os.path.join(tmp, "hires-art", key + ".jpg")))
            fresh = hires_art.HiResArtCache(tmp)
            self.assertEqual(fresh.get(key), b"IMG")
            self.assertFalse(fresh.request(key, lambda: b"X"))
            cache.close()
            fresh.close()

    def test_failed_load_is_not_retried(self):
        cache = hires_art.HiResArtCache(None)
        key = cache.key("h", "x.mp3")
        calls = []
        cache.request(key, lambda: calls.append(1))
        cache._pool.shutdown(wait=True)
        self.assertTrue(cache.known_missing(key))
        self.assertFalse(cache.request(key, lambda: b"x"))


if __name__ == "__main__":
    unittest.main()
