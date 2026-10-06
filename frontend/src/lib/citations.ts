// Splits assistant answers into text and citation markers ([S1], [T2]).

export type AnswerPart = { kind: "text"; text: string } | { kind: "cite"; marker: string };

const MARKER = /\[([ST]\d{1,3})\]/g;

/** Only markers that refer to supplied sources/calculations become chips. */
export function splitCitations(text: string, known: Set<string>): AnswerPart[] {
  const parts: AnswerPart[] = [];
  let last = 0;
  for (const match of text.matchAll(MARKER)) {
    const marker = match[1];
    const index = match.index ?? 0;
    if (!known.has(marker)) continue;
    if (index > last) parts.push({ kind: "text", text: text.slice(last, index) });
    parts.push({ kind: "cite", marker });
    last = index + match[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", text: text.slice(last) });
  return parts;
}

export const ACTIVE_DOCUMENT_STATUSES = new Set(["pending", "processing"]);

/** True while any document is still being indexed, so the list keeps polling. */
export function shouldPollDocuments(statuses: string[]): boolean {
  return statuses.some((status) => ACTIVE_DOCUMENT_STATUSES.has(status));
}
