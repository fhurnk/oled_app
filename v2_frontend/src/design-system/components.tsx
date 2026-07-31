import {
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  useEffect
} from "react";

export type StatusTone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger"
  | "progress";

type ButtonVariant = "primary" | "secondary" | "danger" | "ghost";

export function Button({
  children,
  className = "",
  variant = "secondary",
  compact = false,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  compact?: boolean;
}) {
  return (
    <button
      className={`button button--${variant} ${compact ? "button--compact" : ""} ${className}`.trim()}
      type="button"
      {...props}
    >
      {children}
    </button>
  );
}

export function StatusBadge({
  children,
  tone = "neutral",
  dot = false
}: {
  children: ReactNode;
  tone?: StatusTone;
  dot?: boolean;
}) {
  return (
    <span className={`status-badge status-badge--${tone}`}>
      {dot && <span className="status-badge__dot" aria-hidden="true" />}
      {children}
    </span>
  );
}

const hardwareStatusLabels: Record<string, string> = {
  not_probed: "Не проверено",
  ready: "Готово",
  unavailable: "Недоступно"
};

export function HardwarePill({ label, state }: { label: string; state?: string }) {
  const value = state ?? "not_probed";
  return (
    <div className={`hardware-pill hardware-pill--${value}`}>
      <span className="hardware-pill__dot" aria-hidden="true" />
      <span>{label}</span>
      <strong>{hardwareStatusLabels[value] ?? value}</strong>
    </div>
  );
}

export function MetricCard({
  eyebrow,
  value,
  note,
  tone = "neutral"
}: {
  eyebrow: string;
  value: string;
  note: string;
  tone?: "neutral" | "blue" | "green";
}) {
  return (
    <article className={`metric-card metric-card--${tone}`}>
      <p>{eyebrow}</p>
      <strong>{value}</strong>
      <span>{note}</span>
    </article>
  );
}

export function Panel({
  children,
  className = ""
}: {
  children: ReactNode;
  className?: string;
}) {
  return <article className={`panel ${className}`.trim()}>{children}</article>;
}

const noticeIcons: Record<StatusTone, string> = {
  neutral: "•",
  info: "i",
  success: "✓",
  warning: "!",
  danger: "!",
  progress: "↻"
};

export function Notice({
  title,
  children,
  tone = "info",
  actions
}: {
  title: string;
  children: ReactNode;
  tone?: StatusTone;
  actions?: ReactNode;
}) {
  return (
    <div className={`notice notice--${tone}`} role={tone === "danger" ? "alert" : "status"}>
      <span className="notice__icon" aria-hidden="true">
        {noticeIcons[tone]}
      </span>
      <div className="notice__content">
        <strong>{title}</strong>
        <p>{children}</p>
      </div>
      {actions && <div className="notice__actions">{actions}</div>}
    </div>
  );
}

export function Field({
  label,
  hint,
  error,
  children
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <label className={`field ${error ? "field--error" : ""}`}>
      <span className="field__label">{label}</span>
      {children}
      {(error || hint) && <small>{error || hint}</small>}
    </label>
  );
}

export function TextField({
  label,
  hint,
  error,
  className = "",
  ...props
}: InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  hint?: string;
  error?: string;
}) {
  return (
    <Field label={label} hint={hint} error={error}>
      <input className={`text-input ${className}`.trim()} {...props} />
    </Field>
  );
}

export function SelectField({
  label,
  hint,
  children,
  className = "",
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <Field label={label} hint={hint}>
      <select className={`select-input ${className}`.trim()} {...props}>
        {children}
      </select>
    </Field>
  );
}

export function Dialog({
  open,
  title,
  eyebrow,
  children,
  footer,
  onClose
}: {
  open: boolean;
  title: string;
  eyebrow?: string;
  children: ReactNode;
  footer: ReactNode;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!open) {
      return;
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose, open]);

  if (!open) {
    return null;
  }

  return (
    <div className="dialog-backdrop" onMouseDown={onClose}>
      <section
        aria-labelledby="design-dialog-title"
        aria-modal="true"
        className="dialog"
        onMouseDown={(event) => event.stopPropagation()}
        role="dialog"
      >
        <div className="dialog__header">
          <div>
            {eyebrow && <p>{eyebrow}</p>}
            <h2 id="design-dialog-title">{title}</h2>
          </div>
          <Button aria-label="Закрыть диалог" compact onClick={onClose} variant="ghost">
            ×
          </Button>
        </div>
        <div className="dialog__body">{children}</div>
        <div className="dialog__footer">{footer}</div>
      </section>
    </div>
  );
}

export function Toast({
  visible,
  title,
  children,
  onClose
}: {
  visible: boolean;
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  if (!visible) {
    return null;
  }
  return (
    <div className="toast" role="status">
      <span aria-hidden="true">✓</span>
      <div>
        <strong>{title}</strong>
        <p>{children}</p>
      </div>
      <Button aria-label="Закрыть уведомление" compact onClick={onClose} variant="ghost">
        ×
      </Button>
    </div>
  );
}
