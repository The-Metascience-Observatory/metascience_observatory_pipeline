"use client";
import { useEffect, useState, useCallback } from "react";
import { api, type KeywordList } from "@/lib/api";

function ListEditor({ list, onSaved }: { list: KeywordList; onSaved: () => void }) {
  // One term per line; editing is local until Save.
  const [text, setText] = useState(list.items.join("\n"));
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  // Re-sync when the server list changes and we have no unsaved edits.
  useEffect(() => { if (!dirty) setText(list.items.join("\n")); }, [list.items, dirty]);

  const count = text.split("\n").filter((l) => l.trim()).length;

  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      const items = text.split("\n").map((l) => l.trim()).filter(Boolean);
      const r = await api.saveKeywords(list.key, items);
      setMsg(`saved ${r.count} terms`); setDirty(false); onSaved();
    } catch (e) { setMsg(`error: ${(e as Error).message}`); }
    finally { setBusy(false); }
  };
  const reset = async () => {
    if (!confirm(`Revert "${list.label}" to the code defaults?`)) return;
    setBusy(true); setMsg(null);
    try { await api.resetKeywords(list.key); setDirty(false); setMsg("reverted to defaults"); onSaved(); }
    catch (e) { setMsg(`error: ${(e as Error).message}`); }
    finally { setBusy(false); }
  };

  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-800 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 className="font-medium">{list.label}
            <span className="ml-2 text-xs font-normal text-slate-500">{count} terms</span>
            {list.overridden && <span className="ml-2 text-xs rounded bg-amber-100 dark:bg-amber-900 text-amber-700 dark:text-amber-300 px-1.5 py-0.5">edited</span>}
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            feeds: {list.feeds.join(", ")} · syntax: <code className="font-mono">{list.syntax}</code>
          </p>
        </div>
      </div>
      <textarea
        value={text}
        onChange={(e) => { setText(e.target.value); setDirty(true); setMsg(null); }}
        spellCheck={false}
        className="mt-3 w-full h-56 font-mono text-xs rounded border border-slate-300 dark:border-slate-700 bg-transparent p-2 resize-y"
      />
      <div className="mt-2 flex items-center gap-2">
        <button disabled={busy || !dirty} onClick={save}
          className="text-sm rounded border border-emerald-300 text-emerald-700 px-3 py-1.5 hover:bg-emerald-50 dark:hover:bg-emerald-950 disabled:opacity-40">
          Save
        </button>
        <button disabled={busy || !list.overridden} onClick={reset}
          className="text-sm rounded border border-slate-300 dark:border-slate-700 px-3 py-1.5 hover:bg-slate-100 dark:hover:bg-slate-800 disabled:opacity-40">
          Revert to default
        </button>
        {dirty && <span className="text-xs text-amber-600">unsaved changes</span>}
        {msg && <span className="text-xs text-slate-500">{msg}</span>}
      </div>
    </div>
  );
}

export default function Keywords() {
  const [lists, setLists] = useState<KeywordList[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try { setLists((await api.keywords()).lists); setErr(null); }
    catch (e) { setErr((e as Error).message); }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">Search keywords</h1>
        <p className="text-sm text-slate-500 mt-1">
          Stage-1 query terms. Edits are saved to a runtime overlay (<code className="font-mono">data/keywords.json</code>)
          and take effect on the next search run — the code defaults are untouched and restorable per list.
          One term per line.
        </p>
      </div>
      {err && (
        <div className="rounded-lg border border-rose-300 dark:border-rose-800 bg-rose-50 dark:bg-rose-950/40 px-4 py-3 text-sm text-rose-700 dark:text-rose-300">
          Can&apos;t reach the backend ({err}). Start it with <code className="font-mono">./dev.sh up</code>.
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        {lists.map((l) => <ListEditor key={l.key} list={l} onSaved={refresh} />)}
      </div>
    </div>
  );
}
