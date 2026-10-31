import type { ClaimDetail, Depth, Health, Metrics, Plan, Report, RunDetail, RunSummary, Source } from "./types";

/**
 * Base URL of the FastAPI backend. Empty in development (Vite proxies /api). In production set
 * VITE_API_BASE_URL to the deployed API origin. Only public values belong in VITE_* variables:
 * the Gemini and Tavily keys live on the server and never reach the browser.
 */
export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
const ACCESS_KEY = "deep-research.access-code";

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown> | null;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> | null = null) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function getAccessCode(): string | null {
  try {
    return localStorage.getItem(ACCESS_KEY);
  } catch {
    return null;
  }
}

export function setAccessCode(code: string | null) {
  try {
    if (code) localStorage.setItem(ACCESS_KEY, code);
    else localStorage.removeItem(ACCESS_KEY);
  } catch {
    /* storage unavailable: the code is simply not remembered */
  }
}

function headers(extra?: HeadersInit): Headers {
  const h = new Headers(extra);
  const code = getAccessCode();
  if (code) h.set("X-Access-Code", code);
  return h;
}

async function toError(res: Response): Promise<ApiError> {
  try {
    const body = await res.json();
    const err = body?.error;
    if (err?.code) return new ApiError(res.status, err.code, err.message ?? "Request failed.", err.details ?? null);
  } catch {
    /* non-JSON error body */
  }
  const fallback = res.status >= 500 ? "The server had a problem. Try again shortly." : `Request failed (${res.status}).`;
  return new ApiError(res.status, "http_error", fallback);
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers: headers(init.headers) });
  } catch {
    throw new ApiError(0, "network_error", "Cannot reach the server. Check your connection or the API URL.");
  }
  if (!res.ok) throw await toError(res);
  return (await res.json()) as T;
}

function post(body: unknown, extra: Record<string, string> = {}): RequestInit {
  return { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json", ...extra } };
}

/** The SSE URL. EventSource cannot send headers, so the access code travels as a query parameter. */
export function eventsUrl(runId: string): string {
  const code = getAccessCode();
  const query = code ? `?access_code=${encodeURIComponent(code)}` : "";
  return `${API_BASE}/api/runs/${runId}/events${query}`;
}

export interface CreateRunInput {
  question: string;
  depth: Depth;
  budget_tokens?: number;
  max_workers?: number;
}

export const api = {
  health: () => request<Health>("/api/health"),
  runs: () => request<RunSummary[]>("/api/runs"),
  run: (id: string) => request<RunDetail>(`/api/runs/${id}`),
  createRun: (input: CreateRunInput, idempotencyKey: string) =>
    request<RunDetail>("/api/runs", post(input, { "Idempotency-Key": idempotencyKey })),
  approvePlan: (id: string, plan: Plan) => request<RunDetail>(`/api/runs/${id}/plan/approve`, post({ plan })),
  cancel: (id: string) => request<RunDetail>(`/api/runs/${id}/cancel`, post({})),
  report: (id: string) => request<Report>(`/api/runs/${id}/report`),
  sources: (id: string) => request<Source[]>(`/api/runs/${id}/sources`),
  claim: (id: string) => request<ClaimDetail>(`/api/claims/${id}`),
  metrics: () => request<Metrics>("/api/metrics"),
};
