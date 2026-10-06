import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import type { Chunk, DocumentInfo } from "../api/types";
import { Badge, Button, Card, ConfirmDialog, ErrorState, Loading, Notice, PageHeader, SyntheticBadge } from "../components/ui";
import { shouldPollDocuments } from "../lib/citations";
import { formatDate } from "../lib/format";
import { StatusBadge } from "./Documents";

export default function DocumentDetailPage() {
  const { documentId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [confirm, setConfirm] = useState(false);
  const doc = useQuery({
    queryKey: ["document", documentId],
    queryFn: () => api.get<DocumentInfo>(`/documents/${documentId}`),
    refetchInterval: (query) => (shouldPollDocuments([query.state.data?.status ?? ""]) ? 2000 : false),
  });
  const chunks = useQuery({
    queryKey: ["chunks", documentId, page, doc.data?.indexed_at],
    queryFn: () => api.get<{ items: Chunk[]; total: number; page_size: number }>(`/documents/${documentId}/chunks?page=${page}&page_size=10`),
    enabled: doc.data?.status === "indexed",
    placeholderData: keepPreviousData,
  });
  const reindex = useMutation({
    mutationFn: () => api.post(`/documents/${documentId}/reindex`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["document", documentId] }),
  });
  const remove = useMutation({
    mutationFn: () => api.delete(`/documents/${documentId}`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["documents"] });
      navigate("/documents");
    },
  });

  if (doc.isLoading) return <Loading />;
  if (doc.error) return <ErrorState error={doc.error} />;
  const d = doc.data!;
  const pages = chunks.data ? Math.max(1, Math.ceil(chunks.data.total / chunks.data.page_size)) : 1;

  return (
    <>
      <PageHeader
        title={d.title}
        description={`${d.filename} · ${(d.size_bytes / 1024).toFixed(0)} KB · uploaded ${formatDate(d.created_at)}`}
        actions={
          <>
            <a href={`/api/v1/documents/${d.id}/file`} className="rounded-md border border-ink-600 px-3.5 py-2 text-sm hover:border-ledger/70">Download original</a>
            {d.can_manage && <Button variant="secondary" busy={reindex.isPending} onClick={() => reindex.mutate()}>Re-index</Button>}
            {d.can_manage && <Button variant="danger" onClick={() => setConfirm(true)}>Delete</Button>}
          </>
        }
      />
      <ConfirmDialog open={confirm} title="Delete this document?" message="The file and all its indexed passages will be removed."
        confirmLabel="Delete document" busy={remove.isPending} onConfirm={() => remove.mutate()} onCancel={() => setConfirm(false)} />
      <div className="mb-5 flex flex-wrap items-center gap-2">
        <StatusBadge status={d.status} />
        <Badge>{d.visibility === "shared" ? "Shared library" : "Private"}</Badge>
        <SyntheticBadge show={d.is_synthetic} />
        {d.fund && <Badge tone="info">{d.fund.scheme_code}</Badge>}
        {d.as_of_date && <span className="text-sm text-muted">Reporting date {formatDate(d.as_of_date)}</span>}
        {d.page_count !== null && <span className="text-sm text-muted">{d.page_count} pages · {d.chunk_count} passages</span>}
      </div>
      {d.error_message && <Notice tone={d.status === "needs_ocr" ? "caution" : "loss"}>{d.error_message}</Notice>}
      {reindex.error && <ErrorState error={reindex.error} />}
      {remove.error && <ErrorState error={remove.error} />}

      {d.status === "indexed" && (
        <Card title="Indexed passages" subtitle="What the assistant can retrieve from this document, with page numbers and sections.">
          {chunks.isLoading && <Loading />}
          <ol className="space-y-3">
            {chunks.data?.items.map((c) => (
              <li key={c.id} className="rounded-md border border-ink-700 p-3">
                <div className="flex flex-wrap items-center gap-2 text-xs text-faint">
                  <span>Passage {c.index + 1}</span>
                  <span>Page {c.page_start}{c.page_end !== c.page_start ? `–${c.page_end}` : ""}</span>
                  {c.section && <span>· {c.section}</span>}
                  {c.is_table && <Badge tone="info">Table</Badge>}
                  {c.flags.includes("instruction_like_text") && <Badge tone="caution">Instruction-like text</Badge>}
                </div>
                <p className="mt-2 whitespace-pre-wrap text-sm">{c.content}</p>
              </li>
            ))}
          </ol>
          {pages > 1 && (
            <div className="mt-4 flex items-center gap-3">
              <Button variant="secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</Button>
              <span className="text-sm text-muted">Page {page} of {pages}</span>
              <Button variant="secondary" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</Button>
            </div>
          )}
        </Card>
      )}
    </>
  );
}
