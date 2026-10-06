import type { Assumptions, MetricValue, Metrics } from "../api/types";
import { formatDate, formatMetric, tone } from "../lib/format";
import { InfoTip } from "./ui";

const toneClass = { gain: "text-ledger", loss: "text-loss", neutral: "text-paper" };

export function KpiCard({
  label,
  metric,
  note,
  info,
  signed = false,
  display,
}: {
  label: string;
  metric: MetricValue | undefined;
  note?: string;
  info?: string;
  signed?: boolean;
  display?: string;
}) {
  const value = metric?.value ?? null;
  const text = display ?? (metric ? formatMetric(value, metric.unit) : "n/a");
  const colour = signed ? toneClass[tone(value)] : "text-paper";
  return (
    <div className="panel flex min-h-28 flex-col justify-between p-4">
      <div className="flex items-center gap-2 text-sm text-muted">
        <span>{label}</span>
        {info && <InfoTip text={info} />}
      </div>
      <p className={`num mt-2 text-2xl font-semibold ${colour}`}>{text}</p>
      {value === null && metric?.unavailable_reason ? (
        <p className="mt-1 text-xs text-caution">{metric.unavailable_reason}</p>
      ) : (
        note && <p className="mt-1 text-xs text-faint">{note}</p>
      )}
    </div>
  );
}

/** Standard risk/return KPI grid shared by the dashboard and fund pages. */
export function RiskKpis({ metrics, assumptions, period }: { metrics: Metrics; assumptions: Assumptions; period: { start: string; end: string; note?: string } }) {
  const span = `${formatDate(period.start)} – ${formatDate(period.end)}`;
  const mdd = metrics.max_drawdown;
  const conf = metrics.historical_var?.confidence ?? 0.95;
  const rf = `${(assumptions.risk_free_rate_annual * 100).toFixed(2)}%`;
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {period.note && <p className="text-sm text-caution sm:col-span-2 xl:col-span-4">{period.note}</p>}
      <KpiCard label="Cumulative return" metric={metrics.cumulative_return} signed note={span} info={assumptions.return_basis_note} />
      <KpiCard label="CAGR" metric={metrics.cagr} signed note="Annualised, calendar time" info={assumptions.return_basis_note} />
      <KpiCard label="Volatility" metric={metrics.volatility} note={`Annualised from daily returns (×√${assumptions.periods_per_year})`} />
      <KpiCard label="Sharpe ratio" metric={metrics.sharpe_ratio} note={`Risk-free ${rf} a year`} info={assumptions.sharpe_method} />
      <KpiCard label="Sortino ratio" metric={metrics.sortino_ratio} note={`Target = risk-free ${rf}`} info={assumptions.sortino_method} />
      <KpiCard
        label="Beta"
        metric={metrics.beta}
        note={metrics.beta?.observations ? `${metrics.beta.observations} aligned days vs benchmark` : "vs benchmark"}
      />
      <KpiCard
        label="Maximum drawdown"
        metric={mdd}
        note={
          mdd?.peak_date
            ? `${formatDate(mdd.peak_date)} → ${formatDate(mdd.trough_date)}${mdd.recovery_date ? `, recovered ${formatDate(mdd.recovery_date)}` : ", not recovered"}`
            : undefined
        }
      />
      <KpiCard
        label={`1-day VaR / CVaR (${Math.round(conf * 100)}%)`}
        metric={metrics.historical_var}
        display={`${formatMetric(metrics.historical_var?.value, "fraction")} / ${formatMetric(metrics.historical_cvar?.value, "fraction")}`}
        note="Historical, loss as % of value"
        info={assumptions.var_method}
      />
    </div>
  );
}

export function AssumptionsPanel({ assumptions }: { assumptions: Assumptions }) {
  return (
    <details className="panel p-4 text-sm">
      <summary className="cursor-pointer text-muted">Assumptions and methods</summary>
      <dl className="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-[12rem_1fr]">
        <dt className="text-faint">Frequency</dt>
        <dd>{assumptions.frequency}, {assumptions.periods_per_year} periods a year</dd>
        <dt className="text-faint">Risk-free rate</dt>
        <dd>{(assumptions.risk_free_rate_annual * 100).toFixed(2)}% a year</dd>
        <dt className="text-faint">Return basis</dt>
        <dd>{assumptions.return_basis_note}</dd>
        {assumptions.rebalancing && (
          <>
            <dt className="text-faint">Rebalancing</dt>
            <dd>{assumptions.rebalancing}</dd>
          </>
        )}
        <dt className="text-faint">Sharpe</dt>
        <dd>{assumptions.sharpe_method}</dd>
        <dt className="text-faint">Sortino</dt>
        <dd>{assumptions.sortino_method}</dd>
        <dt className="text-faint">VaR / CVaR</dt>
        <dd>{assumptions.var_method}</dd>
      </dl>
    </details>
  );
}
