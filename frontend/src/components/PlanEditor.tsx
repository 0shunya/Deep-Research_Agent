import { Play, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { DEPTH_LIMITS, planProblems, renumber } from "../lib/format";
import type { Plan } from "../lib/types";
import { Button, Label, Panel, cx } from "./ui";

/** Review the planner's JSON before the expensive part starts: edit, add, remove, rewire dependencies. */
export default function PlanEditor({ initial, depth, onApprove, approving }:
  { initial: Plan; depth: string; onApprove: (plan: Plan) => void; approving: boolean }) {
  const [plan, setPlan] = useState<Plan>(initial);
  const limit = DEPTH_LIMITS[depth] ?? 5;
  const problems = planProblems(plan, limit);

  function update(index: number, patch: Partial<Plan["sub_questions"][number]>) {
    setPlan((p) => ({ ...p, sub_questions: p.sub_questions.map((q, i) => (i === index ? { ...q, ...patch } : q)) }));
  }
  function remove(index: number) {
    setPlan((p) => renumber({ ...p, sub_questions: p.sub_questions.filter((_, i) => i !== index) }));
  }
  function add() {
    setPlan((p) => ({ ...p, sub_questions: [...p.sub_questions,
      { id: `q${p.sub_questions.length + 1}`, ask: "", why: "", depends_on: [] }] }));
  }
  function toggleDependency(index: number, dep: string) {
    const current = plan.sub_questions[index]!.depends_on;
    update(index, { depends_on: current.includes(dep) ? current.filter((d) => d !== dep) : [...current, dep] });
  }

  return (
    <Panel title="Review the plan" actions={<span className="text-xs text-ink-3">Nothing is searched until you approve.</span>}>
      <div>
        <Label htmlFor="brief">Research brief</Label>
        <textarea id="brief" value={plan.brief} rows={2} onChange={(e) => setPlan({ ...plan, brief: e.target.value })}
          className="w-full rounded-md border border-line-strong bg-surface px-2.5 py-2 text-sm focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20" />
      </div>

      <ol className="mt-4 flex flex-col gap-3">
        {plan.sub_questions.map((q, index) => {
          const others = plan.sub_questions.filter((o) => o.id !== q.id);
          return (
            <li key={index} className="rounded-md border border-line bg-canvas p-3">
              <div className="flex items-start gap-3">
                <span className="num mt-2 w-7 shrink-0 font-mono text-xs font-semibold text-accent">{q.id}</span>
                <div className="flex-1">
                  <label className="sr-only" htmlFor={`ask-${index}`}>Sub-question {q.id}</label>
                  <textarea id={`ask-${index}`} value={q.ask} rows={2} placeholder="A focused, searchable question"
                    onChange={(e) => update(index, { ask: e.target.value })}
                    className="w-full rounded-md border border-line-strong bg-surface px-2.5 py-1.5 text-sm focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20" />
                  {q.why && <p className="mt-1 text-xs text-ink-3">Why: {q.why}</p>}
                  {others.length > 0 && (
                    <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-ink-2">
                      <span>Waits for:</span>
                      {others.map((o) => {
                        const on = q.depends_on.includes(o.id);
                        return (
                          <button key={o.id} type="button" onClick={() => toggleDependency(index, o.id)} aria-pressed={on}
                            className={cx("rounded border px-1.5 py-px font-mono", on
                              ? "border-accent bg-accent-soft text-accent" : "border-line-strong bg-surface text-ink-3 hover:text-ink")}>
                            {o.id}
                          </button>
                        );
                      })}
                      {q.depends_on.length === 0 && <span className="text-ink-3">nothing — runs in parallel</span>}
                    </div>
                  )}
                </div>
                <button type="button" onClick={() => remove(index)} aria-label={`Remove ${q.id}`}
                  className="rounded p-1.5 text-ink-3 hover:bg-bad-soft hover:text-bad">
                  <Trash2 className="size-4" />
                </button>
              </div>
            </li>
          );
        })}
      </ol>

      {problems.length > 0 && (
        <ul className="mt-3 list-disc pl-5 text-[13px] text-bad" role="alert">
          {problems.map((p) => <li key={p}>{p}</li>)}
        </ul>
      )}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
        <Button size="sm" icon={<Plus className="size-4" />} onClick={add} disabled={plan.sub_questions.length >= limit}>
          Add sub-question
        </Button>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={() => setPlan(initial)}>Reset</Button>
          <Button variant="primary" icon={<Play className="size-4" />} loading={approving}
            disabled={problems.length > 0} onClick={() => onApprove(plan)}>
            Approve and start research
          </Button>
        </div>
      </div>
    </Panel>
  );
}
