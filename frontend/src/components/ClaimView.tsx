import { useQuery } from "@tanstack/react-query";
import { ExternalLink, X } from "lucide-react";
import { useEffect } from "react";
import { Link } from "react-router";
import { api } from "../lib/api";
import { shortDate } from "../lib/format";
import type { ClaimDetail, Evidence } from "../lib/types";
import { ErrorBox, Spinner, StanceLabel, VerdictBadge, cx } from "./ui";

function Highlighted({ context, quote }: { context: string; quote: string }) {
  const at = context.toLowerCase().indexOf(quote.trim().toLowerCase().slice(0, 60));
  if (at < 0) return <>{context}</>;
  const end = Math.min(context.length, at + quote.trim().length);
  return (
    <>
      {context.slice(0, at)}
      <mark className="rounded-sm bg-accent-soft px-0.5 text-ink">{context.slice(at, end)}</mark>
      {context.slice(end)}
    </>
  );
}

function EvidenceItem({ e }: { e: Evidence }) {
  return (
    <li className="rounded-md border border-line bg-surface p-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <StanceLabel stance={e.stance} />
        {e.score !== null && <span className="num text-xs text-ink-3">confidence {e.score.toFixed(2)}</span>}
        <span className="text-xs text-ink-3">{e.origin === "cross_check" ? "found by cross-check" : "cited by researcher"}</span>
        {!e.quote_found && <span className="text-xs font-semibold text-bad">quote not found in page</span>}
      </div>
      <blockquote className={cx("mt-2 border-l-2 pl-3 text-[13px]", e.quote_found ? "border-accent" : "border-bad/50 text-ink-3 line-through")}>
        {e.quote}
      </blockquote>
      {e.context && (
        <details className="mt-2 text-xs text-ink-2">
          <summary className="cursor-pointer select-none text-ink-3 hover:text-ink">Show in page context</summary>
          <p className="mt-1.5 rounded bg-canvas p-2 leading-relaxed"><Highlighted context={e.context} quote={e.quote} /></p>
        </details>
      )}
      {e.rationale && <p className="mt-2 text-xs text-ink-3">Verifier: {e.rationale}</p>}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-2 text-xs text-ink-2">
        <a href={e.source.url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 font-medium text-accent hover:underline">
          {e.source.domain} <ExternalLink className="size-3" aria-hidden />
        </a>
        <span>published {shortDate(e.source.published_at)}</span>
        <span>fetched {shortDate(e.source.fetched_at)}</span>
        <span className="font-mono text-[11px] text-ink-3" title="sha256 of the stored page text">#{e.source.content_hash.slice(0, 10)}</span>
      </div>
    </li>
  );
}

export function ClaimBody({ claim }: { claim: ClaimDetail }) {
  return (
    <div>
      <div className="flex items-center gap-2">
        <VerdictBadge verdict={claim.verdict} />
        <span className="text-xs text-ink-3">{claim.support_domains} independent origin(s) support it</span>
      </div>
      <p className="mt-2 text-[15px] font-medium leading-6">{claim.text}</p>
      {claim.verdict_reason && <p className="mt-1 text-[13px] text-ink-2">{claim.verdict_reason}</p>}
      {claim.task_question && <p className="mt-1 text-xs text-ink-3">Found while researching: {claim.task_question}</p>}
      <h3 className="mt-4 mb-2 text-xs font-semibold tracking-wide text-ink-2 uppercase">Evidence ({claim.evidence.length})</h3>
      <ul className="flex flex-col gap-2">
        {claim.evidence.map((e) => <EvidenceItem key={e.id} e={e} />)}
      </ul>
    </div>
  );
}

/** Side panel to audit one claim: verdict, every quote, its stance, and the stored page it came from. */
export function ClaimDrawer({ claimId, onClose }: { claimId: string | null; onClose: () => void }) {
  const query = useQuery({ queryKey: ["claim", claimId], queryFn: () => api.claim(claimId!), enabled: !!claimId });
  useEffect(() => {
    if (!claimId) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [claimId, onClose]);
  if (!claimId) return null;
  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label="Claim audit">
      <div className="absolute inset-0 bg-ink/25" onClick={onClose} />
      <aside className="absolute inset-y-0 right-0 flex w-full max-w-xl flex-col bg-canvas shadow-xl">
        <header className="flex h-12 items-center justify-between border-b border-line bg-surface px-4">
          <h2 className="font-semibold">Audit claim</h2>
          <div className="flex items-center gap-1">
            <Link to={`/claims/${claimId}`} className="rounded px-2 py-1 text-xs text-ink-2 hover:bg-sunken">Open page</Link>
            <button onClick={onClose} className="rounded p-1.5 text-ink-3 hover:bg-sunken hover:text-ink" aria-label="Close"><X className="size-4" /></button>
          </div>
        </header>
        <div className="flex-1 overflow-y-auto p-4">
          {query.isPending && <Spinner label="Loading claim" />}
          {query.isError && <ErrorBox error={query.error} onRetry={() => query.refetch()} />}
          {query.data && <ClaimBody claim={query.data} />}
        </div>
      </aside>
    </div>
  );
}
