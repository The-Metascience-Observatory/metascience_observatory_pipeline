import json

import pytest

from mo_pipeline.extract.codex_backend import parse_events


def test_events_capture_final_reply_and_usage():
    events = [
        {"type": "thread.started", "thread_id": "test"},
        {"type": "error", "message": "Reconnecting... 2/5"},
        {"type": "item.completed", "item": {"type": "command_execution", "text": "ignore"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
        {"type": "turn.completed", "usage": {"input_tokens": 20, "cached_input_tokens": 10, "output_tokens": 5}},
    ]
    output = parse_events("\n".join(map(json.dumps, events)))
    assert output["result"] == "done"
    assert output["session_id"] == "test"
    assert output["usage"]["cache_read_input_tokens"] == 10


@pytest.mark.parametrize("events", [[], [{"type": "turn.failed", "error": {"message": "quota"}}]])
def test_incomplete_or_failed_stream_is_not_success(events):
    with pytest.raises(RuntimeError):
        parse_events("\n".join(map(json.dumps, events)))


def test_base_codex_writes_stat_free_result_without_claude_review(tmp_path, monkeypatch):
    from mo_pipeline.extract import extract as ex

    paper = tmp_path / "10.1234--test"
    paper.mkdir()
    (paper / "body.md").write_text("Readable article prose. " * 100)
    commands = []

    class Process:
        returncode = 0

        def __init__(self, cmd, **kwargs):
            commands.append(cmd)
            (paper / "test" / "result.json").write_text(json.dumps({
                "contains_replications": True,
                "replications": [{"confidence": "low", "result": "inconclusive", "replication_n": 99}],
            }))

        def communicate(self, **kwargs):
            return json.dumps({"type": "turn.completed", "usage": {}}), ""

    monkeypatch.setattr(ex.subprocess, "Popen", Process)
    monkeypatch.setattr(ex, "validate_extraction", lambda d: (d, []))
    monkeypatch.setattr(ex, "validate_original_dois", lambda d: ([], {}))
    monkeypatch.setattr(ex, "validate_citation_sentences", lambda *a: [])
    monkeypatch.setattr(ex, "enrich_metadata", lambda d, **kw: (d, []))
    monkeypatch.setattr(ex, "_write_provenance", lambda *a, **kw: None)
    data, usage, messages = ex.extract_paper(paper, model="gpt-5.6-luna", level="base", tag="test", use_codex=True)
    assert len(commands) == 1
    assert commands[0][:2] == ["codex", "exec"]
    assert "replication_n" not in data["replications"][0]
    assert data["replications"][0]["ai_version"].endswith("-base")
    assert (paper / "test" / f"{paper.name}_result.json").exists()
