"""Shared fixtures. Fake credentials are assembled at run time so this repo never contains a
realistic-looking secret (and neither ZeroTrace nor GitHub push protection trips on it)."""
import json
import os
import secrets
import shutil
import string
import subprocess
import sys

import pytest

from zerotrace import gitutil

ALNUM = string.ascii_letters + string.digits


def rand(n: int, alphabet: str = ALNUM) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(n))


class Fake:
    """Format-valid fake tokens, built from parts at run time."""

    @staticmethod
    def aws_key_id() -> str:
        return "AK" + "IA" + rand(16, "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")

    @staticmethod
    def stripe_live() -> str:
        return "sk" + "_live_" + rand(24)

    @staticmethod
    def github() -> str:
        return "gh" + "p_" + rand(36)

    @staticmethod
    def openai() -> str:
        return "sk-" + "proj-" + rand(48)

    @staticmethod
    def anthropic() -> str:
        return "sk-" + "ant-api03-" + rand(90, ALNUM + "-_")

    @staticmethod
    def google() -> str:
        return "AI" + "za" + rand(35)

    @staticmethod
    def slack() -> str:
        return "xo" + "xb-" + rand(12, string.digits) + "-" + rand(24)


@pytest.fixture
def fake() -> type[Fake]:
    return Fake


def _clear_git_caches() -> None:
    gitutil._toplevel.cache_clear()
    gitutil._common_dir.cache_clear()


@pytest.fixture
def git_env(tmp_path, monkeypatch):
    """Isolate git + ZeroTrace config from the developer's machine."""
    home = tmp_path / "home"
    home.mkdir()
    gitconfig = home / "gitconfig"
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("ZEROTRACE_HOME", str(home / ".zerotrace"))
    monkeypatch.setenv("ZEROTRACE_POLICY", str(home / "no-org-policy.yml"))
    monkeypatch.setenv("ZEROTRACE_MODEL_ENDPOINT", "http://127.0.0.1:9")  # nothing listens
    for key, value in (("user.email", "t@example.test"), ("user.name", "T"),
                       ("init.defaultBranch", "main")):
        subprocess.run(["git", "config", "--global", key, value], check=True)
    _clear_git_caches()
    yield home
    _clear_git_caches()


@pytest.fixture
def repo(git_env, tmp_path, monkeypatch):
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    monkeypatch.chdir(path)
    _clear_git_caches()
    return path


# --- a Docker that is whatever the test needs it to be ------------------------------------
# Every Docker state ZeroTrace has to handle (absent, stopped, permission denied, running
# without the image, running with it) is a line in a JSON file rather than a machine someone
# has to own. The fake is Python behind a launcher so it works on Windows too, where a `sh`
# script on PATH is not executable.

_FAKE_DOCKER = '''\
import json, os, sys, time
state = json.load(open(os.environ["FAKE_DOCKER_STATE"], encoding="utf-8"))
argv = sys.argv[1:]
log = open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8")
log.write(" ".join(argv) + "\\n")
log.close()


def save():
    with open(os.environ["FAKE_DOCKER_STATE"], "w", encoding="utf-8") as handle:
        json.dump(state, handle)


daemon = state.get("daemon", "running")
if argv[:1] == ["info"]:
    if daemon == "running":
        print(state.get("server", "27.1.1"))
        sys.exit(0)
    if daemon == "denied":
        sys.stderr.write("permission denied while trying to connect to the Docker daemon "
                         "socket at unix:///var/run/docker.sock\\n")
        sys.exit(1)
    if daemon == "slow":
        time.sleep(float(state.get("sleep", 30)))
        sys.exit(1)
    if daemon == "weird":
        sys.stderr.write("something nobody has seen before\\n")
        sys.exit(1)
    sys.stderr.write("Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
                     "Is the docker daemon running?\\n")
    sys.exit(1)
if argv[:1] == ["version"]:
    print(state.get("client", "27.1.1"))
    sys.exit(0)
if argv[:2] == ["compose", "version"]:
    sys.exit(0 if state.get("compose", True) else 1)
if argv[:2] == ["image", "inspect"]:
    sys.exit(0 if state.get("image") else 1)
if argv[:1] == ["inspect"]:
    container = state.get("container", "gone")
    if container == "gone":
        sys.stderr.write("Error: No such object\\n")
        sys.exit(1)
    print({"up": "running|healthy", "starting": "running|starting",
           "unhealthy": "running|unhealthy", "created": "exited|none"}[container])
    sys.exit(0)
if argv[:1] == ["compose"]:
    if "up" in argv:
        if not state.get("image"):
            print("Pulling ollama ...")
            state["image"] = True
        state["container"] = state.get("start_as", "up")
        save()
        print("Container zerotrace-ollama  Started")
        sys.exit(0)
    if "down" in argv:
        state["container"] = "gone"
        if "--rmi" in argv:
            state["image"] = False
        save()
        print("Container zerotrace-ollama  Removed")
        sys.exit(0)
sys.stderr.write("fake docker: unhandled " + " ".join(argv) + "\\n")
sys.exit(2)
'''


class FakeDocker:
    """Drive the fake: `fake_docker.set(daemon="stopped")`, then read `fake_docker.calls`."""

    def __init__(self, state_file, log_file):
        self._state_file = state_file
        self._log = log_file

    def set(self, **changes) -> None:
        state = json.loads(self._state_file.read_text(encoding="utf-8"))
        state.update(changes)
        self._state_file.write_text(json.dumps(state), encoding="utf-8")

    @property
    def state(self) -> dict:
        return json.loads(self._state_file.read_text(encoding="utf-8"))

    @property
    def calls(self) -> list[str]:
        if not self._log.exists():
            return []
        return [line for line in self._log.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    """A `docker` on PATH that answers however the test says. Defaults to a running daemon
    with no image and no container: the state a first install meets."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    implementation = bin_dir / "fake_docker_impl.py"
    implementation.write_text(_FAKE_DOCKER, encoding="utf-8")
    state_file = tmp_path / "fake-docker-state.json"
    state_file.write_text(json.dumps({"daemon": "running", "image": False, "container": "gone"}),
                          encoding="utf-8")
    log_file = tmp_path / "fake-docker.log"

    if os.name == "nt":
        launcher = bin_dir / "docker.bat"
        launcher.write_text(f'@echo off\r\n"{sys.executable}" "{implementation}" %*\r\n',
                            encoding="utf-8")
    else:
        launcher = bin_dir / "docker"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{implementation}" "$@"\n',
                            encoding="utf-8")
        launcher.chmod(0o755)

    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(state_file))
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log_file))
    return FakeDocker(state_file, log_file)


def is_docker_name(name: str) -> bool:
    return os.path.basename(str(name)).lower().split(".")[0] == "docker"


@pytest.fixture
def no_docker(monkeypatch):
    """A machine with no Docker - and everything else still there.

    Removing PATH entries is what an earlier version did, and it was wrong: on a Linux runner
    `docker` lives in /usr/bin next to `git`, so "no Docker" quietly became "no git" and seven
    tests failed for a reason none of them was about. The lookup itself is what gets an answer
    of None, for docker and nothing else.
    """
    real_which = shutil.which

    def which(cmd, *args, **kwargs):
        return None if is_docker_name(cmd) else real_which(cmd, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", which)
    return which


class FakeOllama:
    """A loopback Ollama: enough of /api/tags and /api/pull to drive the real client."""

    def __init__(self, server, models):
        self.server = server
        self.models = models
        self.pulls: list[str] = []

    @property
    def endpoint(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://127.0.0.1:{port}"


@pytest.fixture
def fake_ollama(monkeypatch):
    import http.server
    import threading

    state = {"models": [], "pulls": [], "fail": None}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):        # silence: pytest owns stderr
            return

        def _json(self, payload: dict, code: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/api/tags":
                self._json({"models": [{"name": name, "model": name,
                                        "digest": "sha256:" + "0" * 64}
                                       for name in state["models"]]})
            else:
                self._json({}, 404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path != "/api/pull":
                self._json({}, 404)
                return
            name = body.get("model") or body.get("name") or ""
            state["pulls"].append(name)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            if state["fail"]:
                self.wfile.write(json.dumps({"error": state["fail"]}).encode() + b"\n")
                return
            for done in (0, 500, 1000):
                self.wfile.write(json.dumps(
                    {"status": "pulling manifest" if not done else "downloading",
                     "completed": done, "total": 1000}).encode() + b"\n")
                self.wfile.flush()
            state["models"].append(name)
            self.wfile.write(json.dumps({"status": "success"}).encode() + b"\n")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fake = FakeOllama(server, state["models"])
    fake.state = state
    monkeypatch.setenv("ZEROTRACE_MODEL_ENDPOINT", fake.endpoint)
    yield fake
    server.shutdown()
    server.server_close()


def write(path, text: str) -> None:
    os.makedirs(os.path.dirname(str(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def git(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, input=input_text)
