import { useQuery } from "@tanstack/react-query";
import { BarChart3, History, Menu, Search, X } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router";
import { api } from "../lib/api";
import { cx } from "./ui";

const NAV = [
  { to: "/", label: "New research", icon: Search, end: true },
  { to: "/runs", label: "History", icon: History },
  { to: "/metrics", label: "Cost & quality", icon: BarChart3 },
];

function Brand() {
  return (
    <div className="flex items-center gap-2 px-1">
      <img src="/favicon.svg" alt="" className="size-6" />
      <span className="font-semibold tracking-tight">Deep Research</span>
    </div>
  );
}

function SystemStatus() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 60_000 });
  if (!health.data) return null;
  const h = health.data;
  const ok = h.database === "ok" && h.model_configured;
  return (
    <div className="rounded-md border border-line bg-canvas px-3 py-2 text-xs text-ink-2">
      <p className="flex items-center gap-1.5 font-medium text-ink">
        <span className={cx("size-1.5 rounded-full", ok ? "bg-ok" : "bg-warn")} aria-hidden />
        {ok ? "Agents online" : h.model_configured ? "Database issue" : "Model not configured"}
      </p>
      <p className="mt-0.5 truncate font-mono text-[11px]" title={h.models.join(", ")}>{h.models[0] ?? "no model"}</p>
      <p className="truncate font-mono text-[11px]">search: {h.search_provider}</p>
      {h.model_calls_left_today !== null && (
        <p className="truncate text-[11px]" title={`A report uses at most ${h.max_model_calls_per_run} model calls`}>
          {h.model_calls_left_today} model calls left today
        </p>
      )}
    </div>
  );
}

function NavItems({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="flex flex-col gap-0.5" aria-label="Main">
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink key={to} to={to} end={end} onClick={onNavigate}
          className={({ isActive }) => cx("flex h-9 items-center gap-2.5 rounded-md px-2.5 text-sm font-medium",
            isActive ? "bg-accent-soft text-accent" : "text-ink-2 hover:bg-sunken hover:text-ink")}>
          <Icon className="size-4" aria-hidden />
          <span className="flex-1">{label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

export default function Layout() {
  const [open, setOpen] = useState(false);

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[232px_1fr]">
      <aside className="sticky top-0 hidden h-dvh flex-col gap-6 border-r border-line bg-surface px-3 py-4 lg:flex">
        <Brand />
        <NavItems />
        <div className="mt-auto"><SystemStatus /></div>
      </aside>

      <header className="sticky top-0 z-30 flex h-12 items-center justify-between border-b border-line bg-surface px-4 lg:hidden">
        <Brand />
        <button onClick={() => setOpen(true)} className="rounded-md p-2 text-ink-2 hover:bg-sunken" aria-label="Open menu" aria-expanded={open}>
          <Menu className="size-5" />
        </button>
      </header>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Menu">
          <div className="absolute inset-0 bg-ink/30" onClick={() => setOpen(false)} />
          <div className="absolute inset-y-0 left-0 flex w-64 flex-col gap-6 bg-surface px-3 py-4 shadow-xl">
            <div className="flex items-center justify-between">
              <Brand />
              <button onClick={() => setOpen(false)} className="rounded-md p-1.5 text-ink-2 hover:bg-sunken" aria-label="Close menu"><X className="size-5" /></button>
            </div>
            <NavItems onNavigate={() => setOpen(false)} />
            <div className="mt-auto"><SystemStatus /></div>
          </div>
        </div>
      )}

      <main className="mx-auto w-full max-w-[1180px] px-4 py-6 sm:px-6 lg:px-8">
        <Outlet />
      </main>
    </div>
  );
}
