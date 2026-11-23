"""Refuse to build a wheel without the browser app (frontend/ built into src/otui/web).

Editable installs (`uv sync`) are let through: a developer may not have built the app yet, and
`otui serve` says how to.
"""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != "wheel" or version == "editable":
            return
        web = Path(self.root) / "src" / "otui" / "web"
        missing = [f for f in ("index.html", "THIRD-PARTY-NOTICES.txt") if not (web / f).exists()]
        if missing:
            raise RuntimeError(
                f"src/otui/web is missing {', '.join(missing)}: build the browser app first "
                "(cd frontend && npm ci && npm run build)"
            )
