"""The worker protocol, against a fake worker (no Julia needed)."""

import sys
from pathlib import Path

import pytest

from otui.bridge import MAX_THREADS, JuliaError, JuliaWorker

FAKE = [sys.executable, str(Path(__file__).parent / "fake_worker.py")]


def test_round_trip_and_log():
    with JuliaWorker(command=FAKE, start_timeout=30) as w:
        assert w.alive
        assert w.request("ping")["ok"]
        r = w.request("echo", a=1, b="x")
        assert r["args"]["a"] == 1 and r["args"]["b"] == "x"
        # lines without the prefix are kept as log, never mistaken for replies
        assert "noise between replies" in w.log
    assert not w.alive


def test_ids_increase():
    with JuliaWorker(command=FAKE, start_timeout=30) as w:
        a = w.request("ping")["id"]
        b = w.request("ping")["id"]
        assert b == a + 1


def test_error_reply_raises():
    with JuliaWorker(command=FAKE, start_timeout=30) as w:
        with pytest.raises(JuliaError, match="unknown command"):
            w.request("nope")
        assert w.request("ping")["ok"]  # the worker survives a failed request


def test_worker_exit_raises():
    w = JuliaWorker(command=FAKE, start_timeout=30).start()
    with pytest.raises(JuliaError, match="exited"):
        w.request("crash", timeout=30)
    w.close()


def test_thread_limit():
    assert JuliaWorker(command=FAKE, threads=48).threads == MAX_THREADS


def test_two_timesjl_environments(tmp_path, monkeypatch):
    from otui import bridge

    monkeypatch.setenv("OTUI_JULIA_ENV", str(tmp_path / "julia"))
    monkeypatch.delenv("OTUI_TIMESJL", raising=False)
    assert bridge.env_dir("pinned") == tmp_path / "julia"
    assert bridge.env_dir("latest") == tmp_path / "julia-latest"
    # the pinned project names a commit; the latest one TIMES.jl's main branch, no manifest
    pinned = bridge.sync_env(channel="pinned")
    latest = bridge.sync_env(channel="latest")
    sha = bridge.pinned_commit()
    assert len(sha) == 40 and sha in (pinned / "Project.toml").read_text()
    text = (latest / "Project.toml").read_text()
    assert 'rev = "main"' in text and sha not in text
    assert (pinned / "Manifest.toml").exists() and not (latest / "Manifest.toml").exists()
    assert (latest / "worker.jl").exists()
    # latest first once it is set up; OTUI_TIMESJL=pinned forces the pinned one
    assert bridge.channels() == ["pinned"]
    (latest / "Manifest.toml").write_text("")
    (latest / bridge.COMMIT_FILE).write_text("abc\n")
    assert bridge.channels() == ["latest", "pinned"]
    assert bridge.env_commit("latest") == "abc" and bridge.env_commit("pinned") == sha
    monkeypatch.setenv("OTUI_TIMESJL", "pinned")
    assert bridge.channels() == ["pinned"]


def test_latest_built_during_a_session_is_used_from_the_next(tmp_path, monkeypatch):
    from otui import bridge

    monkeypatch.setenv("OTUI_JULIA_ENV", str(tmp_path / "julia"))
    cur, nxt = bridge.env_dir("latest"), bridge.staging_dir()
    cur.mkdir()
    (cur / bridge.COMMIT_FILE).write_text("old\n")
    nxt.mkdir()
    assert not bridge.promote_latest()  # not finished: no commit file yet
    (nxt / bridge.COMMIT_FILE).write_text("new\n")
    assert bridge.promote_latest()
    assert (cur / bridge.COMMIT_FILE).read_text() == "new\n" and not nxt.exists()


def test_kill_ends_the_worker_mid_request():
    w = JuliaWorker(command=FAKE, start_timeout=30).start()
    w.kill()
    assert not w.alive
    with pytest.raises(JuliaError):
        w.request("ping", timeout=5)
