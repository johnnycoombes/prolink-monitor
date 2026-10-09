"""Create the signing keys for Prolink Listener updates.

Run this once, on your own PC, from the repository root:

    pip install tufup
    python scripts/tuf_init_keys.py

It asks for a password and writes:

    packaging/tuf/keystore/     private keys — never commit these
    packaging/tuf/tuf-keys.zip  the same keys, for the GitHub secret
    packaging/tuf/root.json     public trust anchor — commit this file

Then add two GitHub Actions secrets (Settings → Secrets and variables →
Actions):

    TUFUP_KEYS_B64        base64 of packaging/tuf/tuf-keys.zip
    TUFUP_KEY_PASSWORD    the password you just chose

PowerShell, from the repository root:

    [Convert]::ToBase64String([IO.File]::ReadAllBytes("packaging\\tuf\\tuf-keys.zip")) | Set-Clipboard

bash:

    base64 -w 0 packaging/tuf/tuf-keys.zip

Copy that text into the TUFUP_KEYS_B64 secret. Do not put it in the repository,
in a pull request, or in a screenshot.
"""

from __future__ import annotations

import getpass
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from securesystemslib.interface import generate_and_write_ed25519_keypair  # noqa: E402

from updater.config import ROLE_NAMES  # noqa: E402
from updater.signing import sign_repository  # noqa: E402


def _write_keys(keystore: Path, password: str) -> None:
    keystore.mkdir(parents=True, exist_ok=True)
    for role in ROLE_NAMES:
        private = keystore / role
        if private.exists() or private.with_suffix(".pub").exists():
            raise SystemExit(
                f"{private} already exists. Not overwriting. "
                "Move the old keystore aside if you really mean to start again."
            )
        generate_and_write_ed25519_keypair(password=password, filepath=str(private))


def _zip_keys(keystore: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(keystore.iterdir()):
            if path.is_file():
                archive.write(path, arcname=path.name)


def main() -> int:
    tuf_dir = ROOT / "packaging" / "tuf"
    keystore = tuf_dir / "keystore"
    work = tuf_dir / "initial-repo"
    public_root = tuf_dir / "root.json"
    zip_path = tuf_dir / "tuf-keys.zip"

    print("Choose a password for the four signing keys (root, targets, snapshot, timestamp).")
    print("You will paste this same password into the TUFUP_KEY_PASSWORD GitHub secret.")
    password = getpass.getpass("Password: ")
    again = getpass.getpass("Repeat password: ")
    if not password or password != again:
        print("The passwords were empty or did not match.", file=sys.stderr)
        return 1

    os.environ["TUFUP_KEY_PASSWORD"] = password
    try:
        _write_keys(keystore, password)
        sign_repository(keys_dir=keystore, repo_dir=work)
    except Exception as exc:
        print(f"Could not create keys: {exc}", file=sys.stderr)
        return 1

    produced = work / "metadata" / "root.json"
    if not produced.is_file():
        print(f"root.json was not created at {produced}", file=sys.stderr)
        return 1
    public_root.write_bytes(produced.read_bytes())
    _zip_keys(keystore, zip_path)

    # Make sure we did not accidentally leave the password where the zip can see it.
    del password, again

    print()
    print(f"Public root written to {public_root}")
    print("Commit that file and push it to main. Do not commit the keystore or the zip.")
    print(f"Private keys: {keystore}")
    print(f"Zip for the GitHub secret: {zip_path}")
    print()
    print("Secret TUFUP_KEYS_B64 is the base64 of that zip.")
    print("Secret TUFUP_KEY_PASSWORD is the password you typed.")
    print()
    print("PowerShell:")
    print(
        '  [Convert]::ToBase64String([IO.File]::ReadAllBytes("packaging\\tuf\\tuf-keys.zip")) | Set-Clipboard'
    )
    print("bash:")
    print("  base64 -w 0 packaging/tuf/tuf-keys.zip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
