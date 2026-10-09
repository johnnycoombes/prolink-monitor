"""Sign a Windows bundle and write TUF metadata. Used by GitHub Actions.

Exits 0 without writing anything when the signing secret or the committed
public root is missing, so a release zip can still be published.

    python scripts/publish_tuf_release.py --bundle dist/ProlinkListener --version 1.2.0 \\
        --metadata-dir previous-metadata --out dist/tuf-publish

Reads TUFUP_KEYS_B64 and TUFUP_KEY_PASSWORD from the environment.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from updater.signing import decode_keys_b64, seed_metadata, sign_repository  # noqa: E402


def skip_reason(*, keys_b64: str, root_json: Path) -> str | None:
    """Why metadata publishing should be skipped, or None to go ahead."""
    if not keys_b64.strip():
        return (
            "TUFUP_KEYS_B64 is not set. Skipping signed update metadata. "
            "The Windows zip is unchanged. Add the secret when you want "
            "the app to be able to update itself."
        )
    if not root_json.is_file():
        return (
            "packaging/tuf/root.json is not in the repository. Skipping signed "
            "update metadata. Run python scripts/tuf_init_keys.py, commit "
            "packaging/tuf/root.json, add the two GitHub secrets, and tag again."
        )
    return None


def publish(
    *,
    bundle_dir: Path,
    version: str,
    keys_b64: str,
    out_dir: Path,
    metadata_seed: Path | None,
    require_existing: bool,
) -> None:
    if not bundle_dir.is_dir():
        raise SystemExit(f"Bundle folder not found: {bundle_dir}")
    work = out_dir.parent / (out_dir.name + "-work")
    if work.exists():
        shutil.rmtree(work)
    keys_dir = work / "keystore"
    repo_dir = work / "repository"
    decode_keys_b64(keys_b64, keys_dir)
    if not (keys_dir / "root").is_file() or not (keys_dir / "targets").is_file():
        raise SystemExit(
            "The key zip does not contain the root and targets private keys. "
            "Re-run scripts/tuf_init_keys.py and update TUFUP_KEYS_B64."
        )
    had_timestamp = seed_metadata(metadata_seed, repo_dir / "metadata")
    if require_existing and not had_timestamp:
        raise SystemExit(
            "The updates release exists but its metadata could not be downloaded. "
            "Refusing to start a new trust chain over the old one."
        )
    # First publish: keep the root.json that is already shipped in the app.
    committed = ROOT / "packaging" / "tuf" / "root.json"
    metadata_dir = repo_dir / "metadata"
    if not (metadata_dir / "root.json").is_file() and committed.is_file():
        metadata_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(committed, metadata_dir / "root.json")

    sign_repository(
        keys_dir=keys_dir,
        repo_dir=repo_dir,
        bundle_dir=bundle_dir,
        version=version,
    )
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    for path in (repo_dir / "metadata").iterdir():
        if path.is_file() and path.suffix == ".json":
            shutil.copyfile(path, out_dir / path.name)
    archive = repo_dir / "targets" / f"prolink-listener-{version}.tar.gz"
    if not archive.is_file():
        raise SystemExit(f"Signed archive missing: {archive}")
    shutil.copyfile(archive, out_dir / archive.name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sign a Prolink Listener update")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--metadata-dir", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--require-existing",
        action="store_true",
        help="Fail if previous timestamp.json was not provided",
    )
    args = parser.parse_args(argv)
    keys_b64 = os.environ.get("TUFUP_KEYS_B64", "")
    reason = skip_reason(keys_b64=keys_b64, root_json=ROOT / "packaging" / "tuf" / "root.json")
    if reason:
        print(reason)
        return 0
    try:
        publish(
            bundle_dir=args.bundle,
            version=args.version,
            keys_b64=keys_b64,
            out_dir=args.out,
            metadata_seed=args.metadata_dir,
            require_existing=args.require_existing,
        )
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Could not sign update metadata: {exc}", file=sys.stderr)
        return 1
    print(f"Signed update files are in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
