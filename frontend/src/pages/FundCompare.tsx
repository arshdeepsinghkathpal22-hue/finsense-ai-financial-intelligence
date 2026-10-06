import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import type { FundSummary, Metrics, Period } from "../api/types";
import { ChartFrame, TimeSeriesChart } from "../components/charts";
import { FundSelect, PeriodSelect, useFunds } from "../components/selectors";
import { Button, Card, ErrorState, KindLabel, Loading, PageHeader, SyntheticBadge } from "../components/ui";
import { formatDate, formatMetric } from "../lib/format";

interface CompareResponse {
  funds: { fund: FundSummary; period: { start: string; end: string }; metrics: Metrics }[];
  chart: { common_start: string; points: Record<string, number | string>[] };
}

const ROWS: { key: string; label: string }[] = [
  { key: "cumulative_return", label: "Cumulative return" },
  { key: "cagr", label: "CAGR" },
  { key: "volatility", label: "Volatility (annualised)" },
  { key: "sharpe_ratio", label: "Sharpe ratio" },
  { key: "sortino_ratio", label: "Sortino ratio" },
  { key: "max_drawdown", label: "Maximum drawdown" },
  { key: "beta", label: "Beta vs own benchmark" },
  { key: "historical_var", label: "1-day VaR 95% (historical)" },
];

export default function FundComparePage() {
  const [params, setParams] = useSearchParams();
  const ids = (params.get("ids") ?? "").split(",").filter(Boolean).map(Number);
  const [period, setPeriod] = useState<Period>("3y");
  const [pending, setPending] = useState<number | null>(null);
  const funds = useFunds();
  const enabled = ids.length >= 2;
  const compare = useQuery({
    queryKey: ["compare", ids.join(","), period],
    queryFn: () => api.get<CompareResponse>(`/funds/compare?ids=${ids.join(",")}&period=${period}`),
    enabled,
  });
  const nameOf = (id: number) => funds.data?.items.find((f) => f.id === id)?.name ?? `Fund ${id}`;
  const setIds = (next: number[]) => setParams(next.length ? { ids: next.join(",") } : {});

  return (
    <>
      <PageHeader title="Compare funds" description="Pick two to five funds. Growth is rebased to 100 at the first date all of them have data." />
      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-64 flex-1"><FundSelect value={pending} onChange={setPending} includeEmpty emptyLabel="Add a fund" /></div>
          <Button disabled={pending === null || ids.includes(pending) || ids.length >= 5} onClick={() => { if (pending) setIds([...ids, pending]); setPending(null); }}>
            Add
          </Button>
          <div className="w-44"><PeriodSelect value={period} onChange={setPeriod} /></div>
        </div>
        <ul className="mt-3 flex flex-wrap gap-2">
          {ids.map((id) => (
            <li key={id} className="flex items-center gap-2 rounded border border-ink-600 px-2 py-1 text-sm">
              {nameOf(id)}
              <button type="button" aria-label={`Remove ${nameOf(id)}`} className="text-faint hover:text-loss" onClick={() => setIds(ids.filter((x) => x !== id))}>×</button>
            </li>
          ))}
        </ul>
      </Card>

      {!enabled && <p className="mt-6 text-muted">Add at least two funds to compare. You can also start from a fund page in the <Link to="/funds" className="text-ledger hover:underline">explorer</Link>.</p>}
      {compare.isLoading && <Loading />}
      {compare.error && <ErrorState error={compare.error} />}
      {compare.data && (
        <div className="mt-6 space-y-6">
          <ChartFrame title="Growth of 100" badge={<KindLabel kind="historical" />} caption={`Common start ${formatDate(compare.data.chart.common_start)}.`} height={320}>
            <TimeSeriesChart data={compare.data.chart.points} lines={compare.data.funds.map((f) => ({ key: String(f.fund.id), name: f.fund.scheme_code }))} yFormat={(v) => v.toFixed(0)} />
          </ChartFrame>
          <div className="panel overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Metric</th>
                  {compare.data.funds.map((f) => (
                    <th key={f.fund.id} className="text-right">
                      {f.fund.scheme_code}
                      <div className="text-xs font-normal">{formatDate(f.period.start)} – {formatDate(f.period.end)}</div>
                      <SyntheticBadge show={f.fund.is_synthetic} />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ROWS.map((row) => (
                  <tr key={row.key}>
                    <td>{row.label}</td>
                    {compare.data.funds.map((f) => {
                      const m = f.metrics[row.key];
                      return <td key={f.fund.id} className="num text-right" title={m?.unavailable_reason}>{m ? formatMetric(m.value, m.unit) : "n/a"}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}
