from __future__ import annotations

import os
from pathlib import Path

from scripts.install_user_commands import (
    inspect_user_commands,
    install_user_commands,
    path_hint_lines,
    uninstall_user_commands,
)


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
        assert mode == "shim"
        text = target.read_text(encoding="utf-8")
        assert str(root.resolve()) in text
        assert "bootstrap_env.py" in text
        assert "busy_installer.app" in text


def test_inspect_and_uninstall_user_commands_track_managed_targets(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"

    initial = inspect_user_commands(repo_root=root, bin_dir=bin_dir)
    assert all(state == "missing" for _name, _target, state in initial)

    install_user_commands(repo_root=root, bin_dir=bin_dir, force=False)
    installed = inspect_user_commands(repo_root=root, bin_dir=bin_dir)
    assert all(state == "managed-shim" for _name, _target, state in installed)

    removed = uninstall_user_commands(repo_root=root, bin_dir=bin_dir)
    assert all(state == "removed" for _name, _target, state in removed)

    final = inspect_user_commands(repo_root=root, bin_dir=bin_dir)
    assert all(state == "missing" for _name, _target, state in final)


def test_uninstall_also_removes_legacy_managed_symlinks(tmp_path: Path) -> None:
    if os.name == "nt":
        return

    root = Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    legacy = bin_dir / "pf"
    legacy.symlink_to(root / "pf")

    observed = inspect_user_commands(repo_root=root, bin_dir=bin_dir)
    assert observed[0][2] == "legacy-managed-symlink"

    removed = uninstall_user_commands(repo_root=root, bin_dir=bin_dir)
    assert removed[0][2] == "removed"
    assert not legacy.exists()


def test_path_hint_lines_are_shell_specific(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"

    assert path_hint_lines(bin_dir, shell="zsh") == [
        f'export PATH="{bin_dir}:$PATH"',
        "Add that line to your ~/.zshrc for persistence.",
    ]
    assert path_hint_lines(bin_dir, shell="fish") == [
        f'fish_add_path "{bin_dir}"',
        "Add that to config.fish for persistence.",
    ]
    assert path_hint_lines(bin_dir, shell="powershell") == [
        f'$env:Path = "{bin_dir};" + $env:Path',
        f'[Environment]::SetEnvironmentVariable("Path", "{bin_dir};" + [Environment]::GetEnvironmentVariable("Path", "User"), "User")',
    ]
