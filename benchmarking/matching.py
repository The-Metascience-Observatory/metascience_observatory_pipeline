"""Match pipeline-extracted replication rows to ground-truth rows, one-to-one.

Three stages per replication paper (see benchmarking/README.md, "Matching"):

  1. deterministic  normalized original-DOI equality -> cost ~0
  2. LLM judge      for GT rows with no DOI hit, or an ambiguous one (several
                    rows on either side share the original DOI), a Haiku judge
                    run through the stage-4 screening backend decides which
                    extracted row is the same effect. Result labels are withheld
                    from the judge. Every decision is cached by content hash and
                    appended to a jsonl log so the run is reproducible and
                    auditable.
  3. assignment     Hungarian assignment over the cost matrix so one extracted
                    row can satisfy at most one GT row. Unmatched GT rows are
                    false negatives; unmatched extracted rows are false positives
                    (unless the GT source is paper-level, where extra rows are
                    reported but not penalized).

The string helpers at the top were lifted verbatim from the Feb-2026
evaluate_enhanced.py so the deterministic fallback scores exactly as before.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from mo_pipeline import config
from mo_pipeline.discover.doi_runs import normalize_doi

# ── string helpers (evaluate_enhanced.py, Feb 2026) ──────────────────────────

def normalize_string(s) -> str:
    if s is None:
        return ""
    s = str(s)
    if s.lower() == "nan":
        return ""
    return s.lower().strip()


def extract_last_names(author_string) -> set[str]:
    author_string = normalize_string(author_string)
    if not author_string:
        return set()
    last_names = set()
    for author in (a.strip() for a in author_string.split(";")):
        if not author:
            continue
        if "," in author:
            lastname = normalize_string(author.split(",")[0])
            if lastname:
                last_names.add(lastname)
        else:
            parts = author.split()
            if parts:
                lastname = normalize_string(parts[-1])
                if lastname and len(lastname) > 1:
                    last_names.add(lastname)
    return last_names


def authors_match(a1, a2, threshold: float = 0.7) -> bool:
    l1, l2 = extract_last_names(a1), extract_last_names(a2)
    if not l1 or not l2:
        return False
    union = l1 | l2
    return (len(l1 & l2) / len(union)) >= threshold if union else False


def string_ratio(s1, s2) -> float:
    a, b = normalize_string(s1), normalize_string(s2)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def fuzzy_match(s1, s2, threshold: float = 0.75) -> bool:
    a, b = normalize_string(s1), normalize_string(s2)
    if not a or not b:
        return False
    return string_ratio(a, b) >= threshold


def year_match(y1, y2) -> bool:
    try:
        a = str(y1).replace(".0", "").strip()
        b = str(y2).replace(".0", "").strip()
        return bool(a) and a == b
    except Exception:
        return False


def doi_of(row: dict, *keys: str) -> str:
    for k in keys:
        d = normalize_doi(row.get(k) or "")
        if d:
            return d
    return ""


# ── the judge ────────────────────────────────────────────────────────────────

RELATIONS = ("same_effect", "subanalysis_of_gt", "same_original_other_effect",
             "different_original", "none")
MATCH_RELATIONS = frozenset({"same_effect", "subanalysis_of_gt"})
CONF_COST = {"high": 0.10, "medium": 0.40, "low": 0.70}

GT_JUDGE_FIELDS = ("original_title", "original_authors", "original_year",
                   "original_journal", "description")
EXT_JUDGE_FIELDS = ("original_title", "original_authors", "original_year",
                    "original_journal", "description", "citation_sentence")


def _clip(v, n: int = 300) -> str:
    s = "" if v is None else str(v)
    return s if len(s) <= n else s[:n] + "…"


class MatchJudge:
    """LLM matcher on the screening backend. `offline=True` answers only from cache."""

    def __init__(self, provider: str = "claude_cli", model: str = "haiku",
                 prompt_path: Path = config.MATCH_PROMPT_FILE,
                 version_path: Path = config.MATCH_VERSION_FILE,
                 cache_dir: Path = config.BENCH_CACHE_DIR / "match",
                 log_path: Path | None = None, offline: bool = False):
        self.provider, self.model = provider, model
        self.system_prompt = Path(prompt_path).read_text(encoding="utf-8")
        self.version = Path(version_path).read_text(encoding="utf-8").strip()
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = Path(log_path) if log_path else None
        self.offline = offline
        self._backend = None
        self.calls = self.cache_hits = self.failures = 0
        # evaluate/retest judge several papers concurrently: guard the
        # counters, the lazy backend, the cache write and the log append.
        self._lock = threading.Lock()

    # lazy so offline/cached runs never import or spawn the CLI
    def _get_backend(self):
        with self._lock:
            if self._backend is None:
                from mo_pipeline.discover.screening_backend import get_backend
                self._backend = get_backend(provider=self.provider, model=self.model)
            return self._backend

    def _key(self, gt: dict, cands: list[dict], granularity: str) -> str:
        payload = {
            "v": self.version, "model": self.model, "granularity": granularity,
            "gt": {k: _clip(gt.get(k)) for k in GT_JUDGE_FIELDS} | {"doi": doi_of(gt, "original_doi", "original_url")},
            "cands": [{k: _clip(c.get(k)) for k in EXT_JUDGE_FIELDS} | {"doi": doi_of(c, "original_url")}
                      for c in cands],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def build_user_prompt(gt: dict, cands: list[dict], granularity: str) -> str:
        gt_doi = doi_of(gt, "original_doi", "original_url")
        lines = [
            "GROUND-TRUTH ENTRY",
            f"  granularity: {granularity}  (paper = one row per replication paper; effect = one row per replicated effect)",
            f"  source: {gt.get('source') or gt.get('provenance') or ''}",
            f"  original DOI: {gt_doi or '(none)'}",
            f"  original title: {_clip(gt.get('original_title'), 300)}",
            f"  original authors: {_clip(gt.get('original_authors'), 200)}",
            f"  original year: {gt.get('original_year') or ''}   journal: {_clip(gt.get('original_journal'), 120)}",
            f"  replicated effect (description): {_clip(gt.get('description'), 600)}",
            "",
            f"PIPELINE ROWS FROM THE SAME REPLICATION PAPER ({len(cands)} rows; index in brackets)",
        ]
        for i, c in enumerate(cands):
            lines += [
                f"  [{i}] original DOI: {doi_of(c, 'original_url') or '(none)'}",
                f"      original title: {_clip(c.get('original_title'), 300)}",
                f"      original authors: {_clip(c.get('original_authors'), 200)}",
                f"      original year: {c.get('original_year') or ''}   journal: {_clip(c.get('original_journal'), 120)}",
                f"      replicated effect (description): {_clip(c.get('description'), 500)}",
                f"      citation sentence: {_clip(c.get('citation_sentence'), 400)}",
            ]
        lines += ["", "Which row corresponds to the ground-truth entry? Answer with the JSON object only."]
        return "\n".join(lines)

    def judge(self, gt: dict, cands: list[dict], granularity: str = "effect") -> dict:
        key = self._key(gt, cands, granularity)
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                out = json.loads(cache_file.read_text(encoding="utf-8"))
                out["cache"] = "hit"
                with self._lock:
                    self.cache_hits += 1
                self._log(gt, cands, out, key)
                return out
            except json.JSONDecodeError:
                pass
        if self.offline:
            out = self._invalid("offline: no cached decision", None)
            out["cache"] = "miss-offline"
            return out
        with self._lock:
            self.calls += 1
        _, parsed = self._get_backend().screen(key[:8], self.system_prompt,
                                               self.build_user_prompt(gt, cands, granularity))
        out = self._validate(parsed, len(cands))
        out.update({"judge_model": self.model, "judge_provider": self.provider,
                    "judge_version": self.version, "cache": "miss", "key": key})
        with self._lock:
            if out.get("error"):
                self.failures += 1
            else:
                cache_file.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        self._log(gt, cands, out, key)
        return out

    def _validate(self, parsed, n_cands: int) -> dict:
        if not isinstance(parsed, dict):
            return self._invalid("no JSON from judge", parsed)
        rel = str(parsed.get("relation") or "").strip()
        conf = str(parsed.get("confidence") or "").strip().lower()
        m = parsed.get("match")
        if isinstance(m, str) and m.strip().lstrip("-").isdigit():
            m = int(m)
        if rel not in RELATIONS:
            return self._invalid(f"bad relation {rel!r}", parsed)
        if conf not in CONF_COST:
            conf = "low"
        if m is not None and not (isinstance(m, int) and 0 <= m < n_cands):
            m = None
            if rel in MATCH_RELATIONS:
                rel = "none"
        if rel in MATCH_RELATIONS and m is None:
            rel = "none"
        return {"match": m, "relation": rel, "confidence": conf,
                "reason": _clip(parsed.get("reason"), 400)}

    @staticmethod
    def _invalid(msg: str, raw) -> dict:
        return {"match": None, "relation": "none", "confidence": "low",
                "reason": msg, "error": msg, "raw": _clip(raw, 300) if raw is not None else None}

    def _log(self, gt: dict, cands: list[dict], out: dict, key: str) -> None:
        if not self.log_path:
            return
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "key": key,
               "gt_row_id": gt.get("row_id"), "replication_doi": gt.get("replication_doi"),
               "n_candidates": len(cands), **{k: out.get(k) for k in
               ("match", "relation", "confidence", "reason", "cache", "error", "judge_model")}}
        with self._lock, open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ── DOI aliases: one work carrying more than one identifier ──────────────────
# Exact DOI equality is the matcher's strongest signal, but a single study can
# be reachable by several DOIs: a preprint and its published version, a JSTOR
# alias beside the publisher's own, and -- in Registered Report replications --
# the protocol paper that a ground-truth set may name in place of the study
# actually replicated. Treating those as different originals turns a correct
# extraction into a false negative, so a curated alias table maps each one to
# its canonical DOI. Unknown DOIs pass through untouched, so a run with no
# alias file behaves exactly as before.
ALIASES_PATH = config.BENCHMARKING_DIR / "doi_aliases.json"
_ALIASES: dict | None = None


def _aliases() -> dict:
    """{alias DOI -> canonical DOI}, read once. Missing/corrupt file -> empty."""
    global _ALIASES
    if _ALIASES is None:
        try:
            raw = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        table = {}
        for alias, spec in raw.items():
            if alias.startswith("_"):          # "_README" and other notes
                continue
            target = spec.get("canonical") if isinstance(spec, dict) else spec
            a = normalize_doi(alias) or ""
            c = normalize_doi(target or "") or ""
            if a and c and a != c:
                table[a] = c
        _ALIASES = table
    return _ALIASES


def canonical_doi(doi: str) -> str:
    """The canonical DOI for a work, following at most one alias hop.

    One hop only: chained aliases would let a single bad entry merge two
    genuinely different studies, which is the one error this must never make.
    """
    d = normalize_doi(doi or "") or (doi or "").strip().lower()
    return _aliases().get(d, d)


def alias_kind(doi_a: str, doi_b: str) -> str:
    """How two DOIs of the same work relate, for the match reason line."""
    try:
        raw = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "alias"
    for alias, spec in raw.items():
        if alias.startswith("_") or not isinstance(spec, dict):
            continue
        a = normalize_doi(alias) or ""
        if a in (normalize_doi(doi_a) or "", normalize_doi(doi_b) or ""):
            return spec.get("kind") or "alias"
    return "alias"


# ── assignment ───────────────────────────────────────────────────────────────

INF = 9.0


@dataclass
class Match:
    gt_index: int
    ext_index: int
    method: str          # doi | doi+judge | llm | fuzzy
    relation: str
    confidence: str
    cost: float
    reason: str = ""


@dataclass
class MatchOutcome:
    matches: list[Match] = field(default_factory=list)
    gt_unmatched: list[int] = field(default_factory=list)
    ext_unmatched: list[int] = field(default_factory=list)
    granularity_misses: list[tuple[int, int]] = field(default_factory=list)  # (gt, ext) same original, other effect
    wrong_original: list[tuple[int, int]] = field(default_factory=list)      # (gt, ext) judge says different original
    judged: dict = field(default_factory=dict)                                 # gt_index -> judge dict
    n_judge_calls: int = 0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["matches"] = [asdict(m) for m in self.matches]
        return d


def _solve(cost: list[list[float]]) -> list[tuple[int, int]]:
    """Min-cost one-to-one assignment; pairs with cost >= INF are dropped."""
    n = len(cost)
    m = len(cost[0]) if n else 0
    if n == 0 or m == 0:
        return []
    try:
        import numpy as np
        from scipy.optimize import linear_sum_assignment
        rows, cols = linear_sum_assignment(np.array(cost))
        pairs = [(int(i), int(j)) for i, j in zip(rows, cols) if cost[i][j] < INF]
    except ImportError:  # greedy: cheapest pair first
        cells = sorted((cost[i][j], i, j) for i in range(n) for j in range(m) if cost[i][j] < INF)
        used_i, used_j, pairs = set(), set(), []
        for c, i, j in cells:
            if i in used_i or j in used_j:
                continue
            used_i.add(i); used_j.add(j); pairs.append((i, j))
    return sorted(pairs)


def _fuzzy_row(i: int, g: dict, ext_rows: list[dict], cost, meta) -> None:
    """Feb-2026 rule: >=2 of {title ratio>=.75, author Jaccard>=.7, year} -> candidate."""
    for j, e in enumerate(ext_rows):
        score = sum([fuzzy_match(e.get("original_title"), g.get("original_title")),
                     authors_match(e.get("original_authors"), g.get("original_authors")),
                     year_match(e.get("original_year"), g.get("original_year"))])
        if score >= 2:
            c = 0.5 + 0.1 * (3 - score)
            if c < cost[i][j]:
                cost[i][j] = c
                meta[(i, j)] = ("fuzzy", "same_effect", "medium" if score == 3 else "low",
                                f"fuzzy {score}/3 (title/authors/year)")


def assign(gt_rows: list[dict], ext_rows: list[dict], judge: MatchJudge | None = None,
           granularity: str = "effect", allow_fuzzy: bool = True) -> MatchOutcome:
    """One-to-one assignment of extracted rows to GT rows for ONE paper."""
    out = MatchOutcome()
    n, m = len(gt_rows), len(ext_rows)
    if n == 0 or m == 0:
        out.gt_unmatched = list(range(n))
        out.ext_unmatched = list(range(m))
        return out
    cost = [[INF] * m for _ in range(n)]
    meta: dict[tuple[int, int], tuple[str, str, str, str]] = {}
    gt_dois = [doi_of(g, "original_doi", "original_url") for g in gt_rows]
    ext_dois = [doi_of(e, "original_url") for e in ext_rows]
    # Compare canonical forms so a preprint, a JSTOR alias or a Registered
    # Report does not read as a different study (see canonical_doi).
    gt_canon = [canonical_doi(d) for d in gt_dois]
    ext_canon = [canonical_doi(d) for d in ext_dois]

    # stage 1: DOI equality (description ratio breaks ties inside a shared DOI)
    for i, g in enumerate(gt_rows):
        for j, e in enumerate(ext_rows):
            if gt_canon[i] and gt_canon[i] == ext_canon[j]:
                r = string_ratio(g.get("description"), e.get("description"))
                cost[i][j] = 0.05 * (1.0 - r)
                why = ("original DOI equal" if gt_dois[i] == ext_dois[j]
                       else f"same work under two DOIs ({alias_kind(gt_dois[i], ext_dois[j])}): "
                            f"{gt_dois[i]} = {ext_dois[j]}")
                meta[(i, j)] = ("doi", "same_effect", "high", why)

    # stage 2: judge (or fuzzy fallback) where DOI did not settle it
    for i, g in enumerate(gt_rows):
        hits = [j for j in range(m) if cost[i][j] < INF]
        shared_doi = gt_canon[i] and sum(1 for d in gt_canon if d == gt_canon[i]) > 1
        ambiguous = len(hits) > 1 and shared_doi
        if hits and not ambiguous:
            continue
        cands_idx = hits if ambiguous else list(range(m))
        cands = [ext_rows[j] for j in cands_idx]
        if judge is not None:
            res = judge.judge(g, cands, granularity)
            out.judged[i] = res
            out.n_judge_calls += 1
            j_local = res.get("match")
            j = cands_idx[j_local] if isinstance(j_local, int) and 0 <= j_local < len(cands_idx) else None
            rel, conf = res.get("relation"), res.get("confidence", "low")
            if rel in MATCH_RELATIONS and j is not None:
                if ambiguous:
                    cost[i][j] = min(cost[i][j], 0.0)      # judge-preferred among DOI hits
                    meta[(i, j)] = ("doi+judge", rel, conf, res.get("reason", ""))
                else:
                    cost[i][j] = min(cost[i][j], CONF_COST.get(conf, 0.7))
                    meta[(i, j)] = ("llm", rel, conf, res.get("reason", ""))
            elif rel == "same_original_other_effect" and j is not None:
                out.granularity_misses.append((i, j))
            elif rel == "different_original" and j is not None:
                out.wrong_original.append((i, j))
            if res.get("error") and allow_fuzzy and not hits:
                _fuzzy_row(i, g, ext_rows, cost, meta)   # judge unavailable: degrade, don't drop
        elif allow_fuzzy and not hits:
            _fuzzy_row(i, g, ext_rows, cost, meta)

    # stage 3: one-to-one
    pairs = _solve(cost)
    matched_i = {i for i, _ in pairs}
    matched_j = {j for _, j in pairs}
    for i, j in pairs:
        method, rel, conf, reason = meta.get((i, j), ("?", "same_effect", "low", ""))
        out.matches.append(Match(i, j, method, rel, conf, round(cost[i][j], 4), reason))
    out.gt_unmatched = [i for i in range(n) if i not in matched_i]
    out.ext_unmatched = [j for j in range(m) if j not in matched_j]
    return out
