import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { eventsUrl } from "./api";
import type { ProgressEvent } from "./types";

/**
 * Subscribe to a run's server-sent progress events while it is active. Each event also refreshes the
 * run detail (throttled), so counts and task statuses stay live without polling. EventSource reconnects
 * on its own with Last-Event-ID, and the server replays anything missed from the run_events table.
 */
export function useRunEvents(runId: string | undefined, active: boolean) {
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const client = useQueryClient();
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (!runId || !active || typeof EventSource === "undefined") return;
    const source = new EventSource(eventsUrl(runId));
    const refresh = () => {
      if (timer.current !== null) return;
      timer.current = window.setTimeout(() => {
        timer.current = null;
        client.invalidateQueries({ queryKey: ["run", runId] });
      }, 400);
    };
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.addEventListener("progress", (message) => {
      const event = { id: Number((message as MessageEvent).lastEventId), ...JSON.parse((message as MessageEvent).data) };
      setEvents((all) => (all.some((e) => e.id === event.id) ? all : [...all, event].slice(-300)));
      refresh();
    });
    source.addEventListener("end", () => {
      source.close();
      setConnected(false);
      client.invalidateQueries({ queryKey: ["run", runId] });
      client.invalidateQueries({ queryKey: ["report", runId] });
    });
    return () => {
      source.close();
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = null;
    };
  }, [runId, active, client]);

  return { events, connected };
}

export function describeEvent(e: ProgressEvent): string {
  const s = (key: string) => String(e[key] ?? "");
  switch (e.type) {
    case "status": return `Run is now ${s("status").replace("_", " ")}`;
    case "plan_ready": return `Planner proposed ${s("sub_questions")} sub-questions`;
    case "task_started": return `${s("key")} started${Number(e.attempt) > 1 ? ` (attempt ${s("attempt")})` : ""}: ${s("question")}`;
    case "search_done": return `Searched “${s("query")}” — ${s("results")} results`;
    case "source_read": return `Read ${s("domain")}: ${s("title")}`;
    case "source_failed": return `Could not read ${s("url")} (${s("reason")})`;
    case "claims_found": return `Extracted ${s("claims")} claims (${s("quotes_found")} quotes verbatim, ${s("quotes_missing")} not found)`;
    case "task_done": return `${s("key")} done — ${s("claims")} claims from ${s("sources")} sources`;
    case "task_retry": return `${s("key")} failed (${s("error")}); retrying`;
    case "task_failed": return `${s("key")} failed: ${s("error")}`;
    case "task_skipped": return `${s("key")} skipped (${s("reason")})`;
    case "research_stopped": return `Research stopped early: ${s("reason")}`;
    case "claims_checked": return `Verifier: ${s("supported")} supported, ${s("disputed")} disputed, ${s("unverified")} unverified`;
    case "verify_degraded": return `Verifier unavailable (${s("error")}); deterministic checks only`;
    case "report_ready": return `Report written — ${s("sentences")} cited sentences, ${s("dropped")} dropped by the citation check`;
    default: return e.type;
  }
}
