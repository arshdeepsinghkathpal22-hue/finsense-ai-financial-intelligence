import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { api, errorMessage } from "../api/client";
import type { Benchmark, Portfolio } from "../api/types";
import { checkWeights, type WeightRow } from "../lib/weights";
import { FundSelect } from "./selectors";
import { Button, Field, Notice } from "./ui";

export interface PortfolioPayload {
  name: string;
  description: string;
  initial_value: number;
  start_date: string | null;
  benchmark_id: number | null;
  assets: { fund_id: number; weight_pct: number }[];
}

export function WeightEditor({ rows, onChange }: { rows: WeightRow[]; onChange: (rows: WeightRow[]) => void }) {
  const check = checkWeights(rows);
  const update = (i: number, patch: Partial<WeightRow>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <fieldset className="space-y-2">
      <legend className="mb-1 text-sm text-muted">Funds and weights</legend>
      {rows.map((row, i) => (
        <div key={i} className="grid grid-cols-[1fr_7rem_auto] items-start gap-2">
          <div>
            <FundSelect value={row.fundId} onChange={(fundId) => update(i, { fundId })} />
            {check.rowErrors[i] && <p className="mt-1 text-xs text-loss">{check.rowErrors[i]}</p>}
          </div>
          <label className="relative">
            <span className="sr-only">Weight for row {i + 1} (%)</span>
            <input className="field-input pr-7 text-right" inputMode="decimal" value={row.weightPct}
              onChange={(e) => update(i, { weightPct: e.target.value })} />
            <span className="pointer-events-none absolute right-2.5 top-2 text-sm text-faint">%</span>
          </label>
          <Button variant="ghost" aria-label={`Remove row ${i + 1}`} onClick={() => onChange(rows.filter((_, j) => j !== i))} disabled={rows.length === 1}>
            Remove
          </Button>
        </div>
      ))}
      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
        <Button variant="secondary" onClick={() => onChange([...rows, { fundId: null, weightPct: "" }])} disabled={rows.length >= 20}>
          Add fund
        </Button>
        <p role="status" className={`num text-sm ${Math.abs(check.total - 100) <= 0.01 ? "text-ledger" : "text-caution"}`}>
          Total {check.total.toFixed(2)}% {Math.abs(check.total - 100) <= 0.01 ? "" : "(must be 100%)"}
        </p>
      </div>
    </fieldset>
  );
}

export function PortfolioEditor({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial?: Portfolio;
  submitLabel: string;
  onSubmit: (payload: PortfolioPayload) => Promise<unknown>;
  onCancel?: () => void;
}) {
  const benchmarks = useQuery({ queryKey: ["benchmarks"], queryFn: () => api.get<{ items: Benchmark[] }>("/benchmarks") });
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [value, setValue] = useState(String(initial?.initial_value ?? 100000));
  const [startDate, setStartDate] = useState(initial?.start_date ?? "");
  const [benchmarkId, setBenchmarkId] = useState<number | null>(initial?.benchmark_id ?? null);
  const [rows, setRows] = useState<WeightRow[]>(
    initial?.assets.map((a) => ({ fundId: a.fund_id, weightPct: String(Number(a.weight_pct.toFixed(4))) })) ?? [
      { fundId: null, weightPct: "" },
      { fundId: null, weightPct: "" },
    ],
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const check = checkWeights(rows);
  const amount = Number(value);
  const valid = check.valid && name.trim().length > 0 && Number.isFinite(amount) && amount > 0;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit({
        name: name.trim(),
        description,
        initial_value: amount,
        start_date: startDate || null,
        benchmark_id: benchmarkId,
        assets: rows.map((r) => ({ fund_id: r.fundId as number, weight_pct: Number(r.weightPct) })),
      });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Portfolio name" error={name.trim() ? null : "Give the portfolio a name."}>
          {(id) => <input id={id} className="field-input" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />}
        </Field>
        <Field label="Amount invested (₹)">
          {(id) => <input id={id} className="field-input" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} />}
        </Field>
        <Field label="Start date (optional)" hint="Buy-and-hold from this date. Empty: use the analysis period.">
          {(id) => <input id={id} type="date" className="field-input" value={startDate} onChange={(e) => setStartDate(e.target.value)} />}
        </Field>
        <Field label="Benchmark (optional)" hint="Empty: the benchmark of the largest holding.">
          {(id) => (
            <select id={id} className="field-input" value={benchmarkId ?? ""} onChange={(e) => setBenchmarkId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">Automatic</option>
              {benchmarks.data?.items.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          )}
        </Field>
      </div>
      <Field label="Description (optional)">
        {(id) => <input id={id} className="field-input" maxLength={500} value={description} onChange={(e) => setDescription(e.target.value)} />}
      </Field>
      <WeightEditor rows={rows} onChange={setRows} />
      {error && <Notice tone="loss">{error}</Notice>}
      <div className="flex gap-2">
        <Button type="submit" disabled={!valid} busy={busy}>{submitLabel}</Button>
        {onCancel && <Button variant="secondary" onClick={onCancel}>Cancel</Button>}
      </div>
    </form>
  );
}
