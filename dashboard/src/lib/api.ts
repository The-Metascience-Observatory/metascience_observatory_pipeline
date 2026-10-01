// Thin client for the FastAPI orchestrator. All calls go through the Next.js
// /api/* rewrite to the backend, so the browser stays same-origin.

type ProbeState =
  | "idle" | "partial" | "done" | "stale" | "failed" | "error" | "unknown";
type RunState = "idle" | "running" | "finished" | "failed" | "stopped" | "unknown";

interface Param {
  name: string; type: "int" | "str" | "bool";
  default: unknown; min: number | null; max: number | null; help: string;
}
export interface StageStatus {
  id: string; num: number; label: string; description: string;
  mutex_groups: string[]; params: Param[];
  run: { state: RunState; pid?: number; exit_code?: number; log?: string; started_at?: string; tag?: string };
  probe: { state: ProbeState; detail?: string; progress_done?: number; progress_total?: number };
}
export interface PipelineState { tag: string | null; }
export interface System {
  mem_available_gb: number; grobid_up: boolean; media_mounted: boolean;
  media_disk: { free_gb: number; total_gb: number; pct_used: number } | null;
}
interface DoiRunStatus {
  n_dois: number; in_corpus: number; converted: number;
  extracted_with_tag: number; inbox_pending: number; missing: number;
  collated_csv: string | null;
  paths: { dois_csv: string; include_list: string };
  error?: string;
}
export interface DoiRun {
  name: string; slug: string; created: string; source: string;
  doi_column: string; n_dois: number; n_rows: number; n_invalid: number;
  status: DoiRunStatus;
}
export interface PaperTypeOption { type: string; count: number; }
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
  state: () => j<{ state: PipelineState }>("/api/state"),
  setState: (patch: Partial<PipelineState>) =>
    j<{ state: PipelineState }>("/api/state", { method: "PUT",
      headers: { "content-type": "application/json" }, body: JSON.stringify(patch) }),
  corpus: (q: Record<string, string> = {}) =>
    j<CorpusResponse>("/api/corpus?" + new URLSearchParams(q).toString()),
  corpusDownloadOptions: () =>
    j<{ types: PaperTypeOption[] }>("/api/corpus/download/options"),
  doiRuns: () => j<{ runs: DoiRun[] }>("/api/doi-runs"),
  createDoiRun: (body: { name: string; csv_text?: string; source_path?: string }) =>
    j<DoiRun>("/api/doi-runs", { method: "POST",
      headers: { "content-type": "application/json" }, body: JSON.stringify(body) }),
  deleteDoiRun: (slug: string) =>
    j<{ deleted: boolean }>(`/api/doi-runs/${slug}`, { method: "DELETE" }),
  keywords: () => j<{ lists: KeywordList[] }>("/api/keywords"),
  saveKeywords: (key: string, items: string[]) =>
    j<{ saved: boolean; count: number }>(`/api/keywords/${key}`, {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ items }) }),
  resetKeywords: (key: string) =>
    j<{ reset: boolean; count: number }>(`/api/keywords/${key}/reset`, { method: "POST" }),
  keywordStats: () => j<KeywordStatsEnvelope>("/api/keywords/stats"),
  refreshKeywordStats: () =>
    j<{ started: boolean }>("/api/keywords/stats/refresh", { method: "POST" }),
  artifacts: () => j<{ artifacts: ArtifactInfo[]; warnings: string[] }>("/api/artifacts"),
};

export interface KeywordList {
  key: string; label: string; feeds: string[]; syntax: string;
  items: string[]; count: number; overridden: boolean;
}

export interface KeywordQueryStat {
  query: string; raw: number; completed: boolean; apiCount: number;
  filtered: number; classified: number; confirmed: number;
  confirmRate: number | null; zeroYield: boolean; notSearched: boolean;
}
export interface KeywordApiStats {
  api: string; label: string; queriesExpected: number; queriesCompleted: number;
  raw: number; zeroYield: number; notSearched: number; queries: KeywordQueryStat[];
}
interface KeywordStats {
  version: number; generatedAt: string; computeSeconds: number;
  staleness: { notes: string[] };
  totals: { raw: number; filtered: number; classified: number; confirmed: number;
            queriesExpected: number; queriesCompleted: number;
            zeroYield: number; notSearched: number };
  apis: KeywordApiStats[];
  orphans: Array<{ api: string; query: string; raw: number; inProgress: boolean }>;
  unknownDownstream: Record<string, Record<string, number>>;
}
export interface KeywordStatsEnvelope {
  state: "ready" | "computing" | "missing"; stale: boolean;
  stats: KeywordStats | null; historyCount: number;
}
export interface ArtifactInfo {
  name: string; stage: string; size: number | null; mtime: number | null;
}

export const PROBE_COLOR: Record<ProbeState, string> = {
  done: "bg-emerald-500", partial: "bg-amber-500",
  idle: "bg-slate-400", stale: "bg-orange-500", failed: "bg-rose-500",
  error: "bg-rose-600", unknown: "bg-slate-300",
};
