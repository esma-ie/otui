"""Open TIMES models to try with one click, beside the demo model: OTNYC and OTCGEN (ESMA,
CC BY-SA 4.0), at the commits otui is tested against.

Their run and DD files are downloaded from GitHub on first use, checked against the SHA-256 in
``open_models.json`` (from each repository's ``dd/manifest.json``), and kept in
``~/.otui/models/<name>-<commit>/``, so later clicks need no network.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

MODELS: dict[str, dict[str, Any]] = json.loads(
    (Path(__file__).parent / "open_models.json").read_text(encoding="utf-8")
)
RAW = "https://raw.githubusercontent.com/{repo}/{commit}/dd/{case}/{name}"
TIMEOUT = 120  # seconds per file


class OpenModelError(RuntimeError):
    pass


def cache_dir() -> Path:
    d = os.environ.get("OTUI_MODELS")
    return Path(d) if d else Path.home() / ".otui" / "models"


def folder(key: str) -> Path:
    m = MODELS[key]
    return cache_dir() / f"{key}-{m['commit'][:7]}"


def _ok(p: Path, sha256: str) -> bool:
    return p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == sha256


def fetch(key: str, opener=urllib.request.urlopen) -> list[Path]:
    """The model's run and DD files, downloaded if not cached yet, each checked by its hash."""
    if key not in MODELS:
        raise OpenModelError(f"no open model {key}: one of {', '.join(MODELS)}")
    m = MODELS[key]
    d = folder(key)
    d.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, f in m["files"].items():
        p = d / name
        if not _ok(p, f["sha256"]):
            url = RAW.format(repo=m["repo"], commit=m["commit"], case=m["case"], name=name)
            try:
                with opener(url, timeout=TIMEOUT) as r:
                    data = r.read()
            except OSError as e:
                raise OpenModelError(
                    f"could not download {m['label']}'s {name} from GitHub: {e}"
                ) from e
            if hashlib.sha256(data).hexdigest() != f["sha256"]:
                raise OpenModelError(f"{m['label']}'s {name} from GitHub is not the expected file")
            # write then rename, so a broken download never looks cached
            with tempfile.NamedTemporaryFile(dir=d, delete=False) as t:
                t.write(data)
            Path(t.name).replace(p)
        paths.append(p)
    return paths


def public() -> list[dict[str, Any]]:
    """The open models for the landing page: name, label, description, download size, cached."""
    return [
        {
            "id": k,
            "label": m["label"],
            "description": m["description"],
            "bytes": sum(f["bytes"] for f in m["files"].values()),
            "cached": all(_ok(folder(k) / n, f["sha256"]) for n, f in m["files"].items()),
            "source": f"https://github.com/{m['repo']}/tree/{m['commit'][:7]}/dd/{m['case']}",
        }
        for k, m in MODELS.items()
    ]
