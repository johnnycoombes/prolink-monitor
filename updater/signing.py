"""Sign a tufup repository without an interactive password prompt.

GitHub Actions stores the private keys as a secret. This module reads the
password from ``TUFUP_KEY_PASSWORD`` and never asks on stdin, so a missing
password fails the step instead of hanging the job.
"""

from __future__ import annotations

import base64
import builtins
import io
import json
import os
import zipfile
from pathlib import Path, PureWindowsPath

from updater.config import APP_NAME, EXPIRATION_DAYS, ROLE_NAMES

_ALLOWED_KEY_FILES = {name for role in ROLE_NAMES for name in (role, role + ".pub")}
_CONFIG_NAME = ".tufup-repo-config"


def install_no_input_hook():
    """Replace ``input`` so a forgotten prompt fails instead of reading stdin.

    Returns the previous ``input`` so the caller can put it back.
    """
    previous = builtins.input

    def _blocked(prompt: str = "") -> str:
        text = str(prompt).strip() or "input"
        raise RuntimeError(
            "Update signing tried to ask a question "
            f"({text}). This step does not read the keyboard. "
            "Directories are created in advance, and the key password "
            "comes from TUFUP_KEY_PASSWORD."
        )

    builtins.input = _blocked
    return previous


def install_key_password_hook() -> None:
    """Teach tufup to decrypt keys with ``TUFUP_KEY_PASSWORD``.

    tufup tries an empty password and then calls ``input`` for an encrypted
    key. Both attempts go through this hook, and neither one reads stdin.
    """
    import tufup.repo as repo_mod
    from securesystemslib.exceptions import CryptoError

    original = repo_mod.import_ed25519_privatekey_from_file

    def _import(filepath, password=None, prompt=False, storage_backend=None):
        del prompt  # never forwarded; tufup would read stdin when this is true
        env_pw = os.environ.get("TUFUP_KEY_PASSWORD") or ""
        attempts: list[str | None] = []
        if password:
            attempts.append(password)
        if env_pw and env_pw not in attempts:
            attempts.append(env_pw)
        attempts.append(None)
        last_error: CryptoError | None = None
        for candidate in attempts:
            try:
                return original(
                    filepath,
                    password=candidate,
                    prompt=False,
                    storage_backend=storage_backend,
                )
            except CryptoError as exc:
                last_error = exc
        name = Path(str(filepath)).name
        if not env_pw and not password:
            raise RuntimeError(
                f"Could not decrypt private key '{name}'. "
                "Set TUFUP_KEY_PASSWORD. This step will not prompt for a password."
            ) from last_error
        raise RuntimeError(
            f"Could not decrypt private key '{name}'. "
            "TUFUP_KEY_PASSWORD does not match this key."
        ) from last_error

    repo_mod.import_ed25519_privatekey_from_file = _import


def _zip_entry_name(filename: str) -> str:
    """Last path piece of a zip entry, for both ``/`` and Windows ``\\``."""
    text = filename.replace("\\", "/").rstrip("/")
    return PureWindowsPath(text).name


def extract_keys_zip(blob: bytes, dest: Path) -> str | None:
    """Unpack a key zip, keeping only the four role files and their ``.pub`` halves.

    ``scripts/tuf_init_keys.py`` writes a flat zip (``root``, ``root.pub``, ...).
    A Windows folder zip may instead use ``keystore\\\\root`` or
    ``packaging/tuf/keystore/root``. Directory pieces are ignored. Only the
    file name is used, so an entry cannot write outside ``dest``.

    Returns ``keys_dir`` from a bundled ``.tufup-repo-config`` when the zip
    has one. That file is not written next to the keys: tufup would load it
    from the working directory and follow a path from the PC that created it.
    """
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    config_keys_dir: str | None = None
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            base = _zip_entry_name(info.filename)
            payload = archive.read(info)
            if base == _CONFIG_NAME:
                config_keys_dir = _keys_dir_from_config_bytes(payload)
                continue
            if base not in _ALLOWED_KEY_FILES:
                continue
            target = dest / base
            if target.parent != dest:
                continue
            target.write_bytes(payload)
    return config_keys_dir


def _keys_dir_from_config_bytes(payload: bytes) -> str | None:
    try:
        parsed = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get("keys_dir")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def resolve_keystore(extracted: Path, config_keys_dir: str | None) -> Path:
    """Pick the keystore that actually contains the keys.

    A ``.tufup-repo-config`` written on Windows stores ``keys_dir`` as a
    drive path (``C:\\\\Users\\\\...\\\\keystore``) or a path relative to
    whatever directory tufup had as its working directory. Neither is the
    runner's extracted keystore. An absolute path is used only when it
    already exists on this machine and holds the private ``root`` key.
    """
    extracted = Path(extracted).resolve()
    if not config_keys_dir:
        return extracted
    text = config_keys_dir.strip().replace("\\", "/")
    windows_abs = (len(text) >= 2 and text[1] == ":") or text.startswith("//")
    if windows_abs and os.name != "nt":
        return extracted
    candidate = Path(text)
    if not candidate.is_absolute():
        return extracted
    try:
        candidate = candidate.resolve()
    except OSError:
        return extracted
    if candidate.is_dir() and (candidate / "root").is_file():
        return candidate
    return extracted


def decode_keys_b64(keys_b64: str, dest: Path) -> str | None:
    """Unpack the secret. Returns a ``keys_dir`` stored in a bundled config."""
    try:
        raw = base64.b64decode(keys_b64.strip(), validate=False)
    except Exception as exc:
        raise RuntimeError(
            "TUFUP_KEYS_B64 is not valid base64. Paste the base64 of "
            "packaging/tuf/tuf-keys.zip. Do not regenerate the keys."
        ) from exc
    try:
        return extract_keys_zip(raw, dest)
    except zipfile.BadZipFile as exc:
        raise RuntimeError(
            "TUFUP_KEYS_B64 did not decode to a zip file. It has to be the "
            "base64 of packaging/tuf/tuf-keys.zip from scripts/tuf_init_keys.py. "
            "Do not regenerate the keys."
        ) from exc


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

    Paths are made absolute before the working directory changes. tufup
    resolves a relative ``keys_dir`` against the current directory, and this
    function moves into the repo parent so ``.tufup-repo-config`` is not
    written into the git checkout. A relative path would then point at
    ``<work>/<work>/keystore``, which does not exist, and tufup would ask
    to create it.
    """
    from tufup.repo import Repository

    keys_dir = Path(keys_dir).resolve()
    repo_dir = Path(repo_dir).resolve()
    if bundle_dir is not None:
        bundle_dir = Path(bundle_dir).resolve()
    metadata_dir = repo_dir / "metadata"
    targets_dir = repo_dir / "targets"
    for path in (keys_dir, metadata_dir, targets_dir):
        path.mkdir(parents=True, exist_ok=True)
    missing = [role for role in ROLE_NAMES if not (keys_dir / role).is_file()]
    if missing:
        found = sorted(path.name for path in keys_dir.iterdir())
        raise RuntimeError(
            "Private key file(s) not found in "
            f"{keys_dir}: {', '.join(missing)}. "
            f"That folder contains: {found or 'nothing'}. "
            "The key zip from scripts/tuf_init_keys.py has to include "
            "root, targets, snapshot, and timestamp."
        )
    # Drop versioned root copies so tufup reads root.json.
    for path in metadata_dir.glob("*.root.json"):
        path.unlink()

    previous = Path.cwd()
    previous_input = None
    try:
        previous_input = install_no_input_hook()
        # Stay next to the repo so tufup's config file is not written into the
        # checkout. The paths above are already absolute, so this move cannot
        # change which keystore is opened.
        os.chdir(repo_dir.parent)
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
        if previous_input is not None:
            builtins.input = previous_input
    if not (metadata_dir / "root.json").is_file():
        raise RuntimeError(f"root.json was not written in {metadata_dir}")
    return metadata_dir
