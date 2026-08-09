"use client";
import { useEffect, useRef, useState, useCallback } from "react";
import Link from "next/link";
import { api, type KeywordStatsEnvelope, type KeywordApiStats, type KeywordQueryStat } from "@/lib/api";

// Funnel-stage colors: a single-hue ordinal sky ramp (deepest stage darkest in
// light mode, lightest in dark mode) + a neutral for candidates dropped at the
// prefilter. Steps chosen to pass ordinal-ramp validation (monotone lightness,
// ΔL ≥ 0.06 between neighbors, ≥2:1 light-end contrast) on slate-50/slate-950.
const SEGMENTS = [
  { label: "Direct", cls: "bg-sky-950 dark:bg-sky-200" },
  { label: "Confirmed", cls: "bg-sky-800 dark:bg-sky-400" },
  { label: "Classified", cls: "bg-sky-600 dark:bg-sky-600" },
  { label: "Filtered", cls: "bg-sky-400 dark:bg-sky-800" },
  { label: "Dropped at prefilter", cls: "bg-slate-500 dark:bg-slate-500" },
];

function Legend() {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
      {SEGMENTS.map((s) => (
        <span key={s.label} className="flex items-center gap-1.5">
          <span className={`inline-block h-2.5 w-2.5 rounded-sm ${s.cls}`} />
          {s.label}
        </span>
      ))}
    </div>
  );
}

function FunnelBar({ q, max }: { q: KeywordQueryStat; max: number }) {
  // Downstream counts are per query string (cross-API), so a multi-API row can
  // exceed this section's raw share — clamp each stage to the one above so the
  // segments always partition the raw bar. Exact numbers live in the columns.
  const f = Math.min(q.filtered, q.raw);
  const c = Math.min(q.classified, f);
  const co = Math.min(q.confirmed, c);
  const d = Math.min(q.direct, co);
  const parts = [d, co - d, c - co, f - c, q.raw - f].map((n, i) => ({
    n, ...SEGMENTS[i],
    count: [q.direct, q.confirmed, q.classified, q.filtered, q.raw - f][i],
  })).filter((s) => s.n > 0);
  return (
    <div className="h-2 w-full min-w-[6rem] rounded bg-slate-200 dark:bg-slate-800">
      {q.raw > 0 && max > 0 && (
        <div className="flex h-full items-stretch gap-[2px] rounded overflow-hidden"
          style={{ width: `${Math.max(1, Math.round((100 * q.raw) / max))}%` }}>
          {parts.map((s) => (
            <span key={s.label} title={`${s.label}: ${s.count.toLocaleString()}`}
              className={s.cls} style={{ flexGrow: s.n, flexBasis: 0, minWidth: 3 }} />
          ))}
        </div>
      )}
    </div>
  );
}

function Badge({ tone, children, title }: { tone: "rose" | "slate"; children: React.ReactNode; title?: string }) {
  const cls = tone === "rose"
    ? "bg-rose-100 dark:bg-rose-900 text-rose-700 dark:text-rose-300"
    : "bg-slate-100 dark:bg-slate-800 text-slate-500";
  return <span title={title} className={`ml-1.5 text-[10px] rounded px-1 py-0.5 whitespace-nowrap ${cls}`}>{children}</span>;
}

function num(n: number) { return n.toLocaleString(); }

function ApiSection({ sec }: { sec: KeywordApiStats }) {
  const max = Math.max(...sec.queries.map((q) => q.raw), 1);
  return (
    <details open={sec.raw > 0} className="rounded-lg border border-slate-200 dark:border-slate-800">
      <summary className="cursor-pointer select-none px-4 py-2.5 text-sm font-medium">
        {sec.label}
        <span className="ml-2 text-xs font-normal text-slate-500">
          {sec.queriesExpected} queries · {num(sec.raw)} docs · {sec.zeroYield} zero-yield
          {sec.notSearched > 0 && ` · ${sec.notSearched} not searched`}
        </span>
      </summary>
      <div className="overflow-x-auto px-4 pb-3">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-slate-500 border-b border-slate-200 dark:border-slate-800">
              <th className="py-1.5 pr-2 font-normal">Query</th>
              <th className="py-1.5 px-2 font-normal text-right" title="Raw candidates this query discovered first (first-discoverer credit)">Raw</th>
              <th className="py-1.5 px-2 font-normal text-right" title="Rows surviving the prefilter (per query string, shared across APIs)">Filtered</th>
              <th className="py-1.5 px-2 font-normal text-right" title="Rows reaching the LLM classifier">Classified</th>
              <th className="py-1.5 px-2 font-normal text-right" title="Confirmed replications (% of classified)">Confirmed</th>
              <th className="py-1.5 px-2 font-normal text-right" title="High-confidence direct replications">Direct</th>
              <th className="py-1.5 pl-2 font-normal w-32"></th>
            </tr>
          </thead>
          <tbody>
            {sec.queries.map((q) => (
              <tr key={q.query}
                className={`border-b border-slate-100 dark:border-slate-800/60 ${q.zeroYield ? "bg-rose-50 dark:bg-rose-950/30" : ""}`}>
                <td className="py-1 pr-2 font-mono break-all max-w-md">
                  {q.query}
                  {q.zeroYield && <Badge tone="rose" title="Searched, but every paper it matched was already found by an earlier query — or it matched nothing">0 yield</Badge>}
                  {q.notSearched && <Badge tone="slate" title="Not in search progress yet — run the search stage">not searched</Badge>}
                  {q.apiCount > 1 && <Badge tone="slate" title={`This query string is issued by ${q.apiCount} APIs; downstream counts are per query string, so they repeat in each section`}>×{q.apiCount} APIs</Badge>}
                </td>
                <td className="py-1 px-2 text-right tabular-nums">{q.notSearched ? "—" : num(q.raw)}</td>
                <td className="py-1 px-2 text-right tabular-nums">{q.notSearched ? "—" : num(q.filtered)}</td>
                <td className="py-1 px-2 text-right tabular-nums">{q.notSearched ? "—" : num(q.classified)}</td>
                <td className="py-1 px-2 text-right tabular-nums whitespace-nowrap">
                  {q.notSearched ? "—" : num(q.confirmed)}
                  {q.confirmRate != null && (
                    <span className="ml-1 text-emerald-600 dark:text-emerald-400">{Math.round(q.confirmRate * 100)}%</span>
                  )}
                </td>
                <td className="py-1 px-2 text-right tabular-nums">{q.notSearched ? "—" : num(q.direct)}</td>
                <td className="py-1 pl-2"><FunnelBar q={q} max={max} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

export default function KeywordStatsPage() {
  const [env, setEnv] = useState<KeywordStatsEnvelope | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const e = await api.keywordStats();
      setEnv(e); setErr(null);
      if (e.state !== "computing" && poll.current) {
        clearInterval(poll.current); poll.current = null;
      }
      return e;
    } catch (e) { setErr((e as Error).message); return null; }
  }, []);

  useEffect(() => {
    load();
    return () => { if (poll.current) clearInterval(poll.current); };
  }, [load]);

  const refresh = async () => {
    try { await api.refreshKeywordStats(); } catch { /* 409 = already running */ }
    setEnv((e) => (e ? { ...e, state: "computing" } : e));
    if (!poll.current) poll.current = setInterval(load, 2000);
  };

  const stats = env?.stats ?? null;
  const computing = env?.state === "computing";

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">Keyword yield</h1>
          <p className="text-sm text-slate-500 mt-1">
            Per-query funnel across the pipeline — raw candidates found, and how many survived
            prefilter, classification, and confirmation. Edit the lists on the{" "}
            <Link href="/keywords" className="underline hover:text-slate-900 dark:hover:text-slate-100">Keywords</Link> page.
          </p>
        </div>
        <div className="flex items-center gap-3 text-xs text-slate-500">
          {stats && (
            <span>
              generated {stats.generatedAt} · {stats.computeSeconds}s
              {env && env.historyCount > 0 && ` · ${env.historyCount} snapshot${env.historyCount === 1 ? "" : "s"}`}
            </span>
          )}
          <button onClick={refresh} disabled={computing}
            className="text-sm rounded border border-slate-300 dark:border-slate-700 px-3 py-1.5 hover:bg-slate-100 dark:hover:bg-slate-800 disabled:opacity-40">
            {computing ? "computing…" : "Refresh"}
          </button>
        </div>
      </div>

      {err && (
        <div className="rounded-lg border border-rose-300 dark:border-rose-800 bg-rose-50 dark:bg-rose-950/40 px-4 py-3 text-sm text-rose-700 dark:text-rose-300">
          Can&apos;t reach the backend ({err}). Start it with <code className="font-mono">./dev.sh up</code>.
        </div>
      )}

      {stats && (stats.staleness.notes.length > 0 || env?.stale) && (
        <div className="rounded-lg border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 px-4 py-3 text-xs text-amber-800 dark:text-amber-300 space-y-1">
          {env?.stale && <p>Input files changed since this snapshot — hit Refresh to recompute.</p>}
          {stats.staleness.notes.map((n) => <p key={n}>{n}</p>)}
        </div>
      )}

      <p className="text-xs text-slate-500 max-w-3xl">
        Documents are credited to the query that <em>first</em> found them — search dedups as it
        writes, so a later query that re-finds a paper gets no credit. A zero-yield keyword may
        still be valuable if an earlier query keeps scooping its papers; check ordering before
        pruning. Downstream counts are per query <em>string</em>: API attribution is lost at dedup,
        so a query issued by several APIs (×N chip) shows the same downstream numbers in each section.
        Bars partition each row&apos;s raw share by the deepest stage its papers reached; for
        multi-API queries the cross-API downstream counts are capped at the row&apos;s raw share in
        the bar — the columns keep the exact numbers.
      </p>

      {env?.state === "missing" && !computing && (
        <div className="rounded-lg border border-slate-200 dark:border-slate-800 px-4 py-6 text-sm text-slate-500">
          No stats computed yet. Hit <span className="font-medium">Refresh</span> above, or run{" "}
          <code className="font-mono">python -m mo_pipeline.discover.keyword_stats</code> from the repo root.
        </div>
      )}

      {stats && (
        <>
          <p className="text-sm text-slate-600 dark:text-slate-400">
            {num(stats.totals.raw)} raw → {num(stats.totals.filtered)} filtered →{" "}
            {num(stats.totals.classified)} classified → {num(stats.totals.confirmed)} confirmed →{" "}
            {num(stats.totals.direct)} direct ·{" "}
            {stats.totals.queriesCompleted}/{stats.totals.queriesExpected} queries searched,{" "}
            {stats.totals.zeroYield} zero-yield
          </p>
          <Legend />
          <div className="space-y-3">
            {stats.apis.map((sec) => <ApiSection key={sec.api} sec={sec} />)}
          </div>
          {stats.orphans.length > 0 && (
            <div className="rounded-lg border border-slate-200 dark:border-slate-800 p-4">
              <h2 className="text-sm font-medium">Orphaned queries</h2>
              <p className="text-xs text-slate-500 mt-0.5 mb-2">
                Results or search progress recorded for queries no longer in the effective lists
                (edited or removed keywords). Their raw candidates remain in the corpus.
              </p>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-slate-500 border-b border-slate-200 dark:border-slate-800">
                    <th className="py-1 pr-2 font-normal">API</th>
                    <th className="py-1 px-2 font-normal">Query</th>
                    <th className="py-1 px-2 font-normal text-right">Raw</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.orphans.map((o) => (
                    <tr key={`${o.api}:${o.query}`} className="border-b border-slate-100 dark:border-slate-800/60">
                      <td className="py-1 pr-2">{o.api}</td>
                      <td className="py-1 px-2 font-mono break-all">{o.query}</td>
                      <td className="py-1 px-2 text-right tabular-nums">{num(o.raw)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
