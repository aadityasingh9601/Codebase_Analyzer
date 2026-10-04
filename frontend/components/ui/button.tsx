import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

const base =
  "inline-flex items-center justify-center gap-2 rounded-xl font-medium " +
  "transition-[background-color,border-color,color,transform,box-shadow] duration-150 " +
  "active:scale-[0.985] disabled:pointer-events-none disabled:opacity-55";

const variants: Record<Variant, string> = {
  primary:
    "bg-accent text-accent-foreground shadow-sm hover:brightness-110 " +
    "shadow-[0_1px_2px_rgba(0,0,0,0.08)]",
  secondary:
    "border border-border-base bg-surface text-foreground hover:bg-surface-subtle " +
    "hover:border-border-strong",
  ghost: "text-muted hover:bg-surface-subtle hover:text-foreground",
  danger:
    "border border-border-base bg-surface text-foreground hover:border-red-500/45 hover:text-red-600",
};

const sizes: Record<Size, string> = {
  sm: "h-8 px-3 text-[13px]",
  md: "h-10 px-4 text-sm",
  lg: "h-11 px-5 text-[15px]",
};

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  children: ReactNode;
};

export function Button({
  variant = "primary",
  size = "md",
  className = "",
  children,
  ...rest
}: Props) {
  return (
    <button
      className={`${base} ${variants[variant]} ${sizes[size]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}
