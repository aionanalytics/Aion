"use client";

import { useEffect, useState } from "react";
import { getIntradaySnapshot } from "@/lib/api";

type IntradayRow = {
  symbol: string;
  price: number | null;
  volume: number | null;
  score: number | null;
  prob_buy: number | null;
  prob_sell: number | null;
  action: string | null;
  position_qty: number;
  position_avg_price: number;
};

type Snapshot = {
  as_of: string;
  cash: number;
  rows: IntradayRow[];
};

export default function IntradayPage() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadOnce() {
      try {
        const data = await getIntradaySnapshot(120);
        if (!cancelled) setSnap(data);
      } catch (e) {
        console.error(e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadOnce();

    const id = setInterval(loadOnce, 5000); // 5s polling
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (loading && !snap) {
    return <div className="p-6">Loading intraday dashboard...</div>;
  }

  const rows = snap?.rows ?? [];
  const activePositions = rows.filter((r) => r.position_qty && r.position_qty !== 0);

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-3xl font-bold">Intraday AI Dashboard</h1>

      {/* Summary strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <SummaryCard
          label="Cash"
          value={snap ? `$${snap.cash.toFixed(2)}` : "--"}
        />
        <SummaryCard
          label="Active Positions"
          value={activePositions.length.toString()}
        />
        <SummaryCard
          label="Universe"
          value={rows.length.toString()}
          hint="Top intraday + held positions"
        />
        <SummaryCard
          label="Last Update"
          value={snap?.as_of ? new Date(snap.as_of).toLocaleTimeString() : "--"}
        />
      </div>

      {/* Main table */}
      <div className="bg-gray-900 border border-gray-800 rounded-2xl shadow-lg overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
          <h2 className="text-lg font-semibold">Live Intraday Signals</h2>
          <span className="text-xs text-gray-400">
            Auto-refreshes every 5 seconds
          </span>
        </div>

        <div className="overflow-auto max-h-[70vh]">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-950 sticky top-0 z-10">
              <tr>
                <Th>Symbol</Th>
                <Th>Price</Th>
                <Th>Volume</Th>
                <Th>Score</Th>
                <Th>Buy Prob</Th>
                <Th>Sell Prob</Th>
                <Th>Action</Th>
                <Th>Position</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {rows.map((row) => {
                const isHolding = row.position_qty && row.position_qty !== 0;
                const isSelected = selected === row.symbol;

                return (
                  <tr
                    key={row.symbol}
                    onClick={() =>
                      setSelected(
                        isSelected ? null : row.symbol
                      )
                    }
                    className={`cursor-pointer transition-colors ${
                      isSelected
                        ? "bg-gray-800/80"
                        : "hover:bg-gray-900/70"
                    }`}
                  >
                    <Td className={isHolding ? "font-semibold" : ""}>
                      {row.symbol}
                    </Td>
                    <Td>
                      {row.price != null ? row.price.toFixed(2) : "--"}
                    </Td>
                    <Td>
                      {row.volume != null
                        ? row.volume.toLocaleString()
                        : "--"}
                    </Td>
                    <Td>
                      {row.score != null ? row.score.toFixed(3) : "--"}
                    </Td>
                    <Td>
                      {row.prob_buy != null
                        ? (row.prob_buy * 100).toFixed(1) + "%"
                        : "--"}
                    </Td>
                    <Td>
                      {row.prob_sell != null
                        ? (row.prob_sell * 100).toFixed(1) + "%"
                        : "--"}
                    </Td>
                    <Td>
                      <ActionBadge action={row.action} />
                    </Td>
                    <Td>
                      {isHolding
                        ? `${row.position_qty.toFixed(2)} @ ${row.position_avg_price.toFixed(
                            2
                          )}`
                        : "—"}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Optional mini-panel to show selected symbol details later */}
      {/* We can add charts / logs here without cluttering the main grid */}
    </div>
  );
}

function SummaryCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-2xl bg-gray-900 border border-gray-800 px-4 py-3 shadow">
      <div className="text-xs uppercase tracking-wide text-gray-400">
        {label}
      </div>
      <div className="text-xl font-semibold mt-1">{value}</div>
      {hint && <div className="text-[10px] text-gray-500 mt-1">{hint}</div>}
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="px-3 py-2 text-left text-xs font-semibold text-gray-300 uppercase tracking-wide">
      {children}
    </th>
  );
}

function Td({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <td className={`px-3 py-2 whitespace-nowrap ${className}`}>{children}</td>
  );
}

function ActionBadge({ action }: { action: string | null }) {
  if (!action) return <span className="text-gray-500">–</span>;
  const a = action.toUpperCase();
  let color = "bg-gray-800 text-gray-200";

  if (a === "BUY") color = "bg-emerald-700 text-emerald-50";
  else if (a === "SELL") color = "bg-rose-700 text-rose-50";
  else if (a === "HOLD") color = "bg-slate-700 text-slate-50";

  return (
    <span className={`px-2 py-1 rounded-full text-xs font-medium ${color}`}>
      {a}
    </span>
  );
}
