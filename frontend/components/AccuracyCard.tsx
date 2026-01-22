"use client";
import { useEffect, useState } from "react";
import { Activity } from "lucide-react";

/**
 * Try multiple URLs in parallel, return first success
 */
async function tryGetFirst<T>(
  urls: string[], 
  timeoutMs: number = 3000
): Promise<{ url: string; data: T } | null> {
  if (urls.length === 0) return null;

  const controllers: AbortController[] = [];

  const promises = urls.map(async (url) => {
    const controller = new AbortController();
    controllers.push(controller);
    
    const timeoutId = setTimeout(() => {
      controller.abort();
    }, timeoutMs);

    try {
      const response = await fetch(url, { 
        method: "GET", 
        cache: "no-store",
        signal: controller.signal,
      });
      
      clearTimeout(timeoutId);
      
      if (!response.ok) {
        throw new Error(`GET ${url} failed: ${response.status}`);
      }
      
      const data = await response.json() as T;
      return { url, data };
    } catch (error) {
      clearTimeout(timeoutId);
      throw error;
    }
  });

  try {
    const result = await Promise.any(promises);
    controllers.forEach(c => c.abort());
    return result;
  } catch (error) {
    controllers.forEach(c => c.abort());
    return null;
  }
}

export default function AccuracyCard() {
  const [acc, setAcc] = useState<number | null>(null);
  const [wow, setWow] = useState<number | null>(null);
  const [summary, setSummary] = useState<string>("");

  useEffect(() => {
    async function fetchAccuracy() {
      try {
        // Try consolidated endpoint first, then fallback to legacy endpoints
        const result = await tryGetFirst<any>([
          "/api/backend/page/dashboard",    // NEW consolidated endpoint through proxy
          "/api/page/dashboard",             // NEW consolidated endpoint direct
          "/api/backend/dashboard/metrics",  // OLD endpoint through proxy (fallback)
          "/api/dashboard/metrics",          // OLD endpoint direct (fallback)
        ]);

        if (result?.data && typeof result.data.accuracy_30d === "number") {
          setAcc(result.data.accuracy_30d * 100);
          setSummary(result.data.summary || "");
          // You could later calculate WoW here if you start tracking weekly snapshots
          setWow(null);
        }
      } catch (err) {
        console.error("Failed to fetch dashboard metrics:", err);
      }
    }
    fetchAccuracy();
  }, []);

  const pct = acc != null ? acc.toFixed(1) : null;

  return (
    <div className="card card-hover p-6 flex-1 min-h-[180px] flex flex-col justify-between">
      <div className="flex items-center gap-2 text-slate-300">
        <Activity size={18} /> <span className="text-sm">Accuracy</span>
      </div>
      <div className="text-center">
        <div className="text-5xl font-extrabold text-brand-400">
          {pct != null ? `${pct}%` : "—"}
        </div>
        {wow != null ? (
          <div className="mt-2 text-sm text-slate-400">
            {`${wow >= 0 ? "+" : ""}${wow.toFixed(1)}% WoW`}
          </div>
        ) : (
          <div className="mt-2 text-sm text-slate-400">{summary}</div>
        )}
      </div>
    </div>
  );
}
