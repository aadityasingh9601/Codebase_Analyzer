import type { InputHTMLAttributes } from "react";

type Props = InputHTMLAttributes<HTMLInputElement> & {
  /** Trailing slot, e.g. an inline action button. */
  trailing?: React.ReactNode;
};

export function Input({ className = "", trailing, ...rest }: Props) {
  return (
    <div className="relative flex items-center">
      <input
        className={
          "h-11 w-full rounded-xl border border-border-base bg-surface px-4 text-sm " +
          "text-foreground placeholder:text-muted/75 outline-none " +
          "focus:border-accent focus:ring-4 focus:ring-accent/12 " +
          "disabled:opacity-60 " +
          (trailing ? "pr-12" : "") +
          ` ${className}`
        }
        {...rest}
      />
      {trailing ? (
        <div className="absolute right-1.5 flex items-center">{trailing}</div>
      ) : null}
    </div>
  );
}
