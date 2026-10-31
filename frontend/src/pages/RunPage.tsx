import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Loader2 } from "lucide-react";
import { useCallback, useState } from "react";
import { useParams } from "react-router";
import { ClaimDrawer } from "../components/ClaimView";
import PlanEditor from "../components/PlanEditor";
import ReportView from "../components/ReportView";
import { useToast } from "../components/toast";
import { Button, ErrorBox, Panel, RunStatusBadge, Spinner, Stat, TaskStatusLabel, cx } from "../components/ui";
import { ApiError, api } from "../lib/api";
import { ACTIVE_STATUSES, formatSeconds, formatTokens, formatUsd } from "../lib/format";
import { describeEvent, useRunEvents } from "../lib/useRunEvents";
import type { Plan, RunDetail } from "../lib/types";

function Progress({ run, events, connected }: { run: RunDetail; events: ReturnType<typeof useRunEvents>["events"]; connected: boolean }) {
  const pct = Math.min(100, Math.round((run.spent_tokens / run.budget_tokens) * 100));
  const keyOf = new Map(run.tasks.map((t) => [t.id, t.key]));
  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_340px]">
      <div className="flex flex-col gap-5">
        <Panel bodyClassName="grid grid-cols-2 divide-x divide-line sm:grid-cols-4">
          <Stat label="Sources read" value={run.counts.sources} hint={`${run.counts.domains} domains`} />
          <Stat label="Claims" value={run.counts.claims} hint={`${run.counts.pending} not yet checked`} />
          <Stat label="Supported" value={run.counts.supported} tone="ok" hint={`${run.counts.disputed} disputed`} />
          <Stat label="Unverified" value={run.counts.unverified} tone={run.counts.unverified ? "warn" : undefined} hint="single origin or no support" />
        </Panel>
        <Panel title="Sub-questions" bodyClassName="p-0">
          <ul className="divide-y divide-line">
            {run.tasks.map((t) => (
              <li key={t.id} className="px-4 py-3">
                <div className="flex items-start gap-3">
                  <span className="mt-0.5 w-7 shrink-0 font-mono text-xs font-semibold text-accent">{t.key}</span>
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">{t.question}</p>
                    {t.depends_on.length > 0 && (
                      <p className="text-xs text-ink-3">after {t.depends_on.map((d) => keyOf.get(d) ?? "?").join(", ")}</p>
                    )}
                    {t.summary && <p className="mt-1 text-[13px] text-ink-2">{t.summary}</p>}
                    {t.error && <p className="mt-1 text-[13px] text-bad">{t.error}</p>}
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-0.5">
                    <TaskStatusLabel status={t.status} />
                    {t.attempts > 1 && <span className="text-[11px] text-ink-3">attempt {t.attempts}</span>}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
      <div className="flex flex-col gap-5">
        <Panel title="Budget" bodyClassName="p-4 text-[13px]">
          <div className="h-2 overflow-hidden rounded bg-sunken" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Token budget used">
            <div className={cx("h-full", pct > 90 ? "bg-bad" : pct > 70 ? "bg-warn" : "bg-accent")} style={{ width: `${pct}%` }} />
          </div>
          <p className="num mt-2 text-ink-2">{formatTokens(run.spent_tokens)} of {formatTokens(run.budget_tokens)} tokens · {formatUsd(run.cost_usd)}</p>
          <p className="mt-1 text-xs text-ink-3">{run.max_workers} parallel researchers · {run.search_calls} searches via {run.search_provider}</p>
          {run.stop_reason && <p className="mt-1 text-xs font-medium text-warn">Stopped early: {run.stop_reason}</p>}
        </Panel>
        <Panel title="Live activity" actions={ACTIVE_STATUSES.includes(run.status) &&
          <span className={cx("text-xs", connected ? "text-ok" : "text-ink-3")}>{connected ? "● live" : "connecting…"}</span>}
          bodyClassName="max-h-[420px] overflow-y-auto p-0">
          {events.length === 0 && <p className="px-4 py-6 text-center text-xs text-ink-3">Events appear here while the run is active.</p>}
          <ol className="divide-y divide-line text-[13px]">
            {[...events].reverse().map((e) => (
              <li key={e.id} className="px-4 py-1.5">
                <span className="num mr-2 text-[11px] text-ink-3">{new Date(e.at).toLocaleTimeString()}</span>{describeEvent(e)}
              </li>
            ))}
          </ol>
        </Panel>
      </div>
    </div>
  );
}

export default function RunPage() {
  const { id } = useParams();
  const client = useQueryClient();
  const toast = useToast();
  const [claimId, setClaimId] = useState<string | null>(null);
  const [tab, setTab] = useState<"report" | "progress">("report");
  const run = useQuery({
    queryKey: ["run", id], queryFn: () => api.run(id!), enabled: !!id,
    // The SSE stream drives refreshes; this slow poll only covers a dropped stream.
    refetchInterval: (q) => (q.state.data && ACTIVE_STATUSES.includes(q.state.data.status) ? 5000 : false),
  });
  const status = run.data?.status;
  const live = !!status && (ACTIVE_STATUSES.includes(status));
  const { events, connected } = useRunEvents(id, live);
  const hasReport = status === "completed" || status === "cancelled";
  const report = useQuery({ queryKey: ["report", id], queryFn: () => api.report(id!), enabled: !!id && hasReport, retry: false });

  const approve = useMutation({
    mutationFn: (plan: Plan) => api.approvePlan(id!, plan),
    onSuccess: (data) => { client.setQueryData(["run", id], data); setTab("progress"); },
    onError: (error) => toast.error(error.message),
  });
  const cancel = useMutation({
    mutationFn: () => api.cancel(id!),
    onSuccess: (data) => { client.setQueryData(["run", id], data); toast.success("Cancel requested — partial results are kept."); },
    onError: (error) => toast.error(error.message),
  });
  const closeClaim = useCallback(() => setClaimId(null), []);

  if (run.isPending) return <Spinner label="Loading run" />;
  if (run.isError) return <ErrorBox error={run.error} onRetry={() => run.refetch()} />;
  const r = run.data;
  const showReport = hasReport && tab === "report" && report.data;

  return (
    <>
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <RunStatusBadge status={r.status} />
            <span className="text-xs text-ink-3">{r.depth} · {formatSeconds(r.wall_seconds)} · {formatUsd(r.cost_usd)}</span>
          </div>
          <h1 className="mt-1.5 text-xl font-semibold tracking-tight">{r.question}</h1>
          {(r.approved_plan ?? r.plan)?.brief && <p className="mt-1 max-w-3xl text-ink-2">{(r.approved_plan ?? r.plan)!.brief}</p>}
        </div>
        {!["completed", "failed", "cancelled"].includes(r.status) && (
          <Button variant="danger" icon={<Ban className="size-4" />} loading={cancel.isPending}
            disabled={r.cancel_requested} onClick={() => cancel.mutate()}>
            {r.cancel_requested ? "Cancelling…" : "Cancel"}
          </Button>
        )}
      </div>

      {r.status === "failed" && (
        <div className="mb-5">
          <ErrorBox error={new Error(`${r.error_code ?? "error"}: ${r.error_message ?? "The run failed."}`)} />
        </div>
      )}

      {r.status === "planning" && (
        <Panel><p className="flex items-center justify-center gap-2 py-8 text-ink-2"><Loader2 className="size-4 animate-spin" /> The planner is splitting your question into sub-questions…</p></Panel>
      )}

      {r.status === "awaiting_approval" && r.plan && (
        <PlanEditor initial={r.plan} depth={r.depth} approving={approve.isPending} onApprove={(plan) => approve.mutate(plan)} />
      )}

      {r.tasks.length > 0 && (
        <>
          {hasReport && (
            <div className="mb-4 flex gap-1 border-b border-line" role="tablist">
              {(["report", "progress"] as const).map((t) => (
                <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                  className={cx("-mb-px border-b-2 px-3 py-2 text-sm font-medium capitalize",
                    tab === t ? "border-accent text-accent" : "border-transparent text-ink-2 hover:text-ink")}>
                  {t === "progress" ? "Research trail" : "Report"}
                </button>
              ))}
            </div>
          )}
          {hasReport && tab === "report" && report.isPending && <Spinner label="Loading report" />}
          {hasReport && tab === "report" && report.isError && !(report.error instanceof ApiError && report.error.code === "report_not_ready") &&
            <ErrorBox error={report.error} onRetry={() => report.refetch()} />}
          {hasReport && tab === "report" && report.isError && report.error instanceof ApiError && report.error.code === "report_not_ready" &&
            <Panel><p className="py-6 text-center text-ink-2">No report was written for this run.</p></Panel>}
          {showReport && <ReportView report={report.data} onOpenClaim={setClaimId} />}
          {(!hasReport || tab === "progress") && <Progress run={r} events={events} connected={connected} />}
        </>
      )}
      <ClaimDrawer claimId={claimId} onClose={closeClaim} />
    </>
  );
}
