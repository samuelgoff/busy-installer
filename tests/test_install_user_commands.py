from __future__ import annotations

import os
from pathlib import Path
import pytest

from scripts.install_user_commands import (
    _detect_windows_shell,
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
        if os.name != "nt":
            assert text.startswith("#!/bin/sh\nset -eu\n")
            assert "PYTHON=python3" in text
            assert "PYTHON=python" in text
        else:
            assert 'set "ROOT=' in text
            assert 'set "BOOTSTRAP=' in text
            assert 'set "VENV_PYTHON=' in text
            assert 'set "PYTHON=python3"' in text
            assert 'set "PYTHON=python"' in text


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


def test_install_user_commands_is_idempotent_for_managed_shims(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"

    first = install_user_commands(repo_root=root, bin_dir=bin_dir, force=False)
    second = install_user_commands(repo_root=root, bin_dir=bin_dir, force=False)

    assert all(mode == "shim" for _name, _target, mode in first)
    assert all(mode == "managed" for _name, _target, mode in second)


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


def test_install_user_commands_migrates_legacy_managed_targets(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)

    if os.name == "nt":
        legacy_name = "pf.cmd"
    else:
        legacy_name = "pf"

    legacy_target = bin_dir / legacy_name
    legacy_target.write_bytes((root / legacy_name).read_bytes())

    installed = install_user_commands(repo_root=root, bin_dir=bin_dir, force=False)

    assert installed[0][0] == legacy_name
    assert installed[0][2] == "migrated-shim"
    assert inspect_user_commands(repo_root=root, bin_dir=bin_dir)[0][2] == "managed-shim"


def test_force_refuses_to_replace_foreign_files(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    foreign = bin_dir / ("pf" if os.name != "nt" else "pf.cmd")
    foreign.write_text("foreign", encoding="utf-8")

    with pytest.raises(SystemExit, match="refusing to replace non-managed target"):
        install_user_commands(repo_root=root, bin_dir=bin_dir, force=True)

    assert foreign.read_text(encoding="utf-8") == "foreign"


def test_foreign_target_preflight_prevents_partial_install(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)

    foreign_name = "busy" if os.name != "nt" else "busy.cmd"
    (bin_dir / foreign_name).write_text("foreign", encoding="utf-8")

    with pytest.raises(SystemExit, match="refusing to replace non-managed target"):
        install_user_commands(repo_root=root, bin_dir=bin_dir, force=False)

    expected_names = ("pf", "pillowfort", "busy") if os.name != "nt" else (
        "pf.cmd",
        "pillowfort.cmd",
        "busy.cmd",
        "pf.ps1",
        "pillowfort.ps1",
        "busy.ps1",
    )
    for name in expected_names:
        target = bin_dir / name
        if name == foreign_name:
            assert target.read_text(encoding="utf-8") == "foreign"
        else:
            assert not target.exists()


def test_path_hint_lines_are_shell_specific(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    windows_bin_dir = tmp_path / "bin & tools"

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
        '$userPath = [Environment]::GetEnvironmentVariable("Path", "User")',
        f'if (($userPath -split ";") -notcontains "{bin_dir}") ' + "{ [Environment]::SetEnvironmentVariable(\"Path\", (\"" + str(bin_dir) + ';\" + $userPath).TrimEnd(\';\'), \"User\") }',
    ]
    assert path_hint_lines(windows_bin_dir, shell="cmd") == [
        f'set "PATH={windows_bin_dir};%PATH%"',
        "Use System Properties > Environment Variables for a persistent cmd PATH update,",
        "or run the PowerShell persistence command shown by --shell powershell.",
    ]


def test_detect_windows_shell_prefers_cmd_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPT", "$P$G")
    assert _detect_windows_shell() == "cmd"

    monkeypatch.delenv("PROMPT", raising=False)
    assert _detect_windows_shell() == "powershell"
