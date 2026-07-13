"use client";
import { useEffect, useState, useCallback, use } from "react";
import Link from "next/link";
import { api, PROBE_COLOR, type StageStatus } from "@/lib/api";

export default function StageDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [s, setS] = useState<StageStatus | null>(null);
  const [log, setLog] = useState("");
  const [form, setForm] = useState<Record<string, unknown>>({});
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const st = await api.stage(id);
      setS(st);
      if (st.run.state === "running" || st.run.log) {
        const l = await api.logs(id, 400); setLog(l.log);
      }
      setErr(null);
    } catch (e) { setErr((e as Error).message); }
  }, [id]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [refresh]);

  if (!s) return <div className="text-sm text-slate-500">{err ? `Error: ${err}` : "Loading…"}</div>;
  const running = s.run.state === "running";

  const runIt = async () => {
    try { await api.run(id, form); refresh(); }
    catch (e) { alert((e as Error).message); }
  };

  return (
    <div className="space-y-4">
      <Link href="/" className="text-xs text-slate-500 hover:underline">← Pipeline</Link>
      <div className="flex items-center gap-2">
        <h1 className="text-lg font-semibold">{s.num}. {s.label}</h1>
        <span className="flex items-center gap-1.5 text-xs">
          <span className={`inline-block h-2.5 w-2.5 rounded-full ${running ? "bg-sky-500 animate-pulse" : PROBE_COLOR[s.probe.state]}`} />
          {running ? "running" : s.probe.state}
        </span>
      </div>
      <p className="text-sm text-slate-600 dark:text-slate-400">{s.description}</p>
      <p className="text-sm">{s.probe.detail}</p>
      <p className="text-xs text-slate-500">mutex: {s.mutex_groups.join(", ")}
        {s.run.pid ? ` · pid ${s.run.pid}` : ""}{s.run.started_at ? ` · started ${s.run.started_at}` : ""}
        {s.run.exit_code != null ? ` · exit ${s.run.exit_code}` : ""}</p>

      <div className="rounded-lg border border-slate-200 dark:border-slate-800 p-4">
        <h2 className="text-sm font-medium mb-3">Run parameters</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          {s.params.map((p) => (
            <label key={p.name} className="text-xs flex flex-col gap-1">
              <span className="text-slate-500">{p.name}{p.help ? ` — ${p.help}` : ""}</span>
              {p.type === "bool" ? (
                <input type="checkbox" onChange={(e) => setForm((f) => ({ ...f, [p.name]: e.target.checked }))} />
              ) : (
                <input type={p.type === "int" ? "number" : "text"}
                  min={p.min ?? undefined} max={p.max ?? undefined}
                  defaultValue={(p.default as string) ?? ""}
                  onChange={(e) => setForm((f) => ({ ...f, [p.name]: p.type === "int" ? Number(e.target.value) : e.target.value }))}
                  className="rounded border border-slate-300 dark:border-slate-700 bg-transparent px-2 py-1" />
              )}
            </label>
          ))}
        </div>
        <div className="mt-4 flex gap-2">
          {running ? (
            <>
              <button onClick={() => api.stop(id).then(refresh)}
                className="text-sm rounded border border-rose-300 text-rose-600 px-3 py-1.5 hover:bg-rose-50 dark:hover:bg-rose-950">Stop</button>
              <button onClick={() => api.stop(id, true).then(refresh)}
                className="text-sm rounded border border-rose-300 text-rose-600 px-3 py-1.5 hover:bg-rose-50 dark:hover:bg-rose-950">Force kill</button>
            </>
          ) : (
            <button onClick={runIt}
              className="text-sm rounded border border-emerald-300 text-emerald-700 px-3 py-1.5 hover:bg-emerald-50 dark:hover:bg-emerald-950">Run</button>
          )}
        </div>
      </div>

      <div className="rounded-lg border border-slate-200 dark:border-slate-800">
        <div className="px-4 py-2 text-sm font-medium border-b border-slate-200 dark:border-slate-800">Log</div>
        <pre className="p-4 text-xs overflow-auto max-h-[28rem] whitespace-pre-wrap">{log || "(no log yet)"}</pre>
      </div>
    </div>
  );
}
