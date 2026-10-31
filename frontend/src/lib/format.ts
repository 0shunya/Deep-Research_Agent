import type { Plan, RunStatus, Stance, TaskStatus, Verdict } from "./types";

export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  planning: "Planning",
  awaiting_approval: "Plan ready",
  researching: "Researching",
  verifying: "Verifying",
  writing: "Writing",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const ACTIVE_STATUSES: RunStatus[] = ["planning", "researching", "verifying", "writing"];

export const TASK_STATUS_LABEL: Record<TaskStatus, string> = {
  pending: "Waiting",
  running: "Running",
  done: "Done",
  failed: "Failed",
  skipped: "Skipped",
  cancelled: "Cancelled",
};

export const VERDICT_LABEL: Record<Verdict, string> = {
  supported: "Supported",
  disputed: "Disputed",
  unverified: "Unverified",
  pending: "Not checked",
};

export const STANCE_LABEL: Record<Stance, string> = {
  supports: "Supports",
  contradicts: "Contradicts",
  insufficient: "Not enough",
  pending: "Not judged",
};

export function formatUsd(value: number | string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const n = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(n)) return "—";
  if (n === 0) return "$0";
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
}

export function formatTokens(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n >= 10_000 ? `${(n / 1000).toFixed(1)}k` : n.toLocaleString("en-US");
}

export function formatSeconds(s: number | null | undefined): string {
  if (s === null || s === undefined) return "—";
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
  return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { dateStyle: "medium" });
}

/** Client-side mirror of the server's plan checks, so the editor can explain problems before submit. */
export function planProblems(plan: Plan, maxSubQuestions: number): string[] {
  const problems: string[] = [];
  const ids = plan.sub_questions.map((q) => q.id);
  if (!plan.brief.trim()) problems.push("The brief is empty.");
  if (ids.length === 0) problems.push("Add at least one sub-question.");
  if (ids.length > maxSubQuestions) problems.push(`At most ${maxSubQuestions} sub-questions for this depth.`);
  plan.sub_questions.forEach((q) => {
    if (q.ask.trim().length < 3) problems.push(`${q.id} needs a question.`);
    q.depends_on.forEach((d) => { if (!ids.includes(d)) problems.push(`${q.id} depends on missing ${d}.`); });
  });
  // cycle check (Kahn)
  const remaining = new Map(plan.sub_questions.map((q) => [q.id, new Set(q.depends_on.filter((d) => ids.includes(d)))]));
  while (remaining.size) {
    const ready = [...remaining].filter(([, deps]) => deps.size === 0).map(([id]) => id);
    if (!ready.length) {
      problems.push(`Dependencies form a cycle: ${[...remaining.keys()].join(", ")}.`);
      break;
    }
    ready.forEach((id) => remaining.delete(id));
    remaining.forEach((deps) => ready.forEach((id) => deps.delete(id)));
  }
  return problems;
}

/** Renumber sub-questions q1..qN after a delete, rewriting dependencies to match. */
export function renumber(plan: Plan): Plan {
  const mapping = new Map(plan.sub_questions.map((q, i) => [q.id, `q${i + 1}`]));
  return {
    ...plan,
    sub_questions: plan.sub_questions.map((q) => ({
      ...q,
      id: mapping.get(q.id)!,
      depends_on: q.depends_on.filter((d) => mapping.has(d)).map((d) => mapping.get(d)!),
    })),
  };
}

export const DEPTH_LIMITS: Record<string, number> = { quick: 2, standard: 4, deep: 5 };
