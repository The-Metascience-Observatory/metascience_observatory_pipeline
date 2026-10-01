"use client";
import { useEffect, useState, useCallback, useRef } from "react";
import Link from "next/link";
import { api, type DoiRun, type StageStatus } from "@/lib/api";

// Which pipeline stage each per-run action launches, with the params it needs.
const STEPS = (r: DoiRun) => [
  { label: "1. Download PDFs", stage: "download",
    params: { doi_csv: r.status.paths.dois_csv } },
  { label: "2. Convert inbox", stage: "convert", params: {} },
  { label: "3. Extract", stage: "extract",
    params: { include_list: r.status.paths.include_list, tag: r.slug } },
  { label: "4. Collate", stage: "extract",
    params: { collate_only: true, tag: r.slug } },
];

function Stat({ label, value, tone = "" }: { label: string; value: number | string; tone?: string }) {
  return (
    <span className="text-xs px-2 py-1 rounded bg-slate-100 dark:bg-slate-800">
      {label}: <span className={`font-medium ${tone}`}>{value}</span>
    </span>
  );
}

function RunCard({ r, runningStages, onAction }:
  { r: DoiRun; runningStages: Record<string, boolean>; onAction: () => void }) {
  const [busy, setBusy] = useState(false);
  const s = r.status;
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); } catch (e) { alert((e as Error).message); }
    finally { setBusy(false); onAction(); }
  };
  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-800 p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-medium">{r.name}</p>
          <p className="text-xs text-slate-500 mt-0.5">
            {r.created} · {r.n_dois} DOIs from {r.doi_column} · {r.source}
          </p>
        </div>
        <button disabled={busy}
          onClick={() => { if (confirm(`Delete run "${r.name}"? (only removes the DOI list, never corpus data)`)) act(() => api.deleteDoiRun(r.slug)); }}
          className="text-xs rounded border border-rose-300 text-rose-600 px-2 py-1 hover:bg-rose-50 dark:hover:bg-rose-950 disabled:opacity-50">Delete</button>
      </div>

      {s.error ? (
        <p className="text-xs text-rose-500">status error: {s.error}</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Stat label="in corpus" value={s.in_corpus} />
          <Stat label="converted" value={s.converted} />
          <Stat label="extracted (this run)" value={s.extracted_with_tag}
            tone={s.extracted_with_tag >= s.n_dois ? "text-emerald-600" : ""} />
          <Stat label="awaiting convert" value={s.inbox_pending} />
          <Stat label="not downloaded" value={s.missing} tone={s.missing ? "text-amber-600" : "text-emerald-600"} />
          {s.collated_csv && <Stat label="collated" value="✓" tone="text-emerald-600" />}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {STEPS(r).map((step) => {
          const running = runningStages[step.stage];
          return (
            <button key={step.label} disabled={busy || running}
              title={running ? `stage '${step.stage}' is already running` : undefined}
              onClick={() => act(() => api.run(step.stage, step.params))}
              className="text-xs rounded border border-emerald-300 text-emerald-700 px-2 py-1 hover:bg-emerald-50 dark:hover:bg-emerald-950 disabled:opacity-40">
              {step.label}{running ? " (running…)" : ""}
            </button>
          );
        })}
      </div>
      <p className="text-xs text-slate-500">
        Logs on the stage pages:{" "}
        {["download", "convert", "extract"].map((id, i) => (
          <span key={id}>{i > 0 && " · "}<Link className="hover:underline" href={`/stages/${id}`}>{id}</Link></span>
        ))}
        {s.collated_csv && <> · collated CSV (ready for manual ingest): <code className="font-mono">{s.collated_csv}</code></>}
      </p>
    </div>
  );
}

export default function DoiRuns() {
  const [runs, setRuns] = useState<DoiRun[]>([]);
  const [runningStages, setRunningStages] = useState<Record<string, boolean>>({});
  const [err, setErr] = useState<string | null>(null);
  // create form
  const [name, setName] = useState("");
  const [csvText, setCsvText] = useState("");
  const [srcPath, setSrcPath] = useState("");
  const [creating, setCreating] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const [dr, st] = await Promise.all([api.doiRuns(), api.stages()]);
      setRuns(dr.runs);
      setRunningStages(Object.fromEntries(st.stages.map((s: StageStatus) => [s.id, s.run.state === "running"])));
      setErr(null);
    } catch (e) { setErr((e as Error).message); }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  const create = async () => {
    setCreating(true);
    try {
      const body: { name: string; csv_text?: string; source_path?: string } = { name };
      if (csvText.trim()) body.csv_text = csvText;
      else if (srcPath.trim()) body.source_path = srcPath.trim();
      else throw new Error("paste DOIs, choose a CSV file, or give a server path");
      const created = await api.createDoiRun(body);
      setName(""); setCsvText(""); setSrcPath("");
      if (fileRef.current) fileRef.current.value = "";
      alert(`Created "${created.name}": ${created.n_dois} DOIs (column: ${created.doi_column}${created.n_invalid ? `, ${created.n_invalid} rows without a valid DOI skipped` : ""})`);
      refresh();
    } catch (e) { alert((e as Error).message); }
    finally { setCreating(false); }
  };

  const onFile = (f: File | undefined) => {
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
      setCsvText(String(reader.result ?? ""));
      if (!name) setName(f.name.replace(/\.[^.]+$/, ""));
    };
    reader.readAsText(f);
  };

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">DOI runs</h1>
        <p className="text-sm text-slate-600 dark:text-slate-400 mt-1">
          Run the pipeline on an explicit set of DOIs (e.g. a candidate CSV), bypassing
          search/classify. Download → convert → extract (tagged with the run name) → collate.
          Ingest the collated CSV manually with metascience_observatory_website/data_ingestor/data_ingestor.py.
        </p>
      </div>
      {err && <p className="text-sm text-rose-500">Can&apos;t reach backend: {err}</p>}

      <div className="rounded-lg border border-slate-200 dark:border-slate-800 p-4 space-y-3">
        <h2 className="text-sm font-medium">New run</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs flex flex-col gap-1">
            <span className="text-slate-500">run name (becomes the extraction tag)</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="education-candidates-2026-07"
              className="rounded border border-slate-300 dark:border-slate-700 bg-transparent px-2 py-1" />
          </label>
          <label className="text-xs flex flex-col gap-1">
            <span className="text-slate-500">…or CSV file (parsed in the browser)</span>
            <input ref={fileRef} type="file" accept=".csv,.txt" onChange={(e) => onFile(e.target.files?.[0])}
              className="text-xs" />
          </label>
        </div>
        <label className="text-xs flex flex-col gap-1">
          <span className="text-slate-500">paste DOIs or CSV content — the DOI column is auto-detected
            (prefers replication_doi / doi / replication_url)</span>
          <textarea value={csvText} onChange={(e) => setCsvText(e.target.value)} rows={5}
            placeholder={"10.1037/edu0000965\nhttps://doi.org/10.1145/3345328\n…"}
            className="rounded border border-slate-300 dark:border-slate-700 bg-transparent px-2 py-1 font-mono" />
        </label>
        <label className="text-xs flex flex-col gap-1">
          <span className="text-slate-500">…or a CSV path on the server (used only if the box above is empty)</span>
          <input value={srcPath} onChange={(e) => setSrcPath(e.target.value)}
            placeholder="/absolute/path/to/candidates.csv"
            className="rounded border border-slate-300 dark:border-slate-700 bg-transparent px-2 py-1 font-mono" />
        </label>
        <button disabled={creating || !name.trim()} onClick={create}
          className="text-sm rounded border border-emerald-300 text-emerald-700 px-3 py-1.5 hover:bg-emerald-50 dark:hover:bg-emerald-950 disabled:opacity-50">
          {creating ? "Creating…" : "Create run"}
        </button>
      </div>

      <div className="space-y-3">
        {runs.length === 0 && !err && <p className="text-sm text-slate-500">No runs yet.</p>}
        {runs.map((r) => <RunCard key={r.slug} r={r} runningStages={runningStages} onAction={refresh} />)}
      </div>
    </div>
  );
}
