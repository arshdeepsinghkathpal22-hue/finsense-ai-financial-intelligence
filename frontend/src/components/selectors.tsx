import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { FundSummary, Paged, Period } from "../api/types";

export function useFunds() {
  return useQuery({
    queryKey: ["funds", "all"],
    queryFn: () => api.get<Paged<FundSummary>>("/funds?page_size=100"),
    staleTime: 5 * 60_000,
  });
}

export function FundSelect({
  id,
  value,
  onChange,
  includeEmpty = false,
  emptyLabel = "Choose a fund",
}: {
  id?: string;
  value: number | null;
  onChange: (fundId: number | null) => void;
  includeEmpty?: boolean;
  emptyLabel?: string;
}) {
  const { data, isLoading } = useFunds();
  return (
    <select
      id={id}
      className="field-input"
      value={value ?? ""}
      onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}
      disabled={isLoading}
    >
      {(includeEmpty || value === null) && <option value="">{isLoading ? "Loading funds…" : emptyLabel}</option>}
      {data?.items.map((fund) => (
        <option key={fund.id} value={fund.id}>
          {fund.name} ({fund.scheme_code}){fund.is_synthetic ? " · synthetic" : ""}
        </option>
      ))}
    </select>
  );
}

const PERIODS: { value: Period; label: string }[] = [
  { value: "1y", label: "1 year" },
  { value: "3y", label: "3 years" },
  { value: "5y", label: "5 years" },
  { value: "max", label: "Full history" },
];

export function PeriodSelect({ id, value, onChange }: { id?: string; value: Period; onChange: (p: Period) => void }) {
  return (
    <select id={id} className="field-input" value={value} onChange={(e) => onChange(e.target.value as Period)}>
      {PERIODS.map((p) => (
        <option key={p.value} value={p.value}>
          {p.label}
        </option>
      ))}
    </select>
  );
}
