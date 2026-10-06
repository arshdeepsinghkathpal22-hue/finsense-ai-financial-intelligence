# RAG retrieval evaluation

This document explains how FinSense AI's document retrieval was evaluated, what the numbers mean, how
the retrieval settings were chosen, and where the system still fails. All figures below come from
`python -m app.cli rag-eval` run against the shipped configuration; the raw output is in
[`backend/reports/rag_evaluation.json`](../backend/reports/rag_evaluation.json) and
[`.md`](../backend/reports/rag_evaluation.md).

## 1. What is evaluated

**Corpus.** The four synthetic sample documents (two Aurora Bluechip factsheets dated 30 Sep 2025 and
31 Mar 2026, the Meridian Dynamic Bond scheme information document, the Northstar Midcap annual review;
32 indexed passages) plus one *private* research note owned by a temporary evaluation user. The note is
used to test that retrieval never returns another user's documents.

**Questions.** `backend/sample_data/eval/rag_eval_set.json` contains 40 questions with the text snippets
that a correct passage must contain:

| Category | Questions | What it tests |
|---|---|---|
| exact_keyword | 5 | scheme codes, manager names, risk-class labels |
| exact_scheme_name | 2 | full fund names |
| financial_figure | 7 | specific numbers (expense ratio, AUM, turnover, limits) |
| table | 4 | facts that only appear inside tables |
| synonym | 6 | paraphrases ("penalty if I withdraw early" → exit load) |
| multi_section / multi_chunk | 2 + 2 | answers spread over several passages |
| conflicting_periods | 2 | facts that differ between the two factsheet dates |
| follow_up | 2 | "What is its minimum SIP amount?" after a question naming the fund |
| no_evidence | 7 | questions the documents cannot answer (must abstain) |
| permission | 1 | asked by the private note's owner (must find it) and by another user (must not) |

The permission question is scored twice (owner and outsider), giving 41 scored items: 33 answerable
items for ranking metrics and 8 that must be declined.

**Splits.** 30 questions form the *development* split (used to choose settings); 10 *hold-out*
questions (h01-h10) were written before tuning and never used to choose settings.

**Modes.** Every question is run three times: `hybrid` (shipped), `vector` (pgvector only) and
`lexical` (PostgreSQL full-text only). The evidence gate, context selection and extractive answer
generation run in every mode.

## 2. Metrics

* **Recall@k** - share of a question's expected snippets found in the top *k* retrieved passages.
* **Precision@k** - share of the top *k* passages that contain an expected snippet.
* **MRR** - mean reciprocal rank of the first relevant passage.
* **nDCG@5** - normalised discounted cumulative gain with binary relevance.
* **Evidence recall** - share of expected snippets that passed the evidence gate and reached the
  answer context (what the user actually sees cited).
* **Abstention accuracy** - share of no-evidence and outsider items for which nothing passed the gate.
* **False abstention rate** - share of answerable items for which nothing passed the gate.
* Also recorded: conflict detection, whether the extractive answer contains the expected figure,
  invalid citations, permission leaks, latency.

Acceptance thresholds (in the evaluation file): Recall@5 ≥ 0.80, MRR ≥ 0.70, evidence recall ≥ 0.80,
abstention accuracy ≥ 0.80, zero permission leaks, zero invalid citations.

## 3. Results

Configuration: WordLlama `l2_supercat` 256-d embeddings, ~180-word chunks with 30-word overlap,
30 candidates per retriever (lexical over-fetches 90 and re-ranks by IDF-weighted term coverage),
RRF k = 60 with vector weight 0.3 and lexical weight 1.0, no re-ranker, top-k 6.

| Metric (33 answerable / 8 decline items) | Hybrid | Vector only | Lexical only |
|---|---|---|---|
| Recall@1 | 0.606 | 0.424 | 0.697 |
| Recall@3 | 0.894 | 0.561 | 0.924 |
| Recall@5 | **0.970** | 0.651 | 0.939 |
| Precision@1 | 0.636 | 0.424 | 0.758 |
| Precision@3 | 0.364 | 0.212 | 0.384 |
| Precision@5 | 0.242 | 0.151 | 0.242 |
| MRR | 0.788 | 0.556 | **0.849** |
| nDCG@5 | 0.821 | 0.548 | **0.852** |
| Evidence recall | **0.894** | 0.227 | 0.864 |
| False abstention rate | 0.091 | 0.515 | 0.091 |
| Abstention accuracy | 1.000 | 1.000 | 1.000 |
| Mean latency (ms, local) | 12.5 | 8.2 | 10.7 |

| Hybrid by split | Items | Recall@5 | MRR | Evidence recall | Abstention accuracy |
|---|---|---|---|---|---|
| Development | 31 | 0.96 | 0.787 | 0.90 | 1.0 |
| Hold-out | 10 | 1.00 | 0.792 | 0.875 | 1.0 |

Hybrid answer-level checks: conflicting periods detected 2/2, extractive answers containing the
expected figure 0.88, invalid citations 0, permission leaks 0. **Acceptance: passed.**

Recall@5 / MRR by category:

| Category | Hybrid | Vector | Lexical |
|---|---|---|---|
| exact_keyword | 1.00 / 0.87 | 0.60 / 0.53 | 1.00 / 0.90 |
| exact_scheme_name | 1.00 / 0.75 | 0.50 / 0.53 | 1.00 / 1.00 |
| financial_figure | 1.00 / 0.86 | 0.71 / 0.67 | 1.00 / 0.90 |
| table | 1.00 / 0.69 | 0.50 / 0.35 | 1.00 / 0.83 |
| synonym | **0.83** / 0.65 | 0.83 / 0.65 | 0.67 / 0.56 |
| multi_section | 1.00 / 1.00 | 0.75 / 0.50 | 1.00 / 1.00 |
| multi_chunk | 1.00 / 1.00 | 0.50 / 0.57 | 1.00 / 1.00 |
| conflicting_periods | 1.00 / 0.50 | 0.00 / 0.10 | 1.00 / 1.00 |
| follow_up | 1.00 / 0.75 | 1.00 / 1.00 | 1.00 / 0.75 |
| permission (owner) | 1.00 / 1.00 | 1.00 / 0.25 | 1.00 / 1.00 |
| no_evidence + outsider | declined 8/8 | declined 8/8 | declined 8/8 |

**Reproducibility.** Ties between passages with equal scores are broken by passage content (not by
random database ids), so rebuilding the database gives identical results. Before this was fixed, the
same configuration scored between 0.92 and 0.97 Recall@5 depending on the ids assigned during
indexing - a reminder that differences of one or two questions on a set this small are noise.

## 4. Interpretation

* **Hybrid gives the best recall, lexical the best ranking.** Hybrid finds more of the needed passages
  in the top five (0.970 vs 0.939) and gets more of them past the evidence gate (0.894 vs 0.864); the gain
  comes from paraphrased questions, where vector search supplies passages that share no words with the
  question (synonym Recall@5 0.83 vs 0.67). But with this small static embedding model the vector
  ranking also pulls loosely related passages up, so the first relevant passage sits lower on average
  (MRR 0.788 vs 0.849). Because the assistant uses several gated passages (top 6), recall matters more
  than rank 1, which is why hybrid ships - but the evaluation does **not** show hybrid winning on every
  metric.
* **Vector-only retrieval is weak here** (Recall@5 0.65, evidence recall 0.23): 256-dimension static
  word embeddings cannot separate near-identical factsheet passages or match scheme codes.
* **Abstention is reliable on this set.** All 7 unanswerable questions and the outsider's permission
  question were declined in every mode; 9% of answerable questions were wrongly declined (all of them
  paraphrases).
* **Small, synthetic corpus.** 32 passages and 40 questions make each question worth ~3 percentage
  points. The results show the pipeline behaves as designed; they are not an estimate of performance on
  a large real-world library. Re-run the evaluation after adding your own documents and questions.

## 5. Remaining failures (hybrid)

| Question | Problem |
|---|---|
| q08 "Is there a penalty if I withdraw early from the dynamic bond fund?" | exit-load passage not retrieved in the top 5 |
| q09 "How quickly could Northstar Midcap sell most of its holdings if it needed cash?" | liquidity passage retrieved but not admitted by the evidence gate |
| h09 "How often does the Meridian fund publish its NAV?" | retrieved but gated out |
| q11 multi-part risk + sector question | only one of the two needed passages admitted |

All of these are vocabulary-mismatch problems. Likely fixes, in order of expected benefit: a stronger
embedding model (the optional `fastembed` provider, untested here), a cross-encoder re-ranker (optional
`RERANKER=fastembed`, untested), and a finance synonym list for query expansion.

## 6. How the settings were chosen

Two rounds of changes were made using **development questions only**:

1. *Structural fixes* after the first run failed acceptance (MRR 0.61, poor abstention): a context
   label (title, fund, scheme code) added to the full-text index, lexical over-fetch with IDF-weighted
   coverage re-ranking, a stop-list of request words ("what", "tell me"), the two-stage evidence gate,
   merging of tiny sections into the following paragraph, and fund-mention gating.
2. *A small grid* over three parameters on the development split (final, deterministic index):

| Vector weight | Coverage ratio | Similarity margin | Dev Recall@5 | Dev MRR | Dev nDCG@5 | Dev evidence recall |
|---|---|---|---|---|---|---|
| 0.3 | 0.5 | 0.08 / 0.12 | 0.96 | 0.785 | 0.820 | 0.88 |
| **0.3** | **0.6** | **0.08** | **0.96** | **0.787** | **0.822** | **0.90** |
| 0.3 | 0.6 | 0.12 | 0.96 | 0.785 | 0.820 | 0.86 |
| 0.5 | 0.5 | 0.08 / 0.12 | 0.94 | 0.748 | 0.784 | 0.92 |
| 0.5 | 0.6 | 0.08 / 0.12 | 0.94 | 0.768 | 0.793 | 0.90 |
| 1.0 | 0.5 | 0.08 / 0.12 | 0.84 | 0.700 | 0.717 | 0.78 |
| 1.0 | 0.6 | 0.08 | 0.84 | 0.727 | 0.731 | 0.76 |
| 1.0 | 0.6 | 0.12 | 0.84 | 0.720 | 0.726 | 0.76 |

Abstention accuracy was 1.0 and false abstention 0.08 for every combination. The row in bold (best
development Recall@5, MRR and nDCG) is the shipped configuration; the hold-out numbers in section 3 were
measured afterwards and never used for selection. Neighbouring settings differ by one or two questions.

## 7. Reproducing

```bash
cd backend
python -m app.cli rag-eval                    # writes reports/rag_evaluation.{json,md}
# or Admin → Retrieval quality → Run evaluation (stores the run in the database)
# Inspect a single query's scores at every stage:
#   Admin → Retrieval quality → Retrieval diagnostics
```

The evaluation creates two temporary users and one private document, and removes them afterwards.
