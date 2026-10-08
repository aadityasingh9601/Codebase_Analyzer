import Link from "next/link";
import type { ReactNode } from "react";
import { AuthGuard } from "@/components/auth-guard";
import { Brand } from "@/components/brand";
import { ThemeToggle } from "@/components/theme-toggle";
import { UserMenu } from "@/components/user-menu";

/**
 * Chrome shared by the signed-in pages (navbar). Kept in one place so
 * /upload-repo and /chat stay visually identical.
 *
 * The auth guard lives here rather than in each page so a new protected route
 * cannot be added without it: anything rendered inside `AppShell` requires a
 * session. `proxy.ts` is the real gate; this is the client-side backstop.
 */
export function AppShell({
  children,
  /** Shows the repo pill + "New repo" affordance once one is selected. */
  repo,
}: {
  children: ReactNode;
  repo?: { slug: string } | null;
}) {
  return (
    <div className="fixed inset-0 flex flex-col overflow-hidden">
      <header className="sticky top-0 z-30 border-b border-border-base bg-background/85 backdrop-blur-md">
        <div className="mx-auto flex h-14 w-full max-w-[1180px] items-center gap-3 px-5">
          <Link
            href="/upload-repo"
            className="shrink-0 transition-opacity hover:opacity-80"
            aria-label="RepoChat home"
          >
            <Brand />
          </Link>

          <span className="h-4 w-px bg-border-base" aria-hidden="true" />

          <nav className="hidden items-center gap-1 sm:flex">
            <NavLink href="/upload-repo">New repo</NavLink>
            <NavLink href="/chat">Chat</NavLink>
          </nav>

          {repo ? (
            <span className="ml-1 hidden max-w-[220px] items-center gap-1.5 rounded-full border border-border-base bg-surface-subtle px-2.5 py-1 font-mono text-[12px] text-muted md:inline-flex">
              <span className="truncate">{repo.slug}</span>
            </span>
          ) : null}

          <div className="ml-auto flex items-center gap-1.5">
            <ThemeToggle />
            <UserMenu />
          </div>
        </div>
      </header>

      <AuthGuard>{children}</AuthGuard>
    </div>
  );
}

function NavLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link
      href={href}
      className="rounded-lg px-2.5 py-1.5 text-[13.5px] font-medium text-muted transition-colors hover:bg-surface-subtle hover:text-foreground"
    >
      {children}
    </Link>
  );
}
