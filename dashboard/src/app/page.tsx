"use client";
import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { api, PROBE_COLOR, type StageStatus, type System, type PipelineState, type ArtifactInfo } from "@/lib/api";

function fmtBytes(n: number | null) {
  if (n == null) return "—";
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)} KB`;
  return `${n} B`;
}

function AgeBadge({ mtime }: { mtime: number | null }) {
  if (mtime == null) {
    return <span className="text-[10px] rounded px-1.5 py-0.5 bg-slate-100 dark:bg-slate-800 text-slate-500">missing</span>;
  }
  const days = (Date.now() / 1000 - mtime) / 86400;
  const label = days < 1 ? "today" : `${Math.floor(days)}d ago`;
  const cls = days < 7
    ? "bg-emerald-100 dark:bg-emerald-900 text-emerald-700 dark:text-emerald-300"
    : days < 30
      ? "bg-amber-100 dark:bg-amber-900 text-amber-700 dark:text-amber-300"
      : "bg-rose-100 dark:bg-rose-900 text-rose-700 dark:text-rose-300";
  return <span className={`text-[10px] rounded px-1.5 py-0.5 whitespace-nowrap ${cls}`}>{label}</span>;
}

function ArtifactsPanel({ artifacts, warnings }: { artifacts: ArtifactInfo[]; warnings: string[] }) {
  if (!artifacts.length) return null;
  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-800 p-4">
      <h2 className="text-sm font-medium">Data artifacts</h2>
      {warnings.length > 0 && (
        <div className="mt-2 rounded border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 px-3 py-2 text-xs text-amber-800 dark:text-amber-300 space-y-0.5">
          {warnings.map((w) => <p key={w}>{w}</p>)}
        </div>
      )}
      <table className="mt-2 w-full text-xs">
        <thead>
          <tr className="text-left text-slate-500 border-b border-slate-200 dark:border-slate-800">
            <th className="py-1 pr-2 font-normal">File</th>
            <th className="py-1 px-2 font-normal">Stage</th>
            <th className="py-1 px-2 font-normal text-right">Size</th>
            <th className="py-1 pl-2 font-normal">Updated</th>
          </tr>
        </thead>
        <tbody>
          {artifacts.map((a) => (
            <tr key={a.name} className="border-b border-slate-100 dark:border-slate-800/60">
              <td className="py-1 pr-2 font-mono">{a.name}</td>
              <td className="py-1 px-2 text-slate-500">{a.stage}</td>
              <td className="py-1 px-2 text-right tabular-nums">{fmtBytes(a.size)}</td>
              <td className="py-1 pl-2">
                <AgeBadge mtime={a.mtime} />
                {a.mtime != null && (
                  <span className="ml-1.5 text-slate-400">{new Date(a.mtime * 1000).toISOString().slice(0, 10)}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProgressBar({ done, total }: { done?: number; total?: number }) {
  if (done == null || !total) return null;
  const pct = Math.min(100, Math.round((100 * done) / total));
  return (
    <div className="mt-2 h-1.5 w-full rounded bg-slate-200 dark:bg-slate-800">
      <div className="h-full rounded bg-sky-500" style={{ width: `${pct}%` }} />
    </div>
  );
}

function StageCard({ s, onAction }: { s: StageStatus; onAction: () => void }) {
  const [busy, setBusy] = useState(false);
  const running = s.run.state === "running";
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); } catch (e) { alert((e as Error).message); }
    finally { setBusy(false); onAction(); }
  };
  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-800 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <Link href={`/stages/${s.id}`} className="font-medium hover:underline">
            {s.num}. {s.label}
          </Link>
          <p className="text-xs text-slate-500 mt-0.5">{s.description}</p>
        </div>
        <span className="flex items-center gap-1.5 text-xs whitespace-nowrap">
          <span className={`inline-block h-2.5 w-2.5 rounded-full ${running ? "bg-sky-500 animate-pulse" : PROBE_COLOR[s.probe.state]}`} />
          {running ? "running" : s.probe.state}
        </span>
      </div>
      <p className="text-xs text-slate-600 dark:text-slate-400 mt-2 min-h-[1rem]">{s.probe.detail}</p>
      <ProgressBar done={s.probe.progress_done} total={s.probe.progress_total} />
      <div className="mt-3 flex gap-2">
        {running ? (
          <button disabled={busy} onClick={() => act(() => api.stop(s.id))}
            className="text-xs rounded border border-rose-300 text-rose-600 px-2 py-1 hover:bg-rose-50 dark:hover:bg-rose-950 disabled:opacity-50">Stop</button>
        ) : (
          <button disabled={busy} onClick={() => act(() => api.run(s.id, {}))}
            className="text-xs rounded border border-emerald-300 text-emerald-700 px-2 py-1 hover:bg-emerald-50 dark:hover:bg-emerald-950 disabled:opacity-50">Run</button>
        )}
        <Link href={`/stages/${s.id}`} className="text-xs rounded border border-slate-300 dark:border-slate-700 px-2 py-1 hover:bg-slate-100 dark:hover:bg-slate-800">Details</Link>
      </div>
    </div>
  );
}

function SystemStrip({ sys, backendUp }: { sys: System | null; backendUp: boolean }) {
  const chip = (label: string, val: string, ok = true) => (
    <span className="text-xs px-2 py-1 rounded bg-slate-100 dark:bg-slate-800">
      {label}: <span className={ok ? "" : "text-rose-500 font-medium"}>{val}</span>
    </span>
  );
  return (
    <div className="flex flex-wrap gap-2 items-center">
      {chip("backend", backendUp ? "up" : "down", backendUp)}
      {sys && chip("RAM avail", `${sys.mem_available_gb} GB`, sys.mem_available_gb > 20)}
      {sys?.media_disk && chip("drive free", `${sys.media_disk.free_gb} GB (${sys.media_disk.pct_used}% used)`, sys.media_disk.free_gb > 15)}
      {sys && chip("GROBID", sys.grobid_up ? "up" : "down", sys.grobid_up)}
      {sys && chip("/media", sys.media_mounted ? "mounted" : "MISSING", sys.media_mounted)}
    </div>
  );
}

export default function Home() {
  const [stages, setStages] = useState<StageStatus[]>([]);
  const [sys, setSys] = useState<System | null>(null);
  const [pstate, setPstate] = useState<PipelineState>({ batch: null, tag: null });
  const [batches, setBatches] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [backendUp, setBackendUp] = useState(true);
  const [arts, setArts] = useState<{ artifacts: ArtifactInfo[]; warnings: string[] }>({ artifacts: [], warnings: [] });

  const refresh = useCallback(async () => {
    try {
      const [st, sy, ba, ar] = await Promise.all([api.stages(), api.system(), api.batch(), api.artifacts()]);
      setStages(st.stages); setPstate(st.state); setSys(sy);
      setBatches(ba.available_batches); setArts(ar); setErr(null); setBackendUp(true);
    } catch (e) {
      setErr((e as Error).message); setBackendUp(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  const setTag = async (tag: string) => { await api.setBatch({ tag }); refresh(); };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">Pipeline</h1>
        <SystemStrip sys={sys} backendUp={backendUp} />
      </div>
      <div className="flex flex-wrap gap-3 items-center text-sm">
        <label className="flex items-center gap-2">Batch
          <select value={pstate.batch ?? ""} onChange={(e) => { api.setBatch({ batch: e.target.value }).then(refresh); }}
            className="rounded border border-slate-300 dark:border-slate-700 bg-transparent px-2 py-1">
            <option value="">—</option>
            {batches.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2">Tag
          <input defaultValue={pstate.tag ?? ""} onBlur={(e) => setTag(e.target.value)}
            placeholder="extraction tag" className="rounded border border-slate-300 dark:border-slate-700 bg-transparent px-2 py-1 w-44" />
        </label>
      </div>
      {err && (
        <div className="rounded-lg border border-rose-300 dark:border-rose-800 bg-rose-50 dark:bg-rose-950/40 px-4 py-3 text-sm">
          <p className="font-medium text-rose-700 dark:text-rose-300">Can&apos;t reach the pipeline backend (FastAPI on :8090).</p>
          <p className="text-rose-600 dark:text-rose-400 mt-1">
            The dashboard is only a frontend — start the backend too. In the{" "}
            <code className="font-mono">mo_pipeline</code> directory run{" "}
            <code className="font-mono px-1 rounded bg-rose-100 dark:bg-rose-900">./dev.sh up</code>
            {" "}(starts both), or in a separate terminal{" "}
            <code className="font-mono px-1 rounded bg-rose-100 dark:bg-rose-900">python -m uvicorn server.app.main:app --port 8090</code>.
          </p>
          <p className="text-xs text-rose-500 mt-1">detail: {err}</p>
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {stages.map((s) => <StageCard key={s.id} s={s} onAction={refresh} />)}
      </div>
      <ArtifactsPanel artifacts={arts.artifacts} warnings={arts.warnings} />
    </div>
  );
}
