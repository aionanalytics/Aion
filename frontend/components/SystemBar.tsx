"use client";
import { useEffect, useMemo, useState } from "react";

function prettyStatus(s?: string) {
  const v = String(s || "").toLowerCase();
  if (v === "ok") return "✅ Stable";
  if (v === "warning") return "🟡 Warning";
  if (v === "critical" || v === "error") return "🔴 Critical";
  if (v === "running") return "🟠 Running";
  return "⚪ —";
}

export default function SystemBar() {
  const [status, setStatus] = useState({
    drift: "⚪ Checking...",
    retraining: "⚪ Checking...",
    lastUpdate: "—",
    retrainCycles: "—",
    newsCount: "—",
    tickersTracked: "—",
    version: "SAP v1.4.2",
    debug: "", // 👈 show errors / URL
  });

  // Build an API base that works even if env var is missing.
  const apiBase = useMemo(() => {
    const envBase = (process.env.NEXT_PUBLIC_API_BASE || "").trim().replace(/\/$/, "");
    if (envBase) return envBase;

    // Infer: same host as frontend, backend on :8000
    if (typeof window !== "undefined") {
      const host = window.location.hostname;
      const proto = window.location.protocol; // usually "http:"
      return `${proto}//${host}:8000`;
    }

    return ""; // SSR fallback (won't be used in client effect)
  }, []);

  async function fetchStatus() {
    const url = `${apiBase}/api/system/status`;

    try {
      console.log('[SystemBar] Fetching system status from:', url);
      const res = await fetch(url, { cache: "no-store" });
      
      console.log('[SystemBar] Response status:', res.status, res.statusText);
      
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        console.error('[SystemBar] Fetch failed:', {
          status: res.status,
          statusText: res.statusText,
          responseText: text.slice(0, 200)
        });
        throw new Error(`HTTP ${res.status} ${res.statusText}${text ? ` — ${text.slice(0, 120)}` : ""}`);
      }

      const data = await res.json();
      console.log('[SystemBar] System status data:', {
        hasSupervisor: !!data?.supervisor,
        hasCoverage: !!data?.coverage,
        driftStatus: data?.supervisor?.components?.drift?.status,
        modelsStatus: data?.supervisor?.components?.models?.status
      });

      const sup = data?.supervisor;
      const driftStatus = sup?.components?.drift?.status;
      const modelsStatus = sup?.components?.models?.status;
      const newsStatus = sup?.components?.intel?.news_intel?.status;
      const tickers = data?.coverage?.symbols;

      // Log warnings for critical/degraded statuses
      const logStatusWarnings = (status: string | undefined, component: string) => {
        if (!status) return;
        const normalized = status.toLowerCase();
        if (normalized === 'critical' || normalized === 'error') {
          console.warn(`[SystemBar] ${component} - Critical/Error status:`, status);
        } else if (normalized === 'warning' || normalized === 'degraded') {
          console.warn(`[SystemBar] ${component} - Warning/Degraded status:`, status);
        }
      };

      logStatusWarnings(driftStatus, 'Drift');
      logStatusWarnings(modelsStatus, 'Models');
      logStatusWarnings(newsStatus, 'News');

      setStatus({
        drift: prettyStatus(driftStatus),
        retraining: prettyStatus(modelsStatus),
        lastUpdate: sup?.generated_at || data?.server_time || "—",
        retrainCycles: "—",
        newsCount: prettyStatus(newsStatus),
        tickersTracked: typeof tickers === "number" ? String(tickers) : "—",
        version: "AION v1.1.2",
        debug: "",
      });
    } catch (e: any) {
      console.error('[SystemBar] Error fetching system status:', {
        url,
        error: e?.message || String(e),
        stack: e?.stack
      });
      setStatus((prev) => ({
        ...prev,
        drift: "⚠️ Offline",
        retraining: "—",
        debug: `FAIL ${url} — ${e?.message || String(e)}`,
      }));
    }
  }

  useEffect(() => {
    fetchStatus();
    const id = setInterval(fetchStatus, 15000);
    return () => clearInterval(id);
  }, [apiBase]);

  const items = [
    `Drift: ${status.drift}`,
    `Retraining: ${status.retraining}`,
    `Last Update: ${status.lastUpdate}`,
    `Retrain Cycles: ${status.retrainCycles}`,
    `News Articles: ${status.newsCount}`,
    `Tickers Tracked: ${status.tickersTracked}`,
    `${status.version}`,
  ];

  return (
    <div className="fixed bottom-0 left-0 right-0 bg-slate-950/80 backdrop-blur-xs border-t border-slate-800">
      <div className="mx-auto max-w-7xl px-4 py-2 text-xs text-slate-400 flex flex-wrap items-center justify-center gap-4">
        {items.map((t, i) => (
          <span key={i} className="opacity-80">
            {t}
          </span>
        ))}
        {/* Debug line so you can see the actual URL + error while nightly runs */}
        <span className="opacity-50">{status.debug}</span>
      </div>
    </div>
  );
}
