import Link from "next/link";

export default function NotFound() {
  return (
    <main className="flex flex-1 items-center justify-center px-5">
      <div className="text-center">
        <p className="text-[12px] font-semibold uppercase tracking-[0.09em] text-accent">
          404
        </p>
        <h1 className="mt-2 text-[24px] font-semibold tracking-[-0.02em] text-foreground">
          This page doesn&apos;t exist
        </h1>
        <p className="mx-auto mt-2 max-w-[340px] text-[14px] leading-relaxed text-muted">
          The link may be out of date. Head back and connect a repository to
          keep going.
        </p>
        <Link
          href="/get-started"
          className="mt-6 inline-flex h-10 items-center rounded-xl bg-accent px-4 text-sm font-medium text-accent-foreground transition-[filter] hover:brightness-110"
        >
          Go to setup
        </Link>
      </div>
    </main>
  );
}
