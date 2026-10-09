"""Decide whether an update may be installed, and copy files safely.

The Windows app does the real install with a small robocopy script so it can
replace its own exe after this process exits. Tests use ``apply_extracted_tree``
directly. Neither path writes ``~/.prolink-monitor`` or ``~/.prolink-cache``.
"""

from __future__ import annotations

import platform
import shutil
from pathlib import Path

from updater.config import config_dir, library_cache_dir

# Placeholders filled by tufup's Windows installer, plus {exe} for the restart.
WINDOWS_RESTART_BATCH = """@echo off
{log_lines}
echo Moving app files...
robocopy "{src_dir}" "{dst_dir}" {robocopy_options}
echo Restarting Prolink Listener...
start "" "{exe}"
{delete_self}
"""


def is_user_data_dir(path: Path) -> bool:
    """True when ``path`` is the settings folder, the library cache, or inside either."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    protected = []
    for folder in (config_dir(), library_cache_dir()):
        try:
            protected.append(folder.resolve())
        except OSError:
            protected.append(folder)
    for root in protected:
        if resolved == root or root in resolved.parents:
            return True
    return False


def decide_action(status: str, *, confirmed: bool, frozen: bool) -> str:
    """What to do after a check.

    ``ignore`` — nothing to install (up to date, not configured, or an error).
    ``later`` — a version is available and the user said no.
    ``show_download`` — running from source; tell the user, do not write the checkout.
    ``apply`` — frozen Windows (or other) build; user agreed to install and restart.
    """
    if status != "available":
        return "ignore"
    if not confirmed:
        return "later"
    if not frozen:
        return "show_download"
    return "apply"


def apply_extracted_tree(src_dir: Path, dst_dir: Path) -> None:
    """Copy ``src_dir`` onto ``dst_dir``.

    Files that are only in the destination are left in place (no purge).
    Refuses the settings directory and the library cache.
    """
    src = Path(src_dir)
    dst = Path(dst_dir)
    if not src.is_dir():
        raise FileNotFoundError(f"update files not found: {src}")
    if is_user_data_dir(dst):
        raise RuntimeError(
            "Refusing to install an update into the settings folder or the library cache."
        )
    dst.mkdir(parents=True, exist_ok=True)
    for path in src.rglob("*"):
        relative = path.relative_to(src)
        target = dst / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if path.is_symlink():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def windows_batch_preview(src_dir: str, dst_dir: str, exe: str) -> str:
    """The batch script the Windows installer would run. No files are changed."""
    options = "/e /move /v /w:2"
    return WINDOWS_RESTART_BATCH.format(
        log_lines="",
        src_dir=src_dir,
        dst_dir=dst_dir,
        robocopy_options=options,
        delete_self="(goto) 2>nul & del \"%~f0\"",
        exe=exe,
    )


def install_extracted(src_dir: Path, dst_dir: Path, *, exe: str) -> None:
    """Install ``src_dir`` into ``dst_dir`` and restart.

    On Windows this hands off to tufup's robocopy script, which exits this
    process. Elsewhere it copies the files and replaces this process.
    """
    if is_user_data_dir(Path(dst_dir)):
        raise RuntimeError(
            "Refusing to install an update into the settings folder or the library cache."
        )
    if platform.system() == "Windows":
        from tufup.utils.platform_specific import _install_update_win

        _install_update_win(
            src_dir=str(src_dir),
            dst_dir=str(dst_dir),
            purge_dst_dir=False,
            exclude_from_purge=None,
            batch_template=WINDOWS_RESTART_BATCH,
            batch_template_extra_kwargs={"exe": exe},
            log_file_name="install.log",
        )
        return
    apply_extracted_tree(Path(src_dir), Path(dst_dir))
    import os
    import sys

    os.execv(exe, [exe, *sys.argv[1:]])
