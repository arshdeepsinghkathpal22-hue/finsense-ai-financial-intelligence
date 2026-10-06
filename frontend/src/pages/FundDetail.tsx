import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { api } from "../api/client";
import type { FundDetail, FundRiskReport, HoldingsResponse, Period } from "../api/types";
import { ChartFrame, TimeSeriesChart, axisProps, mergeSeries, palette, tooltipStyle } from "../components/charts";
import { AssumptionsPanel, RiskKpis } from "../components/metrics";
import { PeriodSelect } from "../components/selectors";
import { Card, ErrorState, FreshnessBadge, KindLabel, Loading, Notice, PageHeader, SyntheticBadge } from "../components/ui";
import { crore, formatDate, nav, pct, ratio, tone } from "../lib/format";

const PERIOD_LABEL: Record<string, string> = {
  "1m": "1 month", "3m": "3 months", "6m": "6 months", "1y": "1 year", "3y": "3 years", "5y": "5 years", since_start: "Since start of data",
};

export default function FundDetailPage() {
  const fundId = Number(useParams().fundId);
  const [period, setPeriod] = useState<Period>("3y");
  const [asOf, setAsOf] = useState<string>("");
  const detail = useQuery({ queryKey: ["fund", fundId], queryFn: () => api.get<FundDetail>(`/funds/${fundId}`) });
  const risk = useQuery({ queryKey: ["fund-risk", fundId, period], queryFn: () => api.get<FundRiskReport>(`/funds/${fundId}/risk?period=${period}`) });
  const holdings = useQuery({
    queryKey: ["holdings", fundId, asOf],
    queryFn: () => api.get<HoldingsResponse>(`/funds/${fundId}/holdings${asOf ? `?as_of=${asOf}` : ""}`),
  });
  const aum = useQuery({ queryKey: ["aum", fundId], queryFn: () => api.get<{ points: { date: string; value: number }[] }>(`/funds/${fundId}/aum`) });
  const sip = useQuery({
    queryKey: ["sip", fundId],
    queryFn: () => api.get<{ points: { month: string; inflow: number; accounts: number | null }[]; note: string | null }>(`/funds/${fundId}/sip`),
  });

  if (detail.isLoading) return <Loading />;
  if (detail.error) return <ErrorState error={detail.error} />;
  const fund = detail.data!;

  return (
    <>
      <PageHeader
        title={fund.name}
        description={<span>{fund.scheme_code} · {fund.category} · {fund.amc ?? "AMC not stated"}</span>}
        actions={<Link to={`/funds/compare?ids=${fund.id}`} className="rounded-md border border-ink-600 px-3.5 py-2 text-sm hover:border-ledger/70">Compare with others</Link>}
      />
      <div className="mb-5 flex flex-wrap gap-2">
        <SyntheticBadge show={fund.is_synthetic} />
        <FreshnessBadge freshness={fund.freshness} />
        {fund.option && <span className="text-sm text-muted">Option: {fund.option}</span>}
      </div>
      {fund.data_notes.length > 0 && (
        <div className="mb-5 space-y-2">{fund.data_notes.map((n) => <Notice key={n} tone="caution">{n}</Notice>)}</div>
      )}

      <div className="grid gap-4 lg:grid-cols-[1fr_1.4fr]">
        <Card title="Scheme facts">
          <dl className="grid grid-cols-[10rem_1fr] gap-y-2 text-sm">
            <dt className="text-faint">Latest NAV</dt><dd className="num">{nav(fund.latest_nav)} on {formatDate(fund.latest_nav_date)}</dd>
            <dt className="text-faint">AUM</dt><dd className="num">{fund.aum ? `${crore(fund.aum.value_crore)} (as of ${formatDate(fund.aum.as_of)})` : "Not available"}</dd>
            <dt className="text-faint">Expense ratio</dt><dd>{fund.expense_ratio_pct === null ? "Not available" : `${fund.expense_ratio_pct.toFixed(2)}% a year`}</dd>
            <dt className="text-faint">Benchmark</dt><dd>{fund.benchmark?.name ?? "Not assigned"}</dd>
            <dt className="text-faint">Riskometer</dt><dd>{fund.risk_label ?? "Not available"}</dd>
            <dt className="text-faint">Launch date</dt><dd>{formatDate(fund.launch_date)}</dd>
            <dt className="text-faint">History</dt><dd>{fund.history ? `${formatDate(fund.history.start)} – ${formatDate(fund.history.end)} (${fund.history.observations} NAVs)` : "None"}</dd>
            <dt className="text-faint">Data source</dt><dd>{fund.source}</dd>
          </dl>
        </Card>
        <Card title="Trailing returns" subtitle="Periods over one year are annualised (CAGR); shorter ones are absolute.">
          <table className="table-base">
            <thead><tr><th>Period</th><th className="text-right">Return</th><th>From</th></tr></thead>
            <tbody>
              {fund.trailing_returns.map((row) => (
                <tr key={row.period}>
                  <td>{PERIOD_LABEL[row.period] ?? row.period}{row.annualised ? " (annualised)" : ""}</td>
                  <td className={`num text-right ${tone(row.value) === "loss" ? "text-loss" : "text-ledger"}`}>
                    {row.value === null ? <span className="text-faint" title={row.unavailable_reason}>not covered</span> : pct(row.value, 2, true)}
                  </td>
                  <td className="text-faint">{formatDate(row.start_date)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>

      <section className="mt-8 space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex items-center gap-2"><h2 className="text-xl">Performance and risk</h2><KindLabel kind="historical" /></div>
          <div className="w-44"><PeriodSelect value={period} onChange={setPeriod} /></div>
        </div>
        {risk.isLoading && <Loading />}
        {risk.error && <ErrorState error={risk.error} />}
        {risk.data && (
          <>
            <RiskKpis metrics={risk.data.metrics} assumptions={risk.data.assumptions} period={risk.data.period} />
            <ChartFrame title="Growth of 100 vs benchmark" caption={`Rebased at ${formatDate(risk.data.period.start)}. Benchmark: ${risk.data.benchmark?.name ?? "none"}.`}>
              <TimeSeriesChart
                data={mergeSeries({ fund: risk.data.series?.rebased, benchmark: risk.data.series?.benchmark_rebased })}
                lines={[{ key: "fund", name: "Fund" }, { key: "benchmark", name: "Benchmark", color: palette.info, dashed: true }]}
                yFormat={(v) => v.toFixed(0)}
              />
            </ChartFrame>
            <AssumptionsPanel assumptions={risk.data.assumptions} />
          </>
        )}
      </section>

      <section className="mt-8 grid gap-4 xl:grid-cols-2">
        <Card
          title="Holdings"
          subtitle={holdings.data?.as_of ? `Disclosed portfolio as of ${formatDate(holdings.data.as_of)}` : undefined}
          actions={fund.holdings_dates.length > 1 && (
            <select aria-label="Holdings date" className="field-input" value={asOf} onChange={(e) => setAsOf(e.target.value)}>
              <option value="">Latest</option>
              {fund.holdings_dates.map((d) => <option key={d} value={d}>{formatDate(d)}</option>)}
            </select>
          )}
        >
          {holdings.isLoading && <Loading />}
          {holdings.data && holdings.data.holdings.length === 0 && <p className="text-muted">{holdings.data.note}</p>}
          {holdings.data && holdings.data.holdings.length > 0 && (
            <>
              {holdings.data.concentration && (
                <p className="mb-3 text-sm text-muted">
                  Top 10 holdings: <span className="num text-paper">{holdings.data.concentration.top10_weight_pct.toFixed(1)}%</span> ·
                  effective number of holdings: <span className="num text-paper">{ratio(holdings.data.concentration.effective_number_of_holdings, 1)}</span>
                </p>
              )}
              <div className="max-h-80 overflow-y-auto">
                <table className="table-base">
                  <thead><tr><th>Holding</th><th>Sector</th><th className="text-right">Weight</th></tr></thead>
                  <tbody>
                    {holdings.data.holdings.map((h) => (
                      <tr key={h.name}><td>{h.name}</td><td className="text-muted">{h.sector}</td><td className="num text-right">{h.weight_pct.toFixed(1)}%</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </Card>
        <ChartFrame title="Sector allocation" caption={holdings.data?.concentration?.note} height={Math.max(220, (holdings.data?.sectors.length ?? 6) * 30)}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={holdings.data?.sectors ?? []} layout="vertical" margin={{ left: 20, right: 16 }}>
              <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" {...axisProps} tickFormatter={(v: number) => `${v}%`} />
              <YAxis type="category" dataKey="sector" {...axisProps} width={150} />
              <Tooltip {...tooltipStyle} formatter={(v) => [`${Number(v).toFixed(1)}%`, "Weight"]} />
              <Bar dataKey="weight_pct" fill={palette.ledger} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </ChartFrame>
      </section>

      <section className="mt-8 grid gap-4 xl:grid-cols-2">
        <ChartFrame title="Assets under management" caption="Month-end AUM in ₹ crore.">
          {aum.data && aum.data.points.length > 0 ? (
            <TimeSeriesChart data={aum.data.points} lines={[{ key: "value", name: "AUM (₹ cr)" }]} yFormat={(v) => v.toLocaleString("en-IN")} />
          ) : <p className="text-muted">AUM data is not available for this fund.</p>}
        </ChartFrame>
        <ChartFrame title="Monthly SIP inflows" caption="₹ crore per month.">
          {sip.data && sip.data.points.length > 0 ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={sip.data.points}>
                <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="month" {...axisProps} tickFormatter={(v: string) => v.slice(0, 7)} minTickGap={30} />
                <YAxis {...axisProps} width={56} />
                <Tooltip {...tooltipStyle} labelFormatter={(l) => String(l).slice(0, 7)} formatter={(v) => [`₹${Number(v).toFixed(2)} cr`, "SIP inflow"]} />
                <Bar dataKey="inflow" fill={palette.info} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          ) : <p className="text-muted">{sip.data?.note ?? "SIP flow data is not available for this fund."}</p>}
        </ChartFrame>
      </section>
    </>
  );
}
