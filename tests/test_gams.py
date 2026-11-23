"""CPLEX through GAMS TIMES: the run file copy, and the steps read from GAMS's log (a stand-in
for GAMS prints a recorded log, so no GAMS is needed)."""

import os
import stat
import sys
import time
from pathlib import Path

from otui import gams
from otui.bridge import wsl_path

RUN = """* a TIMES run file
option LP=CBC;
$SET RUN_NAME demo
$BATINCLUDE base.dd
$BATINCLUDE maindrv.mod mod
"""

LOG = """--- Job demo.run Start
--- Starting compilation
--- demo.run(4) 2 Mb
--- Starting execution: elapsed 0:00:00.800
--- Generating LP model TIMES
---   3,233 rows  4,485 columns  18,375 non-zeroes
--- Executing CPLEX (Solvelink=2): elapsed 0:00:01.000
--- Starting Cplex
Optimal solution found.
--- Reading solution for model TIMES
--- Executing after solve: elapsed 0:00:01.400
*** Status: Normal completion
--- Job demo.run Stop elapsed 0:00:01.500
"""

FAKE = """import pathlib, sys
log = pathlib.Path(sys.argv[1]).read_text()
args = sys.argv[2:]
until_read = "action=c" in args
for line in log.splitlines():
    print(line, flush=True)
    if until_read and line.startswith("--- demo.run"):
        break
if not until_read:
    pathlib.Path("demo.lst").write_text(
        "GAMS 54.6.1  20a39885 Oct 24, 2026          WEX-WEI x86 64bit/MS Windows\\n"
        "TIMES -- VERSION 4.1.0\\n"  # the run file's own title (demo12's)
        "TIMES -- VERSION 4.9.7\\n"  # initsys.mod's, which follows
        "**** SOLVER STATUS     1 Normal Completion\\n**** MODEL STATUS      1 Optimal\\n"
        "**** OBJECTIVE VALUE         23960685.7370\\n")
    pathlib.Path("QA_CHECK.LOG").write_text(" *01 WARNING - a check\\n")
"""


def fake_gams(tmp: Path) -> str:
    (tmp / "log.txt").write_text(LOG)
    (tmp / "fake.py").write_text(FAKE)
    py, script, log = sys.executable, tmp / "fake.py", tmp / "log.txt"
    if os.name == "nt":
        exe = tmp / "gams.cmd"
        exe.write_text(f'@"{py}" "{script}" "{log}" %*\n')
    else:
        exe = tmp / "gams"
        exe.write_text(f'#!/bin/sh\nexec "{py}" "{script}" "{log}" "$@"\n')
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def model(tmp: Path) -> Path:
    files = tmp / "files"
    files.mkdir()
    (files / "demo.run").write_text(RUN)
    (files / "base.dd").write_text("")
    (files / "cplex.opt").write_text("threads 4\n")
    return files / "demo.run"


def test_run_copy_sets_cplex_and_stops():
    run = Path(__file__).parent / "_tmp_demo.run"
    run.write_text(RUN)
    try:
        text = gams.run_copy(run, "results")
        assert "option LP=CPLEX;" in text and "CBC" not in text
        pre = gams.run_copy(run, "preprocess")
        assert "$SET SOLVE_NOW NO\n$BATINCLUDE maindrv.mod" in pre
        run.write_text("$BATINCLUDE maindrv.mod mod\n")
        assert gams.run_copy(run, "results").startswith("option LP=CPLEX;")
    finally:
        run.unlink()


def test_steps_from_the_log(tmp_path):
    settings = {"exe": fake_gams(tmp_path), "times_source": str(tmp_path)}
    run = model(tmp_path)
    events = []
    r = gams.run(run, run.parent, tmp_path / "out", settings, "results", 4, events.append)
    assert [s["step"] for s in r["steps"]] == list(gams.STEPS)
    assert r["status"] == "optimal" and r["objective"] == 23960685.737
    assert (r["rows"], r["columns"]) == (3233, 4485)
    assert r["version"] == "TIMES 4.9.7, GAMS 54.6.1"
    assert "a check" in r["qa_log"]
    assert (tmp_path / "out" / "cplex.opt").exists()  # option files go to the work folder
    assert "--- Starting Cplex" in r["steps"][4]["log"]
    # step times on GAMS's clock: read ends at its "elapsed 0.8", the solver call at 1.0
    secs = [s["seconds"] for s in r["steps"]]
    assert abs(sum(secs[:1]) - 0.8) < 0.3 and all(s >= 0 for s in secs)
    assert {e["state"] for e in events} == {"running", "done"}


def test_read_only(tmp_path):
    settings = {"exe": fake_gams(tmp_path), "times_source": str(tmp_path)}
    run = model(tmp_path)
    r = gams.run(run, run.parent, tmp_path / "out", settings, "read", 4)
    assert [s["step"] for s in r["steps"]] == ["read"]
    assert r["status"] == "stopped" and "objective" not in r


def test_wsl_path():
    assert wsl_path(r"C:\Users\x\model.run") == "/mnt/c/Users/x/model.run"
    assert wsl_path("/home/x") == "/home/x"


def test_another_solver_without_a_start_line(tmp_path):
    # MOSEK's own start line is not known: its hand-over counts in "solve"
    settings = {"exe": fake_gams(tmp_path), "times_source": str(tmp_path)}
    run = model(tmp_path)
    r = gams.run(run, run.parent, tmp_path / "out", settings, "results", 4, solver="MOSEK")
    assert r["solver"] == "mosek" and r["status"] == "optimal"
    assert [s["step"] for s in r["steps"]] == list(gams.STEPS)
    # "pass" opens and closes on the hand-over line, so it holds no log lines (its time is about 0,
    # but timing asserts are flaky on slow CI runners: 0.086 s once on macOS)
    assert not r["steps"][3]["log"]
    assert "--- Starting Cplex" in r["steps"][4]["log"]
    assert "option LP=MOSEK;" in (tmp_path / "out" / "demo.run").read_text()
    # stopping after "pass" still stops before the solver's run
    p = gams.run(run, run.parent, tmp_path / "out2", settings, "pass", 4, solver="MOSEK")
    assert [s["step"] for s in p["steps"]][-1] == "pass" and p["status"] == "generated"


PROBE = """import pathlib
pathlib.Path("probe.txt").write_text(
    "CPLEX 1 1\\nGUROBI 7 11\\nXPRESS none\\nMOSEK 7 11\\nCOPT none\\nHIGHS 1 1\\nCBC 1 1\\n")
"""


def test_probe_reads_the_licence(tmp_path):
    (tmp_path / "probe.py").write_text(PROBE)
    if os.name == "nt":
        exe = tmp_path / "gams.cmd"
        exe.write_text(f'@"{sys.executable}" "{tmp_path / "probe.py"}" %*\n')
    else:
        exe = tmp_path / "gams"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{tmp_path / "probe.py"}" "$@"\n')
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    why = gams.probe({"exe": str(exe)})
    assert [n for n, w in why.items() if not w] == ["CPLEX", "HIGHS", "CBC"]
    assert why["GUROBI"] == "not in this computer's GAMS licence"
    assert why["XPRESS"] == "not installed in this GAMS"
    assert why["SOPLEX"] == "GAMS's licence check did not finish"  # no line for it


def test_probe_model_is_above_the_free_limit():
    text = gams.probe_model(["CPLEX", "CBC"])
    assert "i1*i6000" in text and "$ifThen.CBC solver CBC" in text
    assert text.count("solve m using lp") == 2


SLOW = """import sys, time
print("--- Job demo.run Start", flush=True)
time.sleep(60)
"""


def test_kill_a_gams_job(tmp_path):
    import threading

    (tmp_path / "slow.py").write_text(SLOW)
    if os.name == "nt":
        exe = tmp_path / "gams.cmd"
        exe.write_text(f'@"{sys.executable}" "{tmp_path / "slow.py"}" %*\n')
    else:
        exe = tmp_path / "gams"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{tmp_path / "slow.py"}" "$@"\n')
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    run = model(tmp_path)
    procs, events = [], []
    out: dict = {}

    def go():
        try:
            gams.run(
                run,
                run.parent,
                tmp_path / "out",
                {"exe": str(exe), "times_source": "."},
                on_event=events.append,
                on_proc=procs.append,
            )
        except RuntimeError as e:
            out["error"] = str(e)

    t = threading.Thread(target=go)
    t.start()
    while not events:
        time.sleep(0.05)
    assert procs  # the process is known before the first step shows
    t0 = time.monotonic()
    gams.kill(procs[0])
    t.join(20)
    assert not t.is_alive() and time.monotonic() - t0 < 15
    assert "GAMS stopped" in out["error"]
