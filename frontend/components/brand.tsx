import type { ReactNode } from "react";

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <span
      className="inline-flex items-center justify-center rounded-[10px] bg-accent text-accent-foreground"
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      <svg
        width={size * 0.58}
        height={size * 0.58}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M7 8.5 10.5 12 7 15.5M12.5 16h4.5" />
      </svg>
    </span>
  );
}

export function Wordmark({ className = "" }: { className?: string }) {
  return (
    <span
      className={`text-[15px] font-semibold tracking-[-0.01em] text-foreground ${className}`}
    >
      Repo<span className="text-accent">Chat</span>
    </span>
  );
}

export function Brand({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2.5 ${className}`}>
      <Logo />
      <Wordmark />
    </span>
  );
}

export function AuthShell({
  eyebrow,
  title,
  subtitle,
  children,
}: {
  eyebrow: string;
  title: string;
  subtitle: string;
  children: ReactNode;
}) {
  return (
    <main className="relative flex min-h-full flex-1 items-center justify-center overflow-hidden px-5 py-12">
      {/* Decorative grid + accent glow, hidden from assistive tech. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 bg-grid opacity-[0.55]"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute left-1/2 top-[-14rem] h-[30rem] w-[52rem] -translate-x-1/2 rounded-full bg-accent/12 blur-[120px]"
      />

      <div className="relative w-full max-w-[400px]">
        <div className="mb-8 flex justify-center">
          <Brand />
        </div>

        <div className="rounded-2xl border border-border-base bg-surface p-7 shadow-[0_1px_2px_rgba(0,0,0,0.04),0_12px_32px_-12px_rgba(0,0,0,0.14)]">
          <div className="mb-6 text-center">
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.09em] text-accent">
              {eyebrow}
            </p>
            <h1 className="text-[22px] font-semibold leading-tight tracking-[-0.02em] text-foreground">
              {title}
            </h1>
            <p className="mt-2 text-[13px] leading-relaxed text-muted">
              {subtitle}
            </p>
          </div>
          {children}
        </div>

        <p className="mt-6 text-center text-[12px] leading-relaxed text-muted">
          By continuing you agree to keep it respectful. No code leaves your
          workspace without your say-so.
        </p>
      </div>
    </main>
  );
}
