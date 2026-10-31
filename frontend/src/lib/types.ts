export type RunStatus =
  | "planning" | "awaiting_approval" | "researching" | "verifying" | "writing" | "completed" | "failed" | "cancelled";
export type TaskStatus = "pending" | "running" | "done" | "failed" | "skipped" | "cancelled";
export type Verdict = "pending" | "supported" | "disputed" | "unverified";
export type Stance = "pending" | "supports" | "contradicts" | "insufficient";
export type Depth = "quick" | "standard" | "deep";

export interface SubQuestion {
  id: string;
  ask: string;
  why: string;
  depends_on: string[];
}

export interface Plan {
  brief: string;
  sub_questions: SubQuestion[];
  report: { max_words: number; must_cite: boolean };
}

export interface Task {
  id: string;
  key: string;
  question: string;
  why: string | null;
  depends_on: string[];
  status: TaskStatus;
  attempts: number;
  steps_used: number;
  summary: string | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface RunSummary {
  id: string;
  question: string;
  depth: Depth;
  status: RunStatus;
  budget_tokens: number;
  spent_tokens: number;
  cost_usd: string;
  created_at: string;
  finished_at: string | null;
  wall_seconds: number | null;
  stop_reason: string | null;
}

export interface RunDetail extends RunSummary {
  max_workers: number;
  search_provider: string;
  search_calls: number;
  error_code: string | null;
  error_message: string | null;
  cancel_requested: boolean;
  plan: Plan | null;
  approved_plan: Plan | null;
  tasks: Task[];
  counts: Record<"sources" | "domains" | "claims" | "supported" | "disputed" | "unverified" | "pending", number>;
}

export interface Source {
  id: string;
  url: string;
  domain: string;
  title: string | null;
  published_at: string | null;
  fetched_at: string;
  content_hash: string;
  search_rank: number | null;
}

export interface Claim {
  id: string;
  task_id: string;
  text: string;
  verdict: Verdict;
  verdict_reason: string | null;
  support_domains: number;
  group_key: string;
}

export interface Evidence {
  id: string;
  quote: string;
  quote_found: boolean;
  origin: "extracted" | "cross_check";
  stance: Stance;
  score: number | null;
  rationale: string | null;
  context: string | null;
  source: Source;
}

export interface ClaimDetail extends Claim {
  run_id: string;
  task_question: string | null;
  evidence: Evidence[];
}

export interface ReportSentence {
  idx: number;
  text: string;
  claim_ids: string[];
}

export interface Report {
  id: string;
  run_id: string;
  version: number;
  title: string;
  markdown: string;
  sections: { heading: string; sentences: ReportSentence[] }[];
  stats: Record<string, number | string>;
  created_at: string;
  claims: Record<string, Claim>;
  sources: Record<string, Source[]>;
}

export interface ProgressEvent {
  id: number;
  type: string;
  at: string;
  [key: string]: unknown;
}

export interface Health {
  status: string;
  version: string;
  database: string;
  model_configured: boolean;
  models: string[];
  search_provider: string;
  worker: string;
  access_code_required: boolean;
}

export interface FeatureMetrics {
  calls: number;
  errors: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
}

export interface Metrics {
  model_calls: number;
  by_feature: Record<string, FeatureMetrics>;
  errors: Record<string, number>;
  by_model: Record<string, number>;
  tool_calls: Record<string, number>;
  runs: Record<string, number>;
  completed_runs: number;
  avg_cost_usd: number | null;
  avg_tokens: number | null;
  p50_wall_seconds: number | null;
  p95_wall_seconds: number | null;
  cited_claims: number;
  cited_supported_share: number | null;
  pricing: { input_per_mtok: number; output_per_mtok: number };
}
