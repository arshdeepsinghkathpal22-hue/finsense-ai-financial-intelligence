// Response shapes of the FinSense API (see docs/openapi.json and docs/api-samples).

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: "user" | "admin";
  preferences: { risk_free_rate?: number; default_period?: Period; risk_profile?: RiskProfile };
  created_at: string;
}

export type Period = "1y" | "3y" | "5y" | "max";
export type RiskProfile = "conservative" | "moderate" | "aggressive";

export interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Freshness {
  latest_observation: string | null;
  age_days: number | null;
  is_stale: boolean;
}

export interface MetricValue {
  value: number | null;
  unit: string;
  unavailable_reason?: string;
  confidence?: number;
  peak_date?: string | null;
  trough_date?: string | null;
  recovery_date?: string | null;
  observations?: number;
}

export type Metrics = Record<string, MetricValue>;

export interface Assumptions {
  frequency: string;
  periods_per_year: number;
  risk_free_rate_annual: number;
  return_basis: string;
  return_basis_note: string;
  var_method: string;
  sharpe_method: string;
  sortino_method: string;
  rebalancing?: string;
}

export interface Point {
  date: string;
  value: number;
}

export interface FundSummary {
  id: number;
  scheme_code: string;
  name: string;
  amc: string | null;
  category: string;
  asset_class: string;
  plan: string | null;
  option: string | null;
  risk_label: string | null;
  is_synthetic: boolean;
  source: string;
  return_basis: string;
  expense_ratio_pct: number | null;
  benchmark: { id: number; name: string } | null;
  latest_nav: number | null;
  latest_nav_date: string | null;
  freshness: Freshness;
  return_1y: number | null;
  aum: { value_crore: number; as_of: string } | null;
}

export interface TrailingReturn {
  period: string;
  value: number | null;
  annualised: boolean;
  start_date?: string;
  unavailable_reason?: string;
}

export interface FundDetail extends FundSummary {
  launch_date: string | null;
  trailing_returns: TrailingReturn[];
  history: { start: string; end: string; observations: number } | null;
  holdings_dates: string[];
  has_sip_data: boolean;
  data_notes: string[];
}

export interface RiskSeries {
  nav: Point[];
  rebased: Point[];
  drawdown: Point[];
  benchmark_rebased?: Point[];
  rolling_sharpe: Point[];
  rolling_volatility: Point[];
  rolling_beta?: Point[];
  rolling_window_days: number;
  return_distribution: { bin_start: number; bin_end: number; count: number }[];
}

export interface FundRiskReport {
  fund: { id: number; name: string; scheme_code: string; is_synthetic: boolean; source: string };
  benchmark: { id: number; name: string; return_basis: string; is_synthetic: boolean } | null;
  period: { label: string; start: string; end: string; history_covers_period?: boolean; note?: string };
  freshness: Freshness;
  assumptions: Assumptions;
  metrics: Metrics;
  series?: RiskSeries;
}

export interface PortfolioHolding {
  fund_id: number;
  scheme_code: string;
  name: string;
  category: string;
  asset_class: string;
  is_synthetic: boolean;
  initial_weight: number;
  current_weight: number;
  current_value: number;
  risk_contribution: number | null;
}

export interface PortfolioReport {
  portfolio: { id: string; name: string; initial_value: number; contains_synthetic_data: boolean };
  period: { label: string; start: string; end: string };
  freshness: Freshness;
  benchmark: { id: number; name: string; return_basis: string } | null;
  assumptions: Assumptions;
  alignment: { dates_in_union: number; dates_aligned: number; dates_dropped: number };
  value: { start: number; end: number; currency: string; as_of: string };
  metrics: Metrics;
  allocation: {
    holdings: PortfolioHolding[];
    by_asset_class: { asset_class: string; weight: number }[];
    herfindahl_index: number;
    effective_number_of_holdings: number;
    diversification_ratio: number | null;
    largest_weight: number;
  };
  correlation: { labels: string[]; matrix: number[][] };
  series: { value: Point[]; drawdown: Point[]; benchmark_scaled?: Point[] };
}

export interface Portfolio {
  id: string;
  name: string;
  description: string;
  initial_value: number;
  start_date: string | null;
  benchmark_id: number | null;
  created_at: string;
  updated_at: string;
  assets: { fund_id: number; scheme_code: string; name: string; is_synthetic: boolean; weight_pct: number }[];
}

export interface AllocationStats {
  weights: number[];
  expected_return: number;
  volatility: number;
  sharpe: number | null;
  herfindahl_index: number;
}

export interface OptimisationResult {
  assets: { fund_id: number; scheme_code: string; name: string; is_synthetic: boolean; expected_return: number; volatility: number }[];
  objective: string;
  risk_aversion: number | null;
  constraints: { min_weight: number; max_weight: number; long_only: boolean; sum_to_one: boolean };
  estimation: {
    lookback: string;
    start: string;
    end: string;
    observations: number;
    shrinkage_applied: boolean;
    shrinkage_intensity: number | null;
    notes: string[];
    risk_free_rate: number;
  };
  solver: { method: string; iterations: number; converged: boolean };
  comparison: { optimised: AllocationStats; equal_weight: AllocationStats; current?: AllocationStats };
  frontier: { expected_return: number; volatility: number; sharpe: number | null; weights: number[] }[];
  disclaimer: string;
}

export interface Holding {
  name: string;
  sector: string;
  asset_type: string;
  weight_pct: number;
}

export interface HoldingsResponse {
  fund_id: number;
  as_of: string | null;
  is_synthetic?: boolean;
  holdings: Holding[];
  sectors: { sector: string; weight_pct: number }[];
  concentration?: {
    top10_weight_pct: number;
    herfindahl_index: number | null;
    effective_number_of_holdings: number | null;
    note: string;
  };
  total_weight_pct?: number;
  note?: string;
}

export interface Benchmark {
  id: number;
  code: string;
  name: string;
  return_basis: string;
  is_synthetic: boolean;
}

export interface Source {
  marker: string;
  cited: boolean;
  chunk_id: string;
  document_id: string;
  document_title: string;
  filename: string;
  doc_type: string;
  as_of_date: string | null;
  page_start: number;
  page_end: number;
  section: string | null;
  excerpt: string;
  is_synthetic: boolean;
  flags: string[];
}

export interface CalculationOut {
  marker: string;
  title: string;
  lines: string[];
  tool?: string;
}

export interface AssistantAnswer {
  conversation_id: string;
  answer: string;
  mode: "llm" | "extractive" | "analytics_only" | "insufficient_evidence";
  model: string | null;
  planner: string;
  sources: Source[];
  calculations: CalculationOut[];
  conflicts: { fund_id: number; periods: { as_of_date: string; document: string }[]; note: string }[];
  warnings: string[];
  limitations: string[];
  funds: { id: number; scheme_code: string; name: string }[];
  retrieval: { query: string; is_follow_up: boolean; candidates: number; selected: number };
  latency_ms: number;
}

export interface DocumentInfo {
  id: string;
  title: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  doc_type: string;
  visibility: "private" | "shared";
  is_owner: boolean;
  can_manage: boolean;
  is_synthetic: boolean;
  fund: { id: number; scheme_code: string; name: string } | null;
  as_of_date: string | null;
  status: "pending" | "processing" | "indexed" | "failed" | "needs_ocr";
  error_message: string | null;
  page_count: number | null;
  chunk_count: number;
  indexed_at: string | null;
  created_at: string;
}

export interface Chunk {
  id: string;
  index: number;
  page_start: number;
  page_end: number;
  section: string | null;
  content: string;
  is_table: boolean;
  word_count: number;
  flags: string[];
}

export interface SystemStatus {
  app: { name: string; environment: string };
  llm: { configured: boolean; provider: string; model: string | null; mode_without_llm: string };
  embeddings: { provider: string; model: string; dimensions: number };
  reranker: string;
  live_data: { amfi_enabled: boolean };
  email: { password_reset_available: boolean };
  conventions: { risk_free_rate: number; trading_days_per_year: number; stale_after_days: number };
  data: { synthetic_funds: number; real_funds: number; latest_nav_date: string | null; documents: number };
  notice: string | null;
}

export interface ImportSummary {
  id: string;
  kind: string;
  filename: string;
  status: string;
  rows_total: number;
  rows_inserted: number;
  rows_updated: number;
  rows_unchanged: number;
  rows_rejected: number;
  warnings: string[];
  rejected_rows?: { line: number; reason: string; values: Record<string, string> }[];
  started_at: string | null;
  finished_at: string | null;
}
