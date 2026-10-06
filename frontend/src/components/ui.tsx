import { useEffect, useId, useRef, type ButtonHTMLAttributes, type ReactNode } from "react";

import { errorMessage } from "../api/client";
import type { Freshness } from "../api/types";
import { formatDate } from "../lib/format";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const variants: Record<ButtonVariant, string> = {
  primary: "bg-ledger text-ink-950 hover:bg-ledger/90 font-semibold",
  secondary: "border border-ink-600 bg-ink-800 text-paper hover:border-ledger/70",
  ghost: "text-muted hover:text-paper hover:bg-ink-800",
  danger: "border border-loss/60 text-loss hover:bg-loss/10",
};

export function Button({
  variant = "primary",
  className = "",
  busy = false,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; busy?: boolean }) {
  return (
    <button
      type="button"
      className={`inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${variants[variant]} ${className}`}
      disabled={busy || props.disabled}
      aria-busy={busy || undefined}
      {...props}
    >
      {busy && <Spinner small />}
      {children}
    </button>
  );
}

export function Card({
  title,
  subtitle,
  actions,
  children,
  className = "",
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel p-4 sm:p-5 ${className}`}>
      {(title || actions) && (
        <header className="mb-3 flex flex-wrap items-start justify-between gap-2">
          <div>
            {title && <h2 className="text-lg">{title}</h2>}
            {subtitle && <p className="text-sm text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

type BadgeTone = "neutral" | "gain" | "loss" | "caution" | "info";
const badgeTones: Record<BadgeTone, string> = {
  neutral: "border-ink-600 text-muted",
  gain: "border-ledger/50 text-ledger",
  loss: "border-loss/50 text-loss",
  caution: "border-caution/50 text-caution",
  info: "border-info/50 text-info",
};

export function Badge({ tone = "neutral", children, title }: { tone?: BadgeTone; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center rounded border px-1.5 py-0.5 text-xs ${badgeTones[tone]}`}>
      {children}
    </span>
  );
}

export function SyntheticBadge({ show = true }: { show?: boolean }) {
  if (!show) return null;
  return (
    <Badge tone="caution" title="Fictional demonstration data generated for this project - not real market data.">
      Synthetic data
    </Badge>
  );
}

export function FreshnessBadge({ freshness }: { freshness: Freshness | undefined }) {
  if (!freshness) return null;
  const date = formatDate(freshness.latest_observation);
  if (freshness.is_stale) {
    return (
      <Badge tone="caution" title={`Latest observation is ${freshness.age_days ?? "?"} days old.`}>
        Stale · data to {date}
      </Badge>
    );
  }
  return <Badge tone="info">Data to {date}</Badge>;
}

export function Spinner({ small = false }: { small?: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block animate-spin rounded-full border-2 border-ink-600 border-t-ledger ${small ? "h-3.5 w-3.5" : "h-5 w-5"}`}
    />
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-3 py-8 text-muted">
      <Spinner />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-md border border-loss/50 bg-loss/5 p-4 text-sm">
      <p className="text-loss">{errorMessage(error)}</p>
      {onRetry && (
        <Button variant="secondary" className="mt-3" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-ink-600 p-6 text-center">
      <p className="font-medium">{title}</p>
      {children && <div className="mt-2 text-sm text-muted">{children}</div>}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "caution" | "loss"; children: ReactNode }) {
  const styles = {
    info: "border-info/40 bg-info/5",
    caution: "border-caution/50 bg-caution/5",
    loss: "border-loss/50 bg-loss/5",
  }[tone];
  return <div className={`rounded-md border px-3 py-2 text-sm ${styles}`}>{children}</div>;
}

export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  children: (id: string) => ReactNode;
}) {
  const id = useId();
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm text-muted">
        {label}
      </label>
      {children(id)}
      {hint && !error && <p className="text-xs text-faint">{hint}</p>}
      {error && (
        <p className="text-xs text-loss" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: { id: T; label: string }[];
  value: T;
  onChange: (id: T) => void;
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="flex flex-wrap gap-1 border-b border-ink-700">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          type="button"
          aria-selected={tab.id === value}
          onClick={() => onChange(tab.id)}
          className={`-mb-px border-b-2 px-3 py-2 text-sm ${
            tab.id === value ? "border-ledger text-paper" : "border-transparent text-muted hover:text-paper"
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export function InfoTip({ text }: { text: string }) {
  return (
    <details className="group relative inline-block align-middle">
      <summary
        className="inline-flex h-4 w-4 cursor-pointer list-none items-center justify-center rounded-full border border-ink-600 text-[10px] text-muted hover:border-ledger"
        aria-label="How this is calculated"
      >
        i
      </summary>
      <div className="absolute left-0 z-20 mt-1 w-72 rounded-md border border-ink-600 bg-ink-850 p-3 text-xs text-muted shadow-lg">
        {text}
      </div>
    </details>
  );
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel,
  onConfirm,
  onCancel,
  busy,
}: {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
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
      onCancel={onCancel}
      className="m-auto max-w-md rounded-lg border border-ink-600 bg-ink-900 p-5 text-paper backdrop:bg-black/60"
    >
      <h2 className="text-lg">{title}</h2>
      <div className="mt-2 text-sm text-muted">{message}</div>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="danger" onClick={onConfirm} busy={busy}>
          {confirmLabel}
        </Button>
      </div>
    </dialog>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="max-w-3xl">
        <h1>{title}</h1>
        {description && <p className="mt-1 text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Disclaimer({ children }: { children?: ReactNode }) {
  return (
    <p className="text-xs text-faint">
      {children ??
        "Decision support only, not investment advice. Historical figures and model estimates do not guarantee future returns."}
    </p>
  );
}

export function KindLabel({ kind }: { kind: "historical" | "estimate" | "hypothetical" }) {
  const map = {
    historical: { label: "Historical", tone: "info" as const },
    estimate: { label: "Model estimate", tone: "caution" as const },
    hypothetical: { label: "Hypothetical", tone: "neutral" as const },
  };
  return <Badge tone={map[kind].tone}>{map[kind].label}</Badge>;
}
