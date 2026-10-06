import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { Freshness, ImportSummary, Paged, SystemStatus } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Badge, Button, Card, ErrorState, Field, FreshnessBadge, Loading, Notice, PageHeader, SyntheticBadge, Tabs } from "../components/ui";
import { formatDate } from "../lib/format";

type Tab = "data" | "import" | "rag" | "audit" | "users";
const KINDS = ["benchmarks", "benchmark_values", "funds", "nav", "aum", "sip_flows", "holdings"];

export default function AdminPage() {
  const [tab, setTab] = useState<Tab>("data");
  return (
    <>
      <PageHeader title="Administration" description="Data quality, imports, retrieval evaluation and the security audit trail." />
      <Tabs label="Administration sections" value={tab} onChange={setTab} tabs={[
        { id: "data", label: "Data status" }, { id: "import", label: "Import data" }, { id: "rag", label: "Retrieval quality" },
        { id: "audit", label: "Audit log" }, { id: "users", label: "Users" },
      ]} />
      <div className="mt-5">
        {tab === "data" && <DataStatus />}
        {tab === "import" && <ImportPanel />}
        {tab === "rag" && <RagPanel />}
        {tab === "audit" && <AuditPanel />}
        {tab === "users" && <UsersPanel />}
      </div>
    </>
  );
}

interface DataStatusResponse {
  sources: { code: string; name: string; kind: string; is_synthetic: boolean; description: string }[];
  funds: { id: number; scheme_code: string; name: string; is_synthetic: boolean; source: string; observations: number; freshness: Freshness }[];
  documents: Record<string, number>;
  documents_needing_reindex: number;
  users: number;
}

function DataStatus() {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["admin-data-status"], queryFn: () => api.get<DataStatusResponse>("/admin/data-status") });
  const reindex = useMutation({
    mutationFn: () => api.post<{ reindexed: { title: string; status: string }[] }>("/admin/documents/reindex-outdated"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-data-status"] }),
  });
  if (status.isLoading) return <Loading />;
  if (status.error) return <ErrorState error={status.error} />;
  const s = status.data!;
  return (
    <div className="space-y-6">
      <Card title="Funds and freshness">
        <div className="overflow-x-auto">
          <table className="table-base">
            <thead><tr><th>Fund</th><th>Source</th><th className="text-right">NAV observations</th><th>Latest</th></tr></thead>
            <tbody>{s.funds.map((f) => (
              <tr key={f.id}><td>{f.name}<div className="text-xs text-faint">{f.scheme_code}</div></td>
                <td><span className="mr-2">{f.source}</span><SyntheticBadge show={f.is_synthetic} /></td>
                <td className="num text-right">{f.observations.toLocaleString("en-IN")}</td><td><FreshnessBadge freshness={f.freshness} /></td></tr>
            ))}</tbody>
          </table>
        </div>
      </Card>
      <div className="grid gap-6 xl:grid-cols-2">
        <Card title="Documents">
          <ul className="space-y-1 text-sm">{Object.entries(s.documents).map(([k, v]) => <li key={k}>{k}: <span className="num">{v}</span></li>)}</ul>
          <p className="mt-3 text-sm text-muted">{s.documents_needing_reindex} documents were indexed with an older parser, chunking or embedding configuration (or failed).</p>
          <Button className="mt-3" variant="secondary" busy={reindex.isPending} onClick={() => reindex.mutate()}>Re-index outdated and failed documents</Button>
          {reindex.data && <Notice>{reindex.data.reindexed.length} documents processed: {reindex.data.reindexed.map((d) => `${d.title} (${d.status})`).join(", ") || "none needed"}.</Notice>}
          {reindex.error && <ErrorState error={reindex.error} />}
        </Card>
        <Card title="Data sources">
          <ul className="space-y-3 text-sm">{s.sources.map((src) => (
            <li key={src.code}><span className="font-medium">{src.name}</span> <Badge>{src.kind}</Badge> <SyntheticBadge show={src.is_synthetic} /><p className="text-faint">{src.description}</p></li>
          ))}</ul>
        </Card>
      </div>
      <AmfiPanel />
    </div>
  );
}

interface AmfiSyncResult {
  tracked: number;
  updated: { scheme_code: string; date: string; nav: string }[];
  missing: string[];
}

/** Optional live NAVs from AMFI's public NAVAll.txt (disabled unless AMFI_ENABLED=true). */
function AmfiPanel() {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["system-status"], queryFn: () => api.get<SystemStatus>("/system/status") });
  const [code, setCode] = useState("");
  const [category, setCategory] = useState("");
  const [assetClass, setAssetClass] = useState("equity");
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["admin-data-status"] });
  const register = useMutation({
    mutationFn: () => api.post<{ id: number; scheme_code: string; name: string }>("/admin/amfi/register",
      { scheme_code: code.trim(), category: category.trim(), asset_class: assetClass }),
    onSuccess: refresh,
  });
  const sync = useMutation({ mutationFn: () => api.post<AmfiSyncResult>("/admin/amfi/sync"), onSuccess: refresh });
  const enabled = status.data?.live_data.amfi_enabled ?? false;
  const submit = (e: FormEvent) => { e.preventDefault(); register.mutate(); };
  return (
    <Card title="Live NAVs from AMFI (optional)" subtitle="Tracks real schemes from AMFI's public daily NAV file. Real and synthetic data are kept separate.">
      {!enabled ? (
        <p className="text-sm text-muted">Disabled. Set <code>AMFI_ENABLED=true</code> in <code>.env</code> and restart to register schemes and sync their latest NAVs.</p>
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          <form onSubmit={submit} className="space-y-3">
            <Field label="AMFI scheme code" hint="Digits only, as listed in NAVAll.txt">
              {(id) => <input id={id} className="field-input" inputMode="numeric" pattern="[0-9]{3,8}" required value={code} onChange={(e) => setCode(e.target.value)} />}
            </Field>
            <Field label="Category">{(id) => <input id={id} className="field-input" required minLength={2} value={category} onChange={(e) => setCategory(e.target.value)} />}</Field>
            <Field label="Asset class">
              {(id) => (
                <select id={id} className="field-input" value={assetClass} onChange={(e) => setAssetClass(e.target.value)}>
                  {["equity", "debt", "hybrid", "other"].map((c) => <option key={c}>{c}</option>)}
                </select>
              )}
            </Field>
            <Button type="submit" busy={register.isPending}>Register scheme</Button>
            {register.data && <Notice>Registered {register.data.name} ({register.data.scheme_code}).</Notice>}
            {register.error && <ErrorState error={register.error} />}
          </form>
          <div className="space-y-3">
            <p className="text-sm text-muted">Fetches the latest NAV for every registered scheme. AMFI usually publishes the previous business day's NAV; the observation date is stored as reported.</p>
            <Button variant="secondary" busy={sync.isPending} onClick={() => sync.mutate()}>Sync AMFI NAVs</Button>
            {sync.data && (
              <Notice>
                {sync.data.updated.length} of {sync.data.tracked} schemes updated
                {sync.data.updated.length > 0 && `: ${sync.data.updated.map((u) => `${u.scheme_code} ${u.nav} (${formatDate(u.date)})`).join(", ")}`}
                {sync.data.missing.length > 0 && `. Not found in the AMFI file: ${sync.data.missing.join(", ")}`}.
              </Notice>
            )}
            {sync.error && <ErrorState error={sync.error} />}
          </div>
        </div>
      )}
    </Card>
  );
}

function ImportPanel() {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState("nav");
  const [source, setSource] = useState("csv-import");
  const [file, setFile] = useState<File | null>(null);
  const history = useQuery({ queryKey: ["admin-imports"], queryFn: () => api.get<Paged<ImportSummary>>("/admin/imports") });
  const run = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.append("file", file as File);
      form.append("source_code", source);
      return api.upload<ImportSummary>(`/admin/imports/${kind}`, form);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-imports"] }),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (file) run.mutate(); };
  const r = run.data;
  return (
    <div className="grid gap-6 xl:grid-cols-[22rem_1fr]">
      <Card title="Import a CSV" subtitle="Rows are validated, duplicates and bad values rejected with reasons; re-importing the same data is safe.">
        <form onSubmit={submit} className="space-y-3">
          <Field label="Data type">{(id) => <select id={id} className="field-input" value={kind} onChange={(e) => setKind(e.target.value)}>{KINDS.map((k) => <option key={k}>{k}</option>)}</select>}</Field>
          <Field label="Provenance" hint="Synthetic and real observations are never mixed in one series.">
            {(id) => (
              <select id={id} className="field-input" value={source} onChange={(e) => setSource(e.target.value)}>
                <option value="csv-import">Real data (user-provided CSV)</option><option value="synthetic-demo">Synthetic demonstration data</option>
              </select>
            )}
          </Field>
          <Field label="File">{(id) => <input id={id} type="file" accept=".csv,text/csv" className="text-sm" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />}</Field>
          <Button type="submit" disabled={!file} busy={run.isPending}>Validate and import</Button>
          <p className="text-xs text-faint">Try backend/sample_data/csv/nav_with_errors.csv with provenance "Synthetic" to see validation in action.</p>
        </form>
      </Card>
      <div className="space-y-4">
        {run.error && <ErrorState error={run.error} />}
        {r && (
          <Card title={`Import ${r.status}`} subtitle={`${r.kind} · ${r.filename}`}>
            <dl className="num grid grid-cols-5 gap-2 text-center text-sm">
              {(["rows_total", "rows_inserted", "rows_updated", "rows_unchanged", "rows_rejected"] as const).map((k) => (
                <div key={k} className="rounded border border-ink-700 p-2"><dt className="text-xs text-faint">{k.replace("rows_", "")}</dt><dd className="text-lg">{r[k]}</dd></div>
              ))}
            </dl>
            {r.warnings.length > 0 && <div className="mt-3 space-y-1">{r.warnings.map((w) => <Notice key={w} tone="caution">{w}</Notice>)}</div>}
            {r.rejected_rows && r.rejected_rows.length > 0 && (
              <table className="table-base mt-3">
                <thead><tr><th>Line</th><th>Reason</th><th>Values</th></tr></thead>
                <tbody>{r.rejected_rows.map((row, i) => <tr key={i}><td className="num">{row.line}</td><td>{row.reason}</td><td className="text-xs text-faint">{Object.values(row.values).join(", ")}</td></tr>)}</tbody>
              </table>
            )}
          </Card>
        )}
        <Card title="Import history">
          {history.isLoading && <Loading />}
          <table className="table-base">
            <thead><tr><th>When</th><th>Type</th><th>File</th><th>Status</th><th className="text-right">In / Up / Same / Rej</th></tr></thead>
            <tbody>{history.data?.items.map((b) => (
              <tr key={b.id}><td>{formatDate(b.started_at)}</td><td>{b.kind}</td><td className="text-muted">{b.filename}</td>
                <td><Badge tone={b.status === "completed" ? "gain" : "loss"}>{b.status}</Badge></td>
                <td className="num text-right">{b.rows_inserted} / {b.rows_updated} / {b.rows_unchanged} / {b.rows_rejected}</td></tr>
            ))}</tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}

interface ModeSummary { [metric: string]: number | null | Record<string, number | null> | undefined }
interface EvalSummary { passed: boolean; modes: Record<string, ModeSummary>; checks: Record<string, { value: number | null; minimum: number }> }

const EVAL_METRICS = ["recall@1", "recall@3", "recall@5", "precision@5", "mrr", "ndcg@5", "evidence_recall", "false_abstention_rate", "abstention_accuracy", "mean_latency_ms"];

function RagPanel() {
  const latest = useQuery({ queryKey: ["rag-eval-latest"], queryFn: () => api.get<{ created_at: string; summary: EvalSummary }>("/admin/rag/evaluations/latest"), retry: false });
  const queryClient = useQueryClient();
  const run = useMutation({ mutationFn: () => api.post<{ summary: EvalSummary }>("/admin/rag/evaluate"), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["rag-eval-latest"] }) });
  const summary = run.data?.summary ?? latest.data?.summary;
  return (
    <div className="space-y-6">
      <Card title="Retrieval evaluation" subtitle="Runs the labelled question set against the live index in three configurations."
        actions={<Button busy={run.isPending} onClick={() => run.mutate()}>Run evaluation</Button>}>
        {run.error && <ErrorState error={run.error} />}
        {!summary && !latest.isLoading && <p className="text-muted">No evaluation has been run yet.</p>}
        {summary && (
          <>
            <p className="mb-3 text-sm">Acceptance: {summary.passed ? <Badge tone="gain">Passed</Badge> : <Badge tone="loss">Failed</Badge>}{latest.data && !run.data && <span className="ml-2 text-faint">last run {formatDate(latest.data.created_at)}</span>}</p>
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead><tr><th>Metric</th>{["hybrid", "vector", "lexical"].map((m) => <th key={m} className="text-right">{m}</th>)}</tr></thead>
                <tbody>{EVAL_METRICS.map((metric) => (
                  <tr key={metric}><td>{metric}</td>{["hybrid", "vector", "lexical"].map((m) => {
                    const v = summary.modes[m]?.[metric];
                    return <td key={m} className="num text-right">{typeof v === "number" ? v.toFixed(metric === "mean_latency_ms" ? 1 : 3) : "n/a"}</td>;
                  })}</tr>
                ))}</tbody>
              </table>
            </div>
          </>
        )}
      </Card>
      <Diagnostics />
    </div>
  );
}

interface DiagnosticsResponse {
  retrieval_query: string;
  is_follow_up: boolean;
  query_lexemes: string[];
  mentioned_funds: string[];
  timings_ms: Record<string, number>;
  candidates: {
    chunk_id: string; document: string; page_start: number; section: string | null; vector_similarity: number | null; vector_rank: number | null;
    lexical_rank: number | null; lexical_coverage: number; rrf_score: number; final_score: number; supported: boolean; selected: boolean; excerpt: string;
  }[];
  user_prompt: string;
}

function Diagnostics() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("hybrid");
  const run = useMutation({ mutationFn: () => api.post<DiagnosticsResponse>("/admin/rag/diagnostics", { query, mode }) });
  const r = run.data;
  return (
    <Card title="Retrieval diagnostics" subtitle="Scores at every stage for one query, using your own document access.">
      <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (query.trim().length >= 2) run.mutate(); }}>
        <input aria-label="Query" className="field-input flex-1" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. exit load of FS-DB-007" />
        <select aria-label="Mode" className="field-input w-32" value={mode} onChange={(e) => setMode(e.target.value)}><option>hybrid</option><option>vector</option><option>lexical</option></select>
        <Button type="submit" busy={run.isPending}>Inspect</Button>
      </form>
      {run.error && <div className="mt-3"><ErrorState error={run.error} /></div>}
      {r && (
        <div className="mt-4 space-y-3 text-sm">
          <p className="text-muted">Lexemes: {r.query_lexemes.join(", ") || "none"} · funds: {r.mentioned_funds.join(", ") || "none"} · timings: {Object.entries(r.timings_ms).map(([k, v]) => `${k} ${v} ms`).join(", ")}</p>
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead><tr><th>Passage</th><th className="text-right">Cosine</th><th className="text-right">Vec rank</th><th className="text-right">Lex rank</th><th className="text-right">Coverage</th><th className="text-right">RRF</th><th>Gate</th></tr></thead>
              <tbody>{r.candidates.map((c) => (
                <tr key={c.chunk_id} className={c.selected ? "bg-ledger/5" : ""}>
                  <td>{c.document} · p.{c.page_start}<div className="max-w-md truncate text-xs text-faint">{c.excerpt}</div></td>
                  <td className="num text-right">{c.vector_similarity?.toFixed(3) ?? "—"}</td>
                  <td className="num text-right">{c.vector_rank ?? "—"}</td>
                  <td className="num text-right">{c.lexical_rank ?? "—"}</td>
                  <td className="num text-right">{c.lexical_coverage.toFixed(2)}</td>
                  <td className="num text-right">{c.rrf_score.toFixed(4)}</td>
                  <td>{c.selected ? <Badge tone="gain">selected</Badge> : c.supported ? <Badge tone="info">passed</Badge> : <Badge>below gate</Badge>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          <details><summary className="cursor-pointer text-muted">Prompt that would be sent to the language model</summary><pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded bg-ink-950 p-3 text-xs">{r.user_prompt}</pre></details>
        </div>
      )}
    </Card>
  );
}

function AuditPanel() {
  const [page, setPage] = useState(1);
  const audit = useQuery({
    queryKey: ["audit", page],
    queryFn: () => api.get<Paged<{ id: number; event_type: string; user_id: string | null; ip_address: string | null; details: Record<string, unknown>; created_at: string }>>(`/admin/audit?page=${page}`),
  });
  if (audit.isLoading) return <Loading />;
  if (audit.error) return <ErrorState error={audit.error} />;
  const pages = Math.max(1, Math.ceil(audit.data!.total / audit.data!.page_size));
  return (
    <Card title="Security audit log">
      <table className="table-base">
        <thead><tr><th>Time</th><th>Event</th><th>User</th><th>IP</th><th>Details</th></tr></thead>
        <tbody>{audit.data!.items.map((e) => (
          <tr key={e.id}><td className="whitespace-nowrap">{new Date(e.created_at).toLocaleString("en-IN")}</td><td>{e.event_type}</td>
            <td className="text-xs text-faint">{e.user_id?.slice(0, 8) ?? "—"}</td><td className="text-xs">{e.ip_address ?? "—"}</td>
            <td className="text-xs text-faint">{JSON.stringify(e.details)}</td></tr>
        ))}</tbody>
      </table>
      <div className="mt-3 flex items-center gap-3">
        <Button variant="secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</Button>
        <span className="text-sm text-muted">Page {page} of {pages}</span>
        <Button variant="secondary" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</Button>
      </div>
    </Card>
  );
}

function UsersPanel() {
  const { user: me } = useAuth();
  const queryClient = useQueryClient();
  const users = useQuery({ queryKey: ["admin-users"], queryFn: () => api.get<{ items: { id: string; email: string; display_name: string; role: string; is_active: boolean; created_at: string }[] }>("/admin/users") });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => api.patch(`/admin/users/${id}`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-users"] }),
  });
  if (users.isLoading) return <Loading />;
  if (users.error) return <ErrorState error={users.error} />;
  return (
    <Card title="Users">
      {update.error && <ErrorState error={update.error} />}
      <table className="table-base">
        <thead><tr><th>User</th><th>Role</th><th>Status</th><th>Joined</th><th /></tr></thead>
        <tbody>{users.data!.items.map((u) => (
          <tr key={u.id}>
            <td>{u.display_name}<div className="text-xs text-faint">{u.email}</div></td>
            <td>{u.role}</td><td>{u.is_active ? "Active" : "Disabled"}</td><td>{formatDate(u.created_at)}</td>
            <td className="space-x-2 text-right">
              {u.id !== me?.id && (
                <>
                  <Button variant="secondary" onClick={() => update.mutate({ id: u.id, body: { role: u.role === "admin" ? "user" : "admin" } })}>{u.role === "admin" ? "Make user" : "Make admin"}</Button>
                  <Button variant="ghost" onClick={() => update.mutate({ id: u.id, body: { is_active: !u.is_active } })}>{u.is_active ? "Disable" : "Enable"}</Button>
                </>
              )}
            </td>
          </tr>
        ))}</tbody>
      </table>
    </Card>
  );
}
