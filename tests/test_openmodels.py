"""The open models beside the demo: downloaded once, hash-checked, cached; and their endpoints."""

import hashlib
import io

import pytest
from fastapi.testclient import TestClient
from test_server import _wait, fake_compiler, fake_runner

from otui import openmodels
from otui.server import create_app

RUN = b"$BATINCLUDE base.dd\n$BATINCLUDE maindrv.mod mod\n"
BASE = b"SET REG / R1 /;\n"


@pytest.fixture
def tiny(tmp_path, monkeypatch):
    """A one-model registry (two files) and a fake GitHub that counts its downloads."""
    monkeypatch.setenv("OTUI_MODELS", str(tmp_path / "models"))
    files = {"m.run": RUN, "base.dd": BASE}
    monkeypatch.setattr(
        openmodels,
        "MODELS",
        {
            "tiny": {
                "label": "TINY",
                "repo": "esma-ie/TINY",
                "commit": "0123456789abcdef",
                "case": "ref",
                "run": "m.run",
                "description": "a tiny model",
                "files": {
                    n: {"bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
                    for n, b in files.items()
                },
            }
        },
    )
    served = {"n": 0, "files": dict(files)}

    def opener(url, timeout):
        served["n"] += 1
        assert url.startswith(
            "https://raw.githubusercontent.com/esma-ie/TINY/0123456789abcdef/dd/ref/"
        )
        return io.BytesIO(served["files"][url.rsplit("/", 1)[1]])

    return opener, served


def test_downloaded_once_then_cached(tiny):
    opener, served = tiny
    paths = openmodels.fetch("tiny", opener)
    assert sorted(p.name for p in paths) == ["base.dd", "m.run"]
    assert served["n"] == 2 and paths[0].parent.name == "tiny-0123456"
    assert openmodels.public()[0]["cached"] is True
    openmodels.fetch("tiny", opener)
    assert served["n"] == 2  # no network the second time


def test_a_changed_file_is_refused(tiny):
    opener, served = tiny
    served["files"]["base.dd"] = b"SET REG / R2 /;\n"
    with pytest.raises(openmodels.OpenModelError, match="not the expected file"):
        openmodels.fetch("tiny", opener)
    assert not (openmodels.folder("tiny") / "base.dd").exists()  # nothing half-cached


def test_unknown_model():
    with pytest.raises(openmodels.OpenModelError):
        openmodels.fetch("nope")


def test_shipped_models_are_pinned():
    assert list(openmodels.MODELS) == ["otnyc", "otcgen", "otus9r"]
    for m in openmodels.MODELS.values():
        assert len(m["commit"]) == 40 and m["run"] in m["files"]
        assert all(len(f["sha256"]) == 64 for f in m["files"].values())


def test_endpoints(tiny, tmp_path, monkeypatch):
    opener, _ = tiny
    real = openmodels.fetch
    monkeypatch.setattr(openmodels, "fetch", lambda key: real(key, opener))
    app = create_app(compiler=fake_compiler, runner=fake_runner, root=tmp_path / "ws")
    with TestClient(app) as c:
        assert [m["id"] for m in c.get("/api/open-models").json()] == ["tiny"]
        m = c.post("/api/models/open/tiny").json()
        assert m["run"] == "m.run" and sorted(m["files"]) == ["base.dd", "m.run"]
        assert _wait(c, m["id"])["status"] == "ready"
        assert c.post("/api/models/open/nope").status_code == 404
