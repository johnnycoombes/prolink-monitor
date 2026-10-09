"""Look for a signed update. Network errors stay inside ``UpdateCheck``."""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prolink.bundle import is_frozen
from updater.config import (
    APP_NAME,
    app_version,
    config_dir,
    metadata_base_url,
    target_base_url,
    trusted_root_in_source,
    update_cache_dir,
)

log = logging.getLogger(__name__)


@dataclass
class UpdateCheck:
    status: str  # not_configured | up_to_date | available | error
    current: str
    available: str | None = None
    filename: str | None = None
    message: str = ""
    # Same tufup client that performed the check. Needed to download afterwards.
    client: Any = field(default=None, repr=False)


def trusted_root_path() -> Path | None:
    """Public root metadata shipped with the app, if JC has generated keys."""
    candidates: list[Path] = []
    if is_frozen():
        import sys

        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "tuf" / "root.json")
        candidates.append(Path(sys.executable).resolve().parent / "tuf" / "root.json")
    candidates.append(trusted_root_in_source())
    for path in candidates:
        if path.is_file():
            return path
    return None


def log_update(message: str) -> None:
    log.info("%s", message)
    try:
        path = config_dir() / "update.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass


def query_repository(
    *,
    current_version: str,
    trusted_root: Path,
    metadata_dir: Path,
    metadata_url: str,
    target_dir: Path,
    target_url: str,
    install_dir: Path,
) -> UpdateCheck:
    """Ask a tufup repository whether ``current_version`` is behind.

    ``metadata_url`` and ``target_url`` must end with a slash. The trusted
    root is copied into ``metadata_dir`` only when that cache does not already
    have one, so a later root rotation is not overwritten by the copy shipped
    in an older build.
    """
    try:
        from tufup.client import Client
    except ImportError as exc:
        return UpdateCheck(
            status="error",
            current=current_version,
            message=f"tufup is not installed ({exc})",
        )

    metadata_dir.mkdir(parents=True, exist_ok=True)
    target_dir.mkdir(parents=True, exist_ok=True)
    install_dir.mkdir(parents=True, exist_ok=True)
    cached_root = metadata_dir / "root.json"
    if not cached_root.is_file():
        shutil.copyfile(trusted_root, cached_root)

    client = Client(
        app_name=APP_NAME,
        app_install_dir=install_dir,
        current_version=current_version,
        metadata_dir=metadata_dir,
        metadata_base_url=metadata_url,
        target_dir=target_dir,
        target_base_url=target_url,
        refresh_required=False,
    )
    # Full archives only. Patch files of a PyInstaller folder are large and
    # need the previous archive on disk, which a hand-installed zip does not have.
    found = client.check_for_updates(pre=None, patch=False)
    if not found:
        return UpdateCheck(status="up_to_date", current=current_version, client=client)
    version = str(found.version)
    return UpdateCheck(
        status="available",
        current=current_version,
        available=version,
        filename=str(found.filename),
        message=f"{version} is available (installed {current_version}).",
        client=client,
    )


def perform_check(current_version: str | None = None) -> UpdateCheck:
    """Check the configured update repository. Does not show a dialog."""
    version = current_version or app_version()
    root = trusted_root_path()
    if root is None:
        return UpdateCheck(
            status="not_configured",
            current=version,
            message=(
                "No trusted root.json is bundled. Update checks stay off "
                "until signing keys are created and root.json is shipped."
            ),
        )
    cache = update_cache_dir()
    install_dir = _install_dir()
    try:
        return query_repository(
            current_version=version,
            trusted_root=root,
            metadata_dir=cache / "metadata",
            metadata_url=metadata_base_url(),
            target_dir=cache / "targets",
            target_url=target_base_url(),
            install_dir=install_dir,
        )
    except Exception as exc:
        log_update(f"update check failed: {exc}")
        return UpdateCheck(status="error", current=version, message=str(exc))


def _install_dir() -> Path:
    if is_frozen():
        import sys

        return Path(sys.executable).resolve().parent
    # Not used for writing when running from source. Still has to exist for tufup.
    return update_cache_dir() / "source-install"


def download_and_apply(check: UpdateCheck, *, exe: str) -> None:
    """Download the archive already found by ``check`` and install it."""
    if check.status != "available" or check.client is None:
        raise RuntimeError("There is no signed update to apply.")
    from updater.apply import install_extracted

    def _install(src_dir, dst_dir, **_kwargs):
        install_extracted(Path(src_dir), Path(dst_dir), exe=exe)

    check.client.download_and_apply_update(skip_confirmation=True, install=_install)
