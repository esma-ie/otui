"""Run a TIMES model's DD files with GAMS TIMES and a GAMS solver, on this computer, step by step.

The third solver option (HiGHS by default, then cuOpt, then CPLEX), and after it any other LP
solver this computer's GAMS licence can run (probe()).
It needs a local GAMS and the TIMES source code (``config.gams``). GAMS runs the whole chain in
one process, so the steps are read from its log as it streams, timestamped here, at these
markers:

    read        from the start to "--- Starting execution"      (compilation)
    preprocess  to "--- Generating LP model"                     (TIMES's preprocessing)
    build       to "--- Executing CPLEX"                         (model generation)
    pass        to the solver's own start line (SOLVE_START)     (GAMS hands it the model)
    solve       to "--- Reading solution"                        (the solver's run)
    results     to the end                                       (solution back, reports, GDX)

For a solver without a known start line, its hand-over is counted in ``solve``.

GAMS buffers its output, so lines can arrive in bursts. GAMS stamps its own elapsed time on
some lines (the start of execution, the solver call, the end of the job): the step times are
mapped from this computer's clock onto GAMS's between those points, and sent again, corrected,
when the run ends.

A run that stops early: ``read`` compiles only (``action=c``); ``preprocess`` runs with
``$SET SOLVE_NOW NO`` before maindrv.mod, as TIMES.jl's run_dd.jl does, so no model is made;
``build`` and ``pass`` stop GAMS at the next marker. The run file is copied, never changed: the
copy sets ``option LP=<solver>;`` (a run file may name another LP solver).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

STEPS = ("read", "preprocess", "build", "pass", "solve", "results")
MARKERS = (  # (pattern, the step it starts)
    (re.compile(r"^--- Starting execution"), "preprocess"),
    (re.compile(r"^--- Generating (LP|MIP|RMIP|NLP) model"), "build"),
    (re.compile(r"^--- Executing (?!after)\w+"), "pass"),
    (re.compile(r"^--- Reading solution for model"), "results"),
)
# GAMS's LP solvers otui offers, in order (GAMS name, otui id, label). CPLEX is otui's third
# option; the others are offered only where this computer's GAMS licence runs them (probe()).
SOLVERS = (
    ("CPLEX", "cplex", "CPLEX"),
    ("GUROBI", "gurobi", "Gurobi"),
    ("XPRESS", "xpress", "Xpress"),
    ("MOSEK", "mosek", "MOSEK"),
    ("COPT", "copt", "COPT"),
    ("HIGHS", "gams-highs", "HiGHS (GAMS)"),
    ("CBC", "cbc", "CBC"),
    ("SOPLEX", "soplex", "SoPlex"),
)
FREE = {"HIGHS", "CBC"}  # open source: any GAMS licence runs them
# where each solver's own run starts in GAMS's log (GAMS 54; leading spaces ignored)
SOLVE_START = {
    "CPLEX": re.compile(r"^--- Starting Cplex"),
    "HIGHS": re.compile(r"^Running HiGHS"),
    "CBC": re.compile(r"^Calling CBC main solution routine"),
    "GUROBI": re.compile(r"^Gurobi Optimizer version"),
}
ELAPSED = re.compile(r"elapsed (\d+):(\d+):(\d+(?:\.\d+)?)")
LP_OPTION = re.compile(r"(?im)^\s*option\s+lp\s*=\s*\w+\s*;")
MAINDRV = re.compile(r"(?im)^(\s*\$\s*batinclude\s+maindrv\.mod)")
MAX_LOG = 400  # lines kept per step


def run_copy(run: Path, until: str, solver: str = "CPLEX") -> str:
    """The run file's text for otui's GAMS run: the solver; SOLVE_NOW NO to stop after
    preprocessing."""
    text = run.read_text(encoding="utf-8", errors="replace")
    text = LP_OPTION.sub(f"option LP={solver};", text)
    if not LP_OPTION.search(text):
        text = f"option LP={solver};\n" + text
    if until == "preprocess":
        text = MAINDRV.sub(r"$SET SOLVE_NOW NO\n\1", text, count=1)
    return text


PROBE_ROWS = 6000  # above GAMS's free (demo and community) size limits for LP


def probe_model(solvers: list[str]) -> str:
    """A GAMS job that solves one LP of PROBE_ROWS rows and columns with each solver, and writes
    "<solver> <solvestat> <modelstat>" lines (or "<solver> none" if not installed) to probe.txt.
    Any solver runs a small model without a licence, so only a model above the free limits says
    whether this computer's licence includes it."""
    lines = [
        f"set i / i1*i{PROBE_ROWS} /;",
        "positive variable x(i);",
        "variable z;",
        "equation obj, c(i);",
        "obj.. z =e= sum(i, x(i));",
        "c(i).. x(i) =g= ord(i);",
        "model m / all /;",
        "m.solprint = 2; m.limrow = 0; m.limcol = 0; m.optfile = 0;",
        "file f / 'probe.txt' /;",
        "put f;",
    ]
    for s in solvers:
        lines += [
            f"$ifThen.{s} solver {s}",
            f"option lp = {s};",
            "solve m using lp minimizing z;",
            f"put '{s} ' m.solvestat:0:0 ' ' m.modelstat:0:0 /;",
            f"$else.{s}",
            f"put '{s} none' /;",
            f"$endif.{s}",
        ]
    return "\n".join(lines) + "\n"


def probe(settings: dict[str, str], timeout: float = 120.0) -> dict[str, str]:
    """Which of SOLVERS this computer's GAMS licence runs: {GAMS name: ""} for those it does,
    {GAMS name: why not} for the others. About 1.5 s (GAMS 54)."""
    import tempfile

    names = [n for n, _, _ in SOLVERS]
    with tempfile.TemporaryDirectory(prefix="otui-gams-", ignore_cleanup_errors=True) as d:
        (Path(d) / "probe.gms").write_text(probe_model(names), encoding="utf-8")
        try:
            subprocess.run(
                [settings["exe"], "probe.gms", "lo=0", "o=probe.lst"],
                cwd=d,
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return {n: f"GAMS did not run ({e})" for n in names}
        out = Path(d) / "probe.txt"
        text = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
    found = dict(line.split(" ", 1) for line in text.splitlines() if " " in line)
    why: dict[str, str] = {}
    for n in names:
        r = found.get(n, "").split()
        if not r:
            why[n] = "GAMS's licence check did not finish"
        elif r[0] == "none":
            why[n] = "not installed in this GAMS"
        elif r[:2] == ["1", "1"]:
            why[n] = ""
        elif r[0] == "7":
            why[n] = "not in this computer's GAMS licence"
        else:
            why[n] = f"GAMS's test solve failed (status {' '.join(r)})"
    return why


def kill(proc: subprocess.Popen):
    if os.name == "nt":  # gams.exe starts child processes: end the whole tree
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.kill()


def run(
    run_file: Path,
    files: Path,
    out: Path,
    settings: dict[str, str],
    until: str = "results",
    threads: int = 12,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    solver: str = "CPLEX",
    on_proc: Callable[[subprocess.Popen], Any] | None = None,
) -> dict[str, Any]:
    """Run ``run_file`` (its DD files in ``files``) with GAMS TIMES and ``solver`` (a GAMS name,
    SOLVERS) in ``out``."""
    sid = next((i for n, i, _ in SOLVERS if n == solver), solver.lower())
    start = SOLVE_START.get(solver)
    on_event = on_event or (lambda e: None)
    out.mkdir(parents=True, exist_ok=True)
    name = run_file.stem
    for f in files.glob("*.opt"):  # solver option files (cplex.opt) are read from the work folder
        shutil.copyfile(f, out / f.name)
    copy = out / f"{name}.run"
    copy.write_text(run_copy(run_file, until, solver), encoding="utf-8")
    cmd = [
        settings["exe"],
        copy.name,
        f"idir1={settings['times_source']}",
        f"idir2={files}",
        "filecase=4",
        "lo=3",
        f"o={name}.lst",
        f"threads={threads}",
        f"gdx={name}.gdx",
    ]
    if until == "read":
        cmd.append("action=c")

    res: dict[str, Any] = {"model": name, "until": until, "solver": sid, "steps": []}
    steps: list[dict[str, Any]] = res["steps"]
    stop_at = STEPS[STEPS.index(until) + 1] if until != "results" else None
    t_start = time.time()
    anchors = [(t_start, 0.0)]  # (this computer's clock, GAMS's elapsed seconds)
    current = {"step": "read", "t0": t_start, "log": []}

    def finish(state: str = "done"):
        d = {
            "step": current["step"],
            "state": state,
            "seconds": time.time() - current["t0"],
            "log": current["log"][-MAX_LOG:],
            "t0": current["t0"],
            "t1": time.time(),
        }
        steps.append(d)
        on_event({k: v for k, v in d.items() if k not in ("t0", "t1")})

    proc = subprocess.Popen(
        cmd,
        cwd=out,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    if on_proc:  # so the job can be killed (server: Kill the job), before its first step shows
        on_proc(proc)
    on_event({"step": "read", "state": "running"})
    stopped = False
    assert proc.stdout
    for line in proc.stdout:
        line = line.rstrip("\r\n")
        if m := ELAPSED.search(line):
            h, mi, s = m.groups()
            anchors.append((time.time(), int(h) * 3600 + int(mi) * 60 + float(s)))
        found = next(
            (step for pat, step in MARKERS if pat.search(line)),
            "solve" if start and start.search(line.lstrip()) else None,
        )
        # without a known start line, the solver's run starts at the hand-over
        news = [found] if found != "pass" or start else ["pass", "solve"]
        for step in news if found and STEPS.index(found) > STEPS.index(current["step"]) else []:
            finish()
            if step == stop_at:
                kill(proc)
                stopped = True
                break
            current = {"step": step, "t0": time.time(), "log": []}
            on_event({"step": step, "state": "running"})
        if stopped:
            break
        current["log"].append(line)
    code = proc.wait()
    if not stopped:
        ok = code == 0
        finish("done" if ok else "failed")
        if not ok:
            raise RuntimeError(
                f"GAMS stopped with code {code}:\n" + "\n".join(current["log"][-30:])
            )

    # step times on GAMS's clock: interpolate between the lines GAMS stamped
    anchors.sort()

    def gclock(t: float) -> float:
        for (w0, g0), (w1, g1) in zip(anchors, anchors[1:], strict=False):
            if t <= w1:
                return g0 + (g1 - g0) * ((t - w0) / (w1 - w0) if w1 > w0 else 1.0)
        return anchors[-1][1] + (t - anchors[-1][0])

    for s in steps:
        s["seconds"] = max(0.0, gclock(s.pop("t1")) - gclock(s.pop("t0")))
        on_event(s)

    # what GAMS left: listing (objective, statuses, model size) and TIMES's QA log
    lst = out / f"{name}.lst"
    text = lst.read_text(encoding="utf-8", errors="replace") if lst.exists() else ""
    if m := re.search(r"\*\*\*\* OBJECTIVE VALUE\s+([-\d.Ee+]+)", text):
        res["objective"] = float(m.group(1))
    if m := re.search(r"\*\*\*\* MODEL STATUS\s+\d+\s+(.+)", text):
        res["termination"] = m.group(1).strip().upper().replace(" ", "_")
    # the versions that ran: TIMES (the title initsys.mod sets, after any title of the run file's
    # own, so the last one) and GAMS (the listing's first line)
    versions = [
        f"{what} {found[-1 if what == 'TIMES' else 0]}"
        for what, pat in (("TIMES", r"TIMES -- VERSION (\S+)"), ("GAMS", r"^GAMS (\d+\.\d+\.\d+)"))
        if (found := re.findall(pat, text, re.M))
    ]
    if versions:
        res["version"] = ", ".join(versions)
    log = "\n".join(line for s in steps for line in s["log"])
    if m := re.search(r"---\s+([\d,]+) rows\s+([\d,]+) columns", log):
        res["rows"], res["columns"] = (int(x.replace(",", "")) for x in m.groups())
    for qa in (out / "QA_CHECK.LOG", out / "qa_check.log"):
        if qa.exists():
            res["qa_log"] = qa.read_text(encoding="utf-8", errors="replace")
            break
    if stopped or until not in ("results", "solve"):  # no solution was asked for
        res.pop("objective", None)
        res.pop("termination", None)
    secs = {s["step"]: s["seconds"] for s in steps}
    res["solve_seconds"] = secs.get("solve")
    last = steps[-1]["step"]
    if until in ("results", "solve") and not stopped:
        res["status"] = "optimal" if res.get("termination") == "OPTIMAL" else "not_optimal"
    else:
        res["status"] = "generated" if last in ("build", "pass") else "stopped"
    res["stopped_after"] = until
    # the Results tab: TIMES's own report attributes, from the GDX file, with GAMS's gdxdump
    gdx, gdxdump = out / f"{name}.gdx", Path(settings["exe"]).with_name("gdxdump.exe")
    if not gdxdump.exists():
        gdxdump = gdxdump.with_suffix("")  # macOS, Linux
    if res["status"] == "optimal" and gdx.exists() and gdxdump.exists():
        from otui import results

        try:
            res["results"] = results.from_gdx(gdx, gdxdump, out / "results")
        except Exception as e:  # noqa: BLE001 - the run itself succeeded
            res["results_error"] = str(e)[:2000]
    return res
