# RAG retrieval evaluation report

Generated 2026-10-05 20:48 UTC by `python -m app.cli rag-eval` (question set: `sample_data/eval/rag_eval_set.json`; corpus: the synthetic sample documents plus one private note owned by a temporary evaluation user).

Configuration: embeddings `wordllama/l2_supercat-256` (256 dims), chunks ~180 words with 30-word overlap, 30 candidates per retriever, RRF k=60 (vector weight 0.3, lexical weight 1.0), reranker `none`, top_k 6.

Metric definitions: recall@k = share of the expected evidence snippets found in the top k passages; precision@k = share of the top k passages containing an expected snippet; MRR = mean reciprocal rank of the first relevant passage; nDCG@5 uses binary relevance; evidence_recall = expected snippets that survived the evidence gate into the answer context; abstention accuracy = no-evidence questions that were correctly declined; false abstention rate = answerable questions for which nothing passed the gate. Questions with no expected evidence are excluded from ranking metrics.

| Metric (all questions) | hybrid | vector | lexical |
|---|---|---|---|
| recall@1 | 0.606 | 0.424 | 0.697 |
| recall@3 | 0.894 | 0.561 | 0.924 |
| recall@5 | 0.970 | 0.651 | 0.939 |
| precision@1 | 0.636 | 0.424 | 0.758 |
| precision@3 | 0.364 | 0.212 | 0.384 |
| precision@5 | 0.242 | 0.151 | 0.242 |
| mrr | 0.788 | 0.556 | 0.849 |
| ndcg@5 | 0.821 | 0.548 | 0.852 |
| evidence_recall | 0.894 | 0.227 | 0.864 |
| false_abstention_rate | 0.091 | 0.515 | 0.091 |
| abstention_accuracy | 1.000 | 1.000 | 1.000 |
| mean_latency_ms | 12.5 | 8.2 | 10.7 |

| Hybrid by split | questions | recall@5 | mrr | evidence_recall | abstention_accuracy |
|---|---|---|---|---|---|
| dev | 31 | 0.96 | 0.7867 | 0.9 | 1.0 |
| holdout | 10 | 1.0 | 0.7917 | 0.875 | 1.0 |

Hybrid: conflict detection rate 1.0, extractive answers containing the expected figure 0.8788, invalid citations 0, permission leaks 0.

Recall@5 by category (hybrid): conflicting_periods 1.0, exact_keyword 1.0, exact_scheme_name 1.0, financial_figure 1.0, follow_up 1.0, multi_chunk 1.0, multi_section 1.0, permission_owner 1.0, synonym 0.8333, table 1.0

Acceptance: PASSED {"recall@5": {"value": 0.9697, "minimum": 0.8}, "mrr": {"value": 0.7879, "minimum": 0.7}, "evidence_recall": {"value": 0.8939, "minimum": 0.8}, "abstention_accuracy": {"value": 1.0, "minimum": 0.8}}

Hybrid questions with misses:
- q08 (synonym): Is there a penalty if I withdraw early from the dynamic bond fund? recall@5=0.0 evidence_recall=0.0 abstained=None
- q09 (synonym): How quickly could Northstar Midcap sell most of its holdings if it needed cash? recall@5=1.0 evidence_recall=0.0 abstained=None
- q11 (multi_chunk): Why has the risk of Aurora Bluechip increased, and what is its sector allocation now? recall@5=1.0 evidence_recall=0.5 abstained=None
- h09 (synonym): How often does the Meridian fund publish its NAV? recall@5=1.0 evidence_recall=0.0 abstained=None
