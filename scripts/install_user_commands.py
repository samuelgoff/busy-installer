from __future__ import annotations

import argparse
import os
import shlex
import shutil
from pathlib import Path


POSIX_COMMANDS = ("pf", "pillowfort", "busy")
WINDOWS_COMMANDS = ("pf.cmd", "pillowfort.cmd", "busy.cmd", "pf.ps1", "pillowfort.ps1", "busy.ps1")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_bin_dir() -> Path:
    home = Path.home()
    if os.name == "nt":
        return home / "bin"
    return home / ".local" / "bin"


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
            '& $VenvPython -m busy_installer.app @args\n'
        )

    root = shlex.quote(str(normalized_root))
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
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
    target.write_bytes(_managed_wrapper_bytes(repo_root, source.name))
    if os.name != "nt":
        target.chmod(0o755)
    return "shim"


def install_user_commands(*, repo_root: Path, bin_dir: Path, force: bool) -> list[tuple[str, Path, str]]:
    planned: list[tuple[str, Path, Path, str]] = []
    for name in _public_commands():
        source = repo_root / name
        if not source.is_file():
            raise SystemExit(f"missing public command wrapper: {source}")
        target = bin_dir / name
        state = _target_state(repo_root, source, target)
        if state in {"foreign-file", "foreign-symlink", "broken-symlink", "foreign-directory"}:
            raise SystemExit(f"refusing to replace non-managed target: {target} ({state})")
        if state != "missing" and not force:
            raise SystemExit(f"target already exists: {target} (use --force to replace it)")
        planned.append((name, source, target, state))

    installed: list[tuple[str, Path, str]] = []
    for name, source, target, state in planned:
        if state != "missing":
            target.unlink()
        mode = _install_one(repo_root, source, target, force=force)
        installed.append((name, target, mode))
    return installed


def inspect_user_commands(*, repo_root: Path, bin_dir: Path) -> list[tuple[str, Path, str]]:
    observed: list[tuple[str, Path, str]] = []
    for name in _public_commands():
        source = repo_root / name
        target = bin_dir / name
        state = _target_state(repo_root, source, target)
        observed.append((name, target, state))
    return observed


def uninstall_user_commands(*, repo_root: Path, bin_dir: Path) -> list[tuple[str, Path, str]]:
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
        return "powershell"
    raw_shell = Path(os.environ.get("SHELL", "")).name.lower()
    if raw_shell in {"bash", "zsh", "fish"}:
        return raw_shell
    return "sh"


def path_hint_lines(bin_dir: Path, shell: str = "auto") -> list[str]:
    normalized_shell = _detect_shell() if shell == "auto" else shell.lower()
    path_value = str(bin_dir)
    if normalized_shell in {"sh", "bash", "zsh"}:
        return [
            f'export PATH="{path_value}:$PATH"',
            f"Add that line to your {'~/.zshrc' if normalized_shell == 'zsh' else 'shell profile'} for persistence.",
        ]
    if normalized_shell == "fish":
        return [
            f'fish_add_path "{path_value}"',
            "Add that to config.fish for persistence.",
        ]
    if normalized_shell in {"powershell", "pwsh"}:
        return [
            f'$env:Path = "{path_value};" + $env:Path',
            '$userPath = [Environment]::GetEnvironmentVariable("Path", "User")',
            f'if (($userPath -split ";") -notcontains "{path_value}") ' + "{ [Environment]::SetEnvironmentVariable(\"Path\", (\"" + path_value + ';\" + $userPath).TrimEnd(\';\'), \"User\") }',
        ]
    if normalized_shell == "cmd":
        return [
            f"set PATH={path_value};%PATH%",
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
    bin_dir = args.bin_dir.expanduser()

    if args.status:
        print(f"[command-install] status for {bin_dir}")
        for name, target, state in inspect_user_commands(repo_root=repo_root, bin_dir=bin_dir):
            print(f"[command-install] {name} -> {target} ({state})")
        _print_path_hint(bin_dir, args.shell)
        return 0

    if args.uninstall:
        print(f"[command-install] uninstall from {bin_dir}")
        for name, target, state in uninstall_user_commands(repo_root=repo_root, bin_dir=bin_dir):
            print(f"[command-install] {name} -> {target} ({state})")
        _print_path_hint(bin_dir, args.shell)
        return 0

    installed = install_user_commands(repo_root=repo_root, bin_dir=bin_dir, force=args.force)
    print(f"[command-install] installed into {bin_dir}")
    for name, target, mode in installed:
        print(f"[command-install] {name} -> {target} ({mode})")
    _print_path_hint(bin_dir, args.shell)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
