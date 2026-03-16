from __future__ import annotations

import os
from pathlib import Path
import pytest

from scripts.install_user_commands import (
    _cmd_escape,
    _detect_shell,
    _has_managed_targets,
    _status_summary,
    main,
    _detect_windows_shell,
    _install_summary_verb,
    _powershell_quote,
    _shim_content,
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
            assert "setlocal DisableDelayedExpansion" in text
            assert "if errorlevel 1 exit /b %errorlevel%" in text
            assert text.count("if ($LASTEXITCODE -ne 0)") == 2

    expected_names = {"pf", "pillowfort", "busy"} if os.name != "nt" else {
        "pf.cmd",
        "pillowfort.cmd",
        "busy.cmd",
        "pf.ps1",
        "pillowfort.ps1",
        "busy.ps1",
    }
    assert {entry.name for entry in bin_dir.iterdir()} == expected_names


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


def test_install_user_commands_force_reinstalls_managed_shims(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"

    install_user_commands(repo_root=root, bin_dir=bin_dir, force=False)
    forced = install_user_commands(repo_root=root, bin_dir=bin_dir, force=True)

    assert all(mode == "reinstalled-shim" for _name, _target, mode in forced)


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
    posix_bin_dir = tmp_path / "bin $ tools"
    windows_bin_dir = tmp_path / "bin & tools"
    windows_percent_bin_dir = tmp_path / "bin % tools"
    powershell_bin_dir = tmp_path / "bin $ tools's"

    assert path_hint_lines(posix_bin_dir, shell="zsh") == [
        f"export PATH='{posix_bin_dir}':\"$PATH\"",
        "Add that line to your ~/.zshrc for persistence.",
    ]
    assert path_hint_lines(posix_bin_dir, shell="fish") == [
        f"fish_add_path '{posix_bin_dir}'",
        "Add that to config.fish for persistence.",
    ]
    assert path_hint_lines(powershell_bin_dir, shell="powershell") == [
        f"$env:Path = {_powershell_quote(str(powershell_bin_dir))} + ';' + $env:Path",
        '$userPath = [Environment]::GetEnvironmentVariable("Path", "User")',
        f"if (($userPath -split ';') -notcontains {_powershell_quote(str(powershell_bin_dir))}) " + "{ [Environment]::SetEnvironmentVariable(\"Path\", (" + _powershell_quote(str(powershell_bin_dir)) + " + ';' + $userPath).TrimEnd(';'), \"User\") }",
    ]
    assert path_hint_lines(powershell_bin_dir, shell="pwsh", os_name="posix") == [
        f"$env:PATH = {_powershell_quote(str(powershell_bin_dir))} + ':' + $env:PATH",
        "Add that line to $PROFILE for persistence.",
    ]
    assert path_hint_lines(windows_bin_dir, shell="cmd") == [
        "setlocal DisableDelayedExpansion",
        f'set "PATH={windows_bin_dir};%PATH%"',
        "Use System Properties > Environment Variables for a persistent cmd PATH update,",
        "or run the PowerShell persistence command shown by --shell powershell.",
    ]
    assert path_hint_lines(windows_percent_bin_dir, shell="cmd") == [
        "setlocal DisableDelayedExpansion",
        f'set "PATH={_cmd_escape(str(windows_percent_bin_dir))};%PATH%"',
        "Use System Properties > Environment Variables for a persistent cmd PATH update,",
        "or run the PowerShell persistence command shown by --shell powershell.",
    ]


def test_cmd_shim_escapes_percent_signs_in_repo_path() -> None:
    spaced_repo = Path("/tmp/repo % root")
    shim = _shim_content(spaced_repo, "pf.cmd")
    expected_spaced = _cmd_escape(str(spaced_repo.resolve()) + "\\")
    assert f'set "ROOT={expected_spaced}"' in shim

    percent_repo = Path("/tmp/repo %value% root")
    percent_shim = _shim_content(percent_repo, "pf.cmd")
    expected_percent = _cmd_escape(str(percent_repo.resolve()) + "\\")
    assert f'set "ROOT={expected_percent}"' in percent_shim


def test_cmd_hint_and_shim_disable_delayed_expansion_for_bang_paths() -> None:
    assert path_hint_lines(Path("/tmp/bin ! tools"), shell="cmd")[0] == "setlocal DisableDelayedExpansion"

    shim = _shim_content(Path("/tmp/repo ! root"), "pf.cmd")
    assert "setlocal DisableDelayedExpansion" in shim
    assert 'set "ROOT=' in shim


def test_powershell_shim_uses_literal_root_resolution() -> None:
    repo_root = Path("/tmp/repo [wild]* root")
    shim = _shim_content(repo_root, "pf.ps1")
    expected_root = str(repo_root.resolve()).replace("'", "''")

    assert f"$Root = Resolve-Path -LiteralPath '{expected_root}'" in shim


def test_path_hint_lines_reject_unknown_shell_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported shell hint style: bogus-shell"):
        path_hint_lines(tmp_path / "bin", shell="bogus-shell")


def test_detect_windows_shell_prefers_cmd_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPT", "$P$G")
    assert _detect_windows_shell() == "cmd"

    monkeypatch.delenv("PROMPT", raising=False)
    assert _detect_windows_shell() == "powershell"


def test_detect_shell_prefers_posix_shells_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROMPT", raising=False)
    assert _detect_shell(os_name="nt", shell_value=r"C:\Program Files\Git\bin\bash.exe") == "bash"


def test_detect_shell_recognizes_powershell_from_shell_value() -> None:
    assert _detect_shell(os_name="posix", shell_value="/usr/local/bin/pwsh") == "pwsh"
    assert _detect_shell(os_name="nt", shell_value=r"C:\Program Files\PowerShell\7\pwsh.exe") == "pwsh"
    assert _detect_shell(os_name="nt", shell_value=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe") == "powershell"
    assert _detect_shell(os_name="nt", shell_value=r"C:\Windows\System32\cmd.exe") == "cmd"


def test_main_normalizes_relative_bin_dir_in_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["install_user_commands.py", "--status", "--bin-dir", "tmp-relative-bin"],
    )

    assert main() == 0

    output = capsys.readouterr().out
    expected = str((tmp_path / "tmp-relative-bin").resolve())
    assert f"status for {expected}" in output
    assert expected in output


def test_helper_functions_normalize_relative_bin_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)

    installed = install_user_commands(repo_root=root, bin_dir=Path("tmp-relative-bin"), force=False)
    inspected = inspect_user_commands(repo_root=root, bin_dir=Path("tmp-relative-bin"))
    removed = uninstall_user_commands(repo_root=root, bin_dir=Path("tmp-relative-bin"))

    expected_prefix = (tmp_path / "tmp-relative-bin").resolve()
    assert all(target.parent == expected_prefix for _name, target, _mode in installed)
    assert all(target.parent == expected_prefix for _name, target, _state in inspected)
    assert all(target.parent == expected_prefix for _name, target, _state in removed)


def test_helper_functions_reject_file_bin_dir(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    file_target = tmp_path / "not-a-directory"
    file_target.write_text("foreign", encoding="utf-8")

    with pytest.raises(SystemExit, match="bin directory is not a directory"):
        install_user_commands(repo_root=root, bin_dir=file_target, force=False)

    with pytest.raises(SystemExit, match="bin directory is not a directory"):
        inspect_user_commands(repo_root=root, bin_dir=file_target)

    with pytest.raises(SystemExit, match="bin directory is not a directory"):
        uninstall_user_commands(repo_root=root, bin_dir=file_target)


def test_main_uninstall_does_not_print_path_hints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["install_user_commands.py", "--uninstall", "--bin-dir", "tmp-relative-bin"],
    )

    assert main() == 0

    output = capsys.readouterr().out
    assert "uninstall from" in output
    assert "PATH already includes" not in output
    assert "export PATH=" not in output
    assert "fish_add_path" not in output
    assert "PowerShell persistence" not in output


def test_main_rejects_unknown_shell_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["install_user_commands.py", "--status", "--shell", "bogus-shell"],
    )

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 2
    error = capsys.readouterr().err
    assert "invalid choice" in error
    assert "bogus-shell" in error


def test_main_status_rejects_file_bin_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_target = tmp_path / "not-a-directory"
    file_target.write_text("foreign", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["install_user_commands.py", "--status", "--bin-dir", str(file_target)],
    )

    with pytest.raises(SystemExit, match="bin directory is not a directory"):
        main()


def test_helper_functions_reject_broken_symlink_bin_dir(tmp_path: Path) -> None:
    if os.name == "nt":
        return

    root = Path(__file__).resolve().parents[1]
    link_target = tmp_path / "missing-target"
    link_path = tmp_path / "bin-link"
    link_path.symlink_to(link_target)

    with pytest.raises(SystemExit, match="bin directory is not a directory"):
        install_user_commands(repo_root=root, bin_dir=link_path, force=False)

    with pytest.raises(SystemExit, match="bin directory is not a directory"):
        inspect_user_commands(repo_root=root, bin_dir=link_path)

    with pytest.raises(SystemExit, match="bin directory is not a directory"):
        uninstall_user_commands(repo_root=root, bin_dir=link_path)


def test_main_status_rejects_broken_symlink_bin_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        return

    link_target = tmp_path / "missing-target"
    link_path = tmp_path / "bin-link"
    link_path.symlink_to(link_target)
    monkeypatch.setattr(
        "sys.argv",
        ["install_user_commands.py", "--status", "--bin-dir", str(link_path)],
    )

    with pytest.raises(SystemExit, match="bin directory is not a directory"):
        main()


def test_helper_functions_reject_bin_dir_beneath_file(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    file_target = tmp_path / "not-a-directory"
    file_target.write_text("foreign", encoding="utf-8")
    nested_bin_dir = file_target / "nested-bin"

    with pytest.raises(SystemExit, match="bin directory is not a directory"):
        install_user_commands(repo_root=root, bin_dir=nested_bin_dir, force=False)

    with pytest.raises(SystemExit, match="bin directory is not a directory"):
        inspect_user_commands(repo_root=root, bin_dir=nested_bin_dir)

    with pytest.raises(SystemExit, match="bin directory is not a directory"):
        uninstall_user_commands(repo_root=root, bin_dir=nested_bin_dir)


def test_main_status_rejects_bin_dir_beneath_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_target = tmp_path / "not-a-directory"
    file_target.write_text("foreign", encoding="utf-8")
    nested_bin_dir = file_target / "nested-bin"
    monkeypatch.setattr(
        "sys.argv",
        ["install_user_commands.py", "--status", "--bin-dir", str(nested_bin_dir)],
    )

    with pytest.raises(SystemExit, match="bin directory is not a directory"):
        main()


def test_install_summary_verb_matches_install_modes(tmp_path: Path) -> None:
    target = tmp_path / "bin" / "pf"

    assert _install_summary_verb([("pf", target, "shim")]) == "installed into"
    assert _install_summary_verb([("pf", target, "managed")]) == "already installed in"
    assert _install_summary_verb([
        ("pf", target, "managed"),
        ("pillowfort", target, "shim"),
    ]) == "reconciled in"
    assert _install_summary_verb([("pf", target, "migrated-shim")]) == "reconciled in"
    assert _install_summary_verb([("pf", target, "reinstalled-shim")]) == "reconciled in"


def test_has_managed_targets_only_matches_managed_states(tmp_path: Path) -> None:
    target = tmp_path / "bin" / "pf"

    assert _has_managed_targets([("pf", target, "managed-shim")]) is True
    assert _has_managed_targets([("pf", target, "legacy-managed-copy")]) is True
    assert _has_managed_targets([("pf", target, "legacy-managed-symlink")]) is True
    assert _has_managed_targets([("pf", target, "missing")]) is False
    assert _has_managed_targets([("pf", target, "foreign-file")]) is False


def test_status_summary_describes_empty_managed_and_mixed_states(tmp_path: Path) -> None:
    target = tmp_path / "bin" / "pf"

    assert _status_summary([("pf", target, "missing")]) == "no managed commands installed"
    assert _status_summary([("pf", target, "managed-shim")]) == "managed commands installed"
    assert _status_summary([("pf", target, "legacy-managed-copy")]) == "managed commands installed"
    assert _status_summary([
        ("pf", target, "managed-shim"),
        ("busy", target, "missing"),
    ]) == "partial managed commands installed"
    assert _status_summary([("pf", target, "foreign-file")]) == "unmanaged commands present"
    assert _status_summary([
        ("pf", target, "managed-shim"),
        ("busy", target, "foreign-file"),
    ]) == "mixed command state detected"


def test_main_rerun_reports_already_installed_heading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["install_user_commands.py", "--bin-dir", "tmp-relative-bin"],
    )

    assert main() == 0
    capsys.readouterr()
    assert main() == 0

    output = capsys.readouterr().out
    expected = str((tmp_path / "tmp-relative-bin").resolve())
    assert f"already installed in {expected}" in output


def test_main_force_rerun_reports_reconciled_heading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["install_user_commands.py", "--bin-dir", "tmp-relative-bin"],
    )

    assert main() == 0
    capsys.readouterr()
    monkeypatch.setattr(
        "sys.argv",
        ["install_user_commands.py", "--bin-dir", "tmp-relative-bin", "--force"],
    )
    assert main() == 0

    output = capsys.readouterr().out
    expected = str((tmp_path / "tmp-relative-bin").resolve())
    assert f"reconciled in {expected}" in output


def test_main_status_without_managed_targets_skips_path_hints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["install_user_commands.py", "--status", "--bin-dir", "tmp-relative-bin"],
    )

    assert main() == 0

    output = capsys.readouterr().out
    assert "status for" in output
    assert "no managed commands installed" in output
    assert "export PATH=" not in output
    assert "fish_add_path" not in output
    assert "PowerShell persistence" not in output


def test_main_install_prints_copy_pasteable_path_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(
        "sys.argv",
        ["install_user_commands.py", "--bin-dir", "tmp-relative-bin", "--shell", "zsh"],
    )

    assert main() == 0

    output = capsys.readouterr().out
    expected = str((tmp_path / "tmp-relative-bin").resolve())
    expected_command = path_hint_lines(Path(expected), shell="zsh")[0]
    assert expected_command in output
    assert f"[command-install] {expected_command}" not in output
    assert "[command-install] Add that line to your ~/.zshrc for persistence." in output


def test_main_status_with_managed_targets_reports_managed_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["install_user_commands.py", "--bin-dir", "tmp-relative-bin"],
    )
    assert main() == 0
    capsys.readouterr()

    monkeypatch.setattr(
        "sys.argv",
        ["install_user_commands.py", "--status", "--bin-dir", "tmp-relative-bin"],
    )
    assert main() == 0

    output = capsys.readouterr().out
    assert "managed commands installed" in output


def test_main_status_with_foreign_targets_reports_unmanaged_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bin_dir = tmp_path / "tmp-relative-bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "pf").write_text("foreign", encoding="utf-8")
    (bin_dir / "pillowfort").write_text("foreign", encoding="utf-8")
    (bin_dir / "busy").write_text("foreign", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["install_user_commands.py", "--status", "--bin-dir", "tmp-relative-bin"],
    )

    assert main() == 0

    output = capsys.readouterr().out
    assert "unmanaged commands present" in output
    assert "export PATH=" not in output


def test_main_status_with_legacy_targets_reports_managed_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "tmp-relative-bin"
    bin_dir.mkdir(parents=True)
    for name in ("pf", "pillowfort", "busy"):
        (bin_dir / name).write_bytes((root / name).read_bytes())

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["install_user_commands.py", "--status", "--bin-dir", "tmp-relative-bin"],
    )

    assert main() == 0

    output = capsys.readouterr().out
    assert "managed commands installed" in output


def test_main_status_with_partial_targets_reports_partial_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "tmp-relative-bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "pf").write_text((root / "pf").read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["install_user_commands.py", "--status", "--bin-dir", "tmp-relative-bin"],
    )

    assert main() == 0

    output = capsys.readouterr().out
    assert "partial managed commands installed" in output
