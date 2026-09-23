"""Where the tie-break model runs: Docker, the Ollama container, and the model itself.

Every Docker question ZeroTrace asks lives here - is Docker installed, is the daemon up, may
this user talk to it, is the image local, is our container running, does the model answer -
so the installer (`install.sh`), the Windows installer (`install.ps1`), `zerotrace doctor`,
`zerotrace model` and `zerotrace setup` all give the same answer in the same words. The two
shell installers call `zerotrace model status` rather than re-implementing any of it.

Nothing here is required for ZeroTrace to protect a repo. Without Docker there is no AI
tie-break: the deterministic rules still block every HIGH/CRITICAL finding and ambiguous
MEDIUM ones WARN instead of being settled (fail closed, ADR 0002). Every function says that
in its own words rather than treating a missing daemon as a failure.

The image reference is read out of the packaged compose file, so the pin lives in exactly one
place (`docker/docker-compose.yml`).
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import yaml

from . import platform_env
from .classifier import llm
from .config import zerotrace_home

CONTAINER = "zerotrace-ollama"
PROJECT = "zerotrace"                  # compose project name, also set in the compose file
_COMPOSE = "docker-compose.yml"
_ENTRYPOINT = "ollama-entrypoint.sh"

# Docker states, in the order they are worth reporting.
ABSENT, STOPPED, DENIED, UNRESPONSIVE, RUNNING = (
    "absent", "stopped", "denied", "unresponsive", "running")
# Container states.
GONE, CREATED, STARTING, UP, UNHEALTHY = "gone", "created", "starting", "up", "unhealthy"

_INFO_TIMEOUT = 15.0                   # `docker info` on a waking Docker Desktop is slow
_QUICK_TIMEOUT = 10.0
_HEALTH_TIMEOUT = 180.0                # the container pulls nothing; it just has to come up
_WAIT_ENV = "ZEROTRACE_MODEL_WAIT_SECONDS"


def _health_timeout() -> float:
    """How long to wait for the model server to answer. Three minutes covers a cold Docker
    Desktop on a laptop; a slow machine (or a test) can say otherwise."""
    try:
        return max(1.0, float(os.environ[_WAIT_ENV]))
    except (KeyError, ValueError):
        return _HEALTH_TIMEOUT

# Read from the daemon's own error text. "dial unix …" is deliberately NOT a permission
# marker: a stopped Docker Desktop on macOS says "dial unix …/docker.sock: connect: no such
# file or directory", which is a daemon that is not running, not a user who may not talk to it.
_DENIED_MARKERS = ("permission denied", "access is denied", "operation not permitted")
_STOPPED_MARKERS = (
    "cannot connect to the docker daemon", "is the docker daemon running",
    "failed to connect to the docker api", "the system cannot find the file specified",
    "error during connect", "docker daemon is not running", "open //./pipe/docker_engine",
    "connection refused", "no such file or directory",
)
_DETAIL_LIMIT = 120                    # the daemon's first line, not its first paragraph


@dataclass(frozen=True)
class Status:
    """What this machine can currently do about the model. `hint` is written for a person."""
    docker: str = ABSENT
    docker_version: str = ""
    compose: bool = False
    image: bool = False
    container: str = GONE
    served: bool = False               # the endpoint answers (Docker or native Ollama)
    model_ready: bool = False          # ... and it serves the model we ask for
    endpoint: str = ""
    model: str = ""
    local: bool = True                 # False when the endpoint is remote/OpenAI-compatible
    detail: str = ""                   # the daemon's own words, first line only
    hint: str = ""                     # the next step, in plain words

    @property
    def usable(self) -> bool:
        """True when a commit can actually get an AI tie-break right now."""
        return self.served and self.model_ready

    @property
    def headline(self) -> str:
        if self.usable:
            return "the AI tie-break is ready"
        if not self.local:
            return "the model is served remotely"
        return {
            ABSENT: "Docker is not installed",
            STOPPED: "Docker is installed, but the daemon is not running",
            DENIED: "Docker is installed, but this user cannot talk to the daemon",
            UNRESPONSIVE: "Docker is installed, but the daemon did not answer",
            RUNNING: "Docker is running",
        }[self.docker]


@dataclass
class Result:
    """The outcome of `up()` / `down()`: what happened, in order, and whether it worked."""
    ok: bool = False
    lines: list[str] = field(default_factory=list)
    status: Status | None = None

    def say(self, line: str) -> None:
        self.lines.append(line)


# --- running docker ---------------------------------------------------------------------

def docker_cli() -> str | None:
    return shutil.which("docker")


def _run(args: list[str], timeout: float) -> tuple[int, str, str]:
    """Run a docker command. A missing binary, a timeout or a crash is a non-zero code and a
    message, never an exception: a probe must not be able to break its caller."""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              check=False)
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout:.0f}s"
    except OSError as exc:
        return 127, "", str(exc)
    return done.returncode, done.stdout.strip(), done.stderr.strip()


def _first_line(text: str, limit: int = _DETAIL_LIMIT) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped if len(stripped) <= limit else stripped[:limit - 1] + "…"
    return ""


def _classify(stderr: str) -> tuple[str, str]:
    """(state, detail). The detail is the daemon's own words, kept only when we could NOT
    explain the failure ourselves - repeating "the daemon is not running" in Docker's phrasing
    under our own sentence is noise, but an error nobody anticipated must not be swallowed."""
    lowered = stderr.lower()
    if any(marker in lowered for marker in _DENIED_MARKERS):
        return DENIED, ""
    if any(marker in lowered for marker in _STOPPED_MARKERS):
        return STOPPED, ""
    if "timed out" in lowered:
        return UNRESPONSIVE, _first_line(stderr)
    return STOPPED, _first_line(stderr)     # unrecognised, but still not usable right now


# --- the compose file -------------------------------------------------------------------

def _candidate_dirs() -> list[str]:
    """Where the compose file and its entrypoint can be, richest source first.

    The compose file mounts the entrypoint by a RELATIVE path, so the two always travel
    together and a directory only counts when it holds both.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return [
        os.path.join(here, "docker"),                                    # installed wheel
        os.path.join(os.path.dirname(os.path.dirname(here)), "docker"),  # a source checkout
        os.path.join(zerotrace_home(), "docker"),                        # a copy we made
    ]


def compose_dir() -> str:
    """The directory holding a usable compose file, copying it into ~/.zerotrace if the
    install put it somewhere this process cannot hand to Docker as a path."""
    dirs = _candidate_dirs()
    for directory in dirs:
        if all(os.path.exists(os.path.join(directory, name))
               for name in (_COMPOSE, _ENTRYPOINT)):
            return directory
    home = dirs[-1]
    os.makedirs(home, exist_ok=True)
    for directory in dirs[:-1]:
        for name in (_COMPOSE, _ENTRYPOINT):
            source = os.path.join(directory, name)
            if os.path.exists(source):
                shutil.copyfile(source, os.path.join(home, name))
    return home


def compose_file() -> str:
    return os.path.join(compose_dir(), _COMPOSE)


def image_ref() -> str:
    """The pinned image from the compose file - the single place the pin is written."""
    try:
        with open(compose_file(), encoding="utf-8") as handle:
            spec = yaml.safe_load(handle) or {}
        image = ((spec.get("services") or {}).get("ollama") or {}).get("image")
        return str(image) if image else ""
    except (OSError, yaml.YAMLError):
        return ""


# --- what this machine can do ------------------------------------------------------------

def _container_state(docker: str) -> str:
    code, out, _ = _run([docker, "inspect", "--format",
                         "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}"
                         "{{else}}none{{end}}", CONTAINER], _QUICK_TIMEOUT)
    if code != 0 or not out:
        return GONE
    state, _, health = out.partition("|")
    if state != "running":
        return CREATED
    if health == "healthy" or health == "none":
        return UP
    if health == "starting":
        return STARTING
    return UNHEALTHY


def _hint_for(state: str, environment: str) -> str:
    """The next step, per OS. Written as an instruction, not as a diagnosis."""
    if state in (STOPPED, UNRESPONSIVE):
        start = {
            "macos": "start Docker Desktop (`open -a Docker`), wait for the whale to settle",
            "windows": "start Docker Desktop from the Start menu and wait for it to say running",
            "wsl": "start Docker Desktop on Windows and enable integration for this distro "
                   "(Settings > Resources > WSL integration)",
        }.get(environment, "start the daemon: `sudo systemctl start docker`")
        return f"{start}, then run `zerotrace model up`"
    if state == DENIED:
        return ("add yourself to the docker group: `sudo usermod -aG docker $USER`, then log "
                "out and back in (that group is root-equivalent - your admin may prefer "
                "rootless Docker), then run `zerotrace model up`")
    install = {
        "macos": "`brew install --cask docker`, or native Ollama (`brew install ollama`), "
                 "which is faster on Apple Silicon",
        "windows": "`winget install Docker.DockerDesktop`",
        "wsl": "install Docker Desktop on Windows and turn on WSL integration for this distro",
    }.get(environment, "docs.docker.com/engine/install")
    return (f"install Docker ({install}), then run `zerotrace model up`. Until then the "
            "deterministic rules still block secrets; ambiguous findings WARN")


def probe(cfg, check_model: bool = True) -> Status:
    """Everything worth knowing, in one pass. Never raises."""
    endpoint, model = cfg.model_endpoint, cfg.model_name
    local = not cfg.model_is_remote and cfg.model_runtime == "ollama"
    served = model_ready = False
    if check_model and cfg.model_enabled:
        llm.forget_digest()
        served = _endpoint_answers(cfg)
        model_ready = served and llm.model_digest(cfg) is not None
    if not local:
        return Status(docker=ABSENT, served=served, model_ready=model_ready, endpoint=endpoint,
                      model=model, local=False,
                      hint="" if model_ready else
                      f"the model is served by {endpoint}; ZeroTrace starts nothing locally")

    docker = docker_cli()
    environment = platform_env.detect().kind
    if docker is None:
        return Status(docker=ABSENT, served=served, model_ready=model_ready, endpoint=endpoint,
                      model=model, hint=_hint_for(ABSENT, environment))

    code, out, err = _run([docker, "info", "--format", "{{.ServerVersion}}"], _INFO_TIMEOUT)
    if code != 0:
        state, detail = _classify(err or out)
        return Status(docker=state, served=served, model_ready=model_ready, endpoint=endpoint,
                      model=model, detail=detail, hint=_hint_for(state, environment))

    _, client, _ = _run([docker, "version", "--format", "{{.Client.Version}}"], _QUICK_TIMEOUT)
    compose = _run([docker, "compose", "version"], _QUICK_TIMEOUT)[0] == 0
    reference = image_ref()
    image = bool(reference) and _run([docker, "image", "inspect", reference],
                                     _QUICK_TIMEOUT)[0] == 0
    container = _container_state(docker)
    status = Status(docker=RUNNING, docker_version=client or out, compose=compose, image=image,
                    container=container, served=served, model_ready=model_ready,
                    endpoint=endpoint, model=model,
                    detail="" if compose else "the `docker compose` plugin is missing")
    if status.usable:
        return status
    if not compose:
        return replace(status, hint="install the Docker Compose plugin (docs.docker.com/"
                                    "compose/install), then run `zerotrace model up`")
    return replace(status, hint="run `zerotrace model up` to start the model container"
                   + ("" if image else f" (this downloads {reference.split('@')[0]})"))


def _endpoint_answers(cfg, timeout: float = 3.0) -> bool:
    try:
        llm.http_json(cfg, "GET", "/api/tags" if cfg.model_runtime == "ollama" else "/v1/models",
                      None, timeout)
        return True
    except (llm.EndpointRefused, urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


# --- starting and stopping ----------------------------------------------------------------

def up(cfg, on_event: Callable[[str], None] | None = None, pull_model: bool = True,
       wait: float | None = None) -> Result:
    """Start the container and make sure the model is there. Reports; never raises."""
    wait = _health_timeout() if wait is None else wait
    result = Result()
    say = on_event or (lambda _line: None)

    def note(line: str) -> None:
        result.say(line)
        say(line)

    status = probe(cfg)
    result.status = status
    docker = docker_cli()
    if not status.local:
        note(f"the model is served by {status.endpoint}; nothing to start locally")
        result.ok = status.usable
        return result
    if status.docker != RUNNING or docker is None:
        note(f"{status.headline}. {status.hint}")
        return result
    if not status.compose:
        note(f"{status.hint}")
        return result
    if not os.path.exists(compose_file()):
        note(f"this install has no {_COMPOSE} ({compose_dir()}); reinstall ZeroTrace, or "
             "start Ollama yourself and point model.endpoint at it")
        return result

    reference = image_ref()
    if not status.image:
        note(f"pulling {reference.split('@')[0]} (first run only)")
    code, _, err = _stream([docker, "compose", "-f", compose_file(), "up", "-d"],
                           say, timeout=3600)
    if code != 0:
        note(f"docker compose could not start the model: {_first_line(err) or 'see the output above'}")
        return result
    note(f"container {CONTAINER} is up")

    if not _wait_for_endpoint(cfg, wait, say):
        note(f"{CONTAINER} did not answer on {cfg.model_endpoint} within {wait:.0f}s; "
             "`zerotrace model status` shows what it is doing")
        return result

    if pull_model and not pull(cfg, on_event=say):
        note(f"could not pull {cfg.model_name}; `zerotrace model up` retries it")
        return result
    llm.forget_digest()
    result.status = probe(cfg)
    result.ok = result.status.usable
    if result.ok:
        note(f"{cfg.model_name} is served at {cfg.model_endpoint}")
    return result


def down(purge: bool = False, on_event: Callable[[str], None] | None = None) -> Result:
    """Stop the container. The image and the model stay unless `purge` - they are a cache
    worth gigabytes, and an uninstall that silently re-downloads them later is a rude one."""
    result = Result()
    say = on_event or (lambda _line: None)
    docker = docker_cli()
    if docker is None:
        result.say("Docker is not installed; nothing to stop")
        result.ok = True
        return result
    args = [docker, "compose", "-f", compose_file(), "down"]
    if purge:
        args += ["--volumes", "--rmi", "all"]
    code, _, err = _stream(args, say, timeout=600)
    if code != 0:
        # A daemon that is not running has nothing of ours running either.
        state, _ = _classify(err)
        result.ok = state in (STOPPED, UNRESPONSIVE)
        result.say("Docker is not running; the model container is not running either"
                   if result.ok else f"could not stop the model container: {_first_line(err)}")
        return result
    result.ok = True
    result.say(f"removed the {CONTAINER} container"
               + (", its image and the model volume" if purge else ""))
    if not purge:
        result.say("the image and the downloaded model are kept; remove them with "
                   "`zerotrace model down --purge`")
    return result


def _stream(args: list[str], say: Callable[[str], None], timeout: float) -> tuple[int, str, str]:
    """Run a command, passing each line of its output on as it arrives.

    A timer kills the process rather than a deadline checked between lines: a pull that
    stalls produces no lines at all, which is exactly the case a timeout is for.
    """
    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1)
    except OSError as exc:
        return 127, "", str(exc)
    expired = threading.Event()

    def give_up() -> None:
        expired.set()
        process.kill()

    watchdog = threading.Timer(timeout, give_up)
    watchdog.daemon = True
    watchdog.start()
    tail: list[str] = []
    try:
        for raw in process.stdout or ():
            line = raw.rstrip()
            if line:
                tail.append(line)
                del tail[:-20]
                say(line)
        process.wait()
    finally:
        watchdog.cancel()
    if expired.is_set():
        return 124, "", f"timed out after {timeout:.0f}s"
    return process.returncode, "", "\n".join(tail)


def _wait_for_endpoint(cfg, seconds: float, say: Callable[[str], None]) -> bool:
    deadline = time.monotonic() + seconds
    told = False
    while time.monotonic() < deadline:
        if _endpoint_answers(cfg):
            return True
        if not told:
            say("waiting for the model server to come up")
            told = True
        time.sleep(1.0)
    return False


def pull(cfg, on_event: Callable[[str], None] | None = None,
         on_progress: Callable[[int, int], None] | None = None) -> bool:
    """Pull the model through Ollama's own API, so the download reports real progress.

    Ollama de-duplicates concurrent pulls of the same blob, so this is safe even though the
    container's entrypoint pulls the model too on a first start.
    """
    say = on_event or (lambda _line: None)
    if cfg.model_runtime != "ollama":
        return True
    payload = json.dumps({"model": cfg.model_name, "stream": True}).encode("utf-8")
    url = cfg.model_endpoint.rstrip("/") + "/api/pull"
    request = urllib.request.Request(url, data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
    last = ""
    try:
        with llm.opener(cfg).open(request, timeout=7200) as response:
            for raw in response:
                event = _event(raw)
                if event is None:
                    continue
                if event.get("error"):
                    say(f"ollama: {event['error']}")
                    return False
                state = str(event.get("status", ""))
                completed, total = int(event.get("completed", 0)), int(event.get("total", 0))
                if on_progress and total:
                    on_progress(completed, total)
                if state and state != last:
                    say(f"{cfg.model_name}: {state}")
                    last = state
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        say(f"could not pull {cfg.model_name}: {exc}")
        return False
    llm.forget_digest()
    return llm.model_digest(cfg) is not None


def _event(raw: bytes) -> dict | None:
    try:
        line = raw.decode("utf-8").strip()
        return json.loads(line) if line else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


# --- the human-readable report --------------------------------------------------------------

def rows(status: Status) -> list[tuple[str, str, str]]:
    """(status, check, result) rows - the same shape `doctor` uses, so both read alike."""
    ok, warn = "ok", "warn"
    if not status.local:
        return [(ok if status.model_ready else warn, "model host",
                 f"served by {status.endpoint} (no local container)")]
    out = [(ok if status.docker == RUNNING else warn, "docker",
            f"{status.docker_version or 'installed'} · running" if status.docker == RUNNING
            else status.headline + (f" ({status.detail})" if status.detail else ""))]
    if status.docker == RUNNING:
        reference = image_ref().split("@")[0]
        out.append((ok if status.image else warn, "model image",
                    f"{reference} present" if status.image
                    else f"{reference} not pulled yet (`zerotrace model up`)"))
        out.append((ok if status.container in (UP,) else warn, "model container",
                    {UP: f"{CONTAINER} running",
                     STARTING: f"{CONTAINER} starting",
                     UNHEALTHY: f"{CONTAINER} is unhealthy (`docker logs {CONTAINER}`)",
                     CREATED: f"{CONTAINER} exists but is stopped",
                     GONE: f"no {CONTAINER} container (`zerotrace model up`)"}[status.container]))
    out.append((ok if status.model_ready else warn, "model",
                f"{status.model} answers at {status.endpoint}" if status.model_ready
                else f"{status.model} is not being served; MEDIUM findings will WARN"))
    return out


def report(cfg, file=None) -> int:
    """`zerotrace model status`: the rows plus the one thing to do next. 0 when usable."""
    from rich.console import Console
    from rich.markup import escape
    from rich.table import Table

    from .ui import glyphs
    console = Console(file=file or sys.stdout)
    status = probe(cfg)
    marks = glyphs.for_console(console)
    table = Table(show_header=False, box=glyphs.box_for(console),
                  title=glyphs.sanitize("ZeroTrace · AI tie-break", console))
    table.add_column("", width=2)
    table.add_column("check", style="bold")
    table.add_column("result", overflow="fold")
    for state, check, result in rows(status):
        colour = {"ok": "green", "warn": "yellow"}[state]
        table.add_row(f"[{colour}]{marks.get(state, state)}[/]", check,
                      escape(glyphs.sanitize(result, console)))
    console.print(table)
    if not status.usable and status.hint:
        # highlight=False: rich's repr highlighter bolds the brackets and paths inside a
        # sentence, which makes an instruction look like a traceback.
        console.print(glyphs.sanitize(f"→ {status.hint}", console), highlight=False)
    return 0 if status.usable else 1
