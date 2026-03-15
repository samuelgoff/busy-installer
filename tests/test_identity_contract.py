from __future__ import annotations

import json
import tomllib
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_public_entrypoints_and_plugin_identity_stay_aligned() -> None:
    root = _repo_root()
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    plugin_manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    ui_manifest = json.loads((root / "ui" / "manifest.json").read_text(encoding="utf-8"))

    scripts = pyproject["project"]["scripts"]
    assert scripts["pf"] == "busy_installer.app:main"
    assert scripts["pillowfort"] == "busy_installer.app:main"
    assert scripts["busy"] == "busy_installer.app:main"
    assert scripts["pillowfort-installer"] == "busy_installer.cli:main"

    assert plugin_manifest["name"] == "busy-installer"
    assert plugin_manifest["entry_point"] == "busy_installer.cli"
    assert ui_manifest["plugin_identity"] == {
        "id": plugin_manifest["name"],
        "aliases": ["busy-installer", "installer"],
    }
    assert ui_manifest["required_api"] == ["/api/plugins/{plugin_id}/ui/debug"]

    sections = {section["id"]: section for section in ui_manifest["sections"]}
    debug_action = sections["diagnostics"]["actions"][0]
    assert debug_action["id"] == "debug"
    assert debug_action["method"] == "GET"
    assert debug_action["entry_point"] == "actions:handle_debug"

    for command_name in ("pf", "pillowfort", "busy"):
        assert (root / command_name).is_file()
        assert (root / f"{command_name}.cmd").is_file()
        assert (root / f"{command_name}.ps1").is_file()
