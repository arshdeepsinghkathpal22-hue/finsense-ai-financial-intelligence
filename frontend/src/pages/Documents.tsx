import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { api, errorMessage } from "../api/client";
import type { DocumentInfo, Paged } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { FundSelect } from "../components/selectors";
import { Badge, Button, Card, EmptyState, ErrorState, Field, Loading, Notice, PageHeader, SyntheticBadge } from "../components/ui";
import { shouldPollDocuments } from "../lib/citations";
import { formatDate } from "../lib/format";

const MAX_MB = 15;
const STATUS_TONE = { indexed: "gain", pending: "info", processing: "info", failed: "loss", needs_ocr: "caution" } as const;
export const DOC_TYPE_LABEL: Record<string, string> = {
  factsheet: "Factsheet", annual_report: "Annual report", sid: "Scheme information document", research: "Research", other: "Other",
};
const STATUS_TEXT = { indexed: "Indexed", pending: "Queued", processing: "Indexing…", failed: "Failed", needs_ocr: "Needs OCR" };

interface SearchResult {
  chunk_id: string;
  document_id: string;
  document_title: string;
  doc_type: string;
  as_of_date: string | null;
  page_start: number;
  page_end: number;
  section: string | null;
  content: string;
  supported: boolean;
  score: number;
}

export function StatusBadge({ status }: { status: DocumentInfo["status"] }) {
  return <Badge tone={STATUS_TONE[status]}>{STATUS_TEXT[status]}</Badge>;
}

export default function DocumentsPage() {
  const documents = useQuery({
    queryKey: ["documents"],
    queryFn: () => api.get<Paged<DocumentInfo>>("/documents?page_size=100"),
    refetchInterval: (query) => (shouldPollDocuments(query.state.data?.items.map((d) => d.status) ?? []) ? 2000 : false),
  });

  return (
    <>
      <PageHeader title="Document library" description="Factsheets, scheme documents and reports the assistant can search. Shared documents are visible to everyone; yours are private to you." />
      <div className="grid gap-6 xl:grid-cols-[1fr_1.6fr]">
        <UploadForm />
        <SearchPanel />
      </div>
      <Card title="Documents" className="mt-6">
        {documents.isLoading && <Loading />}
        {documents.error && <ErrorState error={documents.error} />}
        {documents.data?.items.length === 0 && <EmptyState title="No documents yet">Upload a PDF or text document to make it searchable.</EmptyState>}
        {documents.data && documents.data.items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead><tr><th>Document</th><th>Type</th><th>Fund</th><th>Reporting date</th><th>Status</th><th className="text-right">Pages</th><th className="text-right">Passages</th><th>Access</th></tr></thead>
              <tbody>
                {documents.data.items.map((d) => (
                  <tr key={d.id}>
                    <td>
                      <Link to={`/documents/${d.id}`} className="font-medium hover:text-ledger">{d.title}</Link>
                      <div className="text-xs text-faint">{d.filename}</div>
                    </td>
                    <td>{DOC_TYPE_LABEL[d.doc_type] ?? d.doc_type}</td>
                    <td>{d.fund?.scheme_code ?? <span className="text-faint">—</span>}</td>
                    <td>{formatDate(d.as_of_date)}</td>
                    <td>
                      <StatusBadge status={d.status} />
                      {d.error_message && <p className="mt-1 max-w-xs text-xs text-loss">{d.error_message}</p>}
                    </td>
                    <td className="num text-right">{d.page_count ?? "—"}</td>
                    <td className="num text-right">{d.chunk_count}</td>
                    <td><div className="flex flex-wrap gap-1"><Badge>{d.visibility === "shared" ? "Shared" : "Private"}</Badge><SyntheticBadge show={d.is_synthetic} /></div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

function UploadForm() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [docType, setDocType] = useState("factsheet");
  const [fundId, setFundId] = useState<number | null>(null);
  const [asOf, setAsOf] = useState("");
  const [visibility, setVisibility] = useState("private");
  const [localError, setLocalError] = useState<string | null>(null);
  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.append("file", file as File);
      form.append("doc_type", docType);
      form.append("visibility", visibility);
      if (title.trim()) form.append("title", title.trim());
      if (fundId) form.append("fund_id", String(fundId));
      if (asOf) form.append("as_of_date", asOf);
      return api.upload<DocumentInfo>("/documents", form);
    },
    onSuccess: () => {
      setFile(null);
      setTitle("");
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    setLocalError(null);
    if (!file) return setLocalError("Choose a file to upload.");
    if (!/\.(pdf|txt|md)$/i.test(file.name)) return setLocalError("Upload a PDF, .txt or .md file.");
    if (file.size > MAX_MB * 1024 * 1024) return setLocalError(`Files are limited to ${MAX_MB} MB.`);
    upload.mutate();
  }

  return (
    <Card title="Upload a document" subtitle="PDF (with a text layer), .txt or .md, up to 15 MB. Indexing runs in the background.">
      <form onSubmit={submit} className="space-y-3" noValidate>
        <Field label="File">{(id) => <input id={id} type="file" accept=".pdf,.txt,.md,application/pdf,text/plain" className="text-sm" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />}</Field>
        <Field label="Title (optional)">{(id) => <input id={id} className="field-input" maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />}</Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Document type">
            {(id) => (
              <select id={id} className="field-input" value={docType} onChange={(e) => setDocType(e.target.value)}>
                <option value="factsheet">Factsheet</option><option value="annual_report">Annual report</option>
                <option value="sid">Scheme information document</option><option value="research">Research</option><option value="other">Other</option>
              </select>
            )}
          </Field>
          <Field label="Reporting date (optional)">{(id) => <input id={id} type="date" className="field-input" value={asOf} onChange={(e) => setAsOf(e.target.value)} />}</Field>
        </div>
        <Field label="Fund this document describes (optional)" hint="Linking a fund lets the assistant match questions about that fund.">
          {(id) => <FundSelect id={id} value={fundId} onChange={setFundId} includeEmpty emptyLabel="Not about a specific fund" />}
        </Field>
        {user?.role === "admin" && (
          <Field label="Visibility">
            {(id) => (
              <select id={id} className="field-input" value={visibility} onChange={(e) => setVisibility(e.target.value)}>
                <option value="private">Private to me</option><option value="shared">Shared library (all users)</option>
              </select>
            )}
          </Field>
        )}
        {(localError || upload.error) && <Notice tone="loss">{localError ?? errorMessage(upload.error)}</Notice>}
        {upload.isSuccess && <Notice>Uploaded. Indexing has started; the status updates below.</Notice>}
        <Button type="submit" busy={upload.isPending}>Upload and index</Button>
      </form>
    </Card>
  );
}

function SearchPanel() {
  const [query, setQuery] = useState("");
  const search = useMutation({ mutationFn: (q: string) => api.post<{ results: SearchResult[]; evidence_found: boolean }>("/documents/search", { query: q, top_k: 8 }) });
  return (
    <Card title="Search documents" subtitle="Hybrid search: meaning (embeddings) plus exact words, numbers and scheme codes.">
      <form onSubmit={(e) => { e.preventDefault(); if (query.trim().length >= 2) search.mutate(query.trim()); }} className="flex gap-2">
        <label htmlFor="doc-search" className="sr-only">Search text</label>
        <input id="doc-search" className="field-input" placeholder="e.g. exit load FS-DB-007" value={query} onChange={(e) => setQuery(e.target.value)} />
        <Button type="submit" busy={search.isPending}>Search</Button>
      </form>
      {search.error && <div className="mt-3"><ErrorState error={search.error} /></div>}
      {search.data && (
        <div className="mt-4 space-y-3">
          {!search.data.evidence_found && <Notice tone="caution">No passage is a strong match; the closest candidates are listed for inspection.</Notice>}
          {search.data.results.length === 0 && <p className="text-muted">Nothing matched.</p>}
          {search.data.results.map((r) => (
            <div key={r.chunk_id} className="rounded-md border border-ink-700 p-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Link to={`/documents/${r.document_id}`} className="font-medium hover:text-ledger">{r.document_title}</Link>
                <span className="text-faint">p. {r.page_start}{r.page_end !== r.page_start ? `–${r.page_end}` : ""}{r.section ? ` · ${r.section}` : ""}</span>
                {r.supported ? <Badge tone="gain">Strong match</Badge> : <Badge>Weak match</Badge>}
              </div>
              <p className="mt-2 line-clamp-4 whitespace-pre-wrap text-sm text-muted">{r.content}</p>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
