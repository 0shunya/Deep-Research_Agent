import { useQuery } from "@tanstack/react-query";
import { History, Search } from "lucide-react";
import { Link } from "react-router";
import { EmptyState, ErrorBox, LinkButton, PageHeader, Panel, RunStatusBadge, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { formatDate, formatSeconds, formatTokens, formatUsd } from "../lib/format";

export default function Runs() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs, refetchInterval: 10_000 });
  return (
    <>
      <PageHeader title="History" description="Every run is stored with its plan, sources, claims, evidence and report versions."
        actions={<LinkButton to="/" variant="primary" icon={<Search className="size-4" />}>New research</LinkButton>} />
      <Panel bodyClassName="p-0">
        {runs.isPending && <Spinner label="Loading runs" />}
        {runs.isError && <div className="p-4"><ErrorBox error={runs.error} onRetry={() => runs.refetch()} /></div>}
        {runs.data?.length === 0 && <EmptyState icon={<History className="size-6" />} title="No runs yet">Start one from New research.</EmptyState>}
        {!!runs.data?.length && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[13px]">
              <thead className="border-b border-line bg-canvas text-xs text-ink-2">
                <tr>
                  <th className="px-4 py-2 font-medium">Question</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Depth</th>
                  <th className="px-3 py-2 text-right font-medium">Tokens</th>
                  <th className="px-3 py-2 text-right font-medium">Cost</th>
                  <th className="px-3 py-2 text-right font-medium">Time</th>
                  <th className="px-4 py-2 font-medium">Started</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {runs.data.map((r) => (
                  <tr key={r.id} className="hover:bg-sunken">
                    <td className="max-w-md px-4 py-2.5"><Link to={`/runs/${r.id}`} className="line-clamp-2 font-medium hover:text-accent">{r.question}</Link></td>
                    <td className="px-3 py-2.5"><RunStatusBadge status={r.status} /></td>
                    <td className="px-3 py-2.5 text-ink-2">{r.depth}</td>
                    <td className="num px-3 py-2.5 text-right">{formatTokens(r.spent_tokens)}</td>
                    <td className="num px-3 py-2.5 text-right">{formatUsd(r.cost_usd)}</td>
                    <td className="num px-3 py-2.5 text-right">{formatSeconds(r.wall_seconds)}</td>
                    <td className="whitespace-nowrap px-4 py-2.5 text-ink-2">{formatDate(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}
