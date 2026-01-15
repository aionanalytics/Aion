"use client";

import { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent, CardFooter } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";

const API_BASE = "/api/backend";

type BotConfig = {
  horizon: string;
  bot_key: string;
  max_positions: number;
  base_risk_pct: number;
  conf_threshold: number;
  stop_loss_pct: number;
  take_profit_pct: number;
  max_weight_per_name: number;
  initial_cash: number;
};

type ConfigMap = Record<string, BotConfig>;

export default function EodBotsPage() {
  const [configs, setConfigs] = useState<ConfigMap>({});
  const [loading, setLoading] = useState(true);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Load configs on mount
  useEffect(() => {
    const fetchConfigs = async () => {
      try {
        setLoading(true);
        setError(null);
        setSuccess(null);

        const res = await fetch(`${API_BASE}/api/eod/configs`);
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        const data = (await res.json()) as ConfigMap;
        setConfigs(data || {});
      } catch (e: any) {
        console.error("Failed to load EOD bot configs", e);
        setError(e?.message ?? "Failed to load configs");
      } finally {
        setLoading(false);
      }
    };

    fetchConfigs();
  }, []);

  const handleFieldChange = (botKey: string, field: keyof BotConfig, value: string) => {
    setConfigs((prev) => {
      const cfg = prev[botKey];
      if (!cfg) return prev;

      const next: BotConfig = { ...cfg } as BotConfig;

      // numeric fields
      if (
        field === "max_positions" ||
        field === "initial_cash"
      ) {
        (next as any)[field] = Number(value) || 0;
      } else if (
        field === "base_risk_pct" ||
        field === "conf_threshold" ||
        field === "stop_loss_pct" ||
        field === "take_profit_pct" ||
        field === "max_weight_per_name"
      ) {
        (next as any)[field] = Number(value);
      } else {
        (next as any)[field] = value;
      }

      return {
        ...prev,
        [botKey]: next,
      };
    });
  };

  const handleSave = async (botKey: string) => {
    const cfg = configs[botKey];
    if (!cfg) return;

    try {
      setSavingKey(botKey);
      setError(null);
      setSuccess(null);

      const res = await fetch(`${API_BASE}/api/eod/configs/${botKey}`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(cfg),
      });

      if (!res.ok) {
        const txt = await res.text();
        throw new Error(`Failed to save: HTTP ${res.status} — ${txt}`);
      }

      const saved = (await res.json()) as BotConfig;
      setConfigs((prev) => ({ ...prev, [botKey]: saved }));
      setSuccess(`Saved config for ${botKey}`);
    } catch (e: any) {
      console.error("Failed to save bot config", e);
      setError(e?.message ?? "Failed to save config");
    } finally {
      setSavingKey(null);
    }
  };

  const botEntries = Object.entries(configs);

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 space-y-6">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            EOD Bots — Settings
          </h1>
          <p className="text-sm text-muted-foreground">
            Tune risk, confidence thresholds, and position sizing for your
            nightly EOD swing bots.
          </p>
        </div>
        <div className="text-xs text-muted-foreground">
          {loading ? "Loading bot configs…" : `${botEntries.length} bots loaded`}
        </div>
      </div>

      {error && (
        <div className="text-xs text-red-400 bg-red-950/40 border border-red-900 rounded-md px-3 py-2">
          {error}
        </div>
      )}
      {success && (
        <div className="text-xs text-emerald-400 bg-emerald-950/40 border border-emerald-900 rounded-md px-3 py-2">
          {success}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-3">
        {botEntries.map(([key, cfg]) => (
          <Card key={key} className="border-slate-800 bg-slate-950/60">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">
                {cfg.bot_key.toUpperCase()}{" "}
                <span className="text-xs text-muted-foreground">
                  ({cfg.horizon.toUpperCase()} swing)
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-xs">
              <div className="space-y-1">
                <Label className="text-[11px]">Max Positions</Label>
                <Input
                  type="number"
                  min={1}
                  value={cfg.max_positions}
                  onChange={(e) =>
                    handleFieldChange(key, "max_positions", e.target.value)
                  }
                />
              </div>

              <div className="space-y-1">
                <Label className="text-[11px]">Confidence Threshold</Label>
                <Input
                  type="number"
                  step="0.01"
                  min={0}
                  max={1}
                  value={cfg.conf_threshold}
                  onChange={(e) =>
                    handleFieldChange(key, "conf_threshold", e.target.value)
                  }
                />
                <p className="text-[10px] text-muted-foreground">
                  Min AI confidence required to act (0–1).
                </p>
              </div>

              <div className="space-y-1">
                <Label className="text-[11px]">Stop Loss (%)</Label>
                <Input
                  type="number"
                  step="0.01"
                  value={cfg.stop_loss_pct}
                  onChange={(e) =>
                    handleFieldChange(key, "stop_loss_pct", e.target.value)
                  }
                />
                <p className="text-[10px] text-muted-foreground">
                  -0.05 = -5% below entry.
                </p>
              </div>

              <div className="space-y-1">
                <Label className="text-[11px]">Take Profit (%)</Label>
                <Input
                  type="number"
                  step="0.01"
                  value={cfg.take_profit_pct}
                  onChange={(e) =>
                    handleFieldChange(key, "take_profit_pct", e.target.value)
                  }
                />
                <p className="text-[10px] text-muted-foreground">
                  0.10 = +10% above entry.
                </p>
              </div>

              <div className="space-y-1">
                <Label className="text-[11px]">Max Weight per Name</Label>
                <Input
                  type="number"
                  step="0.01"
                  min={0}
                  max={1}
                  value={cfg.max_weight_per_name}
                  onChange={(e) =>
                    handleFieldChange(key, "max_weight_per_name", e.target.value)
                  }
                />
                <p className="text-[10px] text-muted-foreground">
                  Fraction of bot equity allowed in a single symbol.
                </p>
              </div>

              <div className="space-y-1">
                <Label className="text-[11px]">Initial Cash (sim)</Label>
                <Input
                  type="number"
                  min={0}
                  value={cfg.initial_cash}
                  onChange={(e) =>
                    handleFieldChange(key, "initial_cash", e.target.value)
                  }
                />
                <p className="text-[10px] text-muted-foreground">
                  Used when seeding a brand new bot state.
                </p>
              </div>
            </CardContent>
            <CardFooter className="pt-2 flex justify-end">
              <Button
                size="sm"
                disabled={savingKey === key}
                onClick={() => handleSave(key)}
              >
                {savingKey === key ? "Saving…" : "Save"}
              </Button>
            </CardFooter>
          </Card>
        ))}
      </div>

      {!loading && botEntries.length === 0 && (
        <p className="text-xs text-muted-foreground">
          No EOD bot configs found. Make sure your backend eod_bots_router and
          config_store are wired.
        </p>
      )}
    </div>
  );
}
