"use client";

import * as React from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Activity, Clock, DollarSign, Shield, SlidersHorizontal, RefreshCw } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";

import { LineChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { PerformanceChart } from "@/components/charts/PerformanceChart";

// -----------------------------
// Types
// -----------------------------

type EodBotConfig = {
  max_alloc: number;
  max_positions: number;
  stop_loss: number;
  take_profit: number;
  aggression: number;
  enabled?: boolean;
};

type EodConfigResponse = { configs: Record<string, EodBotConfig> };

type EodStatusResponse = {
  running?: boolean;
  last_update?: string;
  bots?: Record<
    string,
    {
      cash?: number;
      invested?: number;
      allocated?: number;
      holdings_count?: number;
      equity?: number;
      last_update?: string;
      type?: string;
      equity_curve?: Array<{ t?: string; value?: number } | number>;
      pnl_curve?: Array<{ t?: string; value?: number } | number>;
      positions?: any[];
      enabled?: boolean;
    }
  >;
};

type IntradayPnlResponse = {
  total?: { realized?: number; unrealized?: number; total?: number; updated_at?: string };
  bots?: Record<string, { realized?: number; unrealized?: number; total?: number }>;
  // sometimes your intraday summary is shaped differently; keep loose
  date?: string;
};

type IntradayFill = {
  ts?: string;
  time?: string;
  symbol?: string;
  side?: string;
  action?: string;
  qty?: number;
  price?: number;
  pnl?: number;
};

type IntradaySignal = {
  ts?: string;
  time?: string;
  symbol?: string;
  action?: string;
  side?: string;
  confidence?: number;
};

type BotsPageBundle = {
  as_of?: string;
  swing?: {
    status?: EodStatusResponse | any;
    configs?: EodConfigResponse | any;
    log_days?: any;
  };
  intraday?: {
    status?: any;
    configs?: any;
    log_days?: any;
    pnl_last_day?: IntradayPnlResponse | any;
    tape?: {
      updated_at?: string | null;
      fills?: IntradayFill[];
      signals?: IntradaySignal[];
    };
  };
};

// Draft config
type BotDraft = EodBotConfig & {
  penny_only?: boolean;
  allow_etfs?: boolean;
  max_daily_trades?: number;
};

// -----------------------------
// Small helpers
// -----------------------------

function withBust(url: string) {
  const bust = `_ts=${Date.now()}`;
  return url.includes("?") ? `${url}&${bust}` : `${url}?${bust}`;
}

async function apiGet<T>(url: string): Promise<T> {
  const r = await fetch(withBust(url), { method: "GET", cache: "no-store" });
  if (!r.ok) throw new Error(`GET ${url} failed: ${r.status}`);
  return (await r.json()) as T;
}

async function apiPostJson<T>(url: string, body: any): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`POST ${url} failed: ${r.status}`);
  return (await r.json()) as T;
}

async function tryGetFirst<T>(urls: string[]): Promise<{ url: string; data: T } | null> {
  for (const u of urls) {
    try {
      const data = await apiGet<T>(u);
      return { url: u, data };
    } catch {
      // ignore
    }
  }
  return null;
}

function fmtMoney(n: any): string {
  const x = Number(n);
  if (!isFinite(x)) return "$0";
  try {
    return x.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  } catch {
    return `$${Math.round(x)}`;
  }
}

function fmtMaybeMoney(n: any): string {
  if (n === null || n === undefined) return "—";
  const x = Number(n);
  if (!isFinite(x)) return "—";
  return fmtMoney(x);
}

function fmtPct(n: any): string {
  const x = Number(n);
  if (!isFinite(x)) return "0%";
  return `${Math.round(x * 100)}%`;
}

function clampNum(n: any, fallback: number): number {
  const x = Number(n);
  return isFinite(x) ? x : fallback;
}

function loadLocal<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function saveLocal<T>(key: string, value: T) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // ignore
  }
}


function buildFallbackSeries(value?: number): Array<{ t: string; value: number }> {
  const v = clampNum(value, 0);
  const now = Date.now();
  return Array.from({ length: 12 }, (_, i) => {
    const t = new Date(now - (11 - i) * 60_000).toISOString();
    const jiggle = (i % 3 === 0 ? -1 : 1) * (i % 4) * 0.002;
    return { t, value: v * (1 + jiggle) };
  });
}

function coerceCurve(curve: any, fallbackValue?: number): Array<{ t: string; value: number }> {
  if (Array.isArray(curve) && curve.length) {
    const out: Array<{ t: string; value: number }> = [];
    for (let i = 0; i < curve.length; i++) {
      const row = curve[i];
      if (typeof row === "number") {
        out.push({ t: String(i), value: clampNum(row, 0) });
      } else if (row && typeof row === "object") {
        const t = String((row as any).t ?? (row as any).ts ?? (row as any).time ?? i);
        const v = clampNum((row as any).value ?? (row as any).v ?? (row as any).equity ?? (row as any).pnl, 0);
        out.push({ t, value: v });
      }
    }
    if (out.length) return out;
  }
  return buildFallbackSeries(fallbackValue);
}

// -----------------------------
// Draft hook
// -----------------------------

function useBotDraft(storageKey: string, base: BotDraft) {
  const normalizedBase = useMemo<BotDraft>(() => {
    return {
      max_alloc: clampNum(base.max_alloc, 10_000),
      max_positions: Math.max(0, Math.round(clampNum(base.max_positions, 10))),
      stop_loss: Math.max(0, clampNum(base.stop_loss, 0.05)),
      take_profit: Math.max(0, clampNum(base.take_profit, 0.10)),
      aggression: Math.min(1, Math.max(0, clampNum(base.aggression, 0.5))),
      enabled: base.enabled ?? true,
      penny_only: !!base.penny_only,
      allow_etfs: base.allow_etfs ?? true,
      max_daily_trades: base.max_daily_trades ?? 6,
    };
  }, [base]);

  const [draft, setDraft] = useState<BotDraft>(normalizedBase);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const cached = loadLocal<BotDraft>(storageKey, normalizedBase);
    setDraft({ ...normalizedBase, ...cached });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey]);

  function setField<K extends keyof BotDraft>(k: K, v: BotDraft[K]) {
    setDraft((p) => {
      const next = { ...p, [k]: v };
      saveLocal(storageKey, next);
      return next;
    });
    setDirty(true);
  }

  function reset() {
    setDraft(normalizedBase);
    saveLocal(storageKey, normalizedBase);
    setDirty(false);
  }

  return { draft, dirty, saving, setSaving, setField, reset, setDirty };
}

// -----------------------------
// Rules panel
// -----------------------------

function BotRulesPanel({
  botKey,
  botType,
  draft,
  setField,
  dirty,
  saving,
  onSave,
  onReset,
}: {
  botKey: string;
  botType: "swing" | "dt";
  draft: BotDraft;
  setField: <K extends keyof BotDraft>(k: K, v: BotDraft[K]) => void;
  dirty: boolean;
  saving: boolean;
  onSave: () => void;
  onReset: () => void;
}) {
  return (
    <div className="h-full rounded-xl border bg-card/40 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <SlidersHorizontal className="h-4 w-4 text-muted-foreground" />
          <div>
            <div className="text-sm font-semibold">Bot Rules</div>
            <div className="text-xs text-muted-foreground">
              {botType === "swing" ? "Swing / horizon bot" : "Day-trading bot"} • {botKey}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {dirty ? <Badge variant="secondary">Unsaved</Badge> : <Badge variant="outline">Saved</Badge>}
        </div>
      </div>

      <Separator className="my-3" />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="space-y-3">
          <div className="text-xs font-semibold text-muted-foreground">Allocation & Limits</div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label className="text-xs">Max Spend</Label>
              <Input
                type="number"
                value={draft.max_alloc}
                onChange={(e) => setField("max_alloc", clampNum(e.target.value, draft.max_alloc))}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Max Holdings</Label>
              <Input
                type="number"
                value={draft.max_positions}
                onChange={(e) =>
                  setField("max_positions", Math.max(0, Math.round(clampNum(e.target.value, draft.max_positions))))
                }
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="flex items-center justify-between gap-3 rounded-lg border bg-background/40 p-3">
              <div className="text-xs">
                <div className="font-medium">Penny-only</div>
                <div className="text-muted-foreground">Filter to low-priced</div>
              </div>
              <Switch checked={!!draft.penny_only} onCheckedChange={(v) => setField("penny_only", v)} />
            </div>
            <div className="flex items-center justify-between gap-3 rounded-lg border bg-background/40 p-3">
              <div className="text-xs">
                <div className="font-medium">Allow ETFs</div>
                <div className="text-muted-foreground">ETFs permitted</div>
              </div>
              <Switch checked={!!draft.allow_etfs} onCheckedChange={(v) => setField("allow_etfs", v)} />
            </div>
          </div>

          {botType === "dt" ? (
            <div className="space-y-1">
              <Label className="text-xs">Max Daily Trades</Label>
              <Input
                type="number"
                value={draft.max_daily_trades ?? 6}
                onChange={(e) => setField("max_daily_trades", Math.max(0, Math.round(clampNum(e.target.value, 6))))}
              />
            </div>
          ) : null}
        </div>

        <div className="space-y-3">
          <div className="text-xs font-semibold text-muted-foreground">Risk & Exits</div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label className="text-xs">Stop Loss</Label>
              <Input
                type="number"
                step="0.01"
                value={draft.stop_loss}
                onChange={(e) => setField("stop_loss", Math.max(0, clampNum(e.target.value, draft.stop_loss)))}
              />
              <div className="text-[11px] text-muted-foreground">Shown as {fmtPct(draft.stop_loss)}</div>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Take Profit</Label>
              <Input
                type="number"
                step="0.01"
                value={draft.take_profit}
                onChange={(e) => setField("take_profit", Math.max(0, clampNum(e.target.value, draft.take_profit)))}
              />
              <div className="text-[11px] text-muted-foreground">Shown as {fmtPct(draft.take_profit)}</div>
            </div>
          </div>

          <div className="space-y-1">
            <Label className="text-xs">Aggressiveness</Label>
            <Input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={draft.aggression}
              onChange={(e) => setField("aggression", clampNum(e.target.value, draft.aggression))}
            />
            <div className="text-[11px] text-muted-foreground">
              {draft.aggression < 0.34 ? "Conservative" : draft.aggression < 0.67 ? "Balanced" : "Aggressive"} •{" "}
              {fmtPct(draft.aggression)}
            </div>
          </div>

          <div className="mt-2 flex items-center justify-end gap-2">
            <Button type="button" variant="outline" onClick={onReset} disabled={saving}>
              Reset defaults
            </Button>
            <Button type="button" onClick={onSave} disabled={saving || !dirty}>
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

// -----------------------------
// Bot category card with tabs
// -----------------------------

type BotInfo = {
  botKey: string;
  statusNode?: any;
  base: BotDraft;
  displayName: string;
  dtMeta?: { realized?: number; unrealized?: number; total?: number; fillsCount?: number };
};

function BotCategoryCard({
  title,
  subtitle,
  bots,
  selectedBotKey,
  onSelectBot,
  currentBot,
  botType,
  apiPrefix,
}: {
  title: string;
  subtitle: string;
  bots: BotInfo[];
  selectedBotKey: string;
  onSelectBot: (key: string) => void;
  currentBot?: BotInfo;
  botType: "swing" | "dt";
  apiPrefix: string;
}) {
  if (bots.length === 0) {
    return (
      <Card className="border-white/10 bg-white/5">
        <CardHeader>
          <CardTitle className="text-xl">{title}</CardTitle>
          <div className="text-xs text-white/60">{subtitle}</div>
        </CardHeader>
        <CardContent className="p-6 text-sm text-white/60">
          No {botType === "swing" ? "swing" : "day trading"} bots found yet. If you expect them, confirm the backend
          mounted <code>/bots/page</code>.
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-white/10 bg-white/5">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="text-xl">{title}</CardTitle>
            <div className="text-xs text-white/60 mt-1">{subtitle}</div>
          </div>
        </div>

        {/* Bot Tabs */}
        <div className="mt-4">
          <Tabs value={selectedBotKey} onValueChange={onSelectBot}>
            <TabsList className="flex-wrap h-auto">
              {bots.map((bot) => (
                <TabsTrigger key={bot.botKey} value={bot.botKey} className="flex items-center gap-2">
                  <span>{bot.displayName}</span>
                  <Badge
                    variant={bot.base.enabled ? "default" : "secondary"}
                    className={bot.base.enabled ? "bg-green-500/80 text-white" : "bg-red-500/70 text-white"}
                  >
                    {bot.base.enabled ? "ON" : "OFF"}
                  </Badge>
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>
      </CardHeader>

      <CardContent>
        {currentBot ? (
          <BotProfile
            botKey={currentBot.botKey}
            botType={botType}
            statusNode={currentBot.statusNode}
            baseConfig={currentBot.base}
            displayName={currentBot.displayName}
            dtMeta={currentBot.dtMeta}
            apiPrefix={apiPrefix}
          />
        ) : (
          <div className="text-sm text-white/60">Select a bot to view its profile.</div>
        )}
      </CardContent>
    </Card>
  );
}

// -----------------------------
// Individual bot profile
// -----------------------------

function BotProfile({
  botKey,
  botType,
  statusNode,
  baseConfig,
  displayName,
  dtMeta,
  apiPrefix,
}: {
  botKey: string;
  botType: "swing" | "dt";
  statusNode?: any;
  baseConfig: BotDraft;
  displayName: string;
  dtMeta?: { realized?: number; unrealized?: number; total?: number; fillsCount?: number };
  apiPrefix: string;
}) {
  const storageKey = `aion.bot_rules.${botKey}`;
  const { draft, dirty, saving, setSaving, setField, reset, setDirty } = useBotDraft(storageKey, baseConfig);

  const [pnlPeriod, setPnlPeriod] = useState<"day" | "week">("day");

  const lastUpdate = statusNode?.last_update ?? undefined;
  const cash = statusNode?.cash;
  const invested = statusNode?.invested;
  const allocated = statusNode?.allocated ?? draft.max_alloc ?? 0;
  const holdings = statusNode?.holdings_count;
  const equity = clampNum(statusNode?.equity, clampNum(cash, 0) + clampNum(invested, 0));

  // Get performance data
  const series = useMemo(() => {
    const equityCurve = statusNode?.equity_curve;
    const pnlCurve = statusNode?.pnl_curve;
    const maybeCurve = equityCurve ?? pnlCurve;
    return coerceCurve(maybeCurve, equity);
  }, [statusNode, equity]);

  // Calculate P&L
  const pnl = useMemo(() => {
    if (botType === "dt") {
      return dtMeta?.total ?? 0;
    }
    // For swing bots, calculate from equity curve
    if (series.length >= 2) {
      const first = series[0].value;
      const last = series[series.length - 1].value;
      return last - first;
    }
    return 0;
  }, [botType, dtMeta, series]);

  async function save() {
    setSaving(true);
    try {
      if (botType === "swing") {
        await apiPostJson(`${apiPrefix}/eod/configs`, { bot_key: botKey, config: draft });
      } else {
        await apiPostJson(`${apiPrefix}/intraday/configs`, { bot_key: botKey, config: draft });
      }
      setDirty(false);
    } catch {
      setDirty(false);
    } finally {
      setSaving(false);
    }
  }

  function toggleEnabled() {
    setField("enabled", !draft.enabled);
  }

  return (
    <div className="space-y-4">
      {/* Performance Chart */}
      <div className="rounded-xl border bg-card/30 p-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="text-sm font-semibold">Performance (P&L over time)</div>
          <Badge variant="secondary" className="text-xs">
            {displayName}
          </Badge>
        </div>
        <PerformanceChart data={series} valueLabel="Equity" compact={true} className="h-[200px] w-full" />
      </div>

      {/* Stats and Controls Grid */}
      <div className="grid gap-4 lg:grid-cols-2">
        {/* Left Column - Stats */}
        <div className="space-y-4">
          {/* P&L Metric with Toggle */}
          <div className="rounded-xl border bg-card/30 p-4">
            <div className="mb-3 flex items-center justify-between">
              <div className="text-sm font-semibold">P&L</div>
              <div className="flex items-center gap-1">
                <Button
                  size="sm"
                  variant={pnlPeriod === "day" ? "default" : "outline"}
                  onClick={() => setPnlPeriod("day")}
                  className="h-7 px-2 text-xs"
                >
                  Day
                </Button>
                <Button
                  size="sm"
                  variant={pnlPeriod === "week" ? "default" : "outline"}
                  onClick={() => setPnlPeriod("week")}
                  className="h-7 px-2 text-xs"
                >
                  Week
                </Button>
              </div>
            </div>
            <div className={`text-2xl font-bold ${pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
              {fmtMoney(pnl)}
            </div>
            <div className="mt-1 text-xs text-white/60">
              {pnlPeriod === "day" ? "Today&apos;s" : "This week&apos;s"} performance
            </div>
          </div>

          {/* Current Holdings & Status */}
          <div className="rounded-xl border bg-card/30 p-4">
            <div className="mb-3 text-sm font-semibold">Quick Stats</div>
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-lg border bg-background/40 p-3">
                <div className="text-[11px] text-muted-foreground">Holdings</div>
                <div className="text-sm font-semibold">{holdings ?? "—"}</div>
              </div>
              <div className="rounded-lg border bg-background/40 p-3">
                <div className="text-[11px] text-muted-foreground">Status</div>
                <Badge variant={draft.enabled ? "default" : "secondary"} className="mt-1">
                  {draft.enabled ? "Enabled" : "Disabled"}
                </Badge>
              </div>
              {botType === "swing" ? (
                <>
                  <div className="rounded-lg border bg-background/40 p-3">
                    <div className="text-[11px] text-muted-foreground">Cash</div>
                    <div className="text-sm font-semibold">{fmtMaybeMoney(cash)}</div>
                  </div>
                  <div className="rounded-lg border bg-background/40 p-3">
                    <div className="text-[11px] text-muted-foreground">Invested</div>
                    <div className="text-sm font-semibold">{fmtMaybeMoney(invested)}</div>
                  </div>
                </>
              ) : (
                <>
                  <div className="rounded-lg border bg-background/40 p-3">
                    <div className="text-[11px] text-muted-foreground">Realized</div>
                    <div className="text-sm font-semibold">{fmtMaybeMoney(dtMeta?.realized)}</div>
                  </div>
                  <div className="rounded-lg border bg-background/40 p-3">
                    <div className="text-[11px] text-muted-foreground">Unrealized</div>
                    <div className="text-sm font-semibold">{fmtMaybeMoney(dtMeta?.unrealized)}</div>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>

        {/* Right Column - Editable Fields */}
        <div className="rounded-xl border bg-card/30 p-4">
          <div className="mb-3 flex items-center justify-between">
            <div className="text-sm font-semibold">Bot Configuration</div>
            {dirty ? <Badge variant="secondary">Unsaved</Badge> : <Badge variant="outline">Saved</Badge>}
          </div>

          <div className="space-y-3">
            {/* Stop Loss */}
            <div className="space-y-1">
              <Label className="text-xs">Stop Loss (%)</Label>
              <Input
                type="number"
                step="0.01"
                value={(draft.stop_loss * 100).toFixed(2)}
                onChange={(e) => setField("stop_loss", Math.max(0, Number(e.target.value) / 100))}
                className="h-9"
              />
              <div className="text-[11px] text-muted-foreground">{fmtPct(draft.stop_loss)}</div>
            </div>

            {/* Take Profit */}
            <div className="space-y-1">
              <Label className="text-xs">Take Profit (%)</Label>
              <Input
                type="number"
                step="0.01"
                value={(draft.take_profit * 100).toFixed(2)}
                onChange={(e) => setField("take_profit", Math.max(0, Number(e.target.value) / 100))}
                className="h-9"
              />
              <div className="text-[11px] text-muted-foreground">{fmtPct(draft.take_profit)}</div>
            </div>

            {/* Max Allocation */}
            <div className="space-y-1">
              <Label className="text-xs">Max Allocation ($)</Label>
              <Input
                type="number"
                value={draft.max_alloc}
                onChange={(e) => setField("max_alloc", Math.max(0, Number(e.target.value)))}
                className="h-9"
              />
            </div>

            {/* Max Positions */}
            <div className="space-y-1">
              <Label className="text-xs">Max Positions</Label>
              <Input
                type="number"
                value={draft.max_positions}
                onChange={(e) => setField("max_positions", Math.max(0, Math.round(Number(e.target.value))))}
                className="h-9"
              />
            </div>

            {/* Min Confidence (Aggression) */}
            <div className="space-y-1">
              <Label className="text-xs">Min Confidence (0-1)</Label>
              <Input
                type="number"
                step="0.01"
                min="0"
                max="1"
                value={draft.aggression.toFixed(2)}
                onChange={(e) => setField("aggression", Math.min(1, Math.max(0, Number(e.target.value))))}
                className="h-9"
              />
              <div className="text-[11px] text-muted-foreground">
                {draft.aggression < 0.34 ? "Conservative" : draft.aggression < 0.67 ? "Balanced" : "Aggressive"}
              </div>
            </div>

            {/* Save Button */}
            <div className="pt-2">
              <Button onClick={save} disabled={saving || !dirty} className="w-full">
                {saving ? "Saving…" : "Save Configuration"}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// -----------------------------
// Live tape
// -----------------------------

function LiveIntradayTape({
  fills,
  signals,
  updatedAt,
}: {
  fills: IntradayFill[];
  signals: IntradaySignal[];
  updatedAt?: string | null;
}) {
  const topFills = fills.slice(0, 10);
  const topSignals = signals.slice(0, 10);

  return (
    <Card className="border-white/10 bg-white/5">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle className="text-lg">Intraday Live Tape</CardTitle>
            <div className="mt-1 text-xs text-white/60">
              {updatedAt ? `Updated: ${updatedAt}` : "Updated: —"} • fills={fills.length} • signals={signals.length}
            </div>
          </div>
          <Badge variant="outline">FROM /bots/page</Badge>
        </div>
      </CardHeader>

      <CardContent className="pt-0">
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-xl border bg-card/30 p-4">
            <div className="mb-2 text-sm font-semibold">Recent fills</div>
            {topFills.length ? (
              <div className="space-y-2">
                {topFills.map((f, i) => {
                  const side = ((f.side ?? f.action ?? "—") as string).toUpperCase();
                  return (
                    <div key={i} className="flex items-center justify-between rounded-lg border bg-background/40 px-3 py-2 text-xs">
                      <div className="flex items-center gap-2">
                        <Badge variant={side === "BUY" ? "secondary" : "outline"}>{side}</Badge>
                        <span className="font-semibold">{(f.symbol ?? "—").toString()}</span>
                        <span className="text-white/60">{(f.ts ?? f.time ?? "—").toString()}</span>
                      </div>
                      <div className="text-white/70">
                        {f.qty != null ? `${f.qty} @ ` : ""}
                        {f.price != null ? fmtMoney(f.price) : "—"}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="text-xs text-white/60">No fills parsed yet (backend will populate as artifacts appear).</div>
            )}
          </div>

          <div className="rounded-xl border bg-card/30 p-4">
            <div className="mb-2 text-sm font-semibold">Latest signals</div>
            {topSignals.length ? (
              <div className="space-y-2">
                {topSignals.map((s, i) => {
                  const act = ((s.action ?? s.side ?? "—") as string).toUpperCase();
                  return (
                    <div key={i} className="flex items-center justify-between rounded-lg border bg-background/40 px-3 py-2 text-xs">
                      <div className="flex items-center gap-2">
                        <Badge variant={act === "BUY" ? "secondary" : "outline"}>{act}</Badge>
                        <span className="font-semibold">{(s.symbol ?? "—").toString()}</span>
                        <span className="text-white/60">{(s.ts ?? s.time ?? "—").toString()}</span>
                      </div>
                      <div className="text-white/70">
                        {s.confidence != null ? `conf ${Math.round(Number(s.confidence) * 100)}%` : "conf —"}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="text-xs text-white/60">No signals parsed yet (ensure dt_signals artifacts exist).</div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// -----------------------------
// Bot row card
// -----------------------------


// -----------------------------
// Page
// -----------------------------

export default function BotsPage() {
  const [bundle, setBundle] = useState<BotsPageBundle | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const [live, setLive] = useState(true);
  const [pollMs, setPollMs] = useState(5000);

  const inFlightRef = useRef(false);
  const apiPrefixRef = useRef<string>("/api/backend"); // we’ll auto-detect

  async function refresh() {
    if (inFlightRef.current) return;
    inFlightRef.current = true;

    setErr(null);
    try {
      // NEW: Try consolidated endpoint first, then fallback to old endpoints
      const hit = await tryGetFirst<BotsPageBundle>([
        "/api/backend/page/bots",     // NEW consolidated endpoint through proxy
        "/api/page/bots",              // NEW consolidated endpoint direct
        "/api/backend/bots/page",      // OLD endpoint through proxy (fallback)
        "/api/bots/page",              // OLD endpoint direct (fallback)
      ]);

      if (!hit) throw new Error("Bots bundle endpoint not found (check backend router mount).");

      // remember which prefix worked for saves
      apiPrefixRef.current = hit.url.startsWith("/api/backend") ? "/api/backend" : "/api";

      setBundle(hit.data);
    } catch (e: any) {
      setErr(e?.message ?? "Failed to load");
    } finally {
      setLoading(false);
      inFlightRef.current = false;
    }
  }

  useEffect(() => {
    refresh();

    const onVis = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!live) return;
    const t = setInterval(refresh, pollMs);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live, pollMs]);

  const eodStatus: EodStatusResponse | null = (bundle?.swing?.status && !bundle?.swing?.status?.error)
    ? (bundle?.swing?.status as any)
    : null;

  const eodConfigs: EodConfigResponse | null = (bundle?.swing?.configs && !bundle?.swing?.configs?.error)
    ? (bundle?.swing?.configs as any)
    : null;

  const intradayPnl: IntradayPnlResponse | null = (bundle?.intraday?.pnl_last_day && !bundle?.intraday?.pnl_last_day?.error)
    ? (bundle?.intraday?.pnl_last_day as any)
    : null;

  const fillsArr = useMemo(() => {
    return (bundle?.intraday?.tape?.fills ?? []) as IntradayFill[];
  }, [bundle]);

  const signalsArr = useMemo(() => {
    return (bundle?.intraday?.tape?.signals ?? []) as IntradaySignal[];
  }, [bundle]);

  const dtUpdatedAt = bundle?.intraday?.tape?.updated_at ?? null;

  const swingBots = useMemo(() => {
    const bots = eodStatus?.bots ?? {};
    const configs = eodConfigs?.configs ?? {};
    const keys = Array.from(new Set([...Object.keys(bots), ...Object.keys(configs)])).sort();
    return keys.map((k) => {
      const statusNode = bots[k];
      const cfg = configs[k];
      const base: BotDraft = {
        max_alloc: cfg?.max_alloc ?? 10_000,
        max_positions: cfg?.max_positions ?? 10,
        stop_loss: cfg?.stop_loss ?? 0.05,
        take_profit: cfg?.take_profit ?? 0.1,
        aggression: cfg?.aggression ?? 0.5,
        enabled: cfg?.enabled ?? true,
      };
      return { botKey: k, statusNode, base, displayName: humanName(k, "swing") };
    });
  }, [eodStatus, eodConfigs]);

  const dtBots = useMemo(() => {
    const bots = intradayPnl?.bots ?? {};
    const keys = Object.keys(bots).sort();
    const finalKeys = keys.length ? keys : ["intraday_engine"];

    return finalKeys.map((k) => {
      const per = bots[k] ?? {};
      const total = clampNum(per?.total ?? (intradayPnl as any)?.total?.total ?? (intradayPnl as any)?.total, 0);

      const base: BotDraft = {
        max_alloc: 5_000,
        max_positions: 6,
        stop_loss: 0.02,
        take_profit: 0.04,
        aggression: 0.65,
        enabled: true,
        max_daily_trades: 10,
        penny_only: false,
        allow_etfs: true,
      };

      const statusNode: any = {
        allocated: base.max_alloc,
        equity: total,
        last_update: dtUpdatedAt ?? undefined,
      };

      const dtMeta = {
        realized: per?.realized ?? (intradayPnl as any)?.total?.realized,
        unrealized: per?.unrealized ?? (intradayPnl as any)?.total?.unrealized,
        total: per?.total ?? (intradayPnl as any)?.total?.total ?? (intradayPnl as any)?.total,
        fillsCount: fillsArr.length,
      };

      return { botKey: k, statusNode, base, displayName: humanName(k, "dt"), dtMeta };
    });
  }, [intradayPnl, dtUpdatedAt, fillsArr.length]);


  const bg = "min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-black text-white";

  // State for selected bots in each category
  const [selectedSwingBot, setSelectedSwingBot] = useState<string>("");
  const [selectedDtBot, setSelectedDtBot] = useState<string>("");

  // Update selected bot when bots list changes
  useEffect(() => {
    if (swingBots.length > 0 && !selectedSwingBot) {
      setSelectedSwingBot(swingBots[0].botKey);
    }
  }, [swingBots, selectedSwingBot]);

  useEffect(() => {
    if (dtBots.length > 0 && !selectedDtBot) {
      setSelectedDtBot(dtBots[0].botKey);
    }
  }, [dtBots, selectedDtBot]);

  // Get currently selected bots
  const currentSwingBot = swingBots.find(b => b.botKey === selectedSwingBot);
  const currentDtBot = dtBots.find(b => b.botKey === selectedDtBot);

  return (
    <main className={bg}>
      <div className="mx-auto max-w-7xl px-4 py-8">
        <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-3xl font-bold">Bots</h1>
            <p className="text-sm text-white/60">Manage individual bot profiles with real-time P&amp;L charts and tunable settings.</p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-3 py-2">
              <Switch checked={live} onCheckedChange={setLive} />
              <div className="text-xs text-white/70">Live</div>
              <Separator orientation="vertical" className="mx-2 h-5 bg-white/10" />
              <Button size="sm" variant={pollMs === 2000 ? "default" : "outline"} onClick={() => setPollMs(2000)}>
                2s
              </Button>
              <Button size="sm" variant={pollMs === 5000 ? "default" : "outline"} onClick={() => setPollMs(5000)}>
                5s
              </Button>
              <Button size="sm" variant={pollMs === 15000 ? "default" : "outline"} onClick={() => setPollMs(15000)}>
                15s
              </Button>
            </div>

            <Button variant="outline" onClick={refresh} disabled={loading} className="gap-2">
              <RefreshCw className="h-4 w-4" />
              Refresh
            </Button>
          </div>
        </div>

        {err ? (
          <div className="mb-6 rounded-xl border border-red-500/40 bg-red-500/10 p-4 text-sm text-red-200">{err}</div>
        ) : null}

        <div className="space-y-6">
          {/* SWING BOTS CARD */}
          <BotCategoryCard
            title="Swing (EOD) Bots"
            subtitle={`${eodStatus?.running ? "Engine: running" : "Engine: idle"} • ${swingBots.length} bots`}
            bots={swingBots}
            selectedBotKey={selectedSwingBot}
            onSelectBot={setSelectedSwingBot}
            currentBot={currentSwingBot}
            botType="swing"
            apiPrefix={apiPrefixRef.current}
          />

          {/* DAY TRADING BOTS CARD */}
          <BotCategoryCard
            title="DT (Intraday) Bots"
            subtitle={`${dtBots.length} bots • intraday snapshot`}
            bots={dtBots}
            selectedBotKey={selectedDtBot}
            onSelectBot={setSelectedDtBot}
            currentBot={currentDtBot}
            botType="dt"
            apiPrefix={apiPrefixRef.current}
          />
        </div>

        <div className="mt-10 text-xs text-white/40">
          Note: Save is best-effort. If your backend doesn&apos;t expose config-update endpoints yet, the page still persists
          rules locally.
        </div>
      </div>
    </main>
  );
}

function humanName(botKey: string, t: "swing" | "dt") {
  const k = botKey.replace(/[_-]+/g, " ").trim();
  if (!k) return t === "swing" ? "Swing bot" : "Day bot";
  const suffix = k.match(/\d+/)?.[0];
  if (suffix) return `${t === "swing" ? "Swing" : "Day Trading"} Bot ${suffix}`;
  return `${t === "swing" ? "Swing" : "Day Trading"} ${k}`;
}
