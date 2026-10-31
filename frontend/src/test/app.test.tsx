import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import PlanEditor from "../components/PlanEditor";
import ReportView from "../components/ReportView";
import { formatUsd, planProblems, renumber } from "../lib/format";
import type { Plan, Report } from "../lib/types";

const plan: Plan = {
  brief: "Compare two towers.",
  sub_questions: [
    { id: "q1", ask: "How tall is the Burj Khalifa?", why: "", depends_on: [] },
    { id: "q2", ask: "How tall is the Shanghai Tower?", why: "", depends_on: [] },
    { id: "q3", ask: "Which is taller and by how much?", why: "", depends_on: ["q1", "q2"] },
  ],
  report: { max_words: 400, must_cite: true },
};

describe("plan helpers", () => {
  it("accepts a valid DAG and reports cycles, unknown ids and caps", () => {
    expect(planProblems(plan, 5)).toEqual([]);
    const cyclic = { ...plan, sub_questions: plan.sub_questions.map((q) => (q.id === "q1" ? { ...q, depends_on: ["q3"] } : q)) };
    expect(planProblems(cyclic, 5).join(" ")).toMatch(/cycle/);
    expect(planProblems(plan, 2).join(" ")).toMatch(/At most 2/);
  });

  it("renumbers after a delete and drops dependencies on the removed question", () => {
    const without = renumber({ ...plan, sub_questions: plan.sub_questions.filter((q) => q.id !== "q1") });
    expect(without.sub_questions.map((q) => q.id)).toEqual(["q1", "q2"]);
    expect(without.sub_questions[1]!.depends_on).toEqual(["q1"]);
  });

  it("formats small costs without rounding them away", () => {
    expect(formatUsd("0.000937")).toBe("$0.0009");
    expect(formatUsd(0)).toBe("$0");
    expect(formatUsd(1.234)).toBe("$1.23");
  });
});

describe("PlanEditor", () => {
  it("lets the user edit, rewire and approve the plan", async () => {
    const onApprove = vi.fn();
    const user = userEvent.setup();
    render(<PlanEditor initial={plan} depth="deep" approving={false} onApprove={onApprove} />);
    const first = screen.getByLabelText("Sub-question q1");
    await user.clear(first);
    await user.type(first, "Burj Khalifa height in metres");
    await user.click(screen.getByRole("button", { name: "Remove q2" }));
    await user.click(screen.getByRole("button", { name: /Approve and start research/ }));
    const approved: Plan = onApprove.mock.calls[0]![0];
    expect(approved.sub_questions.map((q) => [q.id, q.depends_on])).toEqual([["q1", []], ["q2", ["q1"]]]);
    expect(approved.sub_questions[0]!.ask).toBe("Burj Khalifa height in metres");
  });

  it("blocks approval while the dependencies form a cycle", async () => {
    const user = userEvent.setup();
    render(<PlanEditor initial={plan} depth="deep" approving={false} onApprove={vi.fn()} />);
    const q1 = screen.getByLabelText("Sub-question q1").closest("li")!;
    await user.click(within(q1).getByRole("button", { name: "q3" }));
    expect(screen.getByRole("alert")).toHaveTextContent(/cycle/);
    expect(screen.getByRole("button", { name: /Approve and start research/ })).toBeDisabled();
  });
});

describe("ReportView", () => {
  const source = { id: "s1", url: "https://a.com/x", domain: "a.com", title: "A page", published_at: "2024-01-02",
    fetched_at: "2026-10-31T00:00:00Z", content_hash: "f".repeat(64), search_rank: 1 };
  const report: Report = {
    id: "r1", run_id: "run-1", version: 1, title: "Towers", markdown: "# Towers", created_at: "2026-10-31T00:00:00Z",
    stats: { writer: "model", sentences_kept: 2, sentences_total: 3, sentences_dropped_uncited: 1, cited_claims: 2,
      cited_supported: 1, cited_disputed: 1, cited_unverified: 0 },
    sections: [{ heading: "Answer", sentences: [
      { idx: 0, text: "The Burj Khalifa is 828 m tall.", claim_ids: ["c1"] },
      { idx: 1, text: "Sources disagree on the Shanghai Tower.", claim_ids: ["c2", "c1"] },
    ] }],
    claims: {
      c1: { id: "c1", task_id: "t", text: "Burj Khalifa is 828 m", verdict: "supported", verdict_reason: null, support_domains: 2, group_key: "g1" },
      c2: { id: "c2", task_id: "t", text: "Shanghai Tower is 632 m", verdict: "disputed", verdict_reason: null, support_domains: 1, group_key: "g2" },
    },
    sources: { c1: [source], c2: [source] },
  };

  it("numbers each claim once and opens the audit for the clicked claim", async () => {
    const onOpen = vi.fn();
    const user = userEvent.setup();
    render(<ReportView report={report} onOpenClaim={onOpen} />);
    expect(screen.getAllByRole("button", { name: "Claim 1 (supported)" })).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Claim 2 (disputed)" }));
    expect(onOpen).toHaveBeenCalledWith("c2");
    expect(screen.getByText("Sources cited (1)")).toBeInTheDocument();
  });
});
