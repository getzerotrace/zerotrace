"""Exceptions: two stores, expiry, promotion into the reviewable file, pruning."""
import json
import subprocess
import sys

import pytest

from zerotrace.audit import exceptions

from .conftest import write


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _fingerprint(name: str = "abc") -> str:
    return name * 8


def test_local_exception_silences_a_finding(repo):
    fp = _fingerprint()
    assert not exceptions.is_active(fp)
    path = exceptions.add(fp, "reviewed false positive", ttl_days=30)
    assert path.endswith("exceptions.json") and ".git" in path      # local by default
    assert exceptions.is_active(fp)


def test_shared_file_lives_in_the_repo_and_is_committable(repo):
    fp = _fingerprint("shared")
    path = exceptions.add(fp, "vendored sample key", ttl_days=30, shared=True)
    assert path.endswith(exceptions.SHARED_FILE)
    payload = _read_json(path)
    assert "note" in payload and fp in payload["exceptions"]
    assert exceptions.is_active(fp)
    assert "reviewed" in payload["note"].lower() or "review" in payload["note"].lower()


def test_expired_exceptions_stop_applying(repo):
    fp = _fingerprint("old")
    exceptions.add(fp, "temporary", ttl_days=-1)
    assert not exceptions.is_active(fp)


def test_malformed_entry_is_not_an_exception(repo):
    path = exceptions.shared_path()
    write(path, json.dumps({"exceptions": {_fingerprint("bad"): {"reason": "no expiry"}}}))
    assert not exceptions.is_active(_fingerprint("bad"))


def test_promote_moves_local_entries_into_the_reviewable_file(repo):
    live, expired = _fingerprint("live"), _fingerprint("dead")
    exceptions.add(live, "still needed", ttl_days=10)
    exceptions.add(expired, "stale", ttl_days=-1)

    moved, path = exceptions.promote()
    assert moved == 1 and path.endswith(exceptions.SHARED_FILE)
    shared = _read_json(path)["exceptions"]
    assert live in shared and expired not in shared
    assert exceptions.is_active(live)
    assert live not in exceptions._read(exceptions.local_path())   # no longer duplicated


def test_prune_drops_only_expired_entries(repo):
    exceptions.add(_fingerprint("keep"), "current", ttl_days=5)
    exceptions.add(_fingerprint("drop"), "over", ttl_days=-1, shared=True)
    assert exceptions.prune() == 1
    assert exceptions.is_active(_fingerprint("keep"))


def test_listing_reports_both_scopes(repo):
    exceptions.add(_fingerprint("l"), "local one", ttl_days=5)
    exceptions.add(_fingerprint("s"), "shared one", ttl_days=5, shared=True)
    scopes = {row.scope for row in exceptions.listing()}
    assert scopes == {exceptions.LOCAL, exceptions.SHARED}


def test_new_entries_name_the_rule_and_file_but_never_the_value(repo, fake):
    value = fake.stripe_live()
    exceptions.add(_fingerprint("m"), "vendor sample", ttl_days=5,
                   rule_id="stripe-live-key", path="pay.py")
    (row,) = exceptions.listing()
    assert (row.rule_id, row.path, row.reason) == ("stripe-live-key", "pay.py", "vendor sample")
    with open(exceptions.local_path(), encoding="utf-8") as f:
        assert value not in f.read()


def test_old_entries_without_rule_or_file_still_list(repo):
    write(exceptions.shared_path(), json.dumps({"exceptions": {_fingerprint("o"): {
        "reason": "from v0.1", "expires_at": "2999-01-01T00:00:00+00:00"}}}))
    (row,) = exceptions.listing()
    assert row.active and row.rule_id == "" and row.path == ""


def test_promote_can_move_a_single_exception(repo):
    first, second = _fingerprint("one"), _fingerprint("two")
    exceptions.add(first, "ready for review", ttl_days=5)
    exceptions.add(second, "still local", ttl_days=5)
    moved, _ = exceptions.promote([first])
    assert moved == 1
    scopes = {row.fingerprint: row.scope for row in exceptions.listing()}
    assert scopes == {first: exceptions.SHARED, second: exceptions.LOCAL}


def test_revoke_removes_one_exception_from_one_store(repo):
    fp = _fingerprint("gone")
    exceptions.add(fp, "no longer true", ttl_days=5, shared=True)
    assert exceptions.revoke(fp, exceptions.SHARED) is True
    assert not exceptions.is_active(fp)
    assert exceptions.revoke(fp, exceptions.SHARED) is False, "revoking twice changes nothing"


def test_revoke_refuses_an_unknown_scope(repo):
    with pytest.raises(ValueError):
        exceptions.revoke(_fingerprint(), "everywhere")


def test_a_malformed_shared_file_is_not_trusted(repo):
    """The shared file arrives through pull requests; a non-object entry is not an exception."""
    write(exceptions.shared_path(), json.dumps({"exceptions": {_fingerprint("x"): "yes"}}))
    assert not exceptions.is_active(_fingerprint("x"))
    assert exceptions.listing() == []
    write(exceptions.shared_path(), json.dumps(["not", "an", "object"]))
    assert exceptions.listing() == []


def test_exceptions_cli_lists_and_promotes(repo):
    exceptions.add(_fingerprint("cli"), "needs review", ttl_days=7)
    listed = subprocess.run([sys.executable, "-m", "zerotrace", "exceptions"],
                            capture_output=True, text=True)
    assert "needs review" in listed.stdout

    promoted = subprocess.run([sys.executable, "-m", "zerotrace", "exceptions", "--promote"],
                              capture_output=True, text=True)
    assert "moved 1" in promoted.stdout
    assert (repo / exceptions.SHARED_FILE).exists()


def test_a_shared_exception_actually_allows_the_commit(repo, fake):
    """End to end: fingerprint the finding, record it as reviewed, watch the block clear."""
    from zerotrace import pipeline
    from zerotrace.audit.fingerprint import of_finding
    from zerotrace.collectors.staged_diff import collect_staged
    from zerotrace.config import load_config

    write("pay.py", f'KEY = "{fake.stripe_live()}"\n')
    subprocess.run(["git", "add", "-A"], check=True)
    cfg = load_config()
    (decision,) = pipeline.scan(collect_staged(), cfg, use_model=False)
    assert decision.action == "block"

    exceptions.add(of_finding(decision.finding), "vendor sample, rotated", 30, shared=True)
    (after,) = pipeline.scan(collect_staged(), cfg, use_model=False)
    assert after.action == "allow" and "exception" in after.reason
