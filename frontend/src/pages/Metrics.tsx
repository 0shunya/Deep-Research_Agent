import { useQuery } from "@tanstack/react-query";
import { Fragment } from "react";
import { ErrorBox, PageHeader, Panel, Spinner, Stat } from "../components/ui";
import { api } from "../lib/api";
import { formatSeconds, formatTokens, formatUsd } from "../lib/format";

const FEATURES = ["plan", "research", "verify", "write"];

function KeyValues({ rows, mono = false }: { rows: [string, number][]; mono?: boolean }) {
  if (rows.length === 0) return <p className="text-ink-3">None.</p>;
  return (
    <dl className="grid grid-cols-[1fr_auto] gap-y-1">
      {rows.map(([k, v]) => (
        <Fragment key={k}>
          <dt className={mono ? "font-mono text-xs" : undefined}>{k}</dt>
          <dd className="num">{v}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

/** The cost dashboard (milestone M6): everything here is computed from model_calls, runs and reports. */
export default function Metrics() {
  const metrics = useQuery({ queryKey: ["metrics"], queryFn: api.metrics, refetchInterval: 15_000 });
  if (metrics.isPending) return <Spinner label="Loading metrics" />;
  if (metrics.isError) return <ErrorBox error={metrics.error} onRetry={() => metrics.refetch()} />;
  const m = metrics.data;
  const share = m.cited_supported_share;
  const features = [...FEATURES.filter((f) => m.by_feature[f]), ...Object.keys(m.by_feature).filter((f) => !FEATURES.includes(f))];
  return (
    <>
      <PageHeader title="Cost & quality"
        description={`From this server's own logs. Cost is list price from real token counts ($${m.pricing.input_per_mtok}/M input, $${m.pricing.output_per_mtok}/M output); free-tier calls are billed $0.`} />
      <Panel bodyClassName="grid grid-cols-2 divide-x divide-line md:grid-cols-5">
        <Stat label="Completed runs" value={m.completed_runs} hint={`${m.model_calls} model calls`} />
        <Stat label="Avg cost / report" value={formatUsd(m.avg_cost_usd)} hint={`${formatTokens(m.avg_tokens)} tokens`} />
        <Stat label="Run time p50" value={formatSeconds(m.p50_wall_seconds)} hint={`p95 ${formatSeconds(m.p95_wall_seconds)}`} />
        <Stat label="Cited claims" value={m.cited_claims} hint="across all reports" />
        <Stat label="Cited & supported" value={share === null ? "—" : `${Math.round(share * 100)}%`}
          hint="2+ independent origins" tone={share !== null && share < 0.5 ? "warn" : "ok"} />
      </Panel>

      <Panel title="Model calls by stage" className="mt-5" bodyClassName="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[13px]">
            <thead className="border-b border-line bg-canvas text-xs text-ink-2">
              <tr>
                <th className="px-4 py-2 font-medium">Stage</th>
                <th className="px-3 py-2 text-right font-medium">Calls</th>
                <th className="px-3 py-2 text-right font-medium">Errors</th>
                <th className="px-3 py-2 text-right font-medium">Input tokens</th>
                <th className="px-3 py-2 text-right font-medium">Output tokens</th>
                <th className="px-3 py-2 text-right font-medium">Cost</th>
                <th className="px-3 py-2 text-right font-medium">p50 latency</th>
                <th className="px-4 py-2 text-right font-medium">p95 latency</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {features.length === 0 && <tr><td colSpan={8} className="px-4 py-6 text-center text-ink-3">No model calls yet.</td></tr>}
              {features.map((f) => {
                const row = m.by_feature[f]!;
                return (
                  <tr key={f}>
                    <td className="px-4 py-2 font-medium capitalize">{f}</td>
                    <td className="num px-3 py-2 text-right">{row.calls}</td>
                    <td className="num px-3 py-2 text-right">{row.errors || "—"}</td>
                    <td className="num px-3 py-2 text-right">{formatTokens(row.input_tokens)}</td>
                    <td className="num px-3 py-2 text-right">{formatTokens(row.output_tokens)}</td>
                    <td className="num px-3 py-2 text-right">{formatUsd(row.cost_usd)}</td>
                    <td className="num px-3 py-2 text-right">{row.p50_latency_ms === null ? "—" : `${Math.round(row.p50_latency_ms)} ms`}</td>
                    <td className="num px-4 py-2 text-right">{row.p95_latency_ms === null ? "—" : `${Math.round(row.p95_latency_ms)} ms`}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="mt-5 grid gap-5 md:grid-cols-3">
        <Panel title="Errors by category" bodyClassName="p-4 text-[13px]">
          <KeyValues rows={Object.entries(m.errors)} mono />
        </Panel>
        <Panel title="Calls by model" bodyClassName="p-4 text-[13px]">
          <KeyValues rows={Object.entries(m.by_model)} mono />
        </Panel>
        <Panel title="Runs and tools" bodyClassName="p-4 text-[13px]">
          <KeyValues rows={[...Object.entries(m.runs).map(([k, v]) => [k.replace("_", " "), v] as [string, number]),
            ...Object.entries(m.tool_calls).map(([k, v]) => [`${k} calls`, v] as [string, number])]} />
        </Panel>
      </div>
    </>
  );
}
