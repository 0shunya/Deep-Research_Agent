import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowRight, Search } from "lucide-react";
import { useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { useToast } from "../components/toast";
import { Button, Label, PageHeader, Panel, RunStatusBadge, TextInput, cx } from "../components/ui";
import { api } from "../lib/api";
import { formatDate, formatUsd } from "../lib/format";
import type { Depth } from "../lib/types";

const DEPTHS: { value: Depth; label: string; hint: string }[] = [
  { value: "quick", label: "Quick", hint: "2 sub-questions · 2 sources each" },
  { value: "standard", label: "Standard", hint: "up to 4 · 3 sources · follow-up search" },
  { value: "deep", label: "Deep", hint: "up to 5 · 4 sources · more steps" },
];

const EXAMPLES = [
  "Which is taller, the Burj Khalifa or the Shanghai Tower, and what are their heights?",
  "When did the James Webb Space Telescope launch, and at which Lagrange point does it operate?",
  "Who won the 2022 FIFA World Cup, and who was the tournament's top scorer?",
];

function newKey() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

export default function NewResearch() {
  const navigate = useNavigate();
  const toast = useToast();
  const [question, setQuestion] = useState("");
  const [depth, setDepth] = useState<Depth>("quick");
  const [budget, setBudget] = useState("120");
  const [workers, setWorkers] = useState("3");
  // One key per submitted form: a double click or a retried request returns the same run.
  const key = useRef(newKey());
  const recent = useQuery({ queryKey: ["runs"], queryFn: api.runs });

  const create = useMutation({
    mutationFn: () => api.createRun({ question: question.trim(), depth, budget_tokens: Math.round(Number(budget) * 1000),
      max_workers: Number(workers) }, key.current),
    onSuccess: (run) => {
      key.current = newKey();
      navigate(`/runs/${run.id}`);
    },
    onError: (error) => toast.error(error.message),
  });

  const tooShort = question.trim().length < 10;
  const budgetOk = Number(budget) >= 5 && Number(budget) <= 400;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!tooShort && budgetOk) create.mutate();
  }

  return (
    <>
      <PageHeader title="New research"
        description="A planner splits your question into sub-questions you can edit. Researcher agents search and read in parallel, a verifier checks every claim against its quotes, and the report cites each claim." />
      <form onSubmit={submit}>
        <Panel>
          <Label htmlFor="question">Question</Label>
          <textarea id="question" value={question} onChange={(e) => setQuestion(e.target.value)} rows={3} maxLength={1000}
            placeholder="Ask something that needs several sources to answer well…"
            className="w-full rounded-md border border-line-strong bg-surface px-3 py-2 text-[15px] placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20" />
          <div className="mt-2 flex flex-wrap gap-1.5">
            {EXAMPLES.map((ex) => (
              <button key={ex} type="button" onClick={() => setQuestion(ex)}
                className="rounded-full border border-line bg-canvas px-2.5 py-1 text-xs text-ink-2 hover:border-line-strong hover:text-ink">
                {ex}
              </button>
            ))}
          </div>

          <fieldset className="mt-5">
            <legend className="mb-1 block text-xs font-medium text-ink-2">Depth</legend>
            <div className="grid gap-2 sm:grid-cols-3">
              {DEPTHS.map((d) => (
                <label key={d.value} className={cx("cursor-pointer rounded-md border px-3 py-2",
                  depth === d.value ? "border-accent bg-accent-soft" : "border-line-strong bg-surface hover:bg-sunken")}>
                  <input type="radio" name="depth" value={d.value} checked={depth === d.value}
                    onChange={() => setDepth(d.value)} className="sr-only" />
                  <span className="block text-sm font-medium">{d.label}</span>
                  <span className="block text-xs text-ink-3">{d.hint}</span>
                </label>
              ))}
            </div>
          </fieldset>

          <div className="mt-5 grid gap-4 sm:grid-cols-[180px_180px_1fr] sm:items-end">
            <div>
              <Label htmlFor="budget">Token budget (thousands)</Label>
              <TextInput id="budget" type="number" min={5} max={400} value={budget} onChange={(e) => setBudget(e.target.value)} />
            </div>
            <div>
              <Label htmlFor="workers">Parallel researchers</Label>
              <TextInput id="workers" type="number" min={1} max={8} value={workers} onChange={(e) => setWorkers(e.target.value)} />
            </div>
            <div className="flex justify-end">
              <Button type="submit" variant="primary" icon={<Search className="size-4" />} loading={create.isPending}
                disabled={tooShort || !budgetOk}>
                Plan research
              </Button>
            </div>
          </div>
          {!budgetOk && <p className="mt-2 text-[13px] text-bad">Budget must be between 5k and 400k tokens.</p>}
        </Panel>
      </form>

      <Panel title="Recent runs" className="mt-6" bodyClassName="p-0"
        actions={<Link to="/runs" className="inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline">All runs <ArrowRight className="size-3" /></Link>}>
        {recent.data && recent.data.length === 0 && <p className="px-4 py-6 text-center text-ink-3">No research yet.</p>}
        <ul className="divide-y divide-line">
          {recent.data?.slice(0, 5).map((run) => (
            <li key={run.id}>
              <Link to={`/runs/${run.id}`} className="flex items-center gap-3 px-4 py-2.5 hover:bg-sunken">
                <span className="flex-1 truncate">{run.question}</span>
                <span className="num hidden text-xs text-ink-3 sm:inline">{formatUsd(run.cost_usd)}</span>
                <span className="hidden text-xs text-ink-3 md:inline">{formatDate(run.created_at)}</span>
                <RunStatusBadge status={run.status} />
              </Link>
            </li>
          ))}
        </ul>
      </Panel>
    </>
  );
}
