"""Sign a tufup repository without an interactive password prompt.

GitHub Actions stores the private keys as a secret. This module reads the
password from ``TUFUP_KEY_PASSWORD`` and never asks on stdin, so a missing
password fails the step instead of hanging the job.
"""

from __future__ import annotations

import base64
import io
import os
import zipfile
from pathlib import Path

from updater.config import APP_NAME, EXPIRATION_DAYS, ROLE_NAMES

_ALLOWED_KEY_FILES = {name for role in ROLE_NAMES for name in (role, role + ".pub")}


def install_key_password_hook() -> None:
    """Teach tufup to decrypt keys with ``TUFUP_KEY_PASSWORD``."""
    import tufup.repo as repo_mod
    from securesystemslib.exceptions import CryptoError

    original = repo_mod.import_ed25519_privatekey_from_file

    def _import(filepath, password=None, prompt=False, storage_backend=None):
        try:
            return original(
                filepath,
                password=password,
                prompt=False,
                storage_backend=storage_backend,
            )
        except CryptoError:
            env_pw = os.environ.get("TUFUP_KEY_PASSWORD") or ""
            if not env_pw:
                raise RuntimeError(
                    "A private key is encrypted. Set TUFUP_KEY_PASSWORD. "
                    "This step will not prompt for a password."
                ) from None
            return original(
                filepath,
                password=env_pw,
                prompt=False,
                storage_backend=storage_backend,
            )

    repo_mod.import_ed25519_privatekey_from_file = _import


def extract_keys_zip(blob: bytes, dest: Path) -> None:
    """Unpack a key zip, keeping only the four role files and their ``.pub`` halves.

    Directory names inside the zip are ignored, so both a flat zip and a
    ``keystore/`` zip work. Only the file name is used, so a zip entry cannot
    write outside ``dest``.
    """
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            base = Path(info.filename.replace("\\", "/")).name
            if base not in _ALLOWED_KEY_FILES:
                continue
            target = dest / base
            if target.parent != dest:
                continue
            target.write_bytes(archive.read(info))


def decode_keys_b64(keys_b64: str, dest: Path) -> None:
    raw = base64.b64decode(keys_b64.strip(), validate=False)
    extract_keys_zip(raw, dest)


def seed_metadata(src: Path | None, metadata_dir: Path) -> bool:
    """Copy unversioned metadata into ``metadata_dir``.

    Versioned ``1.root.json`` files are left behind on purpose. tufup loads the
    first file whose name starts with the role, and a versioned copy can hide
    ``root.json``. Returns True when a previous ``timestamp.json`` was copied.
    """
    metadata_dir.mkdir(parents=True, exist_ok=True)
    if src is None or not src.is_dir():
        return False
    found_timestamp = False
    for name in ("root.json", "targets.json", "snapshot.json", "timestamp.json"):
        path = src / name
        if path.is_file():
            (metadata_dir / name).write_bytes(path.read_bytes())
            if name == "timestamp.json":
                found_timestamp = True
    return found_timestamp


def sign_repository(
    *,
    keys_dir: Path,
    repo_dir: Path,
    bundle_dir: Path | None = None,
    version: str | None = None,
) -> Path:
    """Load keys, optionally add a bundle, and sign metadata.

    Encrypted keys are unlocked with ``TUFUP_KEY_PASSWORD``. Nothing is read
    from the keyboard. Returns the metadata directory.
    """
    from tufup.repo import Repository

    keys_dir = Path(keys_dir)
    repo_dir = Path(repo_dir)
    metadata_dir = repo_dir / "metadata"
    targets_dir = repo_dir / "targets"
    for path in (keys_dir, metadata_dir, targets_dir):
        path.mkdir(parents=True, exist_ok=True)
    # Drop versioned root copies so tufup reads root.json.
    for path in metadata_dir.glob("*.root.json"):
        path.unlink()

    previous = Path.cwd()
    os.chdir(repo_dir.parent)
    try:
        install_key_password_hook()
        repo = Repository(
            app_name=APP_NAME,
            repo_dir=repo_dir,
            keys_dir=keys_dir,
            encrypted_keys=[],
            expiration_days=dict(EXPIRATION_DAYS),
        )
        repo._load_keys_and_roles(create_keys=False)
        if bundle_dir is not None:
            if not version:
                raise RuntimeError("A version is required when adding a bundle.")
            repo.add_bundle(
                new_bundle_dir=bundle_dir,
                new_version=version,
                skip_patch=True,
            )
            archive = targets_dir / f"{APP_NAME}-{version}.tar.gz"
            if not archive.is_file():
                raise RuntimeError(
                    f"No archive named {archive.name} was created. "
                    "The version has to be newer than the last signed release."
                )
        repo.publish_changes(private_key_dirs=[keys_dir])
    finally:
        os.chdir(previous)
    if not (metadata_dir / "root.json").is_file():
        raise RuntimeError(f"root.json was not written in {metadata_dir}")
    return metadata_dir
