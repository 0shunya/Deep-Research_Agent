import { AlertTriangle, Loader2, X } from "lucide-react";
import { useEffect, useId, useRef } from "react";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { Link } from "react-router";
import { ACTIVE_STATUSES, RUN_STATUS_LABEL, STANCE_LABEL, TASK_STATUS_LABEL, VERDICT_LABEL } from "../lib/format";
import type { RunStatus, Stance, TaskStatus, Verdict } from "../lib/types";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "ghost" | "danger";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-white hover:bg-accent-hover border border-accent",
  secondary: "bg-surface text-ink border border-line-strong hover:bg-sunken",
  ghost: "text-ink-2 hover:bg-sunken hover:text-ink border border-transparent",
  danger: "bg-surface text-bad border border-line-strong hover:bg-bad-soft hover:border-bad/40",
};

function buttonClass(variant: Variant, size: "sm" | "md", className?: string) {
  return cx(
    "inline-flex items-center justify-center gap-1.5 rounded-md font-medium whitespace-nowrap transition-colors",
    "disabled:cursor-not-allowed disabled:opacity-50",
    size === "sm" ? "h-7 px-2.5 text-[13px]" : "h-9 px-3.5 text-sm",
    VARIANTS[variant], className);
}

export function LinkButton({ to, variant = "secondary", size = "md", icon, children, className }:
  { to: string; variant?: Variant; size?: "sm" | "md"; icon?: ReactNode; children: ReactNode; className?: string }) {
  return <Link to={to} className={buttonClass(variant, size, className)}>{icon}{children}</Link>;
}

export function Button({ variant = "secondary", size = "md", loading = false, icon, children, className, disabled, ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md"; loading?: boolean; icon?: ReactNode }) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={buttonClass(variant, size, className)}
    >
      {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
}

const RUN_TONE: Record<RunStatus, string> = {
  planning: "bg-info-soft text-info border-info/20",
  awaiting_approval: "bg-warn-soft text-warn border-warn/25",
  researching: "bg-info-soft text-info border-info/20",
  verifying: "bg-info-soft text-info border-info/20",
  writing: "bg-info-soft text-info border-info/20",
  completed: "bg-ok-soft text-ok border-ok/25",
  failed: "bg-bad-soft text-bad border-bad/25",
  cancelled: "bg-sunken text-ink-3 border-line",
};

export function RunStatusBadge({ status }: { status: RunStatus }) {
  return (
    <span className={cx("inline-flex items-center gap-1 rounded border px-1.5 py-px text-xs font-medium", RUN_TONE[status])}>
      {ACTIVE_STATUSES.includes(status) && <Loader2 className="size-3 animate-spin" aria-hidden />}
      {RUN_STATUS_LABEL[status]}
    </span>
  );
}

const VERDICT_TONE: Record<Verdict, string> = {
  supported: "bg-ok-soft text-ok border-ok/25",
  disputed: "bg-bad-soft text-bad border-bad/25",
  unverified: "bg-warn-soft text-warn border-warn/25",
  pending: "bg-sunken text-ink-3 border-line",
};

export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  return (
    <span className={cx("inline-flex items-center rounded border px-1.5 py-px text-xs font-medium", VERDICT_TONE[verdict])}>
      {VERDICT_LABEL[verdict]}
    </span>
  );
}

const STANCE_TONE: Record<Stance, string> = {
  supports: "text-ok",
  contradicts: "text-bad",
  insufficient: "text-ink-3",
  pending: "text-ink-3",
};

export function StanceLabel({ stance }: { stance: Stance }) {
  return <span className={cx("text-xs font-semibold", STANCE_TONE[stance])}>{STANCE_LABEL[stance]}</span>;
}

const TASK_DOT: Record<TaskStatus, string> = {
  pending: "border border-ink-3 bg-transparent",
  running: "bg-info animate-pulse",
  done: "bg-ok",
  failed: "bg-bad",
  skipped: "bg-warn",
  cancelled: "bg-line-strong",
};

export function TaskStatusLabel({ status }: { status: TaskStatus }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-ink-2">
      <span className={cx("size-2 rounded-full", TASK_DOT[status])} aria-hidden />
      {TASK_STATUS_LABEL[status]}
    </span>
  );
}

export function Panel({ title, actions, children, className, bodyClassName }:
  { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; bodyClassName?: string }) {
  return (
    <section className={cx("rounded-md border border-line bg-surface", className)}>
      {(title || actions) && (
        <header className="flex min-h-11 items-center justify-between gap-3 border-b border-line px-4 py-2">
          <h2 className="text-[13px] font-semibold tracking-wide text-ink">{title}</h2>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={bodyClassName ?? "p-4"}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-ink-2">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function EmptyState({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
      {icon && <div className="mb-3 text-ink-3">{icon}</div>}
      <p className="font-medium">{title}</p>
      {children && <p className="mt-1 max-w-sm text-ink-2">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <div role="alert" className="flex items-start gap-3 rounded-md border border-bad/30 bg-bad-soft px-4 py-3 text-bad">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="flex-1">
        <p className="font-medium">Could not load this data</p>
        <p className="text-[13px]">{message}</p>
      </div>
      {onRetry && <Button size="sm" onClick={onRetry}>Retry</Button>}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-ink-2" role="status">
      <Loader2 className="size-4 animate-spin" aria-hidden /> {label}…
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("animate-pulse rounded bg-sunken", className)} aria-hidden />;
}

export function Label({ htmlFor, children }: { htmlFor: string; children: ReactNode }) {
  return <label htmlFor={htmlFor} className="mb-1 block text-xs font-medium text-ink-2">{children}</label>;
}

export function TextInput({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={cx("h-9 w-full rounded-md border border-line-strong bg-surface px-2.5 text-sm placeholder:text-ink-3",
        "focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20 disabled:bg-sunken", className)}
    />
  );
}

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={cx("h-9 w-full rounded-md border border-line-strong bg-surface px-2 text-sm",
        "focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20", className)}
    >
      {children}
    </select>
  );
}

export function Dialog({ open, title, onClose, children, footer }:
  { open: boolean; title: string; onClose: () => void; children: ReactNode; footer: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal?.();
    if (!open && dialog.open) dialog.close?.();
  }, [open]);
  if (!open) return null;
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      className="m-auto w-[min(92vw,30rem)] rounded-md border border-line bg-surface p-0 text-ink shadow-xl backdrop:bg-ink/30"
    >
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 id={titleId} className="font-semibold">{title}</h2>
        <button onClick={onClose} className="rounded p-1 text-ink-3 hover:bg-sunken hover:text-ink" aria-label="Close">
          <X className="size-4" />
        </button>
      </div>
      <div className="px-4 py-4">{children}</div>
      <div className="flex justify-end gap-2 border-t border-line bg-canvas px-4 py-3">{footer}</div>
    </dialog>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "warn" | "bad" | "ok" }) {
  return (
    <div className="min-w-0 px-4 py-3">
      <p className="text-xs font-medium text-ink-2">{label}</p>
      <p className={cx("num mt-1 text-2xl font-semibold tracking-tight",
        tone === "warn" && "text-warn", tone === "bad" && "text-bad", tone === "ok" && "text-ok")}>{value}</p>
      {hint && <p className="mt-0.5 truncate text-xs text-ink-3">{hint}</p>}
    </div>
  );
}
