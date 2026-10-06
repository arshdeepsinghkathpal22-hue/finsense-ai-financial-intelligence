import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { api } from "../api/client";
import type { FundRiskReport, HoldingsResponse, Period } from "../api/types";
import { ChartFrame, DrawdownChart, TimeSeriesChart, axisProps, palette, pctTick, tooltipStyle } from "../components/charts";
import { AssumptionsPanel } from "../components/metrics";
import { FundSelect, PeriodSelect, useFunds } from "../components/selectors";
import { Button, Card, ErrorState, Field, FreshnessBadge, KindLabel, Loading, PageHeader, SyntheticBadge } from "../components/ui";
import { formatDate, formatMetric, pct, ratio } from "../lib/format";

interface CorrelationResponse {
  labels: string[];
  names: string[];
  matrix: number[][];
  observations: number;
  period: { start: string; end: string };
}

export default function RiskPage() {
  const funds = useFunds();
  const [fundId, setFundId] = useState<number | null>(null);
  const [period, setPeriod] = useState<Period>("3y");
  const [confidence, setConfidence] = useState("0.95");
  const [rf, setRf] = useState("");
  const selected = fundId ?? funds.data?.items[0]?.id ?? null;
  const params = new URLSearchParams({ period, confidence });
  if (rf.trim() !== "" && Number.isFinite(Number(rf))) params.set("risk_free_rate", String(Number(rf) / 100));
  const risk = useQuery({
    queryKey: ["risk", selected, period, confidence, rf],
    queryFn: () => api.get<FundRiskReport>(`/funds/${selected}/risk?${params}`),
    enabled: selected !== null,
  });
  const holdings = useQuery({
    queryKey: ["holdings", selected, ""],
    queryFn: () => api.get<HoldingsResponse>(`/funds/${selected}/holdings`),
    enabled: selected !== null,
  });

  const r = risk.data;
  const m = r?.metrics;
  const varLine = m?.historical_var?.value;
  const histogram = (r?.series?.return_distribution ?? []).map((b) => ({ mid: (b.bin_start + b.bin_end) / 2, count: b.count }));

  return (
    <>
      <PageHeader title="Risk analytics" description="Distribution of daily returns, drawdowns, rolling risk-adjusted performance, VaR/CVaR and how funds move together." />
      <Card>
        <div className="grid gap-3 sm:grid-cols-4">
          <Field label="Fund">{(id) => <FundSelect id={id} value={selected} onChange={setFundId} />}</Field>
          <Field label="Period">{(id) => <PeriodSelect id={id} value={period} onChange={setPeriod} />}</Field>
          <Field label="VaR confidence">
            {(id) => (
              <select id={id} className="field-input" value={confidence} onChange={(e) => setConfidence(e.target.value)}>
                <option value="0.95">95%</option><option value="0.99">99%</option>
              </select>
            )}
          </Field>
          <Field label="Risk-free rate override (% a year)" hint="Leave empty to use the configured rate.">
            {(id) => <input id={id} className="field-input" inputMode="decimal" placeholder="e.g. 6.5" value={rf} onChange={(e) => setRf(e.target.value)} />}
          </Field>
        </div>
      </Card>

      {risk.isLoading && <Loading />}
      {risk.error && <ErrorState error={risk.error} />}
      {r && m && (
        <div className="mt-6 space-y-6">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="mr-2 text-xl">{r.fund.name}</h2>
            <SyntheticBadge show={r.fund.is_synthetic} /><FreshnessBadge freshness={r.freshness} /><KindLabel kind="historical" />
            <span className="text-sm text-muted">{formatDate(r.period.start)} – {formatDate(r.period.end)}, {m.observations?.value} daily returns</span>
          </div>
          <div className="grid gap-4 xl:grid-cols-2">
            <ChartFrame title="Distribution of daily returns" caption={`Dashed line: historical one-day VaR at ${Number(confidence) * 100}% (${pct(varLine)} loss).`}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={histogram} margin={{ left: 4, right: 12 }}>
                  <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="mid" type="number" domain={["dataMin", "dataMax"]} {...axisProps} tickFormatter={(v: number) => pct(v, 1)} />
                  <YAxis {...axisProps} width={40} />
                  <Tooltip {...tooltipStyle} labelFormatter={(v) => `Return ≈ ${pct(Number(v), 2)}`} formatter={(v) => [v, "Days"]} />
                  <Bar dataKey="count" fill={palette.info} isAnimationActive={false} />
                  {typeof varLine === "number" && <ReferenceLine x={-varLine} stroke={palette.loss} strokeDasharray="5 4" />}
                </BarChart>
              </ResponsiveContainer>
            </ChartFrame>
            <ChartFrame title="Drawdown curve" caption={`Maximum drawdown ${pct(m.max_drawdown?.value)} from ${formatDate(m.max_drawdown?.peak_date)} to ${formatDate(m.max_drawdown?.trough_date)}.`}>
              <DrawdownChart points={r.series?.drawdown ?? []} />
            </ChartFrame>
            <ChartFrame title={`Rolling Sharpe ratio (${r.series?.rolling_window_days} days)`} caption={r.assumptions.sharpe_method}>
              <TimeSeriesChart data={r.series?.rolling_sharpe ?? []} lines={[{ key: "value", name: "Sharpe" }]} yFormat={(v) => v.toFixed(1)} />
            </ChartFrame>
            <ChartFrame title="Rolling volatility (63 days)" caption="Annualised standard deviation of daily returns.">
              <TimeSeriesChart data={r.series?.rolling_volatility ?? []} lines={[{ key: "value", name: "Volatility", color: palette.caution }]} yFormat={pctTick} />
            </ChartFrame>
            {r.series?.rolling_beta && (
              <ChartFrame title={`Rolling beta vs ${r.benchmark?.name ?? "benchmark"}`} caption="Sensitivity of the fund to its benchmark over a rolling window.">
                <TimeSeriesChart data={r.series.rolling_beta} lines={[{ key: "value", name: "Beta", color: palette.info }]} yFormat={(v) => v.toFixed(2)} />
              </ChartFrame>
            )}
            <Card title="Value at Risk and Expected Shortfall" subtitle="One trading day, as a share of the holding's value.">
              <table className="table-base">
                <thead><tr><th>Method</th><th className="text-right">VaR</th><th className="text-right">CVaR</th></tr></thead>
                <tbody>
                  <tr><td>Historical (observed returns)</td><td className="num text-right">{pct(m.historical_var?.value)}</td><td className="num text-right">{pct(m.historical_cvar?.value)}</td></tr>
                  <tr><td>Parametric (normal model)</td><td className="num text-right">{pct(m.parametric_var?.value)}</td><td className="num text-right">{pct(m.parametric_cvar?.value)}</td></tr>
                </tbody>
              </table>
              <p className="mt-3 text-xs text-faint">{r.assumptions.var_method}</p>
              <h3 className="mt-5">Benchmark sensitivity</h3>
              <dl className="mt-2 grid grid-cols-2 gap-y-1 text-sm">
                {(["beta", "correlation", "alpha_annual", "tracking_error", "information_ratio"] as const).map((key) => (
                  <div key={key} className="contents">
                    <dt className="text-faint">{key.replace("_annual", " (annual)").replace("_", " ")}</dt>
                    <dd className="num text-right" title={m[key]?.unavailable_reason}>{m[key] ? formatMetric(m[key].value, m[key].unit) : "n/a"}</dd>
                  </div>
                ))}
              </dl>
            </Card>
          </div>
          {holdings.data?.concentration && (
            <Card title="Concentration of disclosed holdings" subtitle={`As of ${formatDate(holdings.data.as_of)}`}>
              <p className="text-sm">
                Top 10 holdings <span className="num font-semibold">{holdings.data.concentration.top10_weight_pct.toFixed(1)}%</span>;
                Herfindahl index <span className="num font-semibold">{ratio(holdings.data.concentration.herfindahl_index, 3)}</span>;
                effective number of holdings <span className="num font-semibold">{ratio(holdings.data.concentration.effective_number_of_holdings, 1)}</span>.
              </p>
              <p className="mt-1 text-xs text-faint">{holdings.data.concentration.note}</p>
            </Card>
          )}
          <AssumptionsPanel assumptions={r.assumptions} />
        </div>
      )}
      <CorrelationSection defaultIds={(funds.data?.items ?? []).slice(0, 4).map((f) => f.id)} />
    </>
  );
}

function heat(value: number): string {
  // Green for positive, red for negative correlation, transparency by strength.
  const alpha = Math.min(1, Math.abs(value)) * 0.75;
  return value >= 0 ? `rgba(95,191,143,${alpha})` : `rgba(224,122,107,${alpha})`;
}

function CorrelationSection({ defaultIds }: { defaultIds: number[] }) {
  const funds = useFunds();
  const [chosen, setChosen] = useState<number[] | null>(null);
  const ids = chosen ?? defaultIds;
  const mutation = useMutation({ mutationFn: (fundIds: number[]) => api.post<CorrelationResponse>("/analytics/correlation", { fund_ids: fundIds }) });
  const toggle = (id: number) => setChosen(ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]);

  return (
    <Card className="mt-6" title="Correlation matrix" subtitle="Correlation of daily returns on dates all selected funds have NAVs.">
      <div className="flex flex-wrap gap-2">
        {funds.data?.items.map((f) => (
          <label key={f.id} className="flex items-center gap-1.5 rounded border border-ink-700 px-2 py-1 text-sm">
            <input type="checkbox" checked={ids.includes(f.id)} onChange={() => toggle(f.id)} /> {f.scheme_code}
          </label>
        ))}
      </div>
      <Button className="mt-3" disabled={ids.length < 2 || ids.length > 12} busy={mutation.isPending} onClick={() => mutation.mutate(ids)}>
        Calculate correlations
      </Button>
      {mutation.error && <div className="mt-3"><ErrorState error={mutation.error} /></div>}
      {mutation.data && (
        <div className="mt-4 overflow-x-auto">
          <table className="num text-sm" aria-label="Correlation matrix">
            <thead>
              <tr><th />{mutation.data.labels.map((l) => <th key={l} className="whitespace-nowrap px-2 py-1 text-xs font-medium text-muted">{l}</th>)}</tr>
            </thead>
            <tbody>
              {mutation.data.matrix.map((row, i) => (
                <tr key={mutation.data.labels[i]}>
                  <th className="whitespace-nowrap pr-2 text-left text-xs font-medium text-muted" title={mutation.data.names[i]}>{mutation.data.labels[i]}</th>
                  {row.map((v, j) => (
                    <td key={j} className="h-10 w-16 border border-ink-900 text-center" style={{ background: heat(v) }}>{v.toFixed(2)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-faint">
            {mutation.data.observations} aligned daily returns, {formatDate(mutation.data.period.start)} – {formatDate(mutation.data.period.end)}.
          </p>
        </div>
      )}
    </Card>
  );
}
