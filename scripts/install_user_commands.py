from __future__ import annotations

import argparse
import os
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


def _install_one(source: Path, target: Path, *, force: bool) -> str:
    if target.exists() or target.is_symlink():
        if not force:
            raise SystemExit(f"target already exists: {target} (use --force to replace it)")
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()

    target.parent.mkdir(parents=True, exist_ok=True)

    if os.name != "nt":
        try:
            target.symlink_to(source)
            return "symlink"
        except OSError:
            pass

    shutil.copy2(source, target)
    return "copy"


def install_user_commands(*, repo_root: Path, bin_dir: Path, force: bool) -> list[tuple[str, Path, str]]:
    installed: list[tuple[str, Path, str]] = []
    for name in _public_commands():
        source = repo_root / name
        if not source.is_file():
            raise SystemExit(f"missing public command wrapper: {source}")
        target = bin_dir / name
        mode = _install_one(source, target, force=force)
        installed.append((name, target, mode))
    return installed


def inspect_user_commands(*, repo_root: Path, bin_dir: Path) -> list[tuple[str, Path, str]]:
    observed: list[tuple[str, Path, str]] = []
    for name in _public_commands():
        source = repo_root / name
        target = bin_dir / name
        if target.is_symlink():
            try:
                state = "managed-symlink" if target.resolve() == source.resolve() else "foreign-symlink"
            except OSError:
                state = "broken-symlink"
        elif target.is_file():
            try:
                state = "managed-copy" if target.read_bytes() == source.read_bytes() else "foreign-file"
            except OSError:
                state = "foreign-file"
        else:
            state = "missing"
        observed.append((name, target, state))
    return observed


def uninstall_user_commands(*, repo_root: Path, bin_dir: Path) -> list[tuple[str, Path, str]]:
    removed: list[tuple[str, Path, str]] = []
    for name, target, state in inspect_user_commands(repo_root=repo_root, bin_dir=bin_dir):
        if state in {"managed-symlink", "managed-copy"}:
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


def _print_path_hint(bin_dir: Path) -> None:
    if _path_contains(bin_dir):
        print(f"[command-install] PATH already includes {bin_dir}")
        return
    if os.name == "nt":
        print(f"[command-install] Add {bin_dir} to your user PATH to run pf / pillowfort / busy from any shell.")
        return
    print(f'[command-install] Add this to your shell profile: export PATH="{bin_dir}:$PATH"')


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
        _print_path_hint(bin_dir)
        return 0

    if args.uninstall:
        print(f"[command-install] uninstall from {bin_dir}")
        for name, target, state in uninstall_user_commands(repo_root=repo_root, bin_dir=bin_dir):
            print(f"[command-install] {name} -> {target} ({state})")
        _print_path_hint(bin_dir)
        return 0

    installed = install_user_commands(repo_root=repo_root, bin_dir=bin_dir, force=args.force)
    print(f"[command-install] installed into {bin_dir}")
    for name, target, mode in installed:
        print(f"[command-install] {name} -> {target} ({mode})")
    _print_path_hint(bin_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
