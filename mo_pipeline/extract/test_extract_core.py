"""Tests for the single-shot core-fields extractor and the prompt mode renderer.

Run: cd mo_pipeline && python -m pytest mo_pipeline/extract -q
No model calls, no network: the backend and the metadata fetchers are faked.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mo_pipeline import config

if not config.ONTOLOGY_PATH.exists():  # extract.py loads the website ontology at import
    pytest.skip("website ontology not available (set MO_WEBSITE_DATA_DIR)", allow_module_level=True)

from mo_pipeline.discover import screening_backend as sb
from mo_pipeline.extract import extract as ex
from mo_pipeline.extract import extract_core as core

FOLDER = "10.1234--test.2020.1"
CORE_FIELDS_16 = set(core.CORE_FIELDS)


# ── prompt rendering ────────────────────────────────────────────────────────

def test_render_mode_keeps_one_side_and_strips_markers():
    text = ("a\n<!-- mode:full -->\nF1\nF2\n<!-- /mode -->\n<!-- mode:core -->\nC1\n<!-- /mode -->\n"
            "b,\n<!-- mode:full -->\n  \"k\": 1\n<!-- /mode -->\n}\n")
    assert ex._render_mode(text, "full") == "a\nF1\nF2\nb,\n  \"k\": 1\n}\n"
    # core: the other side, and the trailing comma left by the dropped key collapses
    assert ex._render_mode(text, "core") == "a\nC1\nb\n}\n"


def test_render_mode_rejects_malformed_blocks():
    with pytest.raises(ValueError):
        ex._render_mode("<!-- mode:full -->\n<!-- mode:core -->\n<!-- /mode -->\n", "full")
    with pytest.raises(ValueError):
        ex._render_mode("x\n<!-- /mode -->\n", "full")
    with pytest.raises(ValueError):
        ex._render_mode("<!-- mode:full -->\nx\n", "full")


def test_render_mode_axes_are_independent():
    """Statistics and output channel are separate axes: --level base is
    stat-free AND agentic, which one combined axis could not express."""
    text = ("<!-- mode:full -->\nSTATS\n<!-- /mode -->\n<!-- mode:core -->\nNOSTATS\n<!-- /mode -->\n"
            "<!-- mode:write -->\nWRITE\n<!-- /mode -->\n<!-- mode:reply -->\nREPLY\n<!-- /mode -->\n")
    assert ex._render_mode(text, ex._TAGS_FOR_LEVEL["full"]) == "STATS\nWRITE\n"
    assert ex._render_mode(text, ex._TAGS_FOR_LEVEL["base"]) == "NOSTATS\nWRITE\n"
    assert ex._render_mode(text, ex._TAGS_FOR_LEVEL["core"]) == "NOSTATS\nREPLY\n"


def test_full_prompt_equals_file_minus_other_modes_and_markers():
    """Rendering "full" must be the file with only the marker lines and the
    blocks tagged for other renderings removed: nothing else may change."""
    tags = ex._TAGS_FOR_LEVEL["full"]
    for path in (config.PROMPT_SHARED_CORE, config.PROMPT_FILES["full"]):
        raw = path.read_text()
        expected, skipping = [], False
        for line in raw.splitlines(keepends=True):
            m = ex._MODE_OPEN.match(line)
            if m:
                skipping = m.group(1) not in tags
                continue
            if ex._MODE_CLOSE.match(line):
                skipping = False
                continue
            if not skipping:
                expected.append(line)
        assert ex._render_mode(raw, tags) == "".join(expected), path.name
        assert "<!-- mode" not in ex._render_mode(raw, tags)


@pytest.mark.parametrize("level", ["core", "base"])
def test_stat_free_prompt_has_no_stats_and_a_valid_schema(level):
    prompt = ex.load_system_prompt(level)
    for f in ex.STAT_FIELDS:
        assert f not in prompt, f"{f} leaked into the {level} prompt"
    assert "<!--" not in prompt
    for f in core.CORE_FIELDS:
        assert f"`{f}`" in prompt or f'"{f}"' in prompt, f"{f} missing from {level} prompt"
    assert "psychology" in prompt  # discipline list substituted
    blocks = re.findall(r"```json\n(.*?)\n```", prompt, re.DOTALL)
    schema_blocks = [b for b in blocks if '"citation_sentence"' in b and "..." not in b]
    assert len(schema_blocks) == 1, "schema example block missing"
    schema = json.loads(schema_blocks[0])  # would fail on a trailing comma
    assert set(schema["replications"][0]) == CORE_FIELDS_16


def test_output_channel_matches_the_level():
    """The bug this guards: --level base rendered the single-shot "reply with
    JSON only" instruction, so agentic runs answered inline and their papers
    were lost. Agentic levels must always be told to write result.json."""
    for level in ("full", "base", "pdf_only", "html"):
        p = ex.load_system_prompt(level)
        assert "**Write** tool" in p, level
        assert "Reply with the JSON object only" not in p, level
    core = ex.load_system_prompt("core")
    assert "**Write** tool" not in core and "Reply with the JSON object only" in core


def test_full_prompt_still_carries_stats():
    prompt = ex.load_system_prompt("full")
    assert all(f in prompt for f in ex.STAT_FIELDS)
    assert "<!--" not in prompt


# ── references ──────────────────────────────────────────────────────────────

GROBID_REFS = [
    {"id": 1, "authors": ["Smith J", "Jones A"], "title": "A study", "journal": "J Ex",
     "volume": "4", "issue": "2", "pages": "1-9", "year": 2015, "doi": "10.1/abc"},
    {"id": 2, "authors": [], "title": "2. Raney KD (2010) Whole citation in title. J Biol Chem 285: 1.",
     "journal": None, "volume": None, "issue": None, "pages": None, "year": None, "doi": None},
    {"id": 3, "authors": [], "title": "", "journal": None, "year": None},  # empty -> dropped
]


def test_render_references_grobid_shape():
    out = core.render_references(GROBID_REFS).splitlines()
    assert out[0] == "[1] Smith J; Jones A (2015) A study. J Ex 4(2): 1-9. doi:10.1/abc"
    assert out[1].startswith("[2] 2. Raney KD (2010) Whole citation in title.")
    assert len(out) == 2


def test_render_references_raw_shape_and_cap():
    raw = [{"raw": f"Ref {i}"} for i in range(5)]
    out = core.render_references(raw, cap=3).splitlines()
    assert out == ["[1] Ref 0", "[2] Ref 1", "[3] Ref 2"]
    assert core.render_references([]) == ""


def test_reference_ladder(tmp_path, monkeypatch):
    art = {"structured_raw": None, "pdf": None}
    # empty references.json + references.md -> md
    (tmp_path / "references.json").write_text("[]")
    (tmp_path / "references.md").write_text("- A (2001) x\n- B (2002) y\n")
    assert core.reference_block(tmp_path, art) == ("- A (2001) x\n- B (2002) y", "references.md")
    # raw JATS xml when nothing else
    (tmp_path / "references.md").unlink()
    xml = tmp_path / f"{FOLDER}.xml"
    xml.write_text("<article><body><p>text</p></body><back><ref-list><ref id='r1'>"
                   "<mixed-citation>Smith &amp; Jones (2015) A study.</mixed-citation></ref>"
                   "<ref id='r2'><mixed-citation>Doe (2019) Other.</mixed-citation></ref>"
                   "</ref-list></back></article>")
    text, src = core.reference_block(tmp_path, {"structured_raw": xml, "pdf": None})
    assert src == xml.name and text.splitlines() == ["Smith & Jones (2015) A study.", "Doe (2019) Other."]
    # PDF tail as last resort
    pdf = tmp_path / f"{FOLDER}.pdf"
    pdf.write_bytes(b"%PDF-fake")
    monkeypatch.setattr(core, "_pdf_text", lambda p, n, tail_only=False: "REFS TAIL" if tail_only else "ALL")
    assert core.reference_block(tmp_path, {"structured_raw": None, "pdf": pdf}) == ("REFS TAIL", f"{pdf.name} (tail pages)")
    assert core.reference_block(tmp_path, art) == ("", "none")


# ── input assembly ──────────────────────────────────────────────────────────

def _grobid_folder(tmp_path, body="INTRO " * 100 + "\n\n## Discussion\nWe failed to replicate."):
    d = tmp_path / FOLDER
    d.mkdir()
    (d / "abstract.md").write_text("# T\n\nAn abstract.")
    (d / "body.md").write_text(body)
    (d / "references.json").write_text(json.dumps(GROBID_REFS[:1]))
    return d


def test_assemble_input_grobid_sections(tmp_path):
    d = _grobid_folder(tmp_path)
    prompt, info = core.assemble_input(d, ex.paper_artifacts(d), 350_000)
    assert info == {"tier": "grobid", "primary_file": "body.md", "refs_source": "references.json",
                    "chars": len(prompt), "truncated": False}
    for section in ("[PAPER DOI]\nhttps://doi.org/10.1234/test.2020.1", "[ABSTRACT]\n# T",
                    "[FULL TEXT: grobid]\nINTRO", "[REFERENCE LIST: references.json]\n[1] Smith J"):
        assert section in prompt
    assert prompt.rstrip().endswith("Reply with the JSON object only.")


def test_assemble_input_truncation_keeps_tail(tmp_path):
    body = "H" * 300_000 + "\n\n## Discussion\n" + "T" * 300_000
    d = _grobid_folder(tmp_path, body)
    prompt, info = core.assemble_input(d, ex.paper_artifacts(d), 100_000)
    assert info["truncated"] and len(prompt) < 110_000
    assert "characters omitted from the middle" in prompt
    assert prompt.count("T" * 1000) >= 40 and prompt.endswith("T" * 100 + "\n\n[REFERENCE LIST: references.json]\n[1] Smith J; Jones A (2015) A study. J Ex 4(2): 1-9. doi:10.1/abc\n\nReply with the JSON object only.")


def test_assemble_input_xml_rendition_outranks_body(tmp_path):
    d = _grobid_folder(tmp_path)
    (d / f"{FOLDER}_from_xml.md").write_text("XML RENDITION BODY")
    prompt, info = core.assemble_input(d, ex.paper_artifacts(d), 350_000)
    assert info["tier"] == "xml" and "[FULL TEXT: xml]\nXML RENDITION BODY" in prompt
    assert "[ABSTRACT]" not in prompt  # abstract only accompanies the GROBID tier


def _fitz(monkeypatch, pages):
    fake = type("D", (), {"page_count": pages, "__enter__": lambda s: s, "__exit__": lambda *a: False})
    monkeypatch.setitem(__import__("sys").modules, "fitz",
                        type("M", (), {"open": staticmethod(lambda p: fake())}))


def test_husk_gate_refuses_a_folder_with_no_article(tmp_path, monkeypatch):
    """A folder holding an advert or a cover page must never be extracted: the
    agent would find no replications, and that confident negative is
    indistinguishable downstream from a real paper that genuinely has none."""
    d = tmp_path / FOLDER
    d.mkdir()
    (d / "abstract.md").write_text("# " + FOLDER + "\n")
    (d / f"{FOLDER}.pdf").write_bytes(b"%PDF-fake")

    # (a) less text than an abstract: refused whatever the PDF says
    (d / "body.md").write_text("# Body\n\n<!-- image -->\n")
    _fitz(monkeypatch, 40)
    assert "shorter than an abstract" in ex._husk_reason(d, ex.paper_artifacts(d))

    # (b) short text confirmed by a one-page PDF
    (d / "body.md").write_text("Filler sentence about nothing in particular. " * 20)   # ~900 chars
    _fitz(monkeypatch, 1)
    assert "1-page PDF" in ex._husk_reason(d, ex.paper_artifacts(d))

    # (c) the same short text with a real PDF behind it is a research letter, not a husk
    _fitz(monkeypatch, 20)
    assert ex._husk_reason(d, ex.paper_artifacts(d)) == ""

    # (d) a full-length paper never trips the gate
    (d / "body.md").write_text("Real prose about a replication study. " * 200)
    _fitz(monkeypatch, 1)
    assert ex._husk_reason(d, ex.paper_artifacts(d)) == ""

    # (e) a publisher rendition means real markup arrived: gate does not apply
    (d / "body.md").write_text("x")
    (d / f"{FOLDER}_from_xml.md").write_text("y")
    assert ex._husk_reason(d, ex.paper_artifacts(d)) == ""


# ── json parsing + post-processing ──────────────────────────────────────────

def test_parse_json_reply_variants():
    assert sb.parse_json_reply('```json\n{"a": 1}\n```') == {"a": 1}
    obj = '{"contains_replications": true, "replications": [{"x": 1}]}'
    assert sb.parse_json_reply(f"Sure! {obj}\nHope {{this}} helps.") == json.loads(obj)
    assert sb.parse_json_reply('[{"result": "success"}]') == {"contains_replications": True,
                                                              "replications": [{"result": "success"}]}
    assert sb.parse_json_reply("no json here") is None
    assert sb.parse_json_reply("") is None
    assert sb.parse_json_reply("[]") == {"contains_replications": False, "replications": []}


def test_postprocess_strips_stats_fills_core_and_validates(tmp_path):
    d = tmp_path / FOLDER
    reply = {"contains_replications": True, "replications": [{
        "original_title": "A study", "original_year": 2015, "result": "Success",
        "replication_type": "close", "discipline": "psychology", "confidence": "moderate",
        "description": "x", "explanation": "y", "citation_sentence": "Smith (2015)",
        "original_n": 30, "replication_es": 0.2, "replication_es_type": "d", "original_es_95_CI": [0.1, 0.3],
    }]}
    data = core.postprocess(reply, d, "8.7-core")
    rep = data["replications"][0]
    assert not (set(rep) & set(ex.STAT_FIELDS))
    assert set(core.CORE_FIELDS) <= set(rep)
    assert rep["original_year"] == "2015" and rep["subdiscipline"] == "" and rep["original_url"] == ""
    assert rep["replication_url"] == "https://doi.org/10.1234/test.2020.1" and rep["ai_version"] == "8.7-core"
    data, msgs = ex.validate_extraction(data)
    rep = data["replications"][0]
    assert (rep["result"], rep["replication_type"], rep["confidence"]) == ("success", "close experiment", "medium")
    assert not any("not in" in m for m in msgs)


def test_postprocess_wraps_bare_and_odd_envelopes(tmp_path):
    d = tmp_path / FOLDER
    assert core.postprocess({"replications": {"result": "failure"}}, d, "v") ["contains_replications"] is True
    assert core.postprocess({"contains_replications": "yes", "replications": []}, d, "v") == \
        {"contains_replications": False, "replications": []}
    with pytest.raises(ValueError):
        core.postprocess(["not", "a", "dict"], d, "v")


# ── backend.complete ────────────────────────────────────────────────────────

class _Proc:
    def __init__(self, rc, out, err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_cli_backend_complete_and_screen(monkeypatch):
    envelope = {"result": '{"is_replication": true}', "usage": {"input_tokens": 10, "output_tokens": 2},
                "modelUsage": {"claude-sonnet-x": {}}, "total_cost_usd": 0.01, "num_turns": 1}
    calls = []
    monkeypatch.setattr(sb.subprocess, "run", lambda cmd, **kw: (calls.append((cmd, kw)), _Proc(0, json.dumps(envelope)))[1])
    b = sb.ClaudeCLIBackend(model="sonnet", timeout=5, cwd="/tmp")
    r = b.complete("SYS", "USER", cwd="/var")
    assert r.ok and r.text == '{"is_replication": true}'
    assert r.usage["model"] == "claude-sonnet-x" and r.usage["input_tokens"] == 10 and r.usage["cost_usd"] == 0.01
    assert calls[0][1]["cwd"] == "/var" and calls[0][1]["input"] == "USER" and "--tools" in calls[0][0]
    assert b.screen(7, "SYS", "USER") == (7, {"is_replication": True})
    # silent non-zero exit: the shape extract.is_usage_limit_error recognises
    monkeypatch.setattr(sb.subprocess, "run", lambda cmd, **kw: _Proc(1, "", ""))
    r = b.complete("SYS", "USER")
    assert r.error and r.returncode == 1 and r.stdout == "" and r.stderr == ""
    assert ex.is_usage_limit_error(f"claude_cli CLI failed for x:\n{(r.stderr + r.stdout).strip()}")
    assert b.screen(1, "SYS", "USER") == (1, None)
    # an is_error envelope surfaces its text
    monkeypatch.setattr(sb.subprocess, "run", lambda cmd, **kw: _Proc(0, json.dumps({"is_error": True, "result": "Claude AI usage limit reached|123"})))
    r = b.complete("SYS", "USER")
    assert r.error and ex.is_usage_limit_error(r.error)


def test_primary_model_prefers_the_model_that_did_the_work():
    mu = {"claude-haiku-4-5-20251001": {"outputTokens": 18}, "claude-sonnet-5": {"outputTokens": 9005}}
    assert sb.primary_model(mu, "sonnet") == "claude-sonnet-5"
    assert sb.primary_model({}, "sonnet") == "sonnet"
    assert sb.primary_model({"only": "weird-shape"}, "x") == "only"
    assert ex.primary_model is sb.primary_model


def test_salvage_inline_result():
    good = '```json\n{"contains_replications": true, "replications": [{"result": "failure"}]}\n```'
    assert ex.salvage_inline_result(good) == {"contains_replications": True, "replications": [{"result": "failure"}]}
    assert ex.salvage_inline_result('I could not find the file. {"note": "x"}') is None
    assert ex.salvage_inline_result("") is None
    assert ex.salvage_inline_result('{"replications": []}') == {"replications": [], "contains_replications": False}


# ── one paper end to end (fake backend, no network) ─────────────────────────

class _FakeBackend:
    name, model = "fake", "fake-model"

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, system_prompt, user_prompt, cwd=None):
        self.prompts.append(user_prompt)
        return sb.CompletionResult(text=self.replies.pop(0), usage={"model": "fake-model", "input_tokens": 5,
                                                                    "output_tokens": 1, "cost_usd": 0.0})


def _no_network(monkeypatch):
    monkeypatch.setattr(core, "validate_original_dois", lambda data: ([], {}))
    monkeypatch.setattr(core, "enrich_metadata", lambda data, **kw: (data, ["  📖  Replication: fake"]))
    monkeypatch.setattr(core, "_write_provenance", lambda output_dir, **kw: (output_dir / "provenance.json").write_text("{}"))


def test_extract_paper_core_writes_result_and_retries_bad_json(tmp_path, monkeypatch):
    _no_network(monkeypatch)
    d = _grobid_folder(tmp_path)
    good = json.dumps({"contains_replications": True, "replications": [{
        "original_title": "A study", "original_authors": "Smith, J.", "original_year": "2015",
        "description": "d", "result": "failure", "replication_type": "direct", "discipline": "psychology",
        "subdiscipline": "Social Psychology", "confidence": "high", "explanation": "e",
        "citation_sentence": "Smith (2015)", "original_n": 30}]})
    backend = _FakeBackend(["garbage", good])
    data, usage, log = core.extract_paper_core(d, backend, "SYS", None, "t1", None)
    assert usage["attempts"] == 2 and usage["input_tokens"] == 10
    rep = data["replications"][0]
    assert "original_n" not in rep and rep["ai_version"].endswith("-core")
    out = d / "t1" / f"{FOLDER}_result_core.json"
    assert out.exists() and json.loads(out.read_text()) == data
    assert (d / "t1" / "debug_log.json").exists() and (d / "t1" / "provenance.json").exists()
    assert any("not parseable JSON" in m for m in log) and any("Replication: fake" in m for m in log)
    assert "[FULL TEXT: grobid]" in backend.prompts[0]
    with pytest.raises(ex.SkipPaper):  # resume: output exists
        core.extract_paper_core(d, backend, "SYS", None, "t1", None)


def test_extract_paper_core_skips_papers_already_in_db(tmp_path, monkeypatch):
    _no_network(monkeypatch)
    d = _grobid_folder(tmp_path)
    with pytest.raises(ex.SkipPaper):
        core.extract_paper_core(d, _FakeBackend([]), "SYS", {"https://doi.org/10.1234/test.2020.1"}, "t1")


def test_extract_paper_core_backend_error_shape(tmp_path, monkeypatch):
    _no_network(monkeypatch)
    d = _grobid_folder(tmp_path)

    class Silent(_FakeBackend):
        def complete(self, s, u, cwd=None):
            return sb.CompletionResult(error="CLI exit 1: ", returncode=1)
    with pytest.raises(RuntimeError) as e:
        core.extract_paper_core(d, Silent([]), "SYS", None, "t1")
    assert ex.is_usage_limit_error(str(e.value))


# ── collate ─────────────────────────────────────────────────────────────────

def test_collate_picks_up_core_results_and_prefers_full(tmp_path):
    d = _grobid_folder(tmp_path)
    tagdir = d / "t1"
    tagdir.mkdir()
    entry = {"original_title": "A study", "description": "d", "result": "failure", "replication_type": "direct",
             "discipline": "psychology", "confidence": "high", "explanation": "e", "citation_sentence": "s",
             "replication_url": "https://doi.org/10.1234/test.2020.1", "ai_version": "8.7-core"}
    (tagdir / f"{FOLDER}_result_core.json").write_text(json.dumps({"contains_replications": True, "replications": [entry]}))
    csv_path = ex.collate_results(tmp_path, tag="t1")
    import csv
    rows = list(csv.DictReader(open(csv_path)))
    assert len(rows) == 1 and rows[0]["ai_version"] == "8.7-core" and rows[0]["result"] == "failure"
    assert all(rows[0][f] == "" for f in ex.STAT_FIELDS)
    assert rows[0]["citation_sentence"] == "s"
    (tagdir / f"{FOLDER}_result_full.json").write_text(json.dumps({"contains_replications": True,
                                                                   "replications": [{**entry, "ai_version": "8.7"}]}))
    rows = list(csv.DictReader(open(ex.collate_results(tmp_path, tag="t1"))))
    assert rows[0]["ai_version"] == "8.7"
