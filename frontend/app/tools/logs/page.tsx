"use client";

import { useEffect, useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select } from "@/components/ui/select";

const API_BASE = "/api/backend";

type Run = {
  id: string;
  name: string;
  kind: string;
  size_bytes: number;
  mtime: string;
  rel: string;
};

type RunsResponse = { scope: string; count: number; runs: Run[] };

type ReadResponse = {
  id: string;
  name: string;
  path: string;
  size_bytes: number;
  offset: number;
  limit: number;
  next_offset: number | null;
  truncated: boolean;
  content: string;
};

function fmtBytes(n: number) {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export default function LogsPage() {
  const [scope, setScope] = useState<string>("nightly");
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState<Run | null>(null);

  const [query, setQuery] = useState<string>("");
  const [loadingRuns, setLoadingRuns] = useState<boolean>(false);

  const [content, setContent] = useState<string>("");
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loadingContent, setLoadingContent] = useState<boolean>(false);

  const [err, setErr] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return runs;
    return runs.filter((r) => (r.name + " " + r.kind + " " + r.rel).toLowerCase().includes(q));
  }, [runs, query]);

  const fetchRuns = async () => {
    try {
      setLoadingRuns(true);
      setErr(null);
      const res = await fetch(`${API_BASE}/api/logs/nightly/runs?scope=${encodeURIComponent(scope)}` , {
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as RunsResponse;
      setRuns(Array.isArray(data.runs) ? data.runs : []);
    } catch (e: any) {
      setRuns([]);
      setErr(`Failed to load logs (${e?.message ?? "unknown"}).`);
    } finally {
      setLoadingRuns(false);
    }
  };

  const readRun = async (run: Run, offset: number, append: boolean) => {
    try {
      setLoadingContent(true);
      setErr(null);

      const limit = 1_000_000; // 1MB per chunk (backend allows up to 10MB)
      const url = `${API_BASE}/api/logs/nightly/run/${encodeURIComponent(run.id)}?offset=${offset}&limit=${limit}`;
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as ReadResponse;

      setContent((prev) => (append ? prev + data.content : data.content));
      setNextOffset(data.truncated ? data.next_offset ?? null : null);
    } catch (e: any) {
      setContent(append ? content : "");
      setNextOffset(null);
      setErr(`Failed to read log (${e?.message ?? "unknown"}).`);
    } finally {
      setLoadingContent(false);
    }
  };

  useEffect(() => {
    fetchRuns();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

  const onSelect = (r: Run) => {
    setSelected(r);
    setContent("");
    setNextOffset(null);
    readRun(r, 0, false);
  };

  const copyToClipboard = async () => {
    try {
      await navigator.clipboard.writeText(content || "");
    } catch {
      // ignore
    }
  };

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 space-y-6">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="orbitron text-3xl">Logs</h1>
          <p className="text-slate-400 text-sm">
            Browse nightly + scheduled runs (and other scopes) from the backend&apos;s log folders.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <div className="min-w-[190px]">
            <Select
              value={scope}
              onChange={setScope}
              className="w-full"
              options={[
                { value: "nightly", label: "Nightly (incl. scheduler)" },
                { value: "daily", label: "Daily (root)" },
                { value: "backend", label: "Backend" },
                { value: "all", label: "All" },
              ]}
            />
          </div>

          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by name…"
            className="w-[220px] bg-slate-900 border-slate-800"
          />

          <Button onClick={fetchRuns} disabled={loadingRuns}>
            {loadingRuns ? "Refreshing…" : "Refresh"}
          </Button>
        </div>
      </div>

      {err && (
        <div className="text-red-300 text-sm border border-red-900/60 bg-red-950/40 rounded-xl px-4 py-3">
          {err}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="card">
          <CardHeader>
            <CardTitle className="orbitron text-lg">Available Logs</CardTitle>
            <div className="text-xs text-slate-400">
              {filtered.length} file(s)
            </div>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-[520px] pr-3">
              <div className="space-y-2">
                {filtered.map((r) => {
                  const active = selected?.id === r.id;
                  return (
                    <button
                      key={r.id}
                      onClick={() => onSelect(r)}
                      className={`w-full text-left px-3 py-2 rounded-xl border transition ${
                        active
                          ? "border-blue-500/60 bg-slate-800/60"
                          : "border-slate-800 bg-slate-900/40 hover:bg-slate-900/70"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <div className="truncate">
                          <div className="text-sm text-slate-100 truncate">{r.name}</div>
                          <div className="text-xs text-slate-400 truncate">{r.kind} • {r.mtime}</div>
                        </div>
                        <div className="text-xs text-slate-400 whitespace-nowrap">{fmtBytes(r.size_bytes)}</div>
                      </div>
                    </button>
                  );
                })}
                {filtered.length === 0 && (
                  <div className="text-sm text-slate-400">No logs found for this scope.</div>
                )}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>

        <Card className="card lg:col-span-2">
          <CardHeader>
            <div className="flex items-start justify-between gap-4 flex-wrap">
              <div>
                <CardTitle className="orbitron text-lg">{selected ? selected.name : "Select a log"}</CardTitle>
                {selected && (
                  <div className="text-xs text-slate-400 mt-1">
                    {selected.kind} • {fmtBytes(selected.size_bytes)} • {selected.mtime}
                  </div>
                )}
              </div>

              <div className="flex items-center gap-2">
                <Button
                  onClick={copyToClipboard}
                  disabled={!content}
                  className="bg-slate-900 hover:bg-slate-800 border border-slate-700"
                >
                  Copy
                </Button>
                {selected && (
                  <Button
                    onClick={() => readRun(selected, 0, false)}
                    disabled={loadingContent}
                  >
                    {loadingContent ? "Loading…" : "Reload"}
                  </Button>
                )}
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-[520px] pr-3">
              {!selected && (
                <div className="text-slate-400 text-sm">
                  Pick a file on the left to view its contents.
                </div>
              )}

              {selected && !content && loadingContent && (
                <div className="text-slate-400 text-sm">Loading log…</div>
              )}

              {selected && content && (
                <pre className="whitespace-pre-wrap text-xs leading-5 text-slate-100">
                  {content}
                </pre>
              )}

              {selected && nextOffset != null && (
                <div className="mt-4 flex items-center gap-3">
                  <Button
                    onClick={() => readRun(selected, nextOffset, true)}
                    disabled={loadingContent}
                  >
                    {loadingContent ? "Loading…" : "Load more"}
                  </Button>
                  <div className="text-xs text-slate-400">
                    This file is large; showing it in chunks.
                  </div>
                </div>
              )}
            </ScrollArea>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
