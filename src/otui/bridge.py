"""Bridge to TIMES.jl through a long-lived Julia worker.

otui uses TIMES.jl's DD and run-file reader (M1.4) instead of a parser of its
own. A Julia worker process (``julia/worker.jl``) is started once and kept
alive, so Julia's start-up and compile time is paid once per otui session,
not once per model. Requests and replies are JSON lines (see worker.jl).
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

PREFIX = "@@OTUI "
JULIA_SRC = Path(__file__).parent / "julia"
ENV_FILES = ("Project.toml", "Manifest.toml", "worker.jl", "runchain.jl")

# At most 12 threads per run, so several runs can share one computer.
MAX_THREADS = 12


class JuliaError(RuntimeError):
    """The worker could not start, or a request failed in Julia."""


def julia_exe() -> str:
    exe = os.environ.get("OTUI_JULIA") or shutil.which("julia")
    if not exe:
        raise JuliaError(
            "Julia was not found. Install it from https://julialang.org/install/ "
            "(juliaup), or set OTUI_JULIA to the julia executable."
        )
    return exe


# Which TIMES.jl otui runs. TIMES.jl is in active development, so the most recent version is
# the default and the pinned one the backup:
#   latest  TIMES.jl's main branch, updated when main moves (julia-setup, or at `otui serve`)
#   pinned  the commit otui is tested against (julia/Project.toml and Manifest.toml)
# The server uses latest when its environment is set up and it reads and solves demo12 to the
# reference objective; otherwise pinned (server.Engine). OTUI_TIMESJL=pinned forces pinned.
CHANNELS = ("latest", "pinned")
TIMESJL_URL = "https://github.com/esma-ie/timesjl.git"
PINNED_REV = re.compile(r'(TIMES = \{url = "[^"]+", rev = )"([0-9a-f]{40})"')
COMMIT_FILE = "TIMESJL_COMMIT"  # the TIMES.jl commit an environment was set up with
# TIMES.jl to main's newest commit: Pkg.update moves an installed TIMES.jl; a new environment
# (no manifest yet) resolves, which takes main's newest too
UPDATE_TIMES = (
    'isfile(joinpath(dirname(Base.active_project()), "Manifest.toml")) ? '
    'Pkg.update("TIMES") : Pkg.resolve(); '
)


def env_dir(channel: str = "pinned") -> Path:
    """Where otui's Julia environment for ``channel`` lives (writable, outside the installed
    package): ~/.otui/julia (pinned) and ~/.otui/julia-latest."""
    d = os.environ.get("OTUI_JULIA_ENV")
    base = Path(d) if d else Path.home() / ".otui" / "julia"
    return base if channel == "pinned" else base.with_name(base.name + "-latest")


def pinned_commit() -> str:
    m = PINNED_REV.search((JULIA_SRC / "Project.toml").read_text(encoding="utf-8"))
    return m.group(2) if m else ""


def is_set_up(channel: str) -> bool:
    return (env_dir(channel) / "Manifest.toml").exists() and (
        channel == "pinned" or (env_dir(channel) / COMMIT_FILE).exists()
    )


def channels() -> list[str]:
    """The TIMES.jl versions to try, in order: latest (if set up), then pinned."""
    want = os.environ.get("OTUI_TIMESJL", "latest").lower()
    if want == "pinned" or not is_set_up("latest"):
        return ["pinned"]
    return ["latest", "pinned"]


def env_commit(channel: str) -> str:
    """The TIMES.jl commit ``channel``'s environment was set up with."""
    if channel == "pinned":
        return pinned_commit()
    f = env_dir(channel) / COMMIT_FILE
    return f.read_text(encoding="utf-8").strip() if f.exists() else ""


def remote_main(timeout: float = 20.0) -> str:
    """TIMES.jl's main branch head on GitHub ("" if it cannot be read)."""
    try:
        r = subprocess.run(
            ["git", "ls-remote", TIMESJL_URL, "refs/heads/main"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.stdout.split()[0] if r.returncode == 0 and r.stdout.strip() else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def default_threads() -> int:
    return max(1, min(MAX_THREADS, os.cpu_count() or 1))


def sync_env(target: Path | None = None, channel: str = "pinned") -> Path:
    """Copy the bundled Julia project and worker to the environment folder. The latest channel
    gets the project with TIMES.jl's main branch and keeps its own manifest."""
    target = target or env_dir(channel)
    target.mkdir(parents=True, exist_ok=True)
    for name in ENV_FILES:
        src = JULIA_SRC / name
        if not src.exists() or (channel == "latest" and name == "Manifest.toml"):
            continue
        data = src.read_bytes()
        if channel == "latest" and name == "Project.toml":
            data = PINNED_REV.sub(r'\1"main"', data.decode("utf-8")).encode("utf-8")
        dst = target / name
        if not dst.exists() or dst.read_bytes() != data:
            dst.write_bytes(data)
    return target


def setup_env(target: Path | None = None, log=print, channel: str = "pinned") -> Path:
    """Instantiate otui's Julia environment for ``channel`` (downloads TIMES.jl and its
    dependencies). For latest, Pkg.update moves TIMES.jl to main's newest commit."""
    target = sync_env(target, channel)
    dev = os.environ.get("OTUI_TIMESJL_PATH")
    head = remote_main() if channel == "latest" and not dev else ""
    code = (
        "import Pkg; "
        # a fresh Julia has no registry, and Pkg.resolve (unlike instantiate) won't add one
        + 'isempty(Pkg.Registry.reachable_registries()) && Pkg.Registry.add("General"); '
        + (f'Pkg.develop(path=raw"{Path(dev).resolve()}"); ' if dev else "")
        + (UPDATE_TIMES if channel == "latest" and not dev else "Pkg.resolve(); ")
        + "Pkg.instantiate(); Pkg.precompile(); using TIMES; "
        + 'println("TIMES.jl ", pkgversion(TIMES), " ready")'
    )
    what = "TIMES.jl's main branch" if channel == "latest" else f"TIMES.jl {pinned_commit()[:7]}"
    log(f"Setting up otui's Julia environment with {what} in {target} (first time: minutes)")
    r = subprocess.run(
        [julia_exe(), "--startup-file=no", f"--project={target}", "-e", code],
        cwd=target,
    )
    if r.returncode != 0:
        raise JuliaError(f"Julia environment setup failed (exit {r.returncode})")
    if channel == "latest":
        (target / COMMIT_FILE).write_text((head or "main") + "\n", encoding="utf-8")
    return target


def staging_dir() -> Path:
    """Where the next latest environment is built while otui runs (promote_latest swaps it in)."""
    d = env_dir("latest")
    return d.with_name(d.name + "-next")


def promote_latest() -> bool:
    """At start, before any worker: swap in a latest environment built during the last session."""
    nxt, cur = staging_dir(), env_dir("latest")
    if not (nxt / COMMIT_FILE).exists():
        return False
    old = cur.with_name(cur.name + "-old")
    shutil.rmtree(old, ignore_errors=True)
    try:
        if cur.exists():
            cur.rename(old)
        nxt.rename(cur)
    except OSError:
        return False
    shutil.rmtree(old, ignore_errors=True)
    return True


def update_latest(log=lambda s: None) -> dict[str, str]:
    """If TIMES.jl's main has moved since the latest environment was set up, build the new one in
    the staging folder (used from the next start). Returns {"state", "commit", "message"}."""
    head = remote_main()
    have = env_commit("latest") if is_set_up("latest") else ""
    if not head:
        return {"state": "offline", "commit": have, "message": "GitHub not reachable"}
    if head == have:
        return {"state": "current", "commit": head, "message": ""}
    nxt = staging_dir()
    shutil.rmtree(nxt, ignore_errors=True)
    nxt.mkdir(parents=True)
    if (env_dir("latest") / "Manifest.toml").exists():  # move TIMES.jl only, keep the rest
        shutil.copyfile(env_dir("latest") / "Manifest.toml", nxt / "Manifest.toml")
    lines: list[str] = []
    env = dict(os.environ, JULIA_NUM_PRECOMPILE_TASKS="4")  # leave the machine to the runs
    code = (
        "import Pkg; " + UPDATE_TIMES + "Pkg.instantiate(); Pkg.precompile(); using TIMES; "
        'println("TIMES.jl ", pkgversion(TIMES), " ready")'
    )
    sync_env(nxt, "latest")
    r = subprocess.run(
        [julia_exe(), "--startup-file=no", f"--project={nxt}", "-e", code],
        cwd=nxt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    lines = (r.stdout + r.stderr).splitlines()
    log("\n".join(lines[-20:]))
    if r.returncode != 0:
        shutil.rmtree(nxt, ignore_errors=True)
        return {"state": "failed", "commit": head, "message": "\n".join(lines[-10:])}
    (nxt / COMMIT_FILE).write_text(head + "\n", encoding="utf-8")  # complete: promote at start
    return {"state": "staged", "commit": head, "message": "used from otui's next start"}


def wsl_path(p: str | os.PathLike) -> str:
    """A Windows path as WSL sees it: C:\\x\\y -> /mnt/c/x/y (other paths unchanged)."""
    s = str(p)
    if len(s) > 2 and s[1] == ":":
        return f"/mnt/{s[0].lower()}{s[2:]}".replace("\\", "/")
    return s.replace("\\", "/")


def cuopt_command(settings: dict[str, str], threads: int) -> list[str]:
    """The worker in WSL, with the Julia project that has cuOpt.jl (config.cuopt)."""
    worker = wsl_path(sync_env() / "worker.jl")
    env = f"source {settings['env']} && " if settings.get("env") else ""
    script = (
        f"{env}OTUI_SOLVER=cuopt exec julia --startup-file=no --project={settings['project']} "
        f"--threads={threads} {worker}"
    )
    return ["wsl", "-d", settings["distro"], "--", "bash", "-lc", script]


class JuliaWorker:
    """A TIMES.jl worker process. Use as a context manager, or call start() and close()."""

    def __init__(
        self,
        command: list[str] | None = None,
        threads: int | None = None,
        start_timeout: float = 900.0,
        wsl: bool = False,
        channel: str | None = None,
        wsl_distro: str = "",
    ):
        self.threads = min(threads or default_threads(), MAX_THREADS)
        self.channel = channel or channels()[0]  # which TIMES.jl: latest or pinned
        self._command = command
        self.wsl = wsl  # the worker runs in WSL (cuOpt): paths are passed as /mnt/<drive>/...
        self.wsl_distro = wsl_distro  # its WSL distribution, to end it from Windows (kill)
        self.pid: int | None = None  # the Julia process's id, as the worker reports it
        self.start_timeout = start_timeout
        self._proc: subprocess.Popen | None = None
        self._replies: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._next_id = 1
        self.log: deque[str] = deque(maxlen=2000)
        self.started_in: float | None = None

    # -- process ---------------------------------------------------------
    def _default_command(self) -> list[str]:
        d = sync_env(channel=self.channel)
        return [
            julia_exe(),
            "--startup-file=no",
            f"--project={d}",
            f"--threads={self.threads}",
            str(d / "worker.jl"),
        ]

    def start(self) -> JuliaWorker:
        if self._proc is not None:
            return self
        cmd = self._command or self._default_command()
        env = dict(os.environ, TIMESJL_THREADS=str(self.threads))
        t0 = time.perf_counter()
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        ready = self._wait(0, self.start_timeout)
        if not ready.get("ok"):
            raise JuliaError(f"Julia worker failed to start: {ready}")
        self.pid = ready.get("pid")
        self.started_in = time.perf_counter() - t0
        return self

    def _read_stdout(self):
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            line = line.rstrip("\r\n")
            if line.startswith(PREFIX):
                try:
                    self._replies.put(json.loads(line[len(PREFIX) :]))
                except json.JSONDecodeError:
                    self.log.append(line)
            else:
                self.log.append(line)
        self._replies.put({"id": None, "ok": False, "error": "worker exited", "eof": True})

    def _read_stderr(self):
        assert self._proc and self._proc.stderr
        for line in self._proc.stderr:
            self.log.append(line.rstrip("\r\n"))

    def _wait(self, rid: int, timeout: float, on_event=None) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise JuliaError(f"no reply from the Julia worker within {timeout:.0f} s")
            try:
                r = self._replies.get(timeout=left)
            except queue.Empty:
                continue
            if r.get("eof"):
                tail = "\n".join(list(self.log)[-30:])
                raise JuliaError(f"the Julia worker exited.\n{tail}")
            if r.get("id") == rid:
                if r.get("event") == "progress":  # a step started or ended: not the reply yet
                    if on_event:
                        on_event(r)
                    continue
                return r

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def close(self, timeout: float = 10.0):
        if self._proc is None:
            return
        try:
            if self.alive:
                self.request("quit", timeout=timeout)
        except JuliaError:
            pass
        try:
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=timeout)
        for pipe in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
            try:
                if pipe:
                    pipe.close()
            except OSError:
                pass
        self._proc = None

    def kill(self):
        """End the worker now, in the middle of a request (Kill the job): the request waiting on
        it fails with "the Julia worker exited". A Julia solve cannot be interrupted, so its
        process goes; the caller starts a new worker."""
        proc = self._proc
        if proc is None:
            return
        if self.wsl and self.wsl_distro and self.pid:  # Julia runs inside WSL: end it there
            try:
                subprocess.run(
                    ["wsl", "-d", self.wsl_distro, "--", "kill", "-9", str(self.pid)],
                    capture_output=True,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        if os.name == "nt":  # the process and any it started
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            proc.kill()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        self._proc = None

    def __enter__(self) -> JuliaWorker:
        return self.start()

    def __exit__(self, *exc):
        self.close()

    # -- requests --------------------------------------------------------
    def request(self, cmd: str, timeout: float = 3600.0, on_event=None, **args) -> dict[str, Any]:
        if not self.alive:
            raise JuliaError("the Julia worker is not running")
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            assert self._proc and self._proc.stdin
            try:
                self._proc.stdin.write(json.dumps({"id": rid, "cmd": cmd, **args}) + "\n")
                self._proc.stdin.flush()
            except OSError as e:  # the worker has exited (BrokenPipeError, or EINVAL on Windows)
                raise JuliaError(f"the Julia worker exited ({e})") from e
            r = self._wait(rid, timeout, on_event)
        if not r.get("ok"):
            raise JuliaError(r.get("error", "request failed"))
        return r

    def info(self) -> dict[str, Any]:
        return self.request("info")

    def demo_dir(self) -> Path:
        return Path(self.request("demo_dir")["dir"])

    def solve(
        self,
        run: str | os.PathLike,
        out: str | os.PathLike,
        dirs: list[str | os.PathLike] | None = None,
        globals: dict[str, str] | None = None,
        solve: bool = True,
        timeout: float = 4 * 3600.0,
        until: str | None = None,
        solver: str = "highs",
        on_event=None,
    ) -> dict[str, Any]:
        """Run the model on TIMES.jl, step by step up to ``until`` (default: all; ``solve=False``:
        up to the build), with HiGHS or cuOpt. ``on_event`` gets each step's progress."""
        path = self.path
        return self.request(
            "solve",
            timeout=timeout,
            on_event=on_event,
            run=path(run),
            out=path(out),
            dirs=[path(d) for d in (dirs or [])],
            globals=globals or {},
            until=until or ("results" if solve else "build"),
            solver=solver,
        )

    def path(self, p: str | os.PathLike) -> str:
        """A path as the worker sees it: absolute; a worker in WSL sees C:\\x as /mnt/c/x, and
        a path it gave itself (/home/..., e.g. its TIMES_Demo) comes back unchanged."""
        if not self.wsl:
            return str(Path(p).resolve())
        s = str(p)
        if s.startswith(("/", "\\")) and not s.startswith("\\\\"):
            return s.replace("\\", "/")
        return wsl_path(Path(s).resolve())

    def compile(
        self,
        run: str | os.PathLike,
        out: str | os.PathLike,
        dirs: list[str | os.PathLike] | None = None,
        globals: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Compile a run file and its DD files with TIMES.jl; write the Arrow dump to ``out``."""
        return self.request(
            "compile",
            run=str(Path(run).resolve()),
            out=str(Path(out).resolve()),
            dirs=[str(Path(d).resolve()) for d in (dirs or [])],
            globals=globals or {},
        )
