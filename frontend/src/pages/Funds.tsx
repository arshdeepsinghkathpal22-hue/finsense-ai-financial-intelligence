import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { FundSummary, Paged } from "../api/types";
import { Button, EmptyState, ErrorState, FreshnessBadge, Loading, PageHeader, SyntheticBadge } from "../components/ui";
import { crore, formatDate, nav, pct, tone } from "../lib/format";

interface Category {
  category: string;
  asset_class: string;
  count: number;
}

export default function FundsPage() {
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [assetClass, setAssetClass] = useState("");
  const [page, setPage] = useState(1);
  const params = new URLSearchParams({ page: String(page), page_size: "25" });
  if (q.trim()) params.set("q", q.trim());
  if (category) params.set("category", category);
  if (assetClass) params.set("asset_class", assetClass);

  const funds = useQuery({
    queryKey: ["funds", q, category, assetClass, page],
    queryFn: () => api.get<Paged<FundSummary>>(`/funds?${params}`),
    placeholderData: keepPreviousData,
  });
  const categories = useQuery({ queryKey: ["fund-categories"], queryFn: () => api.get<{ items: Category[] }>("/funds/categories") });
  const pages = funds.data ? Math.max(1, Math.ceil(funds.data.total / funds.data.page_size)) : 1;

  return (
    <>
      <PageHeader
        title="Fund explorer"
        description="Search schemes, then open one for NAV history, returns, risk, holdings, AUM and SIP flows."
        actions={<Link to="/funds/compare" className="rounded-md border border-ink-600 px-3.5 py-2 text-sm hover:border-ledger/70">Compare funds</Link>}
      />
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <input aria-label="Search by name or scheme code" className="field-input" placeholder="Search by name or scheme code"
          value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        <select aria-label="Category" className="field-input" value={category} onChange={(e) => { setCategory(e.target.value); setPage(1); }}>
          <option value="">All categories</option>
          {[...new Set(categories.data?.items.map((c) => c.category))].map((c) => <option key={c}>{c}</option>)}
        </select>
        <select aria-label="Asset class" className="field-input" value={assetClass} onChange={(e) => { setAssetClass(e.target.value); setPage(1); }}>
          <option value="">All asset classes</option>
          {["equity", "debt", "hybrid", "other"].map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>

      {funds.isLoading && <Loading />}
      {funds.error && <ErrorState error={funds.error} onRetry={() => funds.refetch()} />}
      {funds.data && funds.data.items.length === 0 && <EmptyState title="No funds match these filters">Clear the search or choose another category.</EmptyState>}
      {funds.data && funds.data.items.length > 0 && (
        <div className="panel overflow-x-auto">
          <table className="table-base">
            <thead>
              <tr>
                <th>Fund</th><th>Category</th><th className="text-right">Latest NAV</th><th className="text-right">1-year return</th>
                <th className="text-right">AUM</th><th className="text-right">Expense ratio</th><th>Data</th>
              </tr>
            </thead>
            <tbody>
              {funds.data.items.map((fund) => (
                <tr key={fund.id} className="hover:bg-ink-850">
                  <td>
                    <Link to={`/funds/${fund.id}`} className="font-medium text-paper hover:text-ledger">{fund.name}</Link>
                    <div className="text-xs text-faint">{fund.scheme_code} · {fund.amc ?? "AMC not stated"}</div>
                  </td>
                  <td>{fund.category}<div className="text-xs capitalize text-faint">{fund.asset_class}</div></td>
                  <td className="num text-right">{nav(fund.latest_nav)}<div className="text-xs text-faint">{formatDate(fund.latest_nav_date)}</div></td>
                  <td className={`num text-right ${tone(fund.return_1y) === "loss" ? "text-loss" : "text-ledger"}`}>
                    {fund.return_1y === null ? <span className="text-faint">not available</span> : pct(fund.return_1y, 2, true)}
                  </td>
                  <td className="num text-right">{fund.aum ? <>{crore(fund.aum.value_crore)}<div className="text-xs text-faint">{formatDate(fund.aum.as_of)}</div></> : <span className="text-faint">not available</span>}</td>
                  <td className="num text-right">{fund.expense_ratio_pct === null ? <span className="text-faint">n/a</span> : `${fund.expense_ratio_pct.toFixed(2)}%`}</td>
                  <td><div className="flex flex-wrap gap-1"><SyntheticBadge show={fund.is_synthetic} /><FreshnessBadge freshness={fund.freshness} /></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pages > 1 && (
        <div className="mt-4 flex items-center gap-3">
          <Button variant="secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</Button>
          <span className="text-sm text-muted">Page {page} of {pages}</span>
          <Button variant="secondary" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</Button>
        </div>
      )}
    </>
  );
}
