import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import type { OptimisationResult, Period, Portfolio, PortfolioReport } from "../api/types";
import { ChartFrame, DrawdownChart, TimeSeriesChart, mergeSeries, palette } from "../components/charts";
import { AssumptionsPanel, RiskKpis } from "../components/metrics";
import { OptimiserPanel } from "../components/OptimiserPanel";
import { PortfolioEditor, type PortfolioPayload } from "../components/PortfolioEditor";
import { PeriodSelect } from "../components/selectors";
import { Button, Card, ConfirmDialog, ErrorState, FreshnessBadge, KindLabel, Loading, PageHeader, SyntheticBadge } from "../components/ui";
import { formatDate, inr, pct } from "../lib/format";

export default function PortfolioDetailPage() {
  const { portfolioId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [period, setPeriod] = useState<Period>("3y");
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const portfolio = useQuery({ queryKey: ["portfolio", portfolioId], queryFn: () => api.get<Portfolio>(`/portfolios/${portfolioId}`) });
  const analytics = useQuery({
    queryKey: ["portfolio-analytics", portfolioId, period],
    queryFn: () => api.get<PortfolioReport>(`/portfolios/${portfolioId}/analytics?period=${period}`),
    enabled: portfolio.isSuccess,
  });
  const remove = useMutation({
    mutationFn: () => api.delete(`/portfolios/${portfolioId}`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["portfolios"] });
      navigate("/portfolios");
    },
  });

  async function refreshAll() {
    await queryClient.invalidateQueries({ queryKey: ["portfolio", portfolioId] });
    await queryClient.invalidateQueries({ queryKey: ["portfolio-analytics", portfolioId] });
    await queryClient.invalidateQueries({ queryKey: ["portfolios"] });
  }

  async function update(payload: PortfolioPayload) {
    await api.put(`/portfolios/${portfolioId}`, payload);
    await refreshAll();
    setEditing(false);
  }

  if (portfolio.isLoading) return <Loading />;
  if (portfolio.error) return <ErrorState error={portfolio.error} />;
  const p = portfolio.data!;
  const report = analytics.data;

  return (
    <>
      <PageHeader
        title={p.name}
        description={p.description || `${inr(p.initial_value)} across ${p.assets.length} funds`}
        actions={
          <>
            <a className="rounded-md border border-ink-600 px-3.5 py-2 text-sm hover:border-ledger/70" href={`/api/v1/portfolios/${p.id}/export.csv`}>
              Export CSV
            </a>
            <Button variant="secondary" onClick={() => setEditing((v) => !v)}>{editing ? "Close editor" : "Edit"}</Button>
            <Button variant="danger" onClick={() => setConfirmDelete(true)}>Delete</Button>
          </>
        }
      />
      <ConfirmDialog
        open={confirmDelete}
        title="Delete this portfolio?"
        message={`"${p.name}" and its allocation will be removed permanently.`}
        confirmLabel="Delete portfolio"
        busy={remove.isPending}
        onConfirm={() => remove.mutate()}
        onCancel={() => setConfirmDelete(false)}
      />
      {editing && (
        <Card title="Edit portfolio" className="mb-6">
          <PortfolioEditor initial={p} submitLabel="Save changes" onSubmit={update} onCancel={() => setEditing(false)} />
        </Card>
      )}

      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <SyntheticBadge show={p.assets.some((a) => a.is_synthetic)} />
          {report && <FreshnessBadge freshness={report.freshness} />}
          <KindLabel kind="historical" />
        </div>
        <div className="w-48">
          <PeriodSelect value={period} onChange={setPeriod} />
        </div>
      </div>
      {p.start_date && <p className="mb-4 text-sm text-muted">Analysed from the portfolio's start date {formatDate(p.start_date)}; the period selector does not apply.</p>}

      {analytics.isLoading && <Loading />}
      {analytics.error && <ErrorState error={analytics.error} />}
      {report && (
        <div className="space-y-6">
          <p className="text-lg">
            <span className="num font-semibold">{inr(report.value.start)}</span> on {formatDate(report.period.start)} grew to{" "}
            <span className="num font-semibold">{inr(report.value.end)}</span> by {formatDate(report.value.as_of)} (buy and hold).
          </p>
          <RiskKpis metrics={report.metrics} assumptions={report.assumptions} period={report.period} />
          <div className="grid gap-4 xl:grid-cols-2">
            <ChartFrame title="Portfolio value" caption={`Benchmark (${report.benchmark?.name ?? "n/a"}) scaled to the same start value.`}>
              <TimeSeriesChart
                data={mergeSeries({ portfolio: report.series.value, benchmark: report.series.benchmark_scaled })}
                lines={[{ key: "portfolio", name: "Portfolio" }, { key: "benchmark", name: "Benchmark", color: palette.info, dashed: true }]}
                yFormat={(v) => inr(v)}
              />
            </ChartFrame>
            <ChartFrame title="Drawdown">
              <DrawdownChart points={report.series.drawdown} />
            </ChartFrame>
          </div>
          <Card title="Holdings" subtitle={`${report.alignment.dates_aligned} dates where every fund has a NAV (${report.alignment.dates_dropped} dropped).`}>
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr><th>Fund</th><th className="text-right">Initial weight</th><th className="text-right">Current weight</th>
                    <th className="text-right">Current value</th><th className="text-right">Share of risk</th></tr>
                </thead>
                <tbody>
                  {report.allocation.holdings.map((h) => (
                    <tr key={h.fund_id}>
                      <td>{h.name}<div className="text-xs text-faint">{h.scheme_code} · {h.category}</div></td>
                      <td className="num text-right">{pct(h.initial_weight, 1)}</td>
                      <td className="num text-right">{pct(h.current_weight, 1)}</td>
                      <td className="num text-right">{inr(h.current_value)}</td>
                      <td className="num text-right">{pct(h.risk_contribution, 1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-xs text-faint">Share of risk = each fund's contribution to portfolio volatility (w·Σw / σ²), using initial weights.</p>
          </Card>
          <AssumptionsPanel assumptions={report.assumptions} />
        </div>
      )}

      <div className="mt-8">
        <OptimiserPanel
          run={(request) => api.post<OptimisationResult>(`/portfolios/${p.id}/optimize`, request)}
          onApply={async (assets) => {
            await api.put(`/portfolios/${p.id}/weights`, { assets });
            await refreshAll();
          }}
        />
      </div>
    </>
  );
}
