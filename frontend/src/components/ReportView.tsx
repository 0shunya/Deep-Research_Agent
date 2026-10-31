import { Download, ExternalLink } from "lucide-react";
import { useMemo } from "react";
import { shortDate } from "../lib/format";
import type { Report, Source, Verdict } from "../lib/types";
import { Button, Panel, cx } from "./ui";

const CHIP: Record<Verdict, string> = {
  supported: "border-ok/30 bg-ok-soft text-ok",
  disputed: "border-bad/30 bg-bad-soft text-bad",
  unverified: "border-warn/30 bg-warn-soft text-warn",
  pending: "border-line bg-sunken text-ink-3",
};

function download(report: Report) {
  const blob = new Blob([report.markdown], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `research-report-${report.run_id.slice(0, 8)}-v${report.version}.md`;
  a.click();
  URL.revokeObjectURL(url);
}

/** The report: every sentence carries chips for the claims it rests on; a chip opens the claim audit. */
export default function ReportView({ report, onOpenClaim }: { report: Report; onOpenClaim: (id: string) => void }) {
  const numbering = useMemo(() => {
    const order = new Map<string, number>();
    report.sections.forEach((s) => s.sentences.forEach((sent) => sent.claim_ids.forEach((id) => {
      if (!order.has(id)) order.set(id, order.size + 1);
    })));
    return order;
  }, [report]);

  const sources = useMemo(() => {
    const seen = new Map<string, Source>();
    Object.values(report.sources).flat().forEach((s) => seen.set(s.id, s));
    return [...seen.values()];
  }, [report]);

  const stats = report.stats;
  const fallback = stats.writer === "fallback";

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_280px]">
      <Panel title={report.title} actions={
        <Button size="sm" icon={<Download className="size-4" />} onClick={() => download(report)}>Markdown</Button>}>
        {fallback && (
          <p className="mb-4 rounded-md border border-warn/30 bg-warn-soft px-3 py-2 text-[13px] text-warn">
            Written without the model ({String(stats.fallback_reason ?? "fallback")}): the checked claims are listed as found.
          </p>
        )}
        <article className="flex flex-col gap-5">
          {report.sections.map((section) => (
            <section key={section.heading}>
              <h3 className="mb-1.5 text-[15px] font-semibold">{section.heading}</h3>
              <p className="leading-7 text-ink">
                {section.sentences.map((sent) => (
                  <span key={sent.idx}>
                    {sent.text}{" "}
                    {sent.claim_ids.map((id) => {
                      const claim = report.claims[id];
                      const verdict = claim?.verdict ?? "pending";
                      return (
                        <button key={id} type="button" onClick={() => onOpenClaim(id)}
                          title={claim ? `${verdict}: ${claim.text}` : "claim"}
                          aria-label={`Claim ${numbering.get(id)} (${verdict})`}
                          className={cx("num mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded border px-1 align-text-top text-[11px] font-semibold hover:brightness-95", CHIP[verdict])}>
                          {numbering.get(id)}
                        </button>
                      );
                    })}{" "}
                  </span>
                ))}
              </p>
            </section>
          ))}
        </article>
      </Panel>

      <div className="flex flex-col gap-5">
        <Panel title="Citation check" bodyClassName="p-4 text-[13px]">
          <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1.5">
            <dt className="text-ink-2">Sentences kept</dt><dd className="num font-medium">{stats.sentences_kept ?? 0} / {stats.sentences_total ?? 0}</dd>
            <dt className="text-ink-2">Dropped (no valid claim)</dt><dd className="num font-medium">{stats.sentences_dropped_uncited ?? 0}</dd>
            <dt className="text-ink-2">Cited claims</dt><dd className="num font-medium">{stats.cited_claims ?? 0}</dd>
            <dt className="text-ok">supported</dt><dd className="num font-medium">{stats.cited_supported ?? 0}</dd>
            <dt className="text-bad">disputed</dt><dd className="num font-medium">{stats.cited_disputed ?? 0}</dd>
            <dt className="text-warn">unverified</dt><dd className="num font-medium">{stats.cited_unverified ?? 0}</dd>
          </dl>
          <p className="mt-3 text-xs text-ink-3">Supported = verbatim quotes from 2+ independent origins and no strong contradiction.</p>
        </Panel>
        <Panel title={`Sources cited (${sources.length})`} bodyClassName="p-0">
          <ol className="divide-y divide-line">
            {sources.map((s) => (
              <li key={s.id} className="px-4 py-2.5 text-[13px]">
                <a href={s.url} target="_blank" rel="noreferrer noopener" className="inline-flex items-start gap-1 font-medium text-accent hover:underline">
                  <span className="line-clamp-2">{s.title || s.url}</span><ExternalLink className="mt-0.5 size-3 shrink-0" aria-hidden />
                </a>
                <p className="text-xs text-ink-3">{s.domain} · published {shortDate(s.published_at)}</p>
              </li>
            ))}
          </ol>
        </Panel>
      </div>
    </div>
  );
}
