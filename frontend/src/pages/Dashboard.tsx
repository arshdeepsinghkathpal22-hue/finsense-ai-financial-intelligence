import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

import { api } from "../api/client";
import type { FundRiskReport, Period, PortfolioReport } from "../api/types";
import { ChartFrame, DrawdownChart, TimeSeriesChart, mergeSeries, palette, tooltipStyle } from "../components/charts";
import { AssumptionsPanel, KpiCard, RiskKpis } from "../components/metrics";
import { FundSelect, PeriodSelect } from "../components/selectors";
import { Card, ErrorState, FreshnessBadge, KindLabel, Loading, PageHeader, SyntheticBadge } from "../components/ui";
import { formatDate, inr, nav, pct, ratio } from "../lib/format";

interface DashboardResponse {
  selection: { kind: "fund"; report: FundRiskReport } | { kind: "portfolio"; report: PortfolioReport } | null;
  portfolios: { id: string; name: string }[];
  message?: string;
}

type Choice = { kind: "default" } | { kind: "fund"; id: number } | { kind: "portfolio"; id: string };

export default function DashboardPage() {
  const [choice, setChoice] = useState<Choice>({ kind: "default" });
  const [period, setPeriod] = useState<Period>("3y");
  const params = new URLSearchParams({ period });
  if (choice.kind === "fund") params.set("fund_id", String(choice.id));
  if (choice.kind === "portfolio") params.set("portfolio_id", choice.id);
  const query = useQuery({
    queryKey: ["dashboard", choice, period],
    queryFn: () => api.get<DashboardResponse>(`/dashboard?${params}`),
  });
  const selection = query.data?.selection;

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Key performance and risk figures for a portfolio or a fund, with the period and method behind each number."
      />
      <div className="mb-6 grid gap-3 sm:grid-cols-[1fr_1fr_12rem]">
        <div>
          <label className="mb-1 block text-sm text-muted" htmlFor="dash-portfolio">Portfolio</label>
          <select
            id="dash-portfolio"
            className="field-input"
            value={choice.kind === "portfolio" ? choice.id : ""}
            onChange={(e) => setChoice(e.target.value ? { kind: "portfolio", id: e.target.value } : { kind: "default" })}
          >
            <option value="">{query.data?.portfolios.length ? "Choose a portfolio" : "No portfolios yet"}</option>
            {query.data?.portfolios.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-sm text-muted" htmlFor="dash-fund">or a single fund</label>
          <FundSelect
            id="dash-fund"
            value={choice.kind === "fund" ? choice.id : null}
            includeEmpty
            onChange={(id) => setChoice(id ? { kind: "fund", id } : { kind: "default" })}
          />
        </div>
        <div>
          <label className="mb-1 block text-sm text-muted" htmlFor="dash-period">Period</label>
          <PeriodSelect id="dash-period" value={period} onChange={setPeriod} />
        </div>
      </div>

      {query.isLoading && <Loading />}
      {query.error && <ErrorState error={query.error} onRetry={() => query.refetch()} />}
      {query.data && !selection && <Card title="No data yet"><p className="text-muted">{query.data.message}</p></Card>}
      {selection?.kind === "fund" && <FundDashboard report={selection.report} />}
      {selection?.kind === "portfolio" && <PortfolioDashboard report={selection.report} />}
      {query.data && query.data.portfolios.length === 0 && (
        <p className="mt-6 text-sm text-muted">
          Showing a demo fund. <Link className="text-ledger hover:underline" to="/portfolios">Build a portfolio</Link> to see
          its value, allocation and concentration here.
        </p>
      )}
    </>
  );
}

function FundDashboard({ report }: { report: FundRiskReport }) {
  const latestNav = report.series?.nav.at(-1);
  const chart = mergeSeries({ fund: report.series?.rebased, benchmark: report.series?.benchmark_rebased });
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-2 text-2xl">{report.fund.name}</h2>
        <SyntheticBadge show={report.fund.is_synthetic} />
        <FreshnessBadge freshness={report.freshness} />
        <KindLabel kind="historical" />
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard label="Latest NAV" metric={{ value: latestNav?.value ?? null, unit: "inr" }} display={nav(latestNav?.value)}
          note={latestNav ? `as of ${formatDate(latestNav.date)}` : undefined} />
      </div>
      <RiskKpis metrics={report.metrics} assumptions={report.assumptions} period={report.period} />
      <div className="grid gap-4 xl:grid-cols-2">
        <ChartFrame title="Growth of 100" caption={`Fund vs ${report.benchmark?.name ?? "benchmark"}, rebased to 100 at ${formatDate(report.period.start)}.`}>
          <TimeSeriesChart data={chart} lines={[{ key: "fund", name: "Fund" }, { key: "benchmark", name: "Benchmark", color: palette.info, dashed: true }]}
            yFormat={(v) => v.toFixed(0)} />
        </ChartFrame>
        <ChartFrame title="Drawdown" caption="Decline from the running peak of the NAV.">
          <DrawdownChart points={report.series?.drawdown ?? []} />
        </ChartFrame>
      </div>
      <AssumptionsPanel assumptions={report.assumptions} />
    </div>
  );
}

function PortfolioDashboard({ report }: { report: PortfolioReport }) {
  const chart = mergeSeries({ portfolio: report.series.value, benchmark: report.series.benchmark_scaled });
  const allocation = report.allocation;
  const change = report.value.end / report.value.start - 1;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-2 text-2xl">{report.portfolio.name}</h2>
        <SyntheticBadge show={report.portfolio.contains_synthetic_data} />
        <FreshnessBadge freshness={report.freshness} />
        <KindLabel kind="historical" />
        <Link to={`/portfolios/${report.portfolio.id}`} className="ml-auto text-sm text-ledger hover:underline">Open portfolio</Link>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard label="Portfolio value" metric={{ value: report.value.end, unit: "inr" }} display={inr(report.value.end)}
          note={`From ${inr(report.value.start)} on ${formatDate(report.period.start)} (${pct(change, 1, true)})`} />
        <KpiCard label="Effective holdings" metric={{ value: allocation.effective_number_of_holdings, unit: "ratio" }}
          note={`HHI ${ratio(allocation.herfindahl_index, 3)}; largest weight ${pct(allocation.largest_weight, 1)}`}
          info="Effective number of holdings = 1 / Herfindahl index of current weights. 1 means everything is in one fund." />
        <KpiCard label="Diversification ratio" metric={{ value: allocation.diversification_ratio, unit: "ratio" }}
          note="Weighted fund volatility / portfolio volatility"
          info="Values above 1 mean the funds' imperfect correlation reduces portfolio risk." />
      </div>
      <RiskKpis metrics={report.metrics} assumptions={report.assumptions} period={report.period} />
      <div className="grid gap-4 xl:grid-cols-[2fr_1fr]">
        <ChartFrame title="Portfolio value" caption={`Buy and hold from ${formatDate(report.period.start)}; benchmark scaled to the same starting value.`}>
          <TimeSeriesChart data={chart} lines={[{ key: "portfolio", name: "Portfolio" }, { key: "benchmark", name: report.benchmark?.name ?? "Benchmark", color: palette.info, dashed: true }]}
            yFormat={(v) => inr(v)} />
        </ChartFrame>
        <ChartFrame title="Current allocation" caption={`Drifted weights as of ${formatDate(report.value.as_of)}.`}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={allocation.holdings} dataKey="current_weight" nameKey="scheme_code" innerRadius="55%" outerRadius="85%" isAnimationActive={false}>
                {allocation.holdings.map((h, i) => <Cell key={h.fund_id} fill={palette.series[i % palette.series.length]} />)}
              </Pie>
              <Tooltip {...tooltipStyle} formatter={(v, name) => [pct(Number(v), 1), String(name)]} />
              <Legend wrapperStyle={{ fontSize: 12 }} formatter={(value, entry) => `${value} ${pct((entry.payload as { current_weight?: number })?.current_weight, 1)}`} />
            </PieChart>
          </ResponsiveContainer>
        </ChartFrame>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <ChartFrame title="Drawdown">
          <DrawdownChart points={report.series.drawdown} />
        </ChartFrame>
        <Card title="Allocation by asset class">
          <ul className="space-y-2">
            {allocation.by_asset_class.map((row) => (
              <li key={row.asset_class}>
                <div className="flex justify-between text-sm"><span className="capitalize">{row.asset_class}</span><span className="num">{pct(row.weight, 1)}</span></div>
                <div className="mt-1 h-1.5 rounded bg-ink-800"><div className="h-1.5 rounded bg-ledger" style={{ width: `${row.weight * 100}%` }} /></div>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <AssumptionsPanel assumptions={report.assumptions} />
    </div>
  );
}
