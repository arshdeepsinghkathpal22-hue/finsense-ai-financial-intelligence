import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Area, Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { api } from "../api/client";
import type { Portfolio } from "../api/types";
import { ChartFrame, axisProps, palette, tooltipStyle } from "../components/charts";
import { WeightEditor } from "../components/PortfolioEditor";
import { Button, Card, ErrorState, Field, KindLabel, PageHeader, Tabs } from "../components/ui";
import { inr, pct, ratio } from "../lib/format";
import { checkWeights, type WeightRow } from "../lib/weights";

type Tab = "sip" | "monte" | "shock" | "allocation" | "stress";

function num(text: string): number {
  return Number(text.replace(/,/g, ""));
}

function NumberInput({ label, value, onChange, hint, suffix }: { label: string; value: string; onChange: (v: string) => void; hint?: string; suffix?: string }) {
  return (
    <Field label={label} hint={hint}>
      {(id) => (
        <div className="relative">
          <input id={id} className="field-input pr-8" inputMode="decimal" value={value} onChange={(e) => onChange(e.target.value)} />
          {suffix && <span className="pointer-events-none absolute right-2.5 top-2 text-sm text-faint">{suffix}</span>}
        </div>
      )}
    </Field>
  );
}

function Assumptions({ data }: { data: Record<string, unknown> }) {
  return (
    <ul className="mt-2 list-disc pl-5 text-xs text-faint">
      {Object.entries(data).map(([k, v]) => (
        <li key={k}>{k.replace(/_/g, " ")}: {typeof v === "number" ? (Math.abs(v) < 1 ? pct(v) : v.toLocaleString("en-IN")) : String(v)}</li>
      ))}
    </ul>
  );
}

export default function WhatIfPage() {
  const [tab, setTab] = useState<Tab>("sip");
  return (
    <>
      <PageHeader title="What-if simulator" description="Explore hypothetical scenarios. Every result follows from the assumptions you enter; none is a prediction." />
      <Tabs label="Scenario type" value={tab} onChange={setTab} tabs={[
        { id: "sip", label: "SIP projection" },
        { id: "monte", label: "Monte Carlo SIP" },
        { id: "shock", label: "Market decline" },
        { id: "allocation", label: "Allocation change" },
        { id: "stress", label: "Volatility & correlation" },
      ]} />
      <div className="mt-5">
        {tab === "sip" && <SipScenario />}
        {tab === "monte" && <MonteCarloScenario />}
        {tab === "shock" && <HoldingsScenario kind="shock" />}
        {tab === "allocation" && <AllocationScenario />}
        {tab === "stress" && <HoldingsScenario kind="stress" />}
      </div>
    </>
  );
}

function ScenarioCard({ title, children, result }: { title: string; children: ReactNode; result?: ReactNode }) {
  return (
    <div className="grid gap-5 xl:grid-cols-[22rem_1fr]">
      <Card title={title}>{children}</Card>
      <div className="min-w-0 space-y-4">{result}</div>
    </div>
  );
}

function SipScenario() {
  const [monthly, setMonthly] = useState("10000");
  const [years, setYears] = useState("15");
  const [rate, setRate] = useState("12");
  const [lumpsum, setLumpsum] = useState("0");
  const [stepUp, setStepUp] = useState("0");
  const run = useMutation({
    mutationFn: () => api.post<{ final_value: number; total_invested: number; gain: number; yearly: { year: number; invested: number; value: number }[]; assumptions: Record<string, unknown> }>(
      "/simulate/sip", { monthly_amount: num(monthly), years: num(years), annual_return: num(rate) / 100, lumpsum: num(lumpsum), annual_step_up: num(stepUp) / 100 }),
  });
  const r = run.data;
  return (
    <ScenarioCard title="Deterministic SIP" result={
      <>
        {run.error && <ErrorState error={run.error} />}
        {r && (
          <>
            <div className="flex items-center gap-2"><KindLabel kind="hypothetical" /></div>
            <p className="text-lg">Invest <span className="num">{inr(r.total_invested)}</span> → about <span className="num font-semibold text-ledger">{inr(r.final_value)}</span> ({inr(r.gain)} growth) if returns are exactly {rate}% every year.</p>
            <ChartFrame title="Invested vs value by year">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={r.yearly}>
                  <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="year" {...axisProps} />
                  <YAxis {...axisProps} width={80} tickFormatter={(v: number) => inr(v)} />
                  <Tooltip {...tooltipStyle} formatter={(v, n) => [inr(Number(v)), String(n)]} labelFormatter={(l) => `Year ${l}`} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="invested" name="Invested" fill={palette.muted} isAnimationActive={false} />
                  <Bar dataKey="value" name="Value" fill={palette.ledger} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </ChartFrame>
            <Assumptions data={r.assumptions} />
          </>
        )}
      </>
    }>
      <div className="space-y-3">
        <NumberInput label="Monthly SIP" value={monthly} onChange={setMonthly} suffix="₹" />
        <NumberInput label="Years" value={years} onChange={setYears} />
        <NumberInput label="Assumed annual return" value={rate} onChange={setRate} suffix="%" />
        <NumberInput label="Initial lump sum" value={lumpsum} onChange={setLumpsum} suffix="₹" />
        <NumberInput label="Yearly SIP step-up" value={stepUp} onChange={setStepUp} suffix="%" />
        <Button busy={run.isPending} onClick={() => run.mutate()}>Project</Button>
      </div>
    </ScenarioCard>
  );
}

function MonteCarloScenario() {
  const [monthly, setMonthly] = useState("10000");
  const [years, setYears] = useState("15");
  const [rate, setRate] = useState("12");
  const [vol, setVol] = useState("16");
  const [paths, setPaths] = useState("2000");
  const run = useMutation({
    mutationFn: () => api.post<{
      total_invested: number; probability_of_loss: number; deterministic_reference: number; paths: number; seed: number;
      final_percentiles: Record<string, number>; yearly: { year: number; p5: number; p50: number; p95: number }[]; assumptions: Record<string, unknown>;
    }>("/simulate/monte-carlo", { monthly_amount: num(monthly), years: num(years), annual_return: num(rate) / 100, annual_volatility: num(vol) / 100, paths: num(paths), seed: 7 }),
  });
  const r = run.data;
  return (
    <ScenarioCard title="Monte Carlo SIP" result={
      <>
        {run.error && <ErrorState error={run.error} />}
        {r && (
          <>
            <div className="flex items-center gap-2"><KindLabel kind="hypothetical" /><span className="text-sm text-muted">{r.paths} simulated paths, seed {r.seed}</span></div>
            <p className="text-lg">
              Median outcome <span className="num font-semibold">{inr(r.final_percentiles.p50)}</span>; 90% of paths end between{" "}
              <span className="num">{inr(r.final_percentiles.p5)}</span> and <span className="num">{inr(r.final_percentiles.p95)}</span>.
              Chance of ending below the {inr(r.total_invested)} invested: <span className="num">{pct(r.probability_of_loss, 1)}</span>.
            </p>
            <ChartFrame title="Range of outcomes by year" caption="Shaded band: 5th–95th percentile; line: median.">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={r.yearly.map((y) => ({ ...y, band: [y.p5, y.p95] }))}>
                  <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="year" {...axisProps} />
                  <YAxis {...axisProps} width={80} tickFormatter={(v: number) => inr(v)} />
                  <Tooltip {...tooltipStyle} formatter={(v, n) => [Array.isArray(v) ? v.map((x) => inr(Number(x))).join(" – ") : inr(Number(v)), String(n)]} labelFormatter={(l) => `Year ${l}`} />
                  <Area dataKey="band" name="5th–95th percentile" stroke="none" fill={palette.info} fillOpacity={0.25} isAnimationActive={false} />
                  <Line dataKey="p50" name="Median" stroke={palette.ledger} dot={false} isAnimationActive={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </ChartFrame>
            <Assumptions data={r.assumptions} />
          </>
        )}
      </>
    }>
      <div className="space-y-3">
        <NumberInput label="Monthly SIP" value={monthly} onChange={setMonthly} suffix="₹" />
        <NumberInput label="Years" value={years} onChange={setYears} />
        <NumberInput label="Expected annual return" value={rate} onChange={setRate} suffix="%" />
        <NumberInput label="Annual volatility" value={vol} onChange={setVol} suffix="%" hint="Equity funds in the demo data: roughly 15–21%." />
        <NumberInput label="Paths" value={paths} onChange={setPaths} />
        <Button busy={run.isPending} onClick={() => run.mutate()}>Simulate</Button>
      </div>
    </ScenarioCard>
  );
}

function useHoldingsInput() {
  const portfolios = useQuery({ queryKey: ["portfolios"], queryFn: () => api.get<{ items: Portfolio[] }>("/portfolios") });
  const [source, setSource] = useState<string>("manual");
  const [rows, setRows] = useState<WeightRow[]>([{ fundId: null, weightPct: "60" }, { fundId: null, weightPct: "40" }]);
  const check = checkWeights(rows);
  const payload = source === "manual"
    ? { fund_ids: rows.map((r) => r.fundId), weights_pct: rows.map((r) => num(r.weightPct)) }
    : { portfolio_id: source };
  const ready = source !== "manual" || check.valid;
  const editor = (
    <div className="space-y-3">
      <Field label="Holdings">
        {(id) => (
          <select id={id} className="field-input" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="manual">Enter funds and weights</option>
            {portfolios.data?.items.map((p) => <option key={p.id} value={p.id}>Portfolio: {p.name}</option>)}
          </select>
        )}
      </Field>
      {source === "manual" && <WeightEditor rows={rows} onChange={setRows} />}
    </div>
  );
  return { payload, ready, editor };
}

interface StressResult {
  baseline: { expected_return: number; volatility: number; sharpe: number | null; parametric_var: number; horizon_days: number; confidence: number };
  scenario: StressResult["baseline"];
  labels: string[];
  estimation_window: { start: string; end: string };
}

interface ShockResult {
  market_move: number;
  estimated_portfolio_return: number;
  estimated_value_after: number;
  estimated_change: number;
  assets: { label: string; weight: number; beta: number; estimated_return: number }[];
  assumptions: Record<string, unknown>;
  estimation_window: { start: string; end: string };
}

function HoldingsScenario({ kind }: { kind: "shock" | "stress" }) {
  const holdings = useHoldingsInput();
  const [move, setMove] = useState("-20");
  const [value, setValue] = useState("100000");
  const [volMult, setVolMult] = useState("1.5");
  const [corr, setCorr] = useState("");
  const [shift, setShift] = useState("0");
  const [horizon, setHorizon] = useState("1");
  const run = useMutation<ShockResult | StressResult>({
    mutationFn: () =>
      kind === "shock"
        ? api.post<ShockResult>("/simulate/market-shock", { ...holdings.payload, market_move: num(move) / 100, portfolio_value: num(value) })
        : api.post<StressResult>("/simulate/stress", {
            ...holdings.payload, volatility_multiplier: num(volMult), correlation_override: corr.trim() === "" ? null : num(corr),
            return_shift: num(shift) / 100, horizon_days: num(horizon),
          }),
  });
  const shock = kind === "shock" ? (run.data as ShockResult | undefined) : undefined;
  const stress = kind === "stress" ? (run.data as StressResult | undefined) : undefined;

  return (
    <ScenarioCard title={kind === "shock" ? "Market decline" : "Change volatility and correlation"} result={
      <>
        {run.error && <ErrorState error={run.error} />}
        {shock && (
          <>
            <div className="flex items-center gap-2"><KindLabel kind="hypothetical" /><span className="text-sm text-muted">Betas estimated {shock.estimation_window.start} – {shock.estimation_window.end}</span></div>
            <p className="text-lg">
              A {pct(shock.market_move, 0)} market move would change the portfolio by about <span className={`num font-semibold ${shock.estimated_portfolio_return < 0 ? "text-loss" : "text-ledger"}`}>{pct(shock.estimated_portfolio_return, 1, true)}</span>{" "}
              ({inr(shock.estimated_change)}), to roughly {inr(shock.estimated_value_after)}.
            </p>
            <table className="table-base">
              <thead><tr><th>Fund</th><th className="text-right">Weight</th><th className="text-right">Beta</th><th className="text-right">Estimated move</th></tr></thead>
              <tbody>{shock.assets.map((a) => (
                <tr key={a.label}><td>{a.label}</td><td className="num text-right">{pct(a.weight, 1)}</td><td className="num text-right">{ratio(a.beta)}</td><td className="num text-right">{pct(a.estimated_return, 1, true)}</td></tr>
              ))}</tbody>
            </table>
            <Assumptions data={shock.assumptions} />
          </>
        )}
        {stress && <StressTable result={stress} />}
      </>
    }>
      <div className="space-y-3">
        {holdings.editor}
        {kind === "shock" ? (
          <>
            <NumberInput label="Market move" value={move} onChange={setMove} suffix="%" hint="Negative for a decline, e.g. -20." />
            <NumberInput label="Portfolio value (manual holdings)" value={value} onChange={setValue} suffix="₹" />
          </>
        ) : (
          <>
            <NumberInput label="Volatility multiplier" value={volMult} onChange={setVolMult} hint="1.5 = every fund 50% more volatile." />
            <NumberInput label="Uniform correlation (optional)" value={corr} onChange={setCorr} hint="-1 to 1; empty keeps historical correlations." />
            <NumberInput label="Shift expected returns by" value={shift} onChange={setShift} suffix="%" />
            <NumberInput label="VaR horizon (trading days)" value={horizon} onChange={setHorizon} />
          </>
        )}
        <Button busy={run.isPending} disabled={!holdings.ready} onClick={() => run.mutate()}>Run scenario</Button>
      </div>
    </ScenarioCard>
  );
}

function StressTable({ result }: { result: StressResult }) {
  const rows: [string, (s: StressResult["baseline"]) => string][] = [
    ["Expected return (annual)", (s) => pct(s.expected_return)],
    ["Volatility (annual)", (s) => pct(s.volatility)],
    ["Sharpe ratio", (s) => ratio(s.sharpe)],
    [`Parametric VaR (${Math.round(result.baseline.confidence * 100)}%, ${result.baseline.horizon_days}-day)`, (s) => pct(s.parametric_var)],
  ];
  return (
    <>
      <div className="flex items-center gap-2"><KindLabel kind="hypothetical" /><span className="text-sm text-muted">Baseline estimated {result.estimation_window.start} – {result.estimation_window.end}</span></div>
      <table className="table-base">
        <thead><tr><th>Measure</th><th className="text-right">Historical baseline</th><th className="text-right">Scenario</th></tr></thead>
        <tbody>{rows.map(([label, f]) => <tr key={label}><td>{label}</td><td className="num text-right">{f(result.baseline)}</td><td className="num text-right">{f(result.scenario)}</td></tr>)}</tbody>
      </table>
      <p className="text-xs text-faint">Parametric model with normally distributed returns; real losses in stressed markets are often larger.</p>
    </>
  );
}

function AllocationScenario() {
  const [rows, setRows] = useState<WeightRow[]>([{ fundId: null, weightPct: "50" }, { fundId: null, weightPct: "50" }]);
  const [proposed, setProposed] = useState<string[]>(["70", "30"]);
  const check = checkWeights(rows);
  const proposedTotal = proposed.slice(0, rows.length).reduce((s, v) => s + num(v || "0"), 0);
  const run = useMutation({
    mutationFn: () => api.post<{ labels: string[]; current: StressResult["baseline"] & { weights: number[] }; proposed: StressResult["baseline"] & { weights: number[] }; estimation_window: { start: string; end: string } }>(
      "/simulate/allocation-change", {
        fund_ids: rows.map((r) => r.fundId), current_weights_pct: rows.map((r) => num(r.weightPct)),
        proposed_weights_pct: rows.map((_, i) => num(proposed[i] ?? "0")),
      }),
  });
  const r = run.data;
  return (
    <ScenarioCard title="Compare two allocations" result={
      <>
        {run.error && <ErrorState error={run.error} />}
        {r && (
          <>
            <div className="flex items-center gap-2"><KindLabel kind="hypothetical" /><span className="text-sm text-muted">Estimated {r.estimation_window.start} – {r.estimation_window.end}</span></div>
            <table className="table-base">
              <thead><tr><th>Measure</th><th className="text-right">Current</th><th className="text-right">Proposed</th></tr></thead>
              <tbody>
                {r.labels.map((label, i) => <tr key={label}><td>Weight {label}</td><td className="num text-right">{pct(r.current.weights[i], 1)}</td><td className="num text-right">{pct(r.proposed.weights[i], 1)}</td></tr>)}
                <tr><td>Expected return (annual)</td><td className="num text-right">{pct(r.current.expected_return)}</td><td className="num text-right">{pct(r.proposed.expected_return)}</td></tr>
                <tr><td>Volatility (annual)</td><td className="num text-right">{pct(r.current.volatility)}</td><td className="num text-right">{pct(r.proposed.volatility)}</td></tr>
                <tr><td>Sharpe ratio</td><td className="num text-right">{ratio(r.current.sharpe)}</td><td className="num text-right">{ratio(r.proposed.sharpe)}</td></tr>
                <tr><td>1-day parametric VaR 95%</td><td className="num text-right">{pct(r.current.parametric_var)}</td><td className="num text-right">{pct(r.proposed.parametric_var)}</td></tr>
              </tbody>
            </table>
            <p className="text-xs text-faint">Uses historical mean returns and covariances; the past need not repeat.</p>
          </>
        )}
      </>
    }>
      <div className="space-y-3">
        <WeightEditor rows={rows} onChange={(next) => { setRows(next); setProposed((p) => next.map((_, i) => p[i] ?? "0")); }} />
        <fieldset className="space-y-2">
          <legend className="text-sm text-muted">Proposed weights (same order)</legend>
          {rows.map((_, i) => (
            <label key={i} className="flex items-center gap-2 text-sm">
              <span className="w-16 text-faint">Row {i + 1}</span>
              <input className="field-input" inputMode="decimal" value={proposed[i] ?? ""} onChange={(e) => setProposed((p) => p.map((v, j) => (j === i ? e.target.value : v)))} />
              <span className="text-faint">%</span>
            </label>
          ))}
          <p className={`num text-sm ${Math.abs(proposedTotal - 100) <= 0.01 ? "text-ledger" : "text-caution"}`}>Total {proposedTotal.toFixed(2)}%</p>
        </fieldset>
        <Button busy={run.isPending} disabled={!check.valid || Math.abs(proposedTotal - 100) > 0.01} onClick={() => run.mutate()}>Compare</Button>
      </div>
    </ScenarioCard>
  );
}
