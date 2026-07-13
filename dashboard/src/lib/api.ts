// Thin client for the FastAPI orchestrator. All calls go through the Next.js
// /api/* rewrite to the backend, so the browser stays same-origin.

export type ProbeState =
  | "idle" | "partial" | "done" | "stale" | "failed" | "error" | "unknown";
export type RunState = "idle" | "running" | "finished" | "failed";

export interface Param {
  name: string; type: "int" | "str" | "bool";
  default: unknown; min: number | null; max: number | null; help: string;
}
export interface StageStatus {
  id: string; num: number; label: string; description: string;
  mutex_groups: string[]; params: Param[];
  run: { state: RunState; pid?: number; exit_code?: number; log?: string; started_at?: string; tag?: string };
  probe: { state: ProbeState; detail?: string; progress_done?: number; progress_total?: number;
           mem_available_gb?: number; grobid_up?: boolean; latest_db?: string };
}
export interface PipelineState { batch: string | null; tag: string | null; }
export interface System {
  mem_available_gb: number; grobid_up: boolean; media_mounted: boolean;
  media_disk: { free_gb: number; total_gb: number; pct_used: number } | null;
}
export interface CorpusResponse {
  available: boolean;
  stats?: { total: number; by_status: Record<string, number>;
            with_replications: number; without_replications: number;
            total_replication_entries: number; ingested: number };
  count?: number;
  papers?: Array<Record<string, unknown>>;
}

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { cache: "no-store", ...init });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || `${r.status} ${r.statusText}`);
  }
  return r.json();
}

export const api = {
  health: () => j<{ ok: boolean }>("/api/health"),
  stages: () => j<{ stages: StageStatus[]; state: PipelineState }>("/api/stages"),
  stage: (id: string) => j<StageStatus>(`/api/stages/${id}`),
  logs: (id: string, lines = 300) => j<{ log: string }>(`/api/stages/${id}/logs?lines=${lines}`),
  run: (id: string, params: Record<string, unknown>) =>
    j(`/api/stages/${id}/run`, { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ params }) }),
  stop: (id: string, force = false) =>
    j(`/api/stages/${id}/stop?force=${force}`, { method: "POST" }),
  system: () => j<System>("/api/system"),
  batch: () => j<{ state: PipelineState; available_batches: string[] }>("/api/batch"),
  setBatch: (patch: Partial<PipelineState>) =>
    j<{ state: PipelineState }>("/api/batch", { method: "PUT",
      headers: { "content-type": "application/json" }, body: JSON.stringify(patch) }),
  corpus: (q: Record<string, string> = {}) =>
    j<CorpusResponse>("/api/corpus?" + new URLSearchParams(q).toString()),
};

export const PROBE_COLOR: Record<ProbeState, string> = {
  done: "bg-emerald-500", partial: "bg-amber-500",
  idle: "bg-slate-400", stale: "bg-orange-500", failed: "bg-rose-500",
  error: "bg-rose-600", unknown: "bg-slate-300",
};
