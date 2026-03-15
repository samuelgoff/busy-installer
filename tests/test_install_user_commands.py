from __future__ import annotations

import os
from pathlib import Path

from scripts.install_user_commands import install_user_commands


def test_install_user_commands_installs_public_wrappers(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"

    installed = install_user_commands(repo_root=root, bin_dir=bin_dir, force=False)

    expected = ("pf", "pillowfort", "busy") if os.name != "nt" else (
        "pf.cmd",
        "pillowfort.cmd",
        "busy.cmd",
        "pf.ps1",
        "pillowfort.ps1",
        "busy.ps1",
    )
    assert tuple(name for name, _target, _mode in installed) == expected

    for name, target, mode in installed:
        assert target.exists()
        if os.name != "nt" and mode == "symlink":
            assert target.is_symlink()
            assert target.resolve() == (root / name).resolve()
        else:
            assert target.read_text(encoding="utf-8") == (root / name).read_text(encoding="utf-8")
