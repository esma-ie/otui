"""TIMES-NZ (EECA, MIT) through otui, read from a folder on this computer.

TIMES-NZ is a national model, much larger than demo12. otui does not download or publish its
files, so it has no "Try" button. This test reads the DD export TIMES.jl's reference suite uses
(TIMES-NZ 3.0.8, EECA main 8165e1d, exported 28 Oct 2026) from the folder named by
``OTUI_TIMES_NZ`` (the one with ``dd/manifest.json``), and checks it by the same SHA-256 as
TIMES.jl's suite:

    OTUI_TIMES_NZ=path/to/TIMES-NZ uv run pytest -m local -s

Add ``OTUI_TIMES_NZ_SOLVE=1`` to solve it with TIMES.jl + HiGHS as well (about 35 minutes on a
48-core computer). Without ``OTUI_TIMES_NZ`` the test is skipped, as in CI.
"""

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.julia, pytest.mark.local]

ROOT = os.environ.get("OTUI_TIMES_NZ")
if not ROOT:
    pytest.skip("OTUI_TIMES_NZ is not set (TIMES-NZ is read locally only)", allow_module_level=True)
if shutil.which("julia") is None:
    pytest.skip("Julia is not installed", allow_module_level=True)

# SHA-256 of dd/manifest.json, as pinned in TIMES.jl's reference/suite.toml (times_nz_dd)
MANIFEST_SHA256 = "a4b7514e65143c5aebf4dcf48b2ca0e98076545bd4f1b1516d2785fe7e081b45"
CASE = "steady-v308"
RUN = f"{CASE}.run"
# GAMS TIMES 4.9.7 + CPLEX on TIMES-NZ-steady-v308 (TIMES.jl's reference, GAMS 54.6.1, REDUCE
# off). REDUCE does not change the optimum: TIMES.jl's own as-run solve matches GAMS's as-run
# objective at 2e-10.
OBJECTIVE = 1333686.0529313223
COMPILE_LIMIT = 900  # seconds: a cold TIMES.jl worker, then the compile and the load
SOLVE_LIMIT = 2 * 3600  # seconds: HiGHS took about 26 minutes on 48 cores


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _manifest_files(manifest: dict, case: str) -> dict[str, dict]:
    """The case's files and their hashes, in the dd/manifest.json layouts of the open models."""
    scenarios = manifest.get("scenarios")
    if isinstance(scenarios, dict) and case in scenarios:
        return scenarios[case]["files"]
    files = manifest.get("files", {})
    prefix = f"{case}/"
    picked = {k[len(prefix) :]: v for k, v in files.items() if k.startswith(prefix)}
    if picked:
        return picked
    pytest.fail(f"dd/manifest.json lists no files for {case} (keys: {sorted(manifest)})")


@pytest.fixture(scope="module")
def case_files() -> list[Path]:
    root = Path(ROOT)
    manifest_path = root / "dd" / "manifest.json"
    assert manifest_path.is_file(), f"no dd/manifest.json in {root}"
    assert _sha256(manifest_path) == MANIFEST_SHA256, (
        "dd/manifest.json is not the TIMES-NZ 3.0.8 export TIMES.jl's suite pins"
    )
    files = _manifest_files(json.loads(manifest_path.read_text(encoding="utf-8")), CASE)
    d = root / "dd" / CASE
    for name, f in files.items():
        assert _sha256(d / name) == f["sha256"], f"{CASE}/{name} differs from dd/manifest.json"
    assert RUN in files
    return [d / name for name in sorted(files)]


def _wait(c, mid: str, key: str, busy: tuple[str, ...], deadline: float) -> dict:
    while True:
        m = c.get(f"/api/models/{mid}").json()
        state = m[key]["status"] if key == "solve" else m["status"]
        if state not in busy:
            return m
        assert time.monotonic() < deadline, f"{key} still {state}"
        time.sleep(1)


def test_times_nz_end_to_end(case_files, tmp_path):
    """Drop TIMES-NZ's run and DD files on the API, as a folder drop sends them; browse it."""
    from fastapi.testclient import TestClient

    from otui.server import create_app

    with TestClient(create_app(root=tmp_path / "ws", warm=False)) as c:
        t0 = time.monotonic()
        while c.get("/api/health").json()["engine"] not in ("ready", "error"):
            assert time.monotonic() - t0 < COMPILE_LIMIT
            time.sleep(0.5)
        assert c.get("/api/health").json()["engine"] == "ready"

        t1 = time.monotonic()
        files = [("files", (p.name, p.read_bytes(), "text/plain")) for p in case_files]
        mid = c.post("/api/models", files=files).json()["id"]
        m = _wait(c, mid, "model", ("compiling", "loading"), t1 + COMPILE_LIMIT)
        assert m["status"] == "ready", m["error"]
        assert m["run"] == RUN
        print(f"\nTIMES-NZ: compiled and loaded in {time.monotonic() - t1:.1f} s")

        counts = m["summary"]["counts"]
        assert counts["PRC"] > 100 and counts["COM"] > 100, counts
        r = c.get(f"/api/models/{mid}/symbols/TOP").json()
        assert r["total"] > counts["PRC"]  # every process has an input or an output

        g = c.get(f"/api/models/{mid}/graph").json()
        assert len(g["nodes"]) > 100 and len(g["links"]) > len(g["nodes"]) // 2
        focus = next(n["id"] for n in g["nodes"] if n["id"].startswith("p:"))
        ego = c.get(f"/api/models/{mid}/graph", params={"focus": focus}).json()
        assert 1 < len(ego["nodes"]) < len(g["nodes"])

        assert c.get(f"/api/models/{mid}/qc").status_code == 200
        x = c.get(f"/api/models/{mid}/export.xlsx")
        assert x.status_code == 200 and x.content[:2] == b"PK"
        print(f"TIMES-NZ: browsed, graphed and exported in {time.monotonic() - t1:.1f} s")

        if os.environ.get("OTUI_TIMES_NZ_SOLVE") != "1":
            return
        t2 = time.monotonic()
        assert c.post(f"/api/models/{mid}/solve").status_code == 200
        m = _wait(c, mid, "solve", ("running",), t2 + SOLVE_LIMIT)
        run = m["solve"]
        assert run["status"] == "done", run["error"]
        res = run["result"]
        assert res["status"] == "optimal"
        assert abs(res["objective"] - OBJECTIVE) <= 1e-6 * OBJECTIVE, res["objective"]
        print(f"TIMES-NZ: solved with HiGHS in {time.monotonic() - t2:.0f} s")
