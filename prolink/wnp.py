"""Optional What's Now Playing remote-input client.

Checked against what's-now-playing main (October 2026):

* ``POST /v1/remoteinput`` with a JSON body (GET with query params also works).
* Default web server port is 8899.
* When a shared secret is set (``remote/remote_key``), send it in the
  ``X-WNP-Client-Auth`` header. A ``secret`` body or query field is the legacy
  fallback; this client uses the header so the secret stays out of access logs.
* Stored fields include ``title``, ``artist``, ``album``, ``bpm``, ``duration``
  (seconds), ``genre``, ``date``, ``key``, ``label``, ``bitrate``, ``comments``.
  ``year`` is accepted and copied onto ``date``.
* Binary artwork (``coverimageraw`` and the other blob fields) is stripped.
  ``coverurl`` is fetched only for an http(s) URL that resolves to a public
  address; loopback and private addresses are rejected. Player artwork lives
  on the local library, so this client does not send a cover.

The listener never writes to the player or to ``master.db``. A down WNP
process must not block the caller: ``observe`` only queues work.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8899
TIMEOUT_SECONDS = 1.5
REMOTE_PATH = "/v1/remoteinput"
STATUS_PATH = "/v1/status"
AUTH_HEADER = "X-WNP-Client-Auth"
USER_AGENT = "prolink-monitor"


@dataclass(frozen=True)
class WnpSettings:
    enabled: bool = False
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    secret: str = ""


@dataclass(frozen=True)
class SendResult:
    ok: bool
    status: str
    detail: str
    track: str = ""

    def as_health(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "track": self.track,
        }


def normalize_target(host: object, port: object) -> tuple[str, int]:
    """Host and port for the WNP web server. Blank host becomes localhost."""
    try:
        port_n = int(port)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        port_n = DEFAULT_PORT
    if port_n < 1 or port_n > 65535:
        port_n = DEFAULT_PORT

    raw = str(host or "").strip()
    if "://" in raw:
        parsed = urllib.parse.urlparse(raw)
        if parsed.hostname:
            raw = parsed.hostname
        if parsed.port:
            port_n = parsed.port
    if raw.count(":") == 1 and not raw.startswith("["):
        maybe_host, maybe_port = raw.rsplit(":", 1)
        if maybe_port.isdigit() and maybe_host.strip():
            raw = maybe_host.strip()
            port_n = int(maybe_port)
    raw = raw.split("/")[0].strip().strip("[]")
    if not raw or any(ch.isspace() for ch in raw):
        raw = DEFAULT_HOST
    if port_n < 1 or port_n > 65535:
        port_n = DEFAULT_PORT
    return raw, port_n


def settings_from(data: dict[str, Any] | None) -> WnpSettings:
    src = data or {}
    host, port = normalize_target(src.get("wnp_host", DEFAULT_HOST), src.get("wnp_port", DEFAULT_PORT))
    return WnpSettings(
        enabled=bool(src.get("wnp_enabled", False)),
        host=host,
        port=port,
        secret=str(src.get("wnp_secret") or "").replace("\r", "").replace("\n", "").strip(),
    )


def audience_token(deck: dict[str, Any] | None) -> tuple[int, int] | None:
    """Identity of the audience track. Same deck and track id means one send."""
    if not deck:
        return None
    try:
        number = int(deck.get("number") or 0)
        track_id = int(deck.get("track_id") or 0)
    except (TypeError, ValueError):
        return None
    if number <= 0 or track_id <= 0:
        return None
    return (number, track_id)


def _text(value: object) -> str:
    return str(value or "").strip()


def _positive_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _bpm_text(deck: dict[str, Any], meta: dict[str, Any] | None) -> str:
    """On-screen BPM, matching the overlay (effective tempo, then track tempo)."""
    for source in (deck.get("bpm"), (meta or {}).get("track_bpm")):
        number = _positive_float(source)
        if number is None:
            continue
        rounded = round(number, 2)
        if abs(rounded - round(rounded)) < 0.001:
            return str(int(round(rounded)))
        return f"{rounded:.2f}"
    return ""


def _duration_seconds(deck: dict[str, Any], meta: dict[str, Any] | None) -> int | None:
    """Track length in seconds, ignoring an inflated absolute-position duration."""
    deck_ms = _positive_float(deck.get("duration_ms")) or 0.0
    meta_ms = _positive_float((meta or {}).get("duration_ms")) or 0.0
    limit = 12 * 3600 * 1000

    def ok(ms: float) -> bool:
        return 1000 < ms < limit

    chosen = 0.0
    if ok(deck_ms) and ok(meta_ms):
        if deck_ms > meta_ms * 2:
            chosen = meta_ms
        elif meta_ms > deck_ms * 2:
            chosen = deck_ms
        else:
            chosen = max(deck_ms, meta_ms)
    elif ok(meta_ms):
        chosen = meta_ms
    elif ok(deck_ms):
        chosen = deck_ms
    if chosen <= 0:
        return None
    seconds = int(round(chosen / 1000.0))
    return seconds if seconds > 0 else None


def _usable_meta(deck: dict[str, Any], meta: dict[str, Any] | None) -> dict[str, Any] | None:
    if not meta:
        return None
    deck_key = _text(deck.get("track_key"))
    meta_key = _text(meta.get("track_key"))
    if deck_key and meta_key and deck_key != meta_key:
        return None
    return meta


def build_payload(deck: dict[str, Any] | None, meta: dict[str, Any] | None = None) -> dict[str, str] | None:
    """JSON body for ``/v1/remoteinput``. None until a title is known.

    Artwork is intentionally omitted. WNP removes binary cover fields and only
    downloads ``coverurl`` when that URL is on the public internet.
    """
    if not deck:
        return None
    info = _usable_meta(deck, meta)
    title = _text(deck.get("title")) or _text((info or {}).get("title"))
    if not title:
        return None
    artist = _text(deck.get("artist")) or _text((info or {}).get("artist"))
    body: dict[str, str] = {
        "title": title,
        "artist": artist,
        "source_agent_name": "prolink-monitor",
    }
    try:
        from prolink import __version__ as version
    except Exception:
        version = "1.0.0"
    body["source_agent_version"] = str(version)

    album = _text((info or {}).get("album"))
    if album:
        body["album"] = album
    genre = _text((info or {}).get("genre"))
    if genre:
        body["genre"] = genre
    label = _text((info or {}).get("label"))
    if label:
        body["label"] = label
    key = _text(deck.get("key")) or _text((info or {}).get("key"))
    if key:
        body["key"] = key
    year = (info or {}).get("year") or (info or {}).get("date")
    year_text = _text(year)
    if year_text and year_text != "0":
        body["date"] = year_text
    comment = _text((info or {}).get("comment")) or _text((info or {}).get("comments"))
    if comment:
        body["comments"] = comment
    bpm = _bpm_text(deck, info)
    if bpm:
        body["bpm"] = bpm
    duration = _duration_seconds(deck, info)
    if duration:
        body["duration"] = str(duration)
    bitrate = _positive_float((info or {}).get("bitrate"))
    if bitrate:
        body["bitrate"] = str(int(bitrate))
    number = deck.get("number")
    if number:
        body["deck"] = str(int(number))
    return body


def track_label(payload: dict[str, str] | None) -> str:
    if not payload:
        return ""
    artist = payload.get("artist") or ""
    title = payload.get("title") or ""
    if artist and title:
        return f"{artist} — {title}"
    return title or artist


def _endpoint(cfg: WnpSettings, path: str) -> str:
    return f"http://{cfg.host}:{cfg.port}{path}"


def _explain_transport(reason: object, cfg: WnpSettings) -> str:
    where = f"{cfg.host}:{cfg.port}"
    text = str(reason or "")
    errno = getattr(reason, "errno", None)
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in text.lower():
        return f"What's Now Playing at {where} did not answer in time."
    if isinstance(reason, ConnectionRefusedError) or errno in (111, 10061) or "refused" in text.lower():
        return f"What's Now Playing is not running at {where}."
    if isinstance(reason, socket.gaierror):
        return f"Could not find What's Now Playing host {cfg.host}."
    if text:
        return f"Could not reach What's Now Playing at {where}."
    return f"Could not reach What's Now Playing at {where}."


def _error_text(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text[:180]
    if isinstance(parsed, dict) and parsed.get("error"):
        return str(parsed["error"])[:180]
    return text[:180]


def _request(cfg: WnpSettings, path: str, payload: dict[str, str] | None, timeout: float) -> tuple[int, bytes]:
    data = None
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if cfg.secret:
        headers[AUTH_HEADER] = cfg.secret
    req = urllib.request.Request(_endpoint(cfg, path), data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read(4096)
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(4096)
        except Exception:
            raw = b""
        return int(exc.code), raw


def post_metadata(cfg: WnpSettings, payload: dict[str, str], timeout: float = TIMEOUT_SECONDS) -> SendResult:
    """Blocking POST. Safe to call off the listener thread. Never raises."""
    label = track_label(payload)
    where = f"{cfg.host}:{cfg.port}"
    try:
        code, raw = _request(cfg, REMOTE_PATH, payload, timeout)
    except urllib.error.URLError as exc:
        return SendResult(False, "error", _explain_transport(exc.reason, cfg), label)
    except (TimeoutError, socket.timeout, OSError) as exc:
        return SendResult(False, "error", _explain_transport(exc, cfg), label)
    except Exception:
        return SendResult(False, "error", f"Could not reach What's Now Playing at {where}.", label)
    if code == 200:
        return SendResult(True, "ok", f"Sent {label} to What's Now Playing at {where}.", label)
    if code == 403:
        return SendResult(False, "error", "What's Now Playing rejected the secret.", label)
    extra = _error_text(raw)
    detail = f"What's Now Playing returned HTTP {code}."
    if extra:
        detail = f"{detail} {extra}"
    return SendResult(False, "error", detail, label)


def get_status(cfg: WnpSettings, timeout: float = TIMEOUT_SECONDS) -> SendResult:
    """Reachability check. ``/v1/status`` does not use the shared secret."""
    where = f"{cfg.host}:{cfg.port}"
    try:
        code, raw = _request(cfg, STATUS_PATH, None, timeout)
    except urllib.error.URLError as exc:
        return SendResult(False, "error", _explain_transport(exc.reason, cfg))
    except (TimeoutError, socket.timeout, OSError) as exc:
        return SendResult(False, "error", _explain_transport(exc, cfg))
    except Exception:
        return SendResult(False, "error", f"Could not reach What's Now Playing at {where}.")
    if code != 200:
        extra = _error_text(raw)
        detail = f"What's Now Playing at {where} returned HTTP {code}."
        if extra:
            detail = f"{detail} {extra}"
        return SendResult(False, "error", detail)
    version = ""
    try:
        parsed = json.loads(raw.decode("utf-8", "replace") or "{}")
        if isinstance(parsed, dict):
            version = str(parsed.get("version") or "").strip()
    except json.JSONDecodeError:
        version = ""
    name = f"What's Now Playing {version}" if version else "What's Now Playing"
    return SendResult(
        True,
        "ok",
        (
            f"Reached {name} at {where}. No audience track is playing, "
            "so nothing was sent and the secret was not checked."
        ),
    )


def test_connection(
    data: dict[str, Any] | None,
    deck: dict[str, Any] | None,
    meta: dict[str, Any] | None = None,
    timeout: float = TIMEOUT_SECONDS,
) -> SendResult:
    """Send the current audience track, or check ``/v1/status`` when there is none.

    Uses the form values even when the feature is still switched off, so the
    Test button can be used before Save.
    """
    cfg = settings_from(data)
    cfg = WnpSettings(enabled=True, host=cfg.host, port=cfg.port, secret=cfg.secret)
    payload = build_payload(deck, meta)
    if payload:
        return post_metadata(cfg, payload, timeout=timeout)
    return get_status(cfg, timeout=timeout)


class WnpPublisher:
    """Sends each audience track once, on a background thread."""

    def __init__(self, timeout: float = TIMEOUT_SECONDS) -> None:
        self.timeout = timeout
        self._lock = threading.Lock()
        self._cfg = WnpSettings()
        self._sent: tuple[int, int] | None = None
        self._inflight: tuple[int, int] | None = None
        self._latest: tuple | None = None
        self._busy = False
        self._waiting = False
        self._last: dict[str, Any] | None = None
        self._gen = 0
        self._disk_check = 0.0

    def configure(self, data: dict[str, Any] | None) -> None:
        self._apply(settings_from(data))

    def refresh_from_disk(self) -> None:
        """Pick up ``settings.json``. At most once a second. Never raises."""
        now = time.monotonic()
        with self._lock:
            if now - self._disk_check < 1.0:
                return
            self._disk_check = now
        try:
            from gui.settings import load_settings

            loaded = load_settings()
        except Exception:
            return
        self._apply(settings_from(loaded))

    def _apply(self, cfg: WnpSettings) -> None:
        with self._lock:
            prev = self._cfg
            endpoint_changed = (cfg.host, cfg.port, cfg.secret) != (prev.host, prev.port, prev.secret)
            turned_on = cfg.enabled and not prev.enabled
            if endpoint_changed or turned_on:
                self._sent = None
                self._gen += 1
            self._cfg = cfg
            if not cfg.enabled:
                self._waiting = False

    def observe(self, deck: dict[str, Any] | None, meta: dict[str, Any] | None = None) -> None:
        """Queue one send when the audience track changes. Returns immediately."""
        token = audience_token(deck)
        payload = build_payload(deck, meta) if token else None
        start = False
        with self._lock:
            if not self._cfg.enabled:
                self._waiting = False
                return
            if token is None:
                self._waiting = False
                return
            if token == self._sent or token == self._inflight:
                self._waiting = False
                return
            if self._latest is not None and self._latest[0] == token:
                return
            if not payload:
                self._waiting = True
                return
            self._waiting = False
            self._latest = (token, payload, self._cfg, self._gen)
            if not self._busy:
                self._busy = True
                start = True
        if start:
            threading.Thread(target=self._worker, name="wnp-remote", daemon=True).start()

    def same_saved_target(self, data: dict[str, Any] | None) -> bool:
        """True when the form matches the saved, enabled endpoint."""
        cfg = settings_from(data)
        with self._lock:
            current = self._cfg
        return (
            current.enabled
            and current.host == cfg.host
            and current.port == cfg.port
            and current.secret == cfg.secret
        )

    def note(self, result: SendResult, token: tuple[int, int] | None = None) -> None:
        """Record a Test-button result so the Health page can show it."""
        with self._lock:
            self._last = result.as_health()
            if result.ok and token is not None:
                self._sent = token
                self._waiting = False

    def health(self) -> dict[str, Any]:
        with self._lock:
            cfg = self._cfg
            last = dict(self._last) if self._last else None
            waiting = self._waiting
        base = {
            "enabled": cfg.enabled,
            "host": cfg.host,
            "port": cfg.port,
            "track": "",
            "status": "disabled",
            "detail": "Off",
        }
        if not cfg.enabled:
            return base
        if waiting:
            base["status"] = "waiting"
            base["detail"] = "Waiting for the track title."
            if last and last.get("track"):
                base["track"] = last["track"]
            return base
        if last:
            base.update(last)
            base["enabled"] = True
            base["host"] = cfg.host
            base["port"] = cfg.port
            return base
        base["status"] = "idle"
        base["detail"] = "On. Waiting for the audience track."
        return base

    def wait_idle(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._busy and self._latest is None:
                    return True
            time.sleep(0.01)
        return False

    def _worker(self) -> None:
        while True:
            with self._lock:
                job = self._latest
                self._latest = None
                if job is None:
                    self._busy = False
                    return
                token, payload, cfg, gen = job
                self._inflight = token
            result = post_metadata(cfg, payload, timeout=self.timeout)
            with self._lock:
                if gen == self._gen:
                    self._sent = token
                    self._last = result.as_health()
                self._inflight = None
