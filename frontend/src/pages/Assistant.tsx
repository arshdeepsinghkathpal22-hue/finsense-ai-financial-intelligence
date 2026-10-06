import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { AssistantAnswer, CalculationOut, DocumentInfo, Paged, Source } from "../api/types";
import { Badge, Button, ErrorState, Notice, PageHeader, SyntheticBadge } from "../components/ui";
import { splitCitations } from "../lib/citations";
import { formatDate } from "../lib/format";

const EXAMPLES = [
  "Why has the risk of Aurora Bluechip increased?",
  "What is the exit load of FS-DB-007?",
  "What does the latest Aurora factsheet say about sector concentration?",
  "Compare Aurora Bluechip and Northstar Midcap over 3 years",
  "Forecast Aurora Bluechip for next month",
  "Optimise my portfolio for a conservative profile",
];

const MODE_LABEL: Record<AssistantAnswer["mode"], { text: string; tone: "gain" | "info" | "caution" | "neutral" }> = {
  llm: { text: "Generated from evidence", tone: "gain" },
  extractive: { text: "Verbatim passages (no language model configured)", tone: "info" },
  analytics_only: { text: "FinSense calculations", tone: "info" },
  insufficient_evidence: { text: "Insufficient evidence", tone: "caution" },
};

interface Turn {
  question: string;
  answer?: AssistantAnswer;
}

interface StoredMessage {
  role: "user" | "assistant";
  content: string;
  payload: Partial<AssistantAnswer>;
}

export function AnswerText({ text, sources, calculations, onCite }: {
  text: string;
  sources: Source[];
  calculations: CalculationOut[];
  onCite: (marker: string) => void;
}) {
  const known = new Set([...sources.map((s) => s.marker), ...calculations.map((c) => c.marker)]);
  return (
    <div className="whitespace-pre-wrap leading-relaxed">
      {splitCitations(text, known).map((part, i) =>
        part.kind === "text" ? (
          <span key={i}>{part.text}</span>
        ) : (
          <button
            key={i}
            type="button"
            onClick={() => onCite(part.marker)}
            className={`mx-0.5 rounded border px-1 align-baseline text-xs ${part.marker.startsWith("S") ? "border-ledger/60 text-ledger" : "border-info/60 text-info"}`}
            aria-label={`Show ${part.marker.startsWith("S") ? "source" : "calculation"} ${part.marker}`}
          >
            {part.marker}
          </button>
        ),
      )}
    </div>
  );
}

function SourceCard({ source, active }: { source: Source; active: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (active) {
      setOpen(true);
      ref.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    }
  }, [active]);
  const pages = source.page_start === source.page_end ? `p. ${source.page_start}` : `pp. ${source.page_start}–${source.page_end}`;
  return (
    <li ref={ref} id={`src-${source.marker}`} className={`rounded-md border p-3 ${active ? "border-ledger" : "border-ink-700"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded border border-ledger/60 px-1 text-xs text-ledger">{source.marker}</span>
        <span className="font-medium">{source.document_title}</span>
      </div>
      <p className="mt-1 text-xs text-faint">
        {pages}{source.section ? ` · ${source.section}` : ""} · {source.doc_type}{source.as_of_date ? ` · as of ${formatDate(source.as_of_date)}` : ""}
        {!source.cited && " · retrieved, not cited"}
      </p>
      <div className="mt-1 flex flex-wrap gap-1">
        <SyntheticBadge show={source.is_synthetic} />
        {source.flags.includes("instruction_like_text") && <Badge tone="caution" title="This passage contains text that looks like instructions; it was treated only as data.">Instruction-like text</Badge>}
      </div>
      <button type="button" className="mt-2 text-xs text-ledger hover:underline" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        {open ? "Hide passage" : "Show passage"}
      </button>
      {open && <blockquote className="mt-2 whitespace-pre-wrap border-l-2 border-ink-600 pl-3 text-sm text-muted">{source.excerpt}</blockquote>}
    </li>
  );
}

function AnswerView({ answer }: { answer: AssistantAnswer }) {
  const [active, setActive] = useState<string | null>(null);
  const mode = MODE_LABEL[answer.mode];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={mode.tone}>{mode.text}</Badge>
        {answer.model && <span className="text-xs text-faint">{answer.model}</span>}
        <span className="text-xs text-faint">{answer.latency_ms} ms</span>
      </div>
      <AnswerText text={answer.answer} sources={answer.sources} calculations={answer.calculations} onCite={setActive} />
      {answer.conflicts.map((c) => (
        <Notice key={c.fund_id} tone="caution">
          {c.note} {c.periods.map((p) => `${p.document} (${formatDate(p.as_of_date)})`).join("; ")}.
        </Notice>
      ))}
      {answer.warnings.map((w) => <Notice key={w} tone="caution">{w}</Notice>)}
      {answer.calculations.length > 0 && (
        <details open className="rounded-md border border-ink-700 p-3">
          <summary className="cursor-pointer text-sm text-muted">Calculations ({answer.calculations.length})</summary>
          <ul className="mt-2 space-y-3">
            {answer.calculations.map((c) => (
              <li key={c.marker} id={`calc-${c.marker}`} className={`text-sm ${active === c.marker ? "rounded ring-1 ring-info" : ""}`}>
                <span className="mr-2 rounded border border-info/60 px-1 text-xs text-info">{c.marker}</span>
                <span className="font-medium">{c.title}</span>
                <ul className="mt-1 list-disc pl-5 text-muted">{c.lines.map((l) => <li key={l}>{l}</li>)}</ul>
              </li>
            ))}
          </ul>
        </details>
      )}
      {answer.sources.length > 0 && (
        <details open className="rounded-md border border-ink-700 p-3">
          <summary className="cursor-pointer text-sm text-muted">Evidence ({answer.sources.length} passages)</summary>
          <ul className="mt-2 space-y-2">
            {answer.sources.map((s) => <SourceCard key={s.marker} source={s} active={active === s.marker} />)}
          </ul>
        </details>
      )}
      {answer.limitations.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-faint">{answer.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
      )}
    </div>
  );
}

export default function AssistantPage() {
  const queryClient = useQueryClient();
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [docFilter, setDocFilter] = useState<string[]>([]);
  const conversations = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api.get<{ items: { id: string; title: string; updated_at: string }[] }>("/assistant/conversations"),
  });
  const documents = useQuery({ queryKey: ["documents"], queryFn: () => api.get<Paged<DocumentInfo>>("/documents?page_size=100") });
  const ask = useMutation({
    mutationFn: (q: string) =>
      api.post<AssistantAnswer>("/assistant/query", {
        question: q,
        conversation_id: conversationId,
        document_ids: docFilter.length ? docFilter : null,
      }),
    onSuccess: (answer, q) => {
      setConversationId(answer.conversation_id);
      setTurns((t) => [...t.slice(0, -1), { question: q, answer }]);
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
    onError: () => setTurns((t) => t.slice(0, -1)),
  });

  function submit(event?: FormEvent, text?: string) {
    event?.preventDefault();
    const q = (text ?? question).trim();
    if (q.length < 2 || ask.isPending) return;
    setTurns((t) => [...t, { question: q }]);
    setQuestion("");
    ask.mutate(q);
  }

  async function openConversation(id: string) {
    const data = await api.get<{ messages: StoredMessage[] }>(`/assistant/conversations/${id}`);
    const restored: Turn[] = [];
    for (const message of data.messages) {
      if (message.role === "user") restored.push({ question: message.content });
      else if (restored.length) {
        restored[restored.length - 1].answer = {
          answer: message.content, mode: "extractive", model: null, planner: "", sources: [], calculations: [], conflicts: [],
          warnings: [], limitations: [], funds: [], retrieval: { query: "", is_follow_up: false, candidates: 0, selected: 0 },
          latency_ms: 0, conversation_id: id, ...message.payload,
        } as AssistantAnswer;
      }
    }
    setConversationId(id);
    setTurns(restored);
  }

  async function deleteConversation(id: string) {
    await api.delete(`/assistant/conversations/${id}`);
    if (id === conversationId) {
      setConversationId(null);
      setTurns([]);
    }
    queryClient.invalidateQueries({ queryKey: ["conversations"] });
  }

  return (
    <>
      <PageHeader
        title="Research assistant"
        description="Ask about funds and your documents. Answers cite the passages and FinSense calculations they rely on, and say so when the evidence isn't there."
      />
      <div className="grid gap-6 lg:grid-cols-[16rem_1fr]">
        <aside className="space-y-3">
          <Button variant="secondary" className="w-full" onClick={() => { setConversationId(null); setTurns([]); }}>New conversation</Button>
          <ul className="space-y-1 text-sm">
            {conversations.data?.items.map((c) => (
              <li key={c.id} className={`group flex items-start gap-1 rounded-md px-2 py-1.5 ${c.id === conversationId ? "bg-ink-800" : "hover:bg-ink-850"}`}>
                <button type="button" className="flex-1 text-left" onClick={() => openConversation(c.id)}>
                  <span className="line-clamp-2">{c.title}</span>
                  <span className="block text-xs text-faint">{formatDate(c.updated_at)}</span>
                </button>
                <button type="button" className="text-faint opacity-60 hover:text-loss group-hover:opacity-100" aria-label={`Delete conversation ${c.title}`}
                  onClick={() => deleteConversation(c.id)}>×</button>
              </li>
            ))}
          </ul>
        </aside>

        <section className="min-w-0 space-y-5">
          {turns.length === 0 && (
            <div className="panel p-5">
              <h2 className="text-lg">Try one of these</h2>
              <div className="mt-3 flex flex-wrap gap-2">
                {EXAMPLES.map((e) => (
                  <button key={e} type="button" onClick={() => submit(undefined, e)}
                    className="rounded-md border border-ink-600 px-3 py-1.5 text-left text-sm hover:border-ledger/70">{e}</button>
                ))}
              </div>
            </div>
          )}
          {turns.map((turn, i) => (
            <article key={i} className="space-y-3">
              <p className="ml-auto max-w-2xl rounded-lg bg-ink-800 px-4 py-2.5 font-serif text-lg">{turn.question}</p>
              <div className="panel p-4">
                {turn.answer ? <AnswerView answer={turn.answer} /> : <p className="text-muted" role="status">Retrieving evidence and running calculations…</p>}
              </div>
            </article>
          ))}
          {ask.error && <ErrorState error={ask.error} />}

          <form onSubmit={submit} className="panel sticky bottom-3 space-y-2 p-3">
            <label htmlFor="question" className="sr-only">Your question</label>
            <textarea id="question" rows={2} maxLength={1000} className="field-input resize-y" placeholder="Ask about a fund, a factsheet or your portfolio…"
              value={question} onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }} />
            <div className="flex flex-wrap items-center justify-between gap-2">
              <details className="text-sm text-muted">
                <summary className="cursor-pointer">Limit to documents{docFilter.length ? ` (${docFilter.length})` : ""}</summary>
                <div className="mt-2 max-h-40 space-y-1 overflow-y-auto">
                  {documents.data?.items.filter((d) => d.status === "indexed").map((d) => (
                    <label key={d.id} className="flex items-center gap-2">
                      <input type="checkbox" checked={docFilter.includes(d.id)}
                        onChange={() => setDocFilter((f) => (f.includes(d.id) ? f.filter((x) => x !== d.id) : [...f, d.id]))} />
                      {d.title}
                    </label>
                  ))}
                </div>
              </details>
              <Button type="submit" busy={ask.isPending} disabled={question.trim().length < 2}>Ask</Button>
            </div>
          </form>
        </section>
      </div>
    </>
  );
}
