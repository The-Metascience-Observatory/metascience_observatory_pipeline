"""Search checkpointing: a failed query must be retried, and a raised cap re-runs."""
import pytest

from mo_pipeline.discover import search_for_replication_studies as s


@pytest.fixture
def loop(monkeypatch):
    monkeypatch.setattr(s, "dedup_and_append", lambda recs: len(recs))
    monkeypatch.setattr(s, "save_progress", lambda p: None)
    monkeypatch.setattr(s.time, "sleep", lambda x: None)
    monkeypatch.setattr(s, "MAX_PER_QUERY", 1000)
    return s._run_query_loop


def test_failed_query_is_not_checkpointed(loop):
    def fetch(q):
        if q == "bad":
            s._REQUEST_FAILED = True          # what _http_get_json does on a 429
            return [{"title": "partial"}]
        return [{"title": q}]
    progress = {"completed_queries": []}
    loop("openalex", ["good", "bad"], fetch, progress)
    assert progress["completed_queries"] == ["openalex:good"]


def test_raising_the_cap_reruns_and_legacy_entries_count_as_default(loop, monkeypatch):
    calls = []
    progress = {"completed_queries": ["osf:q"]}        # legacy: no recorded cap
    loop("osf", ["q"], lambda q: calls.append(q) or [], progress)
    assert calls == []                                 # 1000 >= 1000: skipped
    monkeypatch.setattr(s, "MAX_PER_QUERY", 5000)
    loop("osf", ["q"], lambda q: calls.append(q) or [], progress)
    assert calls == ["q"] and progress["query_caps"]["osf:q"] == 5000
    assert progress["completed_queries"].count("osf:q") == 1


def test_http_terminal_failure_sets_the_flag(monkeypatch):
    class R:
        status_code = 429
        text = ""
    monkeypatch.setattr(s.requests, "get", lambda *a, **k: R())
    monkeypatch.setattr(s.time, "sleep", lambda x: None)
    s._REQUEST_FAILED = False
    assert s._http_get_json("http://x", retries=2) is None
    assert s._REQUEST_FAILED is True


def test_semantic_scholar_waits_between_queries(loop, monkeypatch):
    waits = []
    monkeypatch.setattr(s.time, "sleep", waits.append)
    loop("semantic_scholar", ["a", "b"], lambda q: [], {"completed_queries": []})
    assert waits == [s.S2_DELAY, s.S2_DELAY]
