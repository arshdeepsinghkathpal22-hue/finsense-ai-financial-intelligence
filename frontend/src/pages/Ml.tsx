import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "../api/client";
import { ChartFrame, axisProps, palette, pctTick1, tooltipStyle } from "../components/charts";
import { FundSelect, useFunds } from "../components/selectors";
import { Button, Card, Disclaimer, ErrorState, Field, KindLabel, Notice, PageHeader, SyntheticBadge } from "../components/ui";
import { formatDate, nav, pct, ratio } from "../lib/format";

interface Capabilities {
  models: { type: string; label: string; available: boolean; reason?: string }[];
  horizons_trading_days: number[];
}

interface EvalMetrics {
  mae: number;
  rmse: number;
  directional_accuracy: number | null;
  observations: number;
  interval_coverage_80?: number;
}

interface ForecastResponse {
  fund: { id: number; name: string; scheme_code: string; is_synthetic: boolean };
  model: { type: string; horizon_trading_days: number; hyperparameters: Record<string, number>; trained_at: string | null; features: string[] };
  evaluation: {
    test: EvalMetrics;
    baseline_historical_mean: EvalMetrics;
    baseline_zero_return: EvalMetrics;
    mae_skill_vs_historical_mean: number | null;
    beats_baseline: boolean;
  };
  splits: { train: string[]; validation: string[]; test: string[]; purge_gap_days: number };
  forecast: {
    as_of: string;
    last_nav: number;
    horizon_trading_days: number;
    predicted_return: number;
    predicted_nav: number;
    interval_80: { lower_return: number; upper_return: number; lower_nav: number; upper_nav: number };
  };
  backtest: { date: string; actual: number; predicted: number; lower: number; upper: number }[];
  history: { date: string; nav: number }[];
  explanation: {
    available: boolean;
    reason?: string;
    method?: string;
    local?: { feature: string; label: string | null; value: number; shap: number }[];
    global_importance?: { feature: string; label: string | null; mean_abs_shap: number }[];
    note?: string;
  } | null;
  warning?: string;
  disclaimer: string;
}

interface AnomalyResponse {
  method: string;
  observations: number;
  period: { start: string; end: string };
  anomalies: {
    date: string;
    nav: number;
    return: number;
    benchmark_return: number | null;
    score: number;
    interpretation: string;
    drivers: { label: string; value: number; robust_z: number }[];
  }[];
  scores: { date: string; score: number }[];
  note: string;
}

export default function MlPage() {
  const funds = useFunds();
  const [fundId, setFundId] = useState<number | null>(null);
  const selected = fundId ?? funds.data?.items[0]?.id ?? null;
  const capabilities = useQuery({ queryKey: ["ml-capabilities"], queryFn: () => api.get<Capabilities>("/ml/capabilities") });

  return (
    <>
      <PageHeader title="Forecasts and anomaly detection" description="Statistical forecasts tested against a simple baseline on held-out data, and a scan for unusual days in a fund's history." />
      <Card>
        <Field label="Fund">{(id) => <FundSelect id={id} value={selected} onChange={setFundId} />}</Field>
      </Card>
      {selected !== null && (
        <>
          <ForecastSection fundId={selected} capabilities={capabilities.data} />
          <AnomalySection fundId={selected} />
        </>
      )}
    </>
  );
}

function MetricsRow({ label, m }: { label: string; m: EvalMetrics }) {
  return (
    <tr>
      <td>{label}</td>
      <td className="num text-right">{m.mae.toFixed(4)}</td>
      <td className="num text-right">{m.rmse.toFixed(4)}</td>
      <td className="num text-right">{pct(m.directional_accuracy, 1)}</td>
      <td className="num text-right">{m.interval_coverage_80 === undefined ? "—" : pct(m.interval_coverage_80, 1)}</td>
    </tr>
  );
}

function ForecastSection({ fundId, capabilities }: { fundId: number; capabilities?: Capabilities }) {
  const [model, setModel] = useState("random_forest");
  const [horizon, setHorizon] = useState(21);
  const forecast = useMutation({
    mutationFn: () => api.post<ForecastResponse>("/ml/forecast", { fund_id: fundId, horizon_days: horizon, model_type: model, explain: true }),
  });
  const r = forecast.data?.fund.id === fundId ? forecast.data : undefined;
  // The forecast sits one horizon after the last NAV; filled dot = point estimate, rings = 80% interval.
  const historyRows = r ? [...r.history.map((h) => ({ date: h.date, nav: h.nav })), { date: `+${r.forecast.horizon_trading_days}d` }] : [];

  return (
    <Card className="mt-6" title="Return forecast" subtitle="Target: log return of the NAV over the next 5, 21 or 63 trading days.">
      <div className="grid gap-3 sm:grid-cols-[1fr_12rem_auto] sm:items-end">
        <Field label="Model">
          {(id) => (
            <select id={id} className="field-input" value={model} onChange={(e) => setModel(e.target.value)}>
              {capabilities?.models.map((m) => (
                <option key={m.type} value={m.type} disabled={!m.available}>{m.label}{m.available ? "" : " (not available)"}</option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Horizon">
          {(id) => (
            <select id={id} className="field-input" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>
              <option value={5}>1 week (5 days)</option><option value={21}>1 month (21 days)</option><option value={63}>1 quarter (63 days)</option>
            </select>
          )}
        </Field>
        <Button busy={forecast.isPending} onClick={() => forecast.mutate()}>Train and forecast</Button>
      </div>
      {capabilities?.models.filter((m) => !m.available).map((m) => <p key={m.type} className="mt-2 text-xs text-faint">{m.label}: {m.reason}</p>)}
      {forecast.error && <div className="mt-4"><ErrorState error={forecast.error} /></div>}

      {r && (
        <div className="mt-6 space-y-5">
          {r.warning && <Notice tone="caution">{r.warning}</Notice>}
          <div className="flex flex-wrap items-center gap-2"><KindLabel kind="estimate" /><SyntheticBadge show={r.fund.is_synthetic} /></div>
          <p className="text-lg">
            From {nav(r.forecast.last_nav)} on {formatDate(r.forecast.as_of)}, the model's point estimate over {r.forecast.horizon_trading_days} trading days is{" "}
            <span className="num font-semibold">{pct(r.forecast.predicted_return, 2, true)}</span>, with an 80% interval of{" "}
            <span className="num">{pct(r.forecast.interval_80.lower_return, 2, true)}</span> to <span className="num">{pct(r.forecast.interval_80.upper_return, 2, true)}</span>.
          </p>
          <div className="grid gap-4 xl:grid-cols-2">
            <ChartFrame title="Recent NAV and forecast" badge={<KindLabel kind="estimate" />} caption="Last year of NAVs (historical). At the right edge: forecast NAV (filled dot) and its 80% interval (rings), a model estimate.">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={historyRows}>
                  <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="date" {...axisProps} minTickGap={40} tickFormatter={(v: string) => (v.startsWith("+") ? v : v.slice(0, 7))} />
                  <YAxis {...axisProps} width={64} domain={["auto", "auto"]} />
                  <Tooltip {...tooltipStyle} />
                  <Line dataKey="nav" name="NAV" stroke={palette.ledger} dot={false} isAnimationActive={false} />
                  <ReferenceDot x={`+${r.forecast.horizon_trading_days}d`} y={r.forecast.interval_80.upper_nav} r={3} fill="none" stroke={palette.caution} />
                  <ReferenceDot x={`+${r.forecast.horizon_trading_days}d`} y={r.forecast.predicted_nav} r={5} fill={palette.caution} stroke="none" />
                  <ReferenceDot x={`+${r.forecast.horizon_trading_days}d`} y={r.forecast.interval_80.lower_nav} r={3} fill="none" stroke={palette.caution} />
                </ComposedChart>
              </ResponsiveContainer>
            </ChartFrame>
            <ChartFrame title="Backtest on the held-out test period" badge={<KindLabel kind="historical" />} caption={`Test period ${formatDate(r.splits.test[0])} – ${formatDate(r.splits.test[1])}; never used for training or tuning.`}>
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={r.backtest.map((b) => ({ ...b, band: [b.lower, b.upper] }))}>
                  <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="date" {...axisProps} minTickGap={40} tickFormatter={(v: string) => v.slice(0, 7)} />
                  <YAxis {...axisProps} width={56} tickFormatter={pctTick1} />
                  <Tooltip {...tooltipStyle} formatter={(v, n) => [Array.isArray(v) ? v.map((x) => pct(Number(x))).join(" to ") : pct(Number(v)), String(n)]} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Area dataKey="band" name="80% interval" stroke="none" fill={palette.caution} fillOpacity={0.18} isAnimationActive={false} />
                  <Line dataKey="actual" name="Actual" stroke={palette.info} dot={false} isAnimationActive={false} />
                  <Line dataKey="predicted" name="Predicted" stroke={palette.caution} dot={false} isAnimationActive={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </ChartFrame>
          </div>
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead><tr><th>Held-out test period</th><th className="text-right">MAE</th><th className="text-right">RMSE</th><th className="text-right">Direction right</th><th className="text-right">80% interval coverage</th></tr></thead>
              <tbody>
                <MetricsRow label={`Model: ${r.model.type}`} m={r.evaluation.test} />
                <MetricsRow label="Baseline: historical mean" m={r.evaluation.baseline_historical_mean} />
                <MetricsRow label="Baseline: zero return" m={r.evaluation.baseline_zero_return} />
              </tbody>
            </table>
            <p className="mt-2 text-xs text-faint">
              Errors in log-return units over {r.evaluation.test.observations} test forecasts. Skill vs historical mean: {pct(r.evaluation.mae_skill_vs_historical_mean, 1)} (positive is better).
              Train {formatDate(r.splits.train[0])}–{formatDate(r.splits.train[1])}, validation {formatDate(r.splits.validation[0])}–{formatDate(r.splits.validation[1])}, with a {r.splits.purge_gap_days}-day gap between periods so overlapping targets cannot leak.
              Hyper-parameters: {Object.entries(r.model.hyperparameters).map(([k, v]) => `${k}=${v}`).join(", ") || "none"}.
            </p>
          </div>
          {r.explanation?.available ? (
            <div className="grid gap-4 xl:grid-cols-2">
              <ChartFrame title="Why this forecast (SHAP)" caption={`${r.explanation.method}. ${r.explanation.note}`} height={300}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={r.explanation.local} layout="vertical" margin={{ left: 30, right: 12 }}>
                    <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" horizontal={false} />
                    <XAxis type="number" {...axisProps} tickFormatter={(v: number) => v.toFixed(3)} />
                    <YAxis type="category" dataKey="label" {...axisProps} width={170} />
                    <Tooltip {...tooltipStyle} formatter={(v) => [Number(v).toFixed(4), "SHAP (log return)"]} />
                    <Bar dataKey="shap" isAnimationActive={false} fill={palette.info} />
                  </BarChart>
                </ResponsiveContainer>
              </ChartFrame>
              <ChartFrame title="Overall feature importance" caption="Mean absolute SHAP value over recent observations." height={300}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={r.explanation.global_importance} layout="vertical" margin={{ left: 30, right: 12 }}>
                    <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" horizontal={false} />
                    <XAxis type="number" {...axisProps} tickFormatter={(v: number) => v.toFixed(3)} />
                    <YAxis type="category" dataKey="label" {...axisProps} width={170} />
                    <Tooltip {...tooltipStyle} formatter={(v) => [Number(v).toFixed(4), "mean |SHAP|"]} />
                    <Bar dataKey="mean_abs_shap" fill={palette.ledger} isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              </ChartFrame>
            </div>
          ) : (
            r.explanation && <p className="text-sm text-muted">{r.explanation.reason}</p>
          )}
          <Disclaimer>{r.disclaimer}</Disclaimer>
        </div>
      )}
    </Card>
  );
}

function AnomalySection({ fundId }: { fundId: number }) {
  const [contamination, setContamination] = useState("1");
  const detect = useMutation({
    mutationFn: () => api.post<AnomalyResponse>("/ml/anomalies", { fund_id: fundId, contamination: Number(contamination) / 100 }),
  });
  const r = detect.data;
  return (
    <Card className="mt-6" title="Anomaly detection" subtitle="Isolation Forest on daily return, return relative to recent volatility, volatility regime and return versus benchmark.">
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-56">
          <Field label="Share of days to flag (%)" hint="Between 0.1% and 10%.">
            {(id) => <input id={id} className="field-input" inputMode="decimal" value={contamination} onChange={(e) => setContamination(e.target.value)} />}
          </Field>
        </div>
        <Button busy={detect.isPending} onClick={() => detect.mutate()}>Scan history</Button>
      </div>
      {detect.error && <div className="mt-4"><ErrorState error={detect.error} /></div>}
      {r && (
        <div className="mt-5 space-y-4">
          <div className="flex items-center gap-2"><KindLabel kind="historical" /><span className="text-sm text-muted">{r.anomalies.length} unusual days among {r.observations}, {formatDate(r.period.start)} – {formatDate(r.period.end)}</span></div>
          <ChartFrame title="Anomaly score by day" caption="Higher scores are more unusual for this fund.">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={r.scores}>
                <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" {...axisProps} minTickGap={40} tickFormatter={(v: string) => v.slice(0, 7)} />
                <YAxis {...axisProps} width={48} domain={["auto", "auto"]} />
                <Tooltip {...tooltipStyle} labelFormatter={(l) => formatDate(String(l))} />
                <Line dataKey="score" stroke={palette.muted} dot={false} strokeWidth={1} isAnimationActive={false} />
                {r.anomalies.map((a) => <ReferenceDot key={a.date} x={a.date} y={a.score} r={4} fill={palette.loss} stroke="none" />)}
              </ComposedChart>
            </ResponsiveContainer>
          </ChartFrame>
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead><tr><th>Date</th><th className="text-right">Fund return</th><th className="text-right">Benchmark</th><th>What stands out</th><th className="text-right">Score</th></tr></thead>
              <tbody>
                {r.anomalies.map((a) => (
                  <tr key={a.date}>
                    <td>{formatDate(a.date)}</td>
                    <td className={`num text-right ${a.return < 0 ? "text-loss" : "text-ledger"}`}>{pct(a.return, 2, true)}</td>
                    <td className="num text-right">{pct(a.benchmark_return, 2, true)}</td>
                    <td>
                      {a.interpretation}
                      <div className="text-xs text-faint">{a.drivers.map((d) => `${d.label}: ${ratio(d.robust_z, 1)} robust s.d.`).join("; ")}</div>
                    </td>
                    <td className="num text-right">{a.score.toFixed(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-faint">{r.note}</p>
        </div>
      )}
    </Card>
  );
}
