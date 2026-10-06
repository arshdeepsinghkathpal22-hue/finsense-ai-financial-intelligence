import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, errorMessage } from "../api/client";
import type { OptimisationResult, Portfolio } from "../api/types";
import { OptimiserPanel } from "../components/OptimiserPanel";
import { PortfolioEditor, WeightEditor, type PortfolioPayload } from "../components/PortfolioEditor";
import { Button, Card, EmptyState, ErrorState, Field, Loading, Notice, PageHeader, SyntheticBadge, Tabs } from "../components/ui";
import { formatDate, inr } from "../lib/format";
import { checkWeights, type WeightRow } from "../lib/weights";

export default function PortfoliosPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"create" | "import" | "explore">("create");
  const list = useQuery({ queryKey: ["portfolios"], queryFn: () => api.get<{ items: Portfolio[] }>("/portfolios") });

  async function create(payload: PortfolioPayload) {
    const created = await api.post<Portfolio>("/portfolios", payload);
    await queryClient.invalidateQueries({ queryKey: ["portfolios"] });
    navigate(`/portfolios/${created.id}`);
  }

  return (
    <>
      <PageHeader title="Portfolios" description="Build allocations from the fund universe, analyse them and optimise them within your limits." />
      <div className="grid gap-6 xl:grid-cols-[1fr_1.4fr]">
        <Card title="Your portfolios">
          {list.isLoading && <Loading />}
          {list.error && <ErrorState error={list.error} />}
          {list.data?.items.length === 0 && <EmptyState title="No portfolios yet">Create one with the form, or import a CSV.</EmptyState>}
          <ul className="divide-y divide-ink-800">
            {list.data?.items.map((p) => (
              <li key={p.id} className="py-3">
                <Link to={`/portfolios/${p.id}`} className="font-medium hover:text-ledger">{p.name}</Link>
                <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-faint">
                  <span>{inr(p.initial_value)} · {p.assets.length} funds · updated {formatDate(p.updated_at)}</span>
                  <SyntheticBadge show={p.assets.some((a) => a.is_synthetic)} />
                </div>
              </li>
            ))}
          </ul>
        </Card>
        <Card>
          <Tabs label="New portfolio" value={mode} onChange={setMode} tabs={[
            { id: "create", label: "Create" },
            { id: "import", label: "Import CSV" },
            { id: "explore", label: "Try the optimiser" },
          ]} />
          <div className="pt-4">
            {mode === "create" && <PortfolioEditor submitLabel="Create portfolio" onSubmit={create} />}
            {mode === "import" && <ImportForm onCreated={(id) => navigate(`/portfolios/${id}`)} />}
            {mode === "explore" && <AdHocOptimiser />}
          </div>
        </Card>
      </div>
    </>
  );
}

function ImportForm({ onCreated }: { onCreated: (id: string) => void }) {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [value, setValue] = useState("100000");
  const mutation = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.append("file", file as File);
      form.append("name", name.trim());
      form.append("initial_value", value);
      return api.upload<Portfolio>("/portfolios/import", form);
    },
    onSuccess: async (portfolio) => {
      await queryClient.invalidateQueries({ queryKey: ["portfolios"] });
      onCreated(portfolio.id);
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (file && name.trim()) mutation.mutate();
  };
  return (
    <form onSubmit={submit} className="space-y-3">
      <p className="text-sm text-muted">CSV with columns <code>scheme_code,weight_pct</code>; weights must add up to 100. The sample <code>example_portfolio.csv</code> in the project's sample data works.</p>
      <Field label="Portfolio name">{(id) => <input id={id} className="field-input" value={name} onChange={(e) => setName(e.target.value)} />}</Field>
      <Field label="Amount invested (₹)">{(id) => <input id={id} className="field-input" value={value} onChange={(e) => setValue(e.target.value)} />}</Field>
      <Field label="CSV file">{(id) => <input id={id} type="file" accept=".csv,text/csv" className="text-sm" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />}</Field>
      {mutation.error && <Notice tone="loss">{errorMessage(mutation.error)}</Notice>}
      <Button type="submit" disabled={!file || !name.trim()} busy={mutation.isPending}>Import portfolio</Button>
    </form>
  );
}

function AdHocOptimiser() {
  const [rows, setRows] = useState<WeightRow[]>([{ fundId: null, weightPct: "50" }, { fundId: null, weightPct: "50" }]);
  const check = checkWeights(rows);
  const fundIds = rows.map((r) => r.fundId).filter((id): id is number => id !== null);
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">Pick funds and your current weights, then optimise without saving anything.</p>
      <WeightEditor rows={rows} onChange={setRows} />
      {check.valid && fundIds.length >= 2 ? (
        <OptimiserPanel
          run={(request) =>
            api.post<OptimisationResult>("/portfolios/optimize", {
              ...request,
              fund_ids: fundIds,
              current_weights_pct: rows.map((r) => Number(r.weightPct)),
            })
          }
        />
      ) : (
        <p className="text-sm text-faint">Choose at least two different funds with weights totalling 100% to enable the optimiser.</p>
      )}
    </div>
  );
}
