"use server";

import { getAuthHeaders, getBackendUrl } from "@/lib/utils/getSession";

export type UsageInfo = {
  used: number;
  limit: number;
  remaining: number;
  date: string;
};

export type UsageResult = { ok: true; usage: UsageInfo } | { ok: false; error: string };

export async function getUsage(): Promise<UsageResult> {
  let headers: Record<string, string>;
  try {
    ({ headers } = await getAuthHeaders());
  } catch (e) {
    return {
      ok: false,
      error: e instanceof Error ? e.message : "Your session expired. Sign in again to continue.",
    };
  }

  let res: Response;
  try {
    res = await fetch(`${getBackendUrl()}/api/usage`, {
      headers,
      signal: AbortSignal.timeout(30_000),
      cache: "no-store",
    });
  } catch (e) {
    const timedOut = e instanceof DOMException && e.name === "TimeoutError";
    return {
      ok: false,
      error: timedOut ? "Loading usage timed out. Try again." : "Could not reach the usage service.",
    };
  }

  const payload = await readJson(res);
  if (!res.ok) return { ok: false, error: errorFrom(payload, res.status) };
  if (!isUsage(payload)) return { ok: false, error: "Unexpected usage response." };
  return {
    ok: true,
    usage: { used: payload.used, limit: payload.limit, remaining: payload.remaining, date: payload.date },
  };
}

async function readJson(r: Response) {
  const t = await r.text();
  if (!t) return null;
  try { return JSON.parse(t); } catch { return t; }
}

function errorFrom(p: unknown, status: number): string {
  if (typeof p === "object" && p !== null && "detail" in p) {
    const d = (p as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  if (typeof p === "string" && p.trim() && !p.startsWith("<")) return p.slice(0, 300);
  return `Usage failed (HTTP ${status}).`;
}

function isUsage(v: unknown): v is UsageInfo {
  if (typeof v !== "object" || v === null) return false;
  const c = v as Record<string, unknown>;
  return typeof c.used === "number" && typeof c.limit === "number" && typeof c.remaining === "number" && typeof c.date === "string";
}
