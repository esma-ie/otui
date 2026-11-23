"""otui's local settings: where the optional solvers live on this computer.

``~/.otui/config.toml`` (``OTUI_CONFIG`` overrides the path), all optional::

    [gams]                      # CPLEX through GAMS TIMES (a local GAMS licence)
    exe = 'C:\\GAMS\\54\\gams.exe'   # default: gams on the PATH
    times_source = 'C:\\path\\to\\TIMES_model'   # the TIMES source code (etsap-TIMES/TIMES_model)

    [cuopt]                     # cuOpt on an NVIDIA GPU, through TIMES.jl in WSL2 (Windows)
    wsl_distro = "Ubuntu-24.04"
    project = "~/git/TIMESjl/scripts/cuopt"   # a Julia project with TIMES, JuMP, JSON, cuOpt
    env = "~/.config/timesjl/cuopt.env"       # sourced first (LD_LIBRARY_PATH for libcuopt)

The paths are machine-specific, so they live here and never in the repository.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from functools import cache
from pathlib import Path
from typing import Any


def path() -> Path:
    return Path(os.environ.get("OTUI_CONFIG") or Path.home() / ".otui" / "config.toml")


def load() -> dict[str, Any]:
    p = path()
    if not p.exists():
        return {}
    with p.open("rb") as f:
        return tomllib.load(f)


def gams() -> tuple[dict[str, str] | None, str]:
    """(GAMS settings, "") if CPLEX through GAMS can run here, else (None, why not)."""
    g = load().get("gams", {})
    exe = g.get("exe") or shutil.which("gams")
    if not exe or not Path(exe).exists():
        return None, "GAMS not found: set [gams] exe in ~/.otui/config.toml"
    src = g.get("times_source")
    if not src or not (Path(src) / "maindrv.mod").exists():
        return None, "the TIMES source is not set: [gams] times_source in ~/.otui/config.toml"
    return {"exe": str(exe), "times_source": str(src)}, ""


@cache
def _wsl_has(distro: str, file: str) -> bool:
    try:
        r = subprocess.run(
            ["wsl", "-d", distro, "--", "bash", "-lc", f"test -e {file}"],
            capture_output=True,
            timeout=60,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def cuopt() -> tuple[dict[str, str] | None, str]:
    """(cuOpt settings, "") if the WSL cuOpt worker can run here, else (None, why not)."""
    c = load().get("cuopt")
    if not c:
        return (
            None,
            "cuOpt is not set up: [cuopt] in ~/.otui/config.toml (needs an NVIDIA GPU, WSL2)",
        )
    distro = c.get("wsl_distro", "Ubuntu-24.04")
    project = c.get("project", "~/git/TIMESjl/scripts/cuopt")
    env = c.get("env", "")
    if not shutil.which("wsl"):
        return None, "WSL is not available"
    if not _wsl_has(distro, f"{project}/Project.toml") or (env and not _wsl_has(distro, env)):
        return None, f"the cuOpt project or its environment file is missing in WSL ({distro})"
    return {"distro": distro, "project": project, "env": env}, ""
