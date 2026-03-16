from __future__ import annotations

import argparse
import os
import shlex
import shutil
import tempfile
from pathlib import Path


POSIX_COMMANDS = ("pf", "pillowfort", "busy")
WINDOWS_COMMANDS = ("pf.cmd", "pillowfort.cmd", "busy.cmd", "pf.ps1", "pillowfort.ps1", "busy.ps1")
SUPPORTED_SHELLS = ("auto", "bash", "zsh", "fish", "powershell", "pwsh", "cmd", "sh")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_bin_dir() -> Path:
    home = Path.home()
    if os.name == "nt":
        return home / "bin"
    return home / ".local" / "bin"


def _normalize_bin_dir(bin_dir: Path) -> Path:
    expanded = bin_dir.expanduser()
    candidate = expanded
    while not (candidate.exists() or candidate.is_symlink()):
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    if (candidate.exists() or candidate.is_symlink()) and not candidate.is_dir():
        raise SystemExit(f"bin directory is not a directory: {candidate.absolute()}")
    normalized = expanded.resolve()
    return normalized


def _public_commands() -> tuple[str, ...]:
    if os.name == "nt":
        return WINDOWS_COMMANDS
    return POSIX_COMMANDS


def _shim_content(repo_root: Path, name: str) -> str:
    normalized_root = repo_root.resolve()
    if name.endswith(".cmd"):
        root = str(normalized_root) + "\\"
        return (
            "@echo off\n"
            "setlocal\n\n"
            f'set "ROOT={root}"\n'
            'set "BOOTSTRAP=%ROOT%scripts\\bootstrap_env.py"\n'
            'set "VENV_PYTHON=%ROOT%.venv\\Scripts\\python.exe"\n\n'
            "where python3 >nul 2>nul\n"
            "if %errorlevel%==0 (\n"
            '  set "PYTHON=python3"\n'
            ") else (\n"
            "  where python >nul 2>nul\n"
            "  if %errorlevel%==0 (\n"
            '    set "PYTHON=python"\n'
            "  ) else (\n"
            "    echo Python 3 not found. Install Python 3.10+ and rerun. 1>&2\n"
            "    exit /b 1\n"
            "  )\n"
            ")\n\n"
            "%PYTHON% \"%BOOTSTRAP%\" >nul\n"
            "if errorlevel 1 exit /b %errorlevel%\n"
            "\"%VENV_PYTHON%\" -m busy_installer.app %*\n"
        )
    if name.endswith(".ps1"):
        root = str(normalized_root).replace("'", "''")
        return (
            '$ErrorActionPreference = "Stop"\n\n'
            f"$Root = Resolve-Path '{root}'\n"
            '$VenvPython = Join-Path $Root.Path ".venv\\Scripts\\python.exe"\n'
            '$Bootstrap = Join-Path $Root.Path "scripts\\bootstrap_env.py"\n\n'
            '$python = (Get-Command python3 -ErrorAction SilentlyContinue).Source\n'
            'if (-not $python) {\n'
            '  $python = (Get-Command python -ErrorAction SilentlyContinue).Source\n'
            '}\n\n'
            'if (-not $python) {\n'
            '  throw "Python 3 not found. Install Python 3.10+ and rerun."\n'
            '}\n\n'
            '& $python $Bootstrap\n'
            'if ($LASTEXITCODE -ne 0) {\n'
            '  exit $LASTEXITCODE\n'
            '}\n'
            '& $VenvPython -m busy_installer.app @args\n'
            'if ($LASTEXITCODE -ne 0) {\n'
            '  exit $LASTEXITCODE\n'
            '}\n'
        )

    root = shlex.quote(str(normalized_root))
    return (
        "#!/bin/sh\n"
        "set -eu\n\n"
        f"ROOT_DIR={root}\n\n"
        'if command -v python3 >/dev/null 2>&1; then\n'
        '  PYTHON=python3\n'
        'elif command -v python >/dev/null 2>&1; then\n'
        '  PYTHON=python\n'
        "else\n"
        '  echo "Python 3 not found. Install Python 3.10+ and rerun." >&2\n'
        '  exit 1\n'
        "fi\n\n"
        '"${PYTHON}" "${ROOT_DIR}/scripts/bootstrap_env.py"\n'
        'exec "${ROOT_DIR}/.venv/bin/python" -m busy_installer.app "$@"\n'
    )


def _legacy_wrapper_content(repo_root: Path, name: str) -> bytes:
    return (repo_root / name).read_bytes()


def _managed_wrapper_bytes(repo_root: Path, name: str) -> bytes:
    return _shim_content(repo_root, name).encode("utf-8")


def _target_state(repo_root: Path, source: Path, target: Path) -> str:
    if target.is_symlink():
        try:
            return "legacy-managed-symlink" if target.resolve() == source.resolve() else "foreign-symlink"
        except OSError:
            return "broken-symlink"
    if target.is_file():
        try:
            payload = target.read_bytes()
            if payload == _managed_wrapper_bytes(repo_root, source.name):
                return "managed-shim"
            if payload == _legacy_wrapper_content(repo_root, source.name):
                return "legacy-managed-copy"
        except OSError:
            pass
        return "foreign-file"
    if target.exists():
        return "foreign-directory"
    return "missing"


def _install_one(repo_root: Path, source: Path, target: Path, *, force: bool) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic_bytes(target, _managed_wrapper_bytes(repo_root, source.name), executable=(os.name != "nt"))
    return "shim"


def _write_atomic_bytes(target: Path, payload: bytes, *, executable: bool) -> None:
    fd, temp_path = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temp_target = Path(temp_path)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        if executable:
            temp_target.chmod(0o755)
        temp_target.replace(target)
    except Exception:
        temp_target.unlink(missing_ok=True)
        raise


def install_user_commands(*, repo_root: Path, bin_dir: Path, force: bool) -> list[tuple[str, Path, str]]:
    bin_dir = _normalize_bin_dir(bin_dir)
    planned: list[tuple[str, Path, Path, str, str]] = []
    for name in _public_commands():
        source = repo_root / name
        if not source.is_file():
            raise SystemExit(f"missing public command wrapper: {source}")
        target = bin_dir / name
        state = _target_state(repo_root, source, target)
        if state in {"foreign-file", "foreign-symlink", "broken-symlink", "foreign-directory"}:
            raise SystemExit(f"refusing to replace non-managed target: {target} ({state})")
        if state == "managed-shim":
            if force:
                planned.append((name, source, target, state, "reinstalled-shim"))
            else:
                planned.append((name, source, target, state, "managed"))
            continue
        if state in {"legacy-managed-symlink", "legacy-managed-copy"}:
            planned.append((name, source, target, state, "migrated-shim"))
            continue
        if state != "missing" and not force:
            raise SystemExit(f"target already exists: {target} (use --force to replace it)")
        planned.append((name, source, target, state, "shim"))

    installed: list[tuple[str, Path, str]] = []
    for name, source, target, state, action in planned:
        if action == "managed":
            installed.append((name, target, action))
            continue
        if state != "missing":
            target.unlink()
        mode = _install_one(repo_root, source, target, force=force)
        if action in {"migrated-shim", "reinstalled-shim"}:
            mode = action
        installed.append((name, target, mode))
    return installed


def inspect_user_commands(*, repo_root: Path, bin_dir: Path) -> list[tuple[str, Path, str]]:
    bin_dir = _normalize_bin_dir(bin_dir)
    observed: list[tuple[str, Path, str]] = []
    for name in _public_commands():
        source = repo_root / name
        target = bin_dir / name
        state = _target_state(repo_root, source, target)
        observed.append((name, target, state))
    return observed


def uninstall_user_commands(*, repo_root: Path, bin_dir: Path) -> list[tuple[str, Path, str]]:
    bin_dir = _normalize_bin_dir(bin_dir)
    removed: list[tuple[str, Path, str]] = []
    for name, target, state in inspect_user_commands(repo_root=repo_root, bin_dir=bin_dir):
        if state in {"managed-shim", "legacy-managed-symlink", "legacy-managed-copy"}:
            target.unlink()
            removed.append((name, target, "removed"))
            continue
        removed.append((name, target, "skipped"))
    return removed


def _path_contains(bin_dir: Path) -> bool:
    entries = [Path(entry).expanduser() for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    target = bin_dir.expanduser().resolve()
    for entry in entries:
        try:
            if entry.resolve() == target:
                return True
        except OSError:
            if entry == target:
                return True
    return False


def _detect_shell() -> str:
    if os.name == "nt":
        return _detect_windows_shell()
    raw_shell = Path(os.environ.get("SHELL", "")).name.lower()
    if raw_shell in {"bash", "zsh", "fish"}:
        return raw_shell
    return "sh"


def _detect_windows_shell() -> str:
    return "cmd" if os.environ.get("PROMPT", "").strip() else "powershell"


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def path_hint_lines(bin_dir: Path, shell: str = "auto") -> list[str]:
    normalized_shell = _detect_shell() if shell == "auto" else shell.lower()
    if normalized_shell not in SUPPORTED_SHELLS[1:]:
        raise ValueError(f"unsupported shell hint style: {shell}")
    path_value = str(bin_dir)
    if normalized_shell in {"sh", "bash", "zsh"}:
        quoted_path = shlex.quote(path_value)
        return [
            f'export PATH={quoted_path}:"$PATH"',
            f"Add that line to your {'~/.zshrc' if normalized_shell == 'zsh' else 'shell profile'} for persistence.",
        ]
    if normalized_shell == "fish":
        quoted_path = shlex.quote(path_value)
        return [
            f"fish_add_path {quoted_path}",
            "Add that to config.fish for persistence.",
        ]
    if normalized_shell in {"powershell", "pwsh"}:
        quoted_path = _powershell_quote(path_value)
        return [
            f"$env:Path = {quoted_path} + ';' + $env:Path",
            '$userPath = [Environment]::GetEnvironmentVariable("Path", "User")',
            f"if (($userPath -split ';') -notcontains {quoted_path}) " + "{ [Environment]::SetEnvironmentVariable(\"Path\", (" + quoted_path + " + ';' + $userPath).TrimEnd(';'), \"User\") }",
        ]
    if normalized_shell == "cmd":
        return [
            f'set "PATH={path_value};%PATH%"',
            "Use System Properties > Environment Variables for a persistent cmd PATH update,",
            "or run the PowerShell persistence command shown by --shell powershell.",
        ]
    return [f"Add {path_value} to your PATH to run pf / pillowfort / busy from any shell."]


def _print_path_hint(bin_dir: Path, shell: str) -> None:
    if _path_contains(bin_dir):
        print(f"[command-install] PATH already includes {bin_dir}")
        return
    for line in path_hint_lines(bin_dir, shell=shell):
        print(f"[command-install] {line}")


def _has_managed_targets(observed: list[tuple[str, Path, str]]) -> bool:
    managed_states = {"managed-shim", "legacy-managed-symlink", "legacy-managed-copy"}
    return any(state in managed_states for _name, _target, state in observed)


def _status_summary(observed: list[tuple[str, Path, str]]) -> str:
    states = {state for _name, _target, state in observed}
    managed_states = {"managed-shim", "legacy-managed-symlink", "legacy-managed-copy"}
    if states == {"missing"}:
        return "no managed commands installed"
    if states <= managed_states:
        return "managed commands installed"
    if _has_managed_targets(observed) and states <= managed_states | {"missing"}:
        return "partial managed commands installed"
    if states and states <= {"foreign-file", "foreign-symlink", "broken-symlink", "foreign-directory"}:
        return "unmanaged commands present"
    if _has_managed_targets(observed):
        return "mixed command state detected"
    if any(state != "missing" for state in states):
        return "unmanaged commands present"
    return "no managed commands installed"


def _install_summary_verb(installed: list[tuple[str, Path, str]]) -> str:
    modes = {mode for _name, _target, mode in installed}
    if modes == {"managed"}:
        return "already installed in"
    if "migrated-shim" in modes or "managed" in modes or "reinstalled-shim" in modes:
        return "reconciled in"
    return "installed into"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install pf / pillowfort / busy into a user bin directory")
    parser.add_argument(
        "--bin-dir",
        type=Path,
        default=_default_bin_dir(),
        help="target directory for the public command wrappers",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace existing targets in the bin directory",
    )
    parser.add_argument(
        "--shell",
        default="auto",
        choices=SUPPORTED_SHELLS,
        help="shell hint style for PATH guidance (auto, bash, zsh, fish, powershell, pwsh, cmd, sh)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--status",
        action="store_true",
        help="report whether the user bin directory already contains managed command wrappers",
    )
    mode.add_argument(
        "--uninstall",
        action="store_true",
        help="remove managed command wrappers from the target user bin directory",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo_root = _repo_root()
    bin_dir = _normalize_bin_dir(args.bin_dir)

    if args.status:
        observed = inspect_user_commands(repo_root=repo_root, bin_dir=bin_dir)
        print(f"[command-install] status for {bin_dir}")
        print(f"[command-install] {_status_summary(observed)}")
        for name, target, state in observed:
            print(f"[command-install] {name} -> {target} ({state})")
        if _has_managed_targets(observed):
            _print_path_hint(bin_dir, args.shell)
        return 0

    if args.uninstall:
        print(f"[command-install] uninstall from {bin_dir}")
        for name, target, state in uninstall_user_commands(repo_root=repo_root, bin_dir=bin_dir):
            print(f"[command-install] {name} -> {target} ({state})")
        return 0

    installed = install_user_commands(repo_root=repo_root, bin_dir=bin_dir, force=args.force)
    print(f"[command-install] {_install_summary_verb(installed)} {bin_dir}")
    for name, target, mode in installed:
        print(f"[command-install] {name} -> {target} ({mode})")
    _print_path_hint(bin_dir, args.shell)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
