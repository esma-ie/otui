"""otui's local web server (FastAPI).

Local mode: one user, no login, bound to 127.0.0.1. In a GitHub codespace
(.devcontainer/) the same server runs in the codespace, and GitHub forwards its
port privately to the codespace's owner; /api/health says which. A dropped model is saved
to the workspace, compiled by TIMES.jl through the shared Julia worker, and
loaded into DuckDB (``store.load_dump``). The Julia worker starts and warms up
(compiles demo12 once) when the server starts, so a user's first model does
not pay Julia's compile time.

API (JSON):
  GET  /api/health                       worker state and versions
  POST /api/models                       upload files (multipart, field "files"); optional "run"
  POST /api/models/demo                  load TIMES_Demo demo12 (CC BY 4.0, IEA-ETSAP)
  GET  /api/open-models                  OTNYC and OTCGEN: size, cached or not
  POST /api/models/open/{key}            load one (downloaded from GitHub on first use)
  GET  /api/models                       models in this session
  GET  /api/models/{id}                  status, timings, summary
  GET  /api/models/{id}/symbols          the catalogue: name, kind, headers, records, description
  GET  /api/models/{id}/symbols/{name}   rows: ?offset=&limit=&q=&where=COL:VALUE (repeatable)
  GET  /api/models/{id}/graph            RES network: ?focus=<node id>&depth=2&region=
  GET  /api/models/{id}/search?q=        processes and commodities whose name contains q
  GET  /api/models/{id}/export.zip       every table as CSV (otui table format)
  GET  /api/models/{id}/export.xlsx      every table as a workbook sheet (otui table format)
  POST /api/models/{id}/solve            run on TIMES.jl + HiGHS (form solve=false: generate only)
  GET  /api/models/{id}/report           where to report the last run (an issue, filled in)
  GET  /api/models/{id}/report.zip       the last run's report bundle (?model=false: no model files)
  GET  /api/models/{id}/qc               QC report: TIMES's QA log (after a run) and otui's checks
  GET  /api/results/runs                 every run with results: a scenario (run file) solved
                                         with one solver, labelled <scenario>.<solver>
  GET  /api/results/facets?r=            the values each filter can take in those runs
  POST /api/results/query                {runs, attribute, filters, by, top}: summed by run,
                                         period and group (results.Results.query)
  GET  /api/results/export?r=&fmt=       the runs' results: parquet, duckdb, csv or xlsx
  POST /api/results/charts.xlsx          {charts}: the charts as an Excel workbook, native charts

Two TIMES.jl workers: a reader (compiles uploads, warmed on demo12) and a runner
(preprocesses, generates and solves; warmed with a demo12 solve), so a long run
never blocks reading a new model. One run at a time, within the run limits.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from otui import __version__, openmodels
from otui import graph as resgraph
from otui import report as reportmod
from otui import results as resultsmod
from otui.bridge import JuliaWorker
from otui.references import reference_for
from otui.store import SPECIAL_VALUES, Model, _q, load_dump
from otui.uploads import UploadError, find_run_files, save_uploads

Compiler = Callable[[Path, Path], dict[str, Any]]  # (run file, dump folder) -> info
# (run file, out folder, until=step, on_event=callback) -> result; one per solver
Runner = Callable[..., dict[str, Any]]
STEPS = (  # the steps of a run (julia/runchain.jl, gams.py), with their labels
    ("read", "Read"),
    ("preprocess", "Preprocess"),
    ("build", "Build model"),
    ("pass", "Pass to solver"),
    ("solve", "Solve"),
    ("results", "Results"),
)
SOLVERS = (  # HiGHS by default, then cuOpt, then CPLEX (GAMS)
    ("highs", "HiGHS", "TIMES.jl + HiGHS (open source)"),
    ("cuopt", "cuOpt", "TIMES.jl + NVIDIA cuOpt on the GPU (WSL2)"),
    ("cplex", "CPLEX", "GAMS TIMES + CPLEX, on this computer (licensed)"),
)
# then GAMS's other LP solvers that this computer's licence runs (gams.probe)
# solvers that run on TIMES.jl; the others run through GAMS TIMES
TIMESJL_SOLVERS = ("highs", "cuopt")
# a run's result fields kept with the model's earlier runs (the parity panel, the report)
KEPT = (
    "status",
    "objective",
    "termination",
    "stopped_after",
    "rows",
    "columns",
    "crossover_objective",
)
# demo12's objective with GAMS TIMES + CPLEX (TIMES.jl's reference): a warm-up solve must reach
# it (rtol 1e-6), or the server does not use that TIMES.jl
DEMO12_OBJECTIVE = 23960685.737007827
MAX_LIMIT = 5000
WEB_DIR = Path(__file__).parent / "web"
SUMMARY_SETS = ("REG", "PRC", "COM", "MILESTONYR", "TOP")


def workspace_dir() -> Path:
    return Path.home() / ".otui" / "workspace"


def hosting() -> dict[str, str]:
    """Where this server runs: on the user's own computer ("local"), or in a GitHub codespace
    ("codespaces"), a cloud computer under the user's GitHub account, which GitHub sets
    CODESPACES=true in. The page says which, so users know where their model files go. The
    codespace's name is left out: it starts with the user's GitHub name, and /api/health goes
    into run reports."""
    return {"kind": "codespaces" if os.environ.get("CODESPACES") == "true" else "local"}


def forwarded_url(port: int) -> str:
    """The address GitHub forwards ``port`` to in a codespace (private to the codespace's
    owner), or "" outside one."""
    name = os.environ.get("CODESPACE_NAME", "")
    domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "")
    if hosting()["kind"] != "codespaces" or not name or not domain:
        return ""
    return f"https://{name}-{port}.{domain}"


class Engine:
    """A TIMES.jl worker, started and warmed in the background.

    ``warm`` is "compile" (read demo12), "solve" (run demo12 with HiGHS: Julia
    compiles the whole run chain, about 2-3 minutes the first time) or None.
    """

    def __init__(
        self,
        worker_factory: Callable[[], JuliaWorker] | None = None,
        warm: str | None = "compile",
        channels: list[str] | None = None,
    ):
        self._factory = worker_factory  # None: otui's own TIMES.jl, latest then pinned
        self._channels = channels
        self.warm = warm
        self.worker: JuliaWorker | None = None
        self.state = "starting"  # starting, warming, ready, error
        self.error = ""
        self.info: dict[str, Any] = {}
        self.timings: dict[str, float] = {}
        self._ready = threading.Event()
        self._started = False
        self.solver = ""  # the solver a lazily started engine serves (cuopt)
        self.channel = ""  # the TIMES.jl it runs: latest or pinned (bridge.CHANNELS)
        self.fallback = ""  # why it is not on the latest TIMES.jl

    def start(self, warm: bool = True):
        self._started = True
        threading.Thread(target=self._run, args=(self.warm if warm else None,), daemon=True).start()

    def _run(self, warm: str | None):
        from otui import bridge

        tries: list[str | None] = (
            [None] if self._factory else list(self._channels or bridge.channels())
        )
        if tries == ["pinned"] and not self._channels:
            self.fallback = (
                "TIMES.jl's latest version is not set up (otui julia-setup)"
                if os.environ.get("OTUI_TIMESJL", "latest").lower() != "pinned"
                else "OTUI_TIMESJL=pinned"
            )
        try:
            for i, ch in enumerate(tries):
                try:
                    self._start_one(ch, warm)
                    break
                except Exception as e:  # noqa: BLE001 - the next TIMES.jl, or the error below
                    if self.worker:
                        self.worker.close()
                        self.worker = None
                    if i + 1 == len(tries):
                        raise
                    self.fallback = f"TIMES.jl {ch} did not pass: {str(e)[:400]}"
                    self.state = "starting"
            self.state = "ready"
        except Exception as e:  # noqa: BLE001 - reported through /api/health
            self.state, self.error = "error", str(e)
        finally:
            self._ready.set()

    def _start_one(self, channel: str | None, warm: str | None):
        """Start a worker on ``channel``'s TIMES.jl and warm it on demo12: a warm-up solve must
        reach demo12's reference objective, so a TIMES.jl that solves wrongly is not used."""
        t0 = time.perf_counter()
        w = self._factory() if self._factory else JuliaWorker(channel=channel)
        self.worker = w.start()
        self.channel = channel or ""
        self.timings["start"] = time.perf_counter() - t0
        self.info = {k: v for k, v in self.worker.info().items() if k not in ("id", "ok")}
        if channel:
            from otui import bridge

            self.info["times_jl_channel"] = channel
            self.info["times_jl_commit"] = bridge.env_commit(channel)
        if warm:
            self.state = "warming"
            t1 = time.perf_counter()
            demo = self.worker.demo_dir() / "demo12.run"
            with tempfile.TemporaryDirectory(
                prefix="otui-warm-", ignore_cleanup_errors=True
            ) as out:
                if warm == "solve":
                    r = self.worker.solve(demo, out)
                    obj = r.get("objective")
                    if (
                        r.get("status") != "optimal"
                        or obj is None
                        or not math.isclose(obj, DEMO12_OBJECTIVE, rel_tol=1e-6)
                    ):
                        raise RuntimeError(
                            f"demo12 gave {r.get('status')} {obj}, not the reference "
                            f"objective {DEMO12_OBJECTIVE}"
                        )
                else:
                    self.worker.compile(demo, out)
            self.timings["warm"] = time.perf_counter() - t1

    def _worker(self) -> JuliaWorker:
        self._ready.wait()
        if self.worker is None or self.state == "error":
            raise RuntimeError(f"TIMES.jl is not available: {self.error}")
        return self.worker

    def compile(self, run: Path, out: Path) -> dict[str, Any]:
        return self._worker().compile(run, out)

    def solve(self, run: Path, out: Path, **kw) -> dict[str, Any]:
        if self.state == "starting" and not self._started:
            self.start()
        return self._worker().solve(run, out, **kw)

    def health(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "state": self.state,
            "error": self.error,
            "timings": {k: round(v, 2) for k, v in self.timings.items()},
        }
        if self.channel:
            d["channel"] = self.channel
            d["commit"] = self.info.get("times_jl_commit", "")
        if self.fallback:
            d["fallback"] = self.fallback
        return d

    def kill(self):
        """Kill the job: end the worker in the middle of its request (a
        Julia solve cannot be interrupted) and start a new one on the same TIMES.jl, warmed
        again. The request waiting on the old worker fails; the next run waits for the new."""
        w = self.worker
        self._ready.clear()
        self.state, self.error, self.worker = "starting", "", None
        if self.channel:
            self._channels = [self.channel]
        if w:
            w.kill()
        self.start()

    def close(self):
        if self.worker:
            self.worker.close()


@dataclass
class Entry:
    id: str
    folder: Path
    files: list[str]
    run: str | None = None
    status: str = "uploaded"  # uploaded, needs_run, compiling, loading, ready, error
    error: str = ""
    timings: dict[str, float] = field(default_factory=dict)
    model: Model | None = None
    graphs: dict[str, resgraph.Graph] = field(default_factory=dict)
    result: dict[str, Any] | None = None  # last TIMES.jl run
    run_status: str = ""  # "", running, done, error, killed
    run_error: str = ""
    run_steps: list[dict[str, Any]] = field(default_factory=list)  # the last run's steps, live
    run_solver: str = ""
    run_killed: bool = False  # Kill the job was pressed during the current run
    run_kill_sent: bool = False  # and the runner was told to stop
    # earlier runs, newest first, one per solver other than the current run's: their step
    # times (columns beside the latest run's) and outcomes (the parity panel)
    run_history: list[dict[str, Any]] = field(default_factory=list)
    run_about: dict[str, str] = field(default_factory=dict)  # the current run's code and version
    reference: dict[str, Any] | None = None  # a reference objective for this model (references)
    symbol_counts: dict[str, int] = field(default_factory=dict)  # name -> records, once loaded
    untranslated: dict[str, Any] | None = None  # TIMES.jl's reader met a feature it lacks
    # "<scenario>.<solver>" -> the run kept from this model's last solve of that scenario (its run
    # file) with that solver (results.save), for Results
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def public(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "status": self.status,
            "run": self.run,
            "files": self.files,
            "error": self.error,
            "timings": {k: round(v, 2) for k, v in self.timings.items()},
        }
        if self.status == "error" and self.untranslated:
            d["untranslated"] = self.untranslated
        if self.status == "needs_run":
            d["run_candidates"] = [p.name for p in find_run_files(self.folder / "files")]
        if self.model is not None:
            d["summary"] = summary(self.model)
        if self.reference:
            d["reference"] = self.reference
        if self.run_status:
            hide = ("qa_log", "dummy_flows", "id", "ok")
            r = {k: v for k, v in (self.result or {}).items() if k not in hide}
            r.pop("steps", None)
            d["solve"] = {
                "status": self.run_status,
                "error": self.run_error,
                "result": r,
                "solver": self.run_solver,
                **self.run_about,
                "steps": self.run_steps,
                "history": self.run_history,
            }
        return d

    def summary_of_last(self) -> dict[str, Any]:
        """The current run as kept in the history: outcome and step times, without logs."""
        r = self.result or {}
        return {
            "solver": self.run_solver,
            "status": self.run_status,
            **self.run_about,
            "result": {k: r[k] for k in KEPT if k in r},
            "steps": [
                {k: s.get(k) for k in ("step", "label", "state", "seconds")} for s in self.run_steps
            ],
        }

    def keep_last_run(self, solver: str):
        """Before a run with ``solver``: move the finished run into the history, keeping one
        run per solver (every solver: the parity panel compares them all) and none for
        ``solver`` (the new run takes its place)."""
        if self.run_status in ("done", "error", "killed") and self.run_steps:
            self.run_history.insert(0, self.summary_of_last())
        seen = {solver}
        kept = []
        for h in self.run_history:
            if h["solver"] not in seen:
                seen.add(h["solver"])
                kept.append(h)
        self.run_history = kept


def summary(m: Model) -> dict[str, Any]:
    counts = {n: m.symbols[n].records for n in SUMMARY_SETS if n in m.symbols}
    return {
        "source": m.source,
        "symbols": len(m.symbols),
        "tables": len(m.tables),
        "records": sum(m.symbols[n].records for n in m.tables),
        "counts": counts,
    }


def _cell(v: Any) -> Any:
    if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
        return "INF" if v > 0 else "-INF" if v < 0 else "NA"
    return v


class Registry:
    def __init__(
        self, root: Path, compiler: Compiler, runners: dict[str, Runner] | Runner | None = None
    ):
        self.root = root
        self.compiler = compiler
        self.runners = (
            runners if isinstance(runners, dict) else {"highs": runners} if runners else {}
        )
        self.entries: dict[str, Entry] = {}
        self._run_lock = threading.Lock()  # one run at a time

    def get(self, mid: str) -> Entry:
        e = self.entries.get(mid)
        if e is None:
            raise HTTPException(404, f"no model {mid}")
        return e

    def add(self, uploads: list[tuple[str, Any]], run: str | None) -> Entry:
        mid = uuid.uuid4().hex[:12]
        folder = self.root / mid
        try:
            paths = save_uploads(uploads, folder / "files")
        except UploadError as e:
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(400, str(e)) from e
        e = Entry(id=mid, folder=folder, files=[p.name for p in paths])
        self.entries[mid] = e
        self.choose_run(e, run)
        return e

    def choose_run(self, e: Entry, run: str | None):
        """Pick the run file: the scenario. Another scenario starts with no runs in the Run tab
        (the parity panel compares solvers on one scenario); its kept results stay in Results."""
        if e.run_status == "running":
            raise HTTPException(409, "a run is going: kill it or wait before changing the run file")
        runs = find_run_files(e.folder / "files")
        if run:
            match = [p for p in runs if p.name.lower() == run.lower()]
            if not match:
                raise HTTPException(400, f"{run} is not among the uploaded run files")
            chosen = match[0]
        elif len(runs) == 1:
            chosen = runs[0]
        elif not runs:
            e.status = "error"
            e.error = (
                "no TIMES run file found: upload the .run file (it includes maindrv.mod) "
                "with its .dd files"
            )
            return
        else:
            e.status = "needs_run"
            return
        if e.run and chosen.name != e.run:  # another scenario: the last runs were of the old one
            e.run_status, e.run_error, e.result, e.run_solver = "", "", None, ""
            e.run_steps, e.run_history, e.run_about = [], [], {}
        e.run = chosen.name
        e.status = "compiling"
        threading.Thread(target=self._build, args=(e, chosen), daemon=True).start()

    def _build(self, e: Entry, run: Path):
        with e.lock:
            try:
                dump = e.folder / "dump"
                shutil.rmtree(dump, ignore_errors=True)
                t = time.perf_counter()
                self.compiler(run, dump)
                e.timings["compile"] = time.perf_counter() - t
                e.status = "loading"
                t = time.perf_counter()
                e.model = load_dump(dump)
                e.graphs = {}
                e.symbol_counts = {n: s.records for n, s in e.model.symbols.items()}
                e.reference = reference_for(run)
                e.timings["load"] = time.perf_counter() - t
                e.status = "ready"
            except Exception as ex:  # noqa: BLE001 - shown to the user
                e.status, e.error, e.untranslated = "error", str(ex), None
                if reportmod.is_untranslated(e.error):  # a feature TIMES.jl's reader lacks
                    e.untranslated = reportmod.untranslated(e.error, {}, run_switches(run))
                    e.error = f"not yet translated: {e.untranslated['what']}"

    def start_run(self, e: Entry, until: str = "results", solver: str = "highs"):
        """Run the model's steps up to ``until`` with ``solver``, in the background."""
        names = [s for s, _ in STEPS]
        if until not in names:
            raise HTTPException(400, f"no step {until}: one of {', '.join(names)}")
        runner = self.runners.get(solver)
        if runner is None:
            raise HTTPException(503, f"{solver} is not available here")
        if e.status != "ready" or not e.run:
            raise HTTPException(409, f"model is {e.status}")
        if e.run_status == "running":
            raise HTTPException(409, "a run is already going")
        e.keep_last_run(solver)
        e.run_status, e.run_error, e.result, e.run_solver = "running", "", None, solver
        about = getattr(runner, "about", None)
        e.run_about = {
            "code": "TIMES.jl" if solver in TIMESJL_SOLVERS else "GAMS TIMES",
            **(about() if about else {}),
        }
        e.run_killed = e.run_kill_sent = False
        e.run_steps = [
            {
                "step": s,
                "label": label,
                "state": "pending",
                "seconds": None,
                "started": None,
                "log": [],
            }
            for s, label in STEPS[: names.index(until) + 1]
        ]
        threading.Thread(target=self._run, args=(e, until, runner), daemon=True).start()

    def _run(self, e: Entry, until: str, runner: Runner):
        by = {s["step"]: s for s in e.run_steps}

        def on_event(ev: dict[str, Any]):
            s = by.get(ev.get("step", ""))
            if s is None:
                return
            s["state"] = ev.get("state", s["state"])
            if s["state"] == "running":
                s["started"] = time.time()
            if ev.get("seconds") is not None:
                s["seconds"] = ev["seconds"]
            if ev.get("log"):
                s["log"] = list(ev["log"])
            if e.run_killed:  # killed before its first step began: stop it now
                self._send_kill(e)

        try:
            if not self._run_lock.acquire(blocking=False):
                e.run_steps[0]["log"] = ["waiting for another model's run to finish"]
                self._run_lock.acquire()
            try:
                if e.run_killed:  # killed while it waited: never started
                    raise RuntimeError("killed before it started")
                out = e.folder / "run"
                shutil.rmtree(out, ignore_errors=True)
                t = time.perf_counter()
                assert e.run is not None
                result = runner(e.folder / "files" / e.run, out, until=until, on_event=on_event)
                about = getattr(runner, "about", None)
                if result.get("version"):  # GAMS TIMES: from its listing
                    e.run_about["version"] = result["version"]
                elif about and not e.run_about.get("version"):
                    # a worker started by this run (cuOpt's): known now
                    e.run_about.update(about())
                if result.get("status") == "not_translated":
                    result["untranslated"] = reportmod.untranslated(
                        result.get("message", ""),
                        e.symbol_counts,
                        run_switches(e.folder / "files" / e.run),
                    )
                e.result = result
                e.timings["run"] = time.perf_counter() - t
                if result.get("status") == "optimal" and until == "results":
                    self._keep_results(e, out, result)
            finally:
                self._run_lock.release()
            e.run_status = "done"
        except Exception as ex:  # noqa: BLE001 - shown to the user
            killed = e.run_killed
            e.run_status, e.run_error = ("killed", "") if killed else ("error", str(ex))
            for s in e.run_steps:
                if s["state"] == "running" or (killed and s["state"] == "failed"):
                    s["state"] = "killed" if killed else "failed"
                    if s["state"] == "killed" or s["seconds"] is None:
                        s["seconds"] = s["seconds"] or time.time() - (s["started"] or time.time())
                    if killed:
                        s["log"] = [*s["log"], "killed by you"]

    def _keep_results(self, e: Entry, out: Path, result: dict[str, Any]):
        """Keep the run's results (Results tab) beside the model: one per scenario and solver,
        replacing that scenario's last run with that solver."""
        src = out / "results"
        if not resultsmod.has_results(src):
            return
        solver, scenario = e.run_solver, Path(e.run or "model").stem
        name = f"{scenario}.{solver}"
        meta = {
            "id": f"{e.id}.{name}",
            "model_id": e.id,
            "scenario": scenario,
            "run_file": e.run,
            "solver": solver,
            **e.run_about,
            "objective": result.get("objective"),
        }
        folder = e.folder / "results" / scenario / solver
        try:
            counts = resultsmod.save(src, folder, e.model, meta)
            e.results[name] = {**meta, "folder": folder, "records": counts, "saved": time.time()}
        except Exception as ex:  # noqa: BLE001 - the run itself succeeded
            result["results_error"] = str(ex)[:2000]

    def runs(self) -> dict[str, tuple[dict[str, Any], Path]]:
        """Every kept run: its id -> (its description, its folder), oldest model first."""
        out = {}
        for e in sorted(self.entries.values(), key=lambda e: e.created):
            for kept in e.results.values():
                meta = {k: v for k, v in kept.items() if k != "folder"}
                out[meta["id"]] = (meta, kept["folder"])
        return out

    def kill_run(self, e: Entry):
        """Kill the job: stop the model's run now. GAMS runs end their process tree; TIMES.jl
        runs end their worker, which starts again (Engine.kill)."""
        if e.run_status != "running":
            raise HTTPException(409, "no run is going")
        e.run_killed = True
        if any(s["state"] != "pending" for s in e.run_steps):
            self._send_kill(e)
        # else: still waiting (for another run, or for TIMES.jl to warm up): it stops when it
        # would start (_run)

    def _send_kill(self, e: Entry):
        if e.run_kill_sent:
            return
        e.run_kill_sent = True
        kill = getattr(self.runners.get(e.run_solver), "kill", None)
        if kill:
            threading.Thread(target=kill, daemon=True).start()


def killable(
    runner: Runner, kill: Callable[[], Any], about: Callable[[], dict[str, str]] | None = None
) -> Runner:
    """A runner with its Kill the job (Registry.kill_run) and, optionally, what it runs: its
    version (the parity panel, the report)."""
    runner.kill = kill  # type: ignore[attr-defined]
    if about:
        runner.about = about  # type: ignore[attr-defined]
    return runner


def engine_about(e: Engine) -> Callable[[], dict[str, str]]:
    """The TIMES.jl an engine runs: its channel and commit."""

    def about() -> dict[str, str]:
        commit = str(e.info.get("times_jl_commit", ""))[:7]
        if commit:
            return {"version": f"{e.channel} @ {commit}"}
        # a worker on its own TIMES.jl (cuOpt's, in WSL): its package version
        v = e.info.get("times_jl", "")
        return {"version": f"{v} ({e.solver} worker)" if v else e.channel}

    return about


def run_switches(run: Path) -> dict[str, str]:
    """A run file's $SET switches (name, upper case -> value)."""
    out: dict[str, str] = {}
    try:
        text = run.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for m in re.finditer(r"^\s*\$\s*set\s+(\w+)\s+'?([^'\s]*)", text, re.I | re.M):
        out[m.group(1).upper()] = m.group(2)
    return out


@dataclass
class Solvers:
    """This computer's solvers: a runner for each available one, why the others are not, the
    engines to start (HiGHS, warmed now) and close, GAMS's other licensed LP solvers (offered
    after CPLEX) and the GAMS solvers installed but not in the licence."""

    runners: dict[str, Runner]
    why: dict[str, str]
    engines: list[Engine]
    extra: list[tuple[str, str, str]] = field(default_factory=list)
    unlicensed: list[str] = field(default_factory=list)


def solver_runners() -> Solvers:
    """This computer's solvers (Solvers). cuOpt's worker starts when cuOpt is chosen
    (``/api/solvers/cuopt/prepare``) or on its first run, and warms up on demo12. GAMS's licence
    is checked once, at start (gams.probe: about 1.5 s)."""
    from otui import config
    from otui import gams as gamsrun
    from otui.bridge import cuopt_command, default_threads

    runners: dict[str, Runner] = {}
    why: dict[str, str] = {}
    highs = Engine(warm="solve")
    runners["highs"] = killable(
        lambda run, out, **kw: highs.solve(run, out, solver="highs", **kw),
        highs.kill,
        engine_about(highs),
    )
    engines = [highs]
    cu, why["cuopt"] = config.cuopt()
    if cu:
        threads = default_threads()
        cuopt = Engine(
            lambda: JuliaWorker(
                command=cuopt_command(cu, threads), wsl=True, wsl_distro=cu["distro"]
            ),
            warm="solve",
        )
        cuopt.solver = "cuopt"

        def run_cuopt(run, out, on_event=None, **kw):
            if cuopt.state != "ready" and on_event:  # the first run starts the worker in WSL
                on_event(
                    {
                        "step": "read",
                        "state": "running",
                        "log": ["starting the cuOpt worker in WSL (the first time: a few minutes)"],
                    }
                )
            return cuopt.solve(run, out, solver="cuopt", on_event=on_event, **kw)

        runners["cuopt"] = killable(run_cuopt, cuopt.kill, engine_about(cuopt))
        engines.append(cuopt)
    extra: list[tuple[str, str, str]] = []
    unlicensed: list[str] = []
    g, why["cplex"] = config.gams()
    if g:
        licence = gamsrun.probe(g)

        def gams_runner(name: str) -> Runner:
            procs: list[subprocess.Popen] = []  # the running GAMS job, to kill

            def run_gams(run, out, until="results", on_event=None):
                procs.clear()
                return gamsrun.run(
                    run, run.parent, out, g, until, default_threads(), on_event, name, procs.append
                )

            return killable(run_gams, lambda: [gamsrun.kill(p) for p in procs])

        for name, sid, label in gamsrun.SOLVERS:
            reason = licence.get(name, "")
            if reason:
                why[sid] = f"{label} is {reason}" if reason.startswith("not") else reason
                if reason == "not in this computer's GAMS licence":
                    unlicensed.append(label)
                continue
            runners[sid] = gams_runner(name)
            if sid != "cplex":
                kind = "open source" if name in gamsrun.FREE else "licensed"
                plain = label.removesuffix(" (GAMS)")
                extra.append((sid, label, f"GAMS TIMES + {plain}, on this computer ({kind})"))
    return Solvers(runners, why, engines, extra, unlicensed)


def create_app(
    compiler: Compiler | None = None,
    runner: Runner | dict[str, Runner] | None = None,
    root: Path | None = None,
    warm: bool = True,
) -> FastAPI:
    """The otui app. Tests pass ``compiler`` (and ``runner``); otherwise TIMES.jl workers."""
    engine = None
    started: list[Engine] = []  # started with the app
    lazy: list[Engine] = []  # started on first use, closed with the app
    why: dict[str, str] = {}
    runners: dict[str, Runner] = (
        runner if isinstance(runner, dict) else {"highs": runner} if runner else {}
    )
    offered = list(SOLVERS)
    unlicensed: list[str] = []
    update: dict[str, str] = {}  # the background update of the latest TIMES.jl
    if compiler is None:
        from otui import bridge

        bridge.promote_latest()  # a latest TIMES.jl built during the last session

        def update_latest():
            if os.environ.get("OTUI_TIMESJL", "latest").lower() == "pinned":
                return
            update["state"] = "checking"
            try:
                update.update(bridge.update_latest())
            except Exception as e:  # noqa: BLE001 - reported through /api/health
                update.update(state="failed", message=str(e)[:400])

        threading.Thread(target=update_latest, daemon=True).start()
        engine = Engine(warm="compile")
        compiler = engine.compile
        started.append(engine)
        if runner is None:
            found = solver_runners()
            runners, why = found.runners, found.why
            started.append(found.engines[0])
            lazy += found.engines[1:]
            offered += found.extra
            unlicensed = found.unlicensed
    else:  # tests: GAMS solvers given as runners are offered after CPLEX
        from otui import gams as gamsrun

        offered += [
            (sid, label, f"GAMS TIMES + {label}")
            for _, sid, label in gamsrun.SOLVERS
            if sid in runners and sid != "cplex"
        ]
    runner_engine = started[1] if len(started) > 1 else None
    by_solver = {getattr(x, "solver", ""): x for x in lazy if getattr(x, "solver", "")}
    registry = Registry(Path(root or workspace_dir()), compiler, runners)
    engines = started

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for x in engines:
            x.start(warm=warm)
        yield
        for x in engines + lazy:
            x.close()

    app = FastAPI(title="otui", version=__version__, lifespan=lifespan)
    app.state.registry = registry
    app.state.engine = engine
    app.state.runner_engine = runner_engine

    @app.get("/api/health")
    def health():
        if engine is None:
            d: dict[str, Any] = {"otui": __version__, "engine": "external"}
        else:
            d = {
                "otui": __version__,
                "engine": engine.state,
                "reader": engine.health(),
                **engine.info,
            }
        if runner_engine is not None:
            d["runner"] = runner_engine.health()
        d["hosting"] = hosting()
        if update:
            d["times_jl_update"] = dict(update)
        d["solvers"] = [
            {
                "id": sid,
                "label": label,
                "description": desc,
                "available": sid in registry.runners,
                "reason": why.get(sid, "") if sid not in registry.runners else "",
                # a solver with its own worker: idle (not started), starting, warming, ready
                **(
                    {"state": by_solver[sid].state if by_solver[sid]._started else "idle"}
                    if sid in by_solver
                    else {}
                ),
            }
            for sid, label, desc in offered
        ]
        if unlicensed:  # installed in GAMS, but not in this computer's licence
            d["gams_unlicensed"] = unlicensed
        d["steps"] = [{"step": s, "label": label} for s, label in STEPS]
        return d

    @app.post("/api/solvers/{sid}/prepare")
    def prepare_solver(sid: str):
        """Start a solver's worker now (cuOpt in WSL), so it is warm by the first run."""
        e = by_solver.get(sid)
        if e is not None and not e._started:
            e.start()
        return {"id": sid, "state": e.state if e else "ready"}

    @app.post("/api/theme")
    def company_theme(url: str = Form(..., max_length=2000)):
        """A theme from a company's website (otui.theme): fetched only on this request."""
        from otui import theme

        try:
            return theme.from_url(url)
        except theme.ThemeError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/models")
    def upload(files: list[UploadFile] = File(...), run: str | None = Form(None)):
        e = registry.add([(f.filename or "", f.file) for f in files], run)
        return e.public()

    @app.post("/api/models/demo")
    def demo():
        """TIMES_Demo demo12 (CC BY 4.0, IEA-ETSAP), pinned by TIMES.jl: a one-click model."""
        if engine is None:
            raise HTTPException(503, "no TIMES.jl reader")
        d = engine._worker().demo_dir()
        return add_files([d / "demo12.run", *sorted(d.glob("*.dd"))], "demo12.run").public()

    def add_files(paths: list[Path], run: str) -> Entry:
        handles = [open(p, "rb") for p in paths]
        try:
            return registry.add([(p.name, h) for p, h in zip(paths, handles, strict=True)], run)
        finally:
            for h in handles:
                h.close()

    @app.get("/api/open-models")
    def open_models():
        """The open models to try beside the demo (OTNYC, OTCGEN): size, and whether cached."""
        return openmodels.public()

    @app.post("/api/models/open/{key}")
    def open_model(key: str):
        """An open model (openmodels): downloaded from GitHub on first use, hash-checked."""
        if key not in openmodels.MODELS:
            raise HTTPException(404, f"no open model {key}")
        try:
            paths = openmodels.fetch(key)
        except openmodels.OpenModelError as e:
            raise HTTPException(502, str(e)) from e
        return add_files(paths, openmodels.MODELS[key]["run"]).public()

    @app.get("/api/models")
    def models():
        return [e.public() for e in sorted(registry.entries.values(), key=lambda e: e.created)]

    @app.get("/api/models/{mid}")
    def model(mid: str):
        return registry.get(mid).public()

    @app.post("/api/models/{mid}/run")
    def set_run(mid: str, run: str = Form(...)):
        e = registry.get(mid)
        if e.status not in ("needs_run", "error", "ready"):
            raise HTTPException(409, f"model is {e.status}")
        registry.choose_run(e, run)
        return e.public()

    def ready_model(mid: str) -> Model:
        e = registry.get(mid)
        if e.status != "ready" or e.model is None:
            raise HTTPException(409, f"model is {e.status}")
        return e.model

    @app.get("/api/models/{mid}/symbols")
    def symbols(mid: str):
        m = ready_model(mid)
        return [
            {
                "name": s.name,
                "kind": s.kind,
                "headers": list(s.headers),
                "records": s.records,
                "description": s.description,
                "has_table": s.has_table,
            }
            for s in m.symbols.values()
        ]

    @app.get("/api/models/{mid}/symbols/{name}")
    def rows(
        mid: str,
        name: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=MAX_LIMIT),
        q: str | None = None,
        where: list[str] = Query(default=[]),
    ):
        m = ready_model(mid)
        s = m.symbols.get(name)
        if s is None:
            raise HTTPException(404, f"no symbol {name}")
        if not s.has_table:
            return {"name": name, "columns": list(s.headers), "total": 0, "rows": []}
        cols = list(s.headers)
        conds, params = [], []
        for w in where:
            col, sep, val = w.partition(":")
            if not sep or col not in cols:
                raise HTTPException(400, f"where must be COLUMN:VALUE with a column of {name}")
            conds.append(f'"{col}" = ?')
            params.append(val)
        if q and cols:
            conds.append("(" + " OR ".join(f'"{c}" ILIKE ?' for c in cols) + ")")
            params += [f"%{q}%"] * len(cols)
        sql_where = (" WHERE " + " AND ".join(conds)) if conds else ""
        cur = m.con.cursor()
        try:
            total = cur.execute(f"SELECT count(*) FROM {_q(name)}{sql_where}", params).fetchone()[0]
            res = cur.execute(
                f"SELECT * FROM {_q(name)}{sql_where} LIMIT {limit} OFFSET {offset}", params
            )
            names = [d[0] for d in res.description]
            data = res.fetchall()
        finally:
            cur.close()
        out_cols = [c for c in names if c != "value__sv"]
        out = []
        sv_i = names.index("value__sv") if "value__sv" in names else None
        v_i = names.index("value") if "value" in names else None
        for r in data:
            r = list(r)
            if sv_i is not None and v_i is not None and r[sv_i]:
                r[v_i] = SPECIAL_VALUES.get(int(r[sv_i]), r[v_i])
            out.append([_cell(v) for i, v in enumerate(r) if i != sv_i])
        return {"name": name, "columns": out_cols, "total": total, "rows": out}

    def model_graph(mid: str, region: str | None) -> resgraph.Graph:
        e = registry.get(mid)
        m = ready_model(mid)
        key = region or ""
        if key not in e.graphs:
            e.graphs[key] = resgraph.build(m, region=region or None)
        return e.graphs[key]

    @app.get("/api/models/{mid}/graph")
    def graph(
        mid: str,
        focus: str | None = None,
        depth: int | None = Query(None, ge=0, le=1000),
        region: str | None = None,
    ):
        """The network, or the part within ``depth`` links of ``focus``.

        ``reach`` counts the nodes within 0, 1, 2, ... links of the focus (or, for the
        whole network, of its ``centre``), up to the most links any connected node is away.
        A whole network over the drawing limit is centred on its most central node instead,
        at the most links that fit (``auto``).
        """
        g = model_graph(mid, region)
        limit = resgraph.MAX_NODES
        centre = g.centre()
        auto = False
        if focus is None:
            if len(g.nodes) <= limit:
                reach = g.reach(centre) if centre else []
                return {
                    "focus": None,
                    "centre": centre,
                    "reach": reach,
                    "limit": limit,
                    **g.categories(),
                    **g.public(),
                }
            if centre is None:
                raise HTTPException(413, f"{len(g.nodes):,} nodes: too many to draw at once")
            reach = g.reach(centre)
            focus, depth, auto = centre, max(d for d, n in enumerate(reach) if n <= limit), True
        if focus not in g.nodes:
            raise HTTPException(404, f"no node {focus}")
        reach = g.reach(focus)
        depth = min(2 if depth is None else depth, len(reach) - 1)
        if reach[depth] > limit:
            raise HTTPException(
                413,
                f"{reach[depth]:,} nodes within {depth} links of {g.nodes[focus].name}: too many "
                f"to draw at once (the limit is {limit:,})",
            )
        keep = g.ego(focus, depth)
        return {
            "focus": focus,
            "depth": depth,
            "reach": reach,
            "centre": centre,
            "auto": auto,
            "limit": limit,
            **g.categories(),
            **g.public(keep=keep),
        }

    @app.get("/api/models/{mid}/dummies")
    def dummies(mid: str):
        from otui import dummies as dmod

        e = registry.get(mid)
        m = ready_model(mid)
        result = e.result if e.run_status == "done" else None
        return dmod.summary(m, model_graph(mid, None), result)

    def _export(mid: str, kind: str) -> Response:
        import io

        from otui import export

        e = registry.get(mid)
        m = ready_model(mid)
        buf = io.BytesIO()
        (export.write_csv_zip if kind == "zip" else export.write_xlsx)(m, buf)
        stem = Path(e.run or "model").stem
        media = {
            "zip": "application/zip",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }[kind]
        return Response(
            buf.getvalue(),
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{stem}.{kind}"'},
        )

    @app.get("/api/models/{mid}/export.zip")
    def export_zip(mid: str):
        return _export(mid, "zip")

    @app.get("/api/models/{mid}/export.xlsx")
    def export_xlsx(mid: str):
        return _export(mid, "xlsx")

    @app.post("/api/models/{mid}/solve")
    def solve(
        mid: str,
        until: str = Form("results"),
        solver: str = Form("highs"),
        solve: bool = Form(True),
    ):
        """Run the model's steps up to ``until`` (default: all) with ``solver``."""
        e = registry.get(mid)
        registry.start_run(e, until if solve else "build", solver)
        return e.public()

    @app.post("/api/models/{mid}/kill")
    def kill(mid: str):
        """Kill the job: stop the model's run now (a TIMES.jl worker starts again, warmed)."""
        e = registry.get(mid)
        registry.kill_run(e)
        return e.public()

    def report_entry(e: Entry) -> dict[str, Any]:
        """The data of a report on the model's last run (report.json)."""
        if not e.run_status or e.run_status == "running":
            raise HTTPException(409, "no finished run to report")
        labels = {sid: label for sid, label, _ in offered}
        r = {k: v for k, v in (e.result or {}).items() if k not in ("qa_log", "steps")}
        runs = [e.summary_of_last(), *e.run_history]
        for x in runs:
            x["solver_label"] = labels.get(x["solver"], x["solver"])
            done = [s.get("seconds") or 0 for s in x["steps"]]
            x["total"] = sum(done)
            x.update({k: x["result"].get(k) for k in ("objective", "rows")})
        files = []
        for p in sorted((e.folder / "files").iterdir()):
            if p.is_file():
                files.append(
                    {
                        "name": p.name,
                        "bytes": p.stat().st_size,
                        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                    }
                )
        return {
            "written": reportmod.now(),
            "run_file": e.run,
            "environment": reportmod.environment(health()),
            "run": {
                "solver": e.run_solver,
                "solver_label": labels.get(e.run_solver, e.run_solver),
                "status": e.run_status,
                "error": e.run_error,
                **e.run_about,
                "result": r,
                "steps": e.run_steps,
            },
            "parity": [
                {
                    k: x.get(k)
                    for k in (
                        "code",
                        "version",
                        "solver_label",
                        "status",
                        "objective",
                        "rows",
                        "total",
                    )
                }
                for x in runs
            ],
            "reference": e.reference,
            "files": files,
        }

    @app.get("/api/models/{mid}/report")
    def report(mid: str):
        """Where to report the model's last run: an issue in TIMES.jl or otui, filled in."""
        entry = report_entry(registry.get(mid))
        return {"issue": reportmod.issue_link(entry, entry["environment"])}

    @app.get("/api/models/{mid}/report.zip")
    def report_zip(mid: str, model: bool = True):
        """The model's last run as a zip to attach to an issue (report.bundle)."""
        e = registry.get(mid)
        entry = report_entry(e)
        entry["model_included"] = model
        data = reportmod.bundle(entry, e.folder / "run", e.folder / "files", model)
        name = Path(e.run or "model").stem
        return Response(
            data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}-otui-report.zip"'},
        )

    # --- results: the Results tab (results.py)

    solver_label = {sid: label for sid, label, _ in offered}

    def run_list() -> list[dict[str, Any]]:
        """The kept runs, each labelled <scenario>.<solver>; when two models have a scenario of
        the same name, each label also says which model (the Nth opened)."""
        order = {e.id: n for n, e in enumerate(
            sorted(registry.entries.values(), key=lambda e: e.created), 1)}  # fmt: skip
        out = []
        for rid, (meta, _) in registry.runs().items():
            name = f"{meta['scenario']}.{meta['solver']}"
            slabel = solver_label.get(meta["solver"], meta["solver"])
            out.append({**meta, "id": rid, "name": name, "solver_label": slabel})
        names = [r["name"] for r in out]
        for r in out:
            dup = names.count(r["name"]) > 1
            r["label"] = f"{r.pop('name')} (model {order[r['model_id']]})" if dup else r["name"]
        return out

    def open_results(
        ids: list[str] | None, by_label: bool = False
    ) -> tuple[resultsmod.Results, list[dict[str, Any]]]:
        """The runs' results (all kept runs if none named), each named by its id, or by its
        label (``by_label``: an export, whose run column people read)."""
        known = registry.runs()
        ids = [i for i in (ids or []) if i] or list(known)
        missing = [i for i in ids if i not in known]
        if missing:
            raise HTTPException(404, f"no results for {', '.join(missing)}")
        if not ids:
            raise HTTPException(404, "no results yet: solve a model in the Run tab")
        listed = {r["id"]: r for r in run_list()}
        key = (lambda i: listed[i]["label"]) if by_label else (lambda i: i)
        res = resultsmod.Results({key(i): known[i][1] for i in ids})
        return res, [listed[i] for i in ids]

    @app.get("/api/results/runs")
    def result_runs():
        return {"runs": run_list(), "attributes": resultsmod.ATTRIBUTES}

    @app.get("/api/results/facets")
    def result_facets(r: list[str] | None = Query(None)):
        res, _ = open_results(r)
        try:
            return res.facets()
        finally:
            res.close()

    @app.post("/api/results/query")
    def result_query(body: dict[str, Any]):
        res, _ = open_results(body.get("runs"))
        try:
            return res.query(
                str(body.get("attribute", "VAR_Cap")),
                body.get("filters") or {},
                str(body.get("by", "process_set")),
                int(body.get("top", 12)),
                body.get("base"),
            )
        except resultsmod.ResultsError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            res.close()

    @app.get("/api/results/export")
    def result_export(r: list[str] | None = Query(None), fmt: str = "parquet"):
        res, runs = open_results(r, by_label=True)
        try:
            data, media, ext = resultsmod.export(res, runs, fmt)
        except resultsmod.ResultsError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            res.close()
        stem = runs[0]["scenario"] if len(runs) == 1 else "results"
        return Response(
            data,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{stem}-results.{ext}"'},
        )

    @app.post("/api/results/charts.xlsx")
    def result_charts(body: dict[str, Any]):
        try:
            data = resultsmod.charts_xlsx(body.get("charts") or [])
        except resultsmod.ResultsError as e:
            raise HTTPException(400, str(e)) from e
        return Response(
            data,
            media_type=resultsmod.XLSX,
            headers={"Content-Disposition": 'attachment; filename="otui-charts.xlsx"'},
        )

    @app.get("/api/models/{mid}/qc")
    def qc(mid: str):
        from otui import qc as qcmod

        e = registry.get(mid)
        ready_model(mid)
        from otui import dummies as dmod

        qa = (e.result or {}).get("qa_log")
        m = ready_model(mid)
        g = model_graph(mid, None)
        result = e.result if e.run_status == "done" else None
        extra = [qcmod.Issue("otui", **i) for i in dmod.issues(dmod.summary(m, g, result))]
        return qcmod.report(g, qa, extra)

    @app.get("/api/models/{mid}/search")
    def search(mid: str, q: str = Query(..., min_length=1), region: str | None = None):
        return model_graph(mid, region).find(q)

    # The browser app (frontend/, built into otui/web). Mounted last, so /api/* wins.
    if (WEB_DIR / "index.html").exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    else:

        @app.get("/", include_in_schema=False)
        def no_web():
            return Response(
                "otui's browser app is not built: run `npm install` and `npm run build` "
                "in frontend/. The API is at /api and /docs.",
                media_type="text/plain",
            )

    return app


DEFAULT_PORT = 8642


def port_free(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def serve(host: str = "127.0.0.1", port: int = DEFAULT_PORT, warm: bool = True):
    import uvicorn

    if not port_free(host, port):
        raise RuntimeError(f"port {port} is in use: start otui with --port <another port>")
    url = forwarded_url(port)
    if url:  # in a codespace, 127.0.0.1 is the codespace: GitHub forwards the port, to you only
        print(f"otui: {url}  (your codespace, private to you; TIMES.jl starts in the background)")
    else:
        print(f"otui: http://{host}:{port}  (TIMES.jl starts in the background)")

    uvicorn.run(create_app(warm=warm), host=host, port=port, log_level="info")
