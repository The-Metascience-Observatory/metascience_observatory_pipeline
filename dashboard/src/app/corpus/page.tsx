"use client";
import { useEffect, useState, useCallback } from "react";
import { api, type CorpusResponse, type PaperTypeOption } from "@/lib/api";

const STATUSES = ["", "downloaded", "converted", "screened", "extracted", "ingested"];

export default function Corpus() {
  const [data, setData] = useState<CorpusResponse | null>(null);
  const [status, setStatus] = useState("");
  const [reps, setReps] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [types, setTypes] = useState<PaperTypeOption[]>([]);
  const [checked, setChecked] = useState<Record<string, boolean>>({});

  const refresh = useCallback(async () => {
    try {
      const q: Record<string, string> = { limit: "300" };
      if (status) q.status_filter = status;
      if (reps) q.contains_replications = reps;
      setData(await api.corpus(q)); setErr(null);
    } catch (e) { setErr((e as Error).message); }
  }, [status, reps]);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    api.corpusDownloadOptions().then((r) => setTypes(r.types)).catch(() => setTypes([]));
  }, []);

  const selected = types.filter((t) => checked[t.type]);
  const selectedCount = selected.reduce((n, t) => n + t.count, 0);
  const selectedUrl = "/api/corpus/download?types=" +
    encodeURIComponent(selected.map((t) => t.type).join(","));

  const stats = data?.stats;
  const Stat = ({ label, val }: { label: string; val: number | string }) => (
    <div className="rounded-lg border border-slate-200 dark:border-slate-800 px-4 py-3">
      <div className="text-2xl font-semibold tabular-nums">{val}</div>
      <div className="text-xs text-slate-500">{label}</div>
    </div>
  );

  return (
    <div className="space-y-5">
      <h1 className="text-lg font-semibold">Corpus</h1>
      {err && <div className="text-sm text-rose-500">{err}</div>}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
          <Stat label="papers" val={stats.total} />
          <Stat label="with replications" val={stats.with_replications} />
          <Stat label="no replications" val={stats.without_replications} />
          <Stat label="replication entries" val={stats.total_replication_entries} />
          <Stat label="extracted" val={stats.by_status.extracted ?? 0} />
          <Stat label="ingested" val={stats.ingested} />
        </div>
      )}
      <div className="rounded-lg border border-slate-200 dark:border-slate-800 p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="text-sm font-medium">Download papers</div>
          <a href="/api/corpus/download?types=all"
            className="rounded bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 px-3 py-1.5 text-sm font-medium hover:opacity-90">
            Download entire corpus ({stats?.total ?? "…"} papers)
          </a>
        </div>
        <div className="flex flex-wrap gap-x-5 gap-y-2 text-sm">
          {types.map((t) => (
            <label key={t.type} className="flex items-center gap-1.5">
              <input type="checkbox" checked={!!checked[t.type]}
                onChange={(e) => setChecked({ ...checked, [t.type]: e.target.checked })} />
              {t.type} <span className="text-xs text-slate-500 tabular-nums">({t.count})</span>
            </label>
          ))}
        </div>
        <div className="flex items-center gap-3">
          {selected.length > 0 ? (
            <a href={selectedUrl}
              className="rounded border border-slate-300 dark:border-slate-700 px-3 py-1.5 text-sm hover:bg-slate-50 dark:hover:bg-slate-900">
              Download selected ({selectedCount} papers)
            </a>
          ) : (
            <span className="rounded border border-slate-200 dark:border-slate-800 px-3 py-1.5 text-sm text-slate-400 cursor-not-allowed">
              Download selected (pick a type)
            </span>
          )}
          <span className="text-xs text-slate-500">
            Zip keeps the papers/&lt;doi&gt;/ folder structure. Streams from the corpus
            drive — the full corpus is tens of GB and takes a while.
          </span>
        </div>
      </div>
      <div className="flex flex-wrap gap-3 text-sm items-center">
        <label className="flex items-center gap-2">Status
          <select value={status} onChange={(e) => setStatus(e.target.value)}
            className="rounded border border-slate-300 dark:border-slate-700 bg-transparent px-2 py-1">
            {STATUSES.map((s) => <option key={s} value={s}>{s || "all"}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2">Replications
          <select value={reps} onChange={(e) => setReps(e.target.value)}
            className="rounded border border-slate-300 dark:border-slate-700 bg-transparent px-2 py-1">
            <option value="">all</option>
            <option value="true">contains</option>
            <option value="false">none</option>
          </select>
        </label>
        <span className="text-xs text-slate-500">{data?.count ?? 0} shown</span>
      </div>
      <div className="rounded-lg border border-slate-200 dark:border-slate-800 overflow-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-slate-500 border-b border-slate-200 dark:border-slate-800">
            <tr>{["doi", "status", "reps", "n", "tag", "ver", "batch"].map((h) =>
              <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
          </thead>
          <tbody>
            {(data?.papers ?? []).map((p, i) => (
              <tr key={i} className="border-b border-slate-100 dark:border-slate-900">
                <td className="px-3 py-1.5 font-mono">{String(p.doi)}</td>
                <td className="px-3 py-1.5">{String(p.status)}</td>
                <td className="px-3 py-1.5">{p.contains_replications === 1 ? "✓" : p.contains_replications === 0 ? "—" : "?"}</td>
                <td className="px-3 py-1.5 tabular-nums">{String(p.n_replications ?? "")}</td>
                <td className="px-3 py-1.5">{String(p.latest_tag ?? "")}</td>
                <td className="px-3 py-1.5">{String(p.ai_version ?? "")}</td>
                <td className="px-3 py-1.5 text-slate-500">{String(p.source_batch ?? "")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
