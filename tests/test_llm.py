"""Model adapters against fake local servers; remote guardrails; fail-closed behaviour;
and the core guarantee: no raw value ever reaches a prompt."""
import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from zerotrace.classifier import llm
from zerotrace.config import Config
from zerotrace.detectors import Finding
from zerotrace.policy.engine import decide

from .conftest import Fake, rand


class _Server:
    def __init__(self, reply: dict, digest: str = "sha256:abc123"):
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, body: dict):
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/tags":
                    self._send({"models": [{"name": "m", "digest": digest.removeprefix("sha256:")}]})
                else:
                    self._send({"data": [{"id": "m"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                body["_auth"] = self.headers.get("Authorization")
                outer.requests.append(body)
                content = json.dumps(reply)
                if self.path == "/api/chat":
                    self._send({"message": {"content": content}})
                else:
                    self._send({"choices": [{"message": {"content": content}}]})

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    servers = []

    def make(reply, **kw):
        s = _Server(reply, **kw)
        servers.append(s)
        return s
    yield make
    for s in servers:
        s.close()


def _medium(value: str, line: str | None = None, path: str = "src/app.py") -> Finding:
    line = line or f'client_secret = "{value}"'
    return Finding(rule_id="hardcoded-secret", kind="hardcoded_secret", severity="medium",
                   confidence=0.6, path=path, line_no=3, file_class="code", line_text=line,
                   context_snippet=f"# comment\n{line}\nother = 1", source="code_assign",
                   identifier="client_secret", matched_value=value)


def _cfg(url: str, **kw) -> Config:
    return replace(Config(), model_endpoint=url, model_name="m", **kw)


def test_ollama_adapter_and_schema(server, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = server({"classification": "REAL_SECRET", "confidence": 0.93, "reason": "random key"})
    value = rand(30)
    verdict = llm.classify(_medium(value), _cfg(s.url))
    assert verdict.classification == "REAL_SECRET"
    req = s.requests[0]
    assert req["format"]["properties"]["classification"]["enum"]  # constrained decoding
    assert req["keep_alive"] == "30m" and req["options"]["temperature"] == 0
    assert value not in json.dumps(req)


def test_openai_adapter_with_bearer_token(server, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ZEROTRACE_MODEL_TOKEN", "t0k")
    s = server({"classification": "TEST_FIXTURE_OR_PLACEHOLDER", "confidence": 0.8, "reason": "x"})
    verdict = llm.classify(_medium(rand(30)), _cfg(s.url + "/v1", model_runtime="openai"))
    assert verdict.classification == "TEST_FIXTURE_OR_PLACEHOLDER"
    assert s.requests[0]["_auth"] == "Bearer t0k"
    assert s.requests[0]["response_format"]["type"] == "json_schema"


def test_escalation_and_downgrade_paths(server, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    real = server({"classification": "REAL_SECRET", "confidence": 0.9, "reason": "r"})
    assert decide(_medium(rand(30)), _cfg(real.url)).action == "block"
    fake = server({"classification": "TEST_FIXTURE_OR_PLACEHOLDER", "confidence": 0.9, "reason": "f"})
    assert decide(_medium(rand(30)), _cfg(fake.url)).action == "allow"
    unsure = server({"classification": "UNKNOWN", "confidence": 0.9, "reason": "u"})
    assert decide(_medium(rand(30)), _cfg(unsure.url)).action == "warn"


def test_remote_endpoint_needs_opt_in_and_https(server, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = _cfg("http://inference.corp.example:8000")
    with pytest.raises(llm.EndpointRefused):
        llm.check_endpoint(cfg)
    with pytest.raises(llm.EndpointRefused):
        llm.check_endpoint(replace(cfg, model_allow_remote=True))  # still http
    llm.check_endpoint(replace(cfg, model_endpoint="https://inference.corp.example",
                               model_allow_remote=True))
    assert decide(_medium(rand(30)), cfg).action == "warn"  # refused -> fail closed


def test_digest_mismatch_fails_closed(server, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = server({"classification": "TEST_FIXTURE_OR_PLACEHOLDER", "confidence": 0.99, "reason": "x"},
               digest="sha256:served")
    llm._digest_cache.clear()
    cfg = _cfg(s.url, model_digest="sha256:pinned-other")
    assert llm.classify(_medium(rand(30)), cfg) is None
    assert decide(_medium(rand(30)), cfg).action == "warn"


def test_echoed_value_is_discarded(server, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    value = rand(30)
    s = server({"classification": "TEST_FIXTURE_OR_PLACEHOLDER", "confidence": 0.9,
                "reason": f"value {value} is fake"})
    assert llm.classify(_medium(value), _cfg(s.url)) is None


def test_prompt_injection_in_file_cannot_reach_decision_for_high(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = replace(_medium(Fake.stripe_live()), severity="critical")
    monkeypatch.setattr(llm, "classify", lambda *a, **k: pytest.fail("model consulted"))
    assert decide(f, Config()).action == "block"


@pytest.mark.parametrize("make", [Fake.stripe_live, Fake.github, Fake.openai, Fake.aws_key_id,
                                  lambda: rand(32), lambda: rand(12) + "!@#" + rand(6)])
def test_no_value_ever_reaches_a_prompt(make):
    """Property: the candidate AND every other detected value on nearby lines is masked."""
    for _ in range(25):
        value, neighbour = make(), Fake.github()
        window = (f'# old: {value}\nconfig = {{"token": "{neighbour}", "k": "{value}"}}\n'
                  f"KEY={value}\nurl = 'https://x.test/?sig={value}'")
        finding = replace(_medium(value, line=f'k = "{value}"'), context_snippet=window)
        msgs = llm.build_messages(finding, extra_values=(neighbour,))
        blob = json.dumps(msgs)
        assert value not in blob and neighbour not in blob
