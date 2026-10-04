import { AlertCircleIcon, CheckCircleIcon, SpinnerIcon } from "@/components/icons";

type Tone = "info" | "success" | "error";

const tones: Record<Tone, string> = {
  info: "border-border-base bg-surface-subtle text-muted",
  success:
    "border-emerald-500/25 bg-emerald-500/[0.07] text-emerald-700 dark:text-emerald-300",
  error: "border-red-500/30 bg-red-500/[0.07] text-red-700 dark:text-red-400",
};

const icons: Record<Tone, React.ReactNode> = {
  info: <SpinnerIcon className="h-4 w-4 shrink-0" />,
  success: <CheckCircleIcon className="h-4 w-4 shrink-0" />,
  error: <AlertCircleIcon className="h-4 w-4 shrink-0" />,
};

export function Notice({
  tone = "info",
  children,
}: {
  tone?: Tone;
  children: React.ReactNode;
}) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={`flex items-start gap-2.5 rounded-xl border px-3.5 py-3 text-[13px] leading-relaxed ${tones[tone]}`}
    >
      <span className="mt-px">{icons[tone]}</span>
      <span className="min-w-0">{children}</span>
    </div>
  );
}
