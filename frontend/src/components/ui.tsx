import type { ReactNode } from "react";
import { ApiError } from "../api";
import { humanize } from "../format";

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <span className="spinner" aria-hidden /> {label}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const api = error instanceof ApiError ? error : null;
  return (
    <div className="alert alert-error" role="alert">
      <strong>{api?.status === 0 ? "Backend unreachable" : "Something went wrong"}</strong>
      <p>{error.message}</p>
      {api?.fieldErrors.length ? (
        <ul>
          {api.fieldErrors.map((f, i) => (
            <li key={i}>
              <code>{f.path}</code>: {f.message}
            </li>
          ))}
        </ul>
      ) : null}
      {api?.requestId ? <small>Request id: {api.requestId}</small> : null}
      {onRetry ? (
        <button className="btn" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state empty">
      <strong>{title}</strong>
      {children ? <div>{children}</div> : null}
    </div>
  );
}

const STATUS_CLASS: Record<string, string> = {
  COMPLETED: "ok",
  SATISFIED: "ok",
  SATISFIED_CONFIRMED: "ok",
  AT_LEAST_ONE_POLICY_NOT_REFUTED: "ok",
  RUNNING: "info",
  QUEUED: "muted",
  FAILED: "bad",
  VIOLATED: "bad",
  VIOLATES_AT_LEAST_ONE: "bad",
  NOT_SATISFIED_BY_ANY_TESTED_POLICY: "bad",
  NOT_EVALUABLE: "warn",
  INCOMPLETE: "warn",
  SATISFIED_INCONCLUSIVE: "warn",
  NO_CONSTRAINTS: "muted",
};

export function Badge({ value, title }: { value: string; title?: string }) {
  return (
    <span className={`badge badge-${STATUS_CLASS[value] ?? "muted"}`} title={title}>
      {humanize(value)}
    </span>
  );
}

export function Card({ title, children, actions }: { title?: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="card">
      {title || actions ? (
        <header className="card-head">
          {title ? <h2>{title}</h2> : <span />}
          {actions}
        </header>
      ) : null}
      {children}
    </section>
  );
}

export function Field({ label, hint, error, children }: { label: string; hint?: string; error?: string; children: ReactNode }) {
  return (
    <label className={`field${error ? " has-error" : ""}`}>
      <span className="field-label">{label}</span>
      {children}
      {hint ? <small className="hint">{hint}</small> : null}
      {error ? (
        <small className="field-error" role="alert">
          {error}
        </small>
      ) : null}
    </label>
  );
}

export function Warnings({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="alert alert-warn">
      <strong>Notes and warnings</strong>
      <ul>
        {items.map((w, i) => (
          <li key={i}>{w}</li>
        ))}
      </ul>
    </div>
  );
}
