import { NextResponse } from "next/server";

/**
 * Supabase OAuth landing route.
 *
 * Google returns here with `?code=...`. Supabase's client is configured with
 * `detectSessionInUrl: true`, so the browser-side client normally completes
 * the exchange by itself; this route then forwards the user into the app.
 *
 * We do the exchange server-side too as a fallback: it makes the redirect
 * reliable on browsers where the client-side step races the navigation, and
 * it gives us one place to send people after sign-in.
 */
export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");
  const next = searchParams.get("next") ?? "/get-started";

  // Only allow same-origin relative paths, so `next` can't be used as an open
  // redirect to another site.
  const safeNext = next.startsWith("/") && !next.startsWith("//") ? next : "/get-started";

  if (!code) {
    return NextResponse.redirect(`${origin}${safeNext}`);
  }

  try {
    const { getSupabaseUrl, getSupabaseKey } = await import("@/lib/supabase");
    const url = getSupabaseUrl();
    const key = getSupabaseKey();

    if (url && key) {
      const response = await fetch(`${url}/auth/v1/token?grant_type=pkce`, {
        method: "POST",
        headers: {
          apikey: key,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ auth_code: code }),
      });

      if (response.ok) {
        const payload = await response.json();

        // Hand the session to the browser client via cookies, which is what
        // `persistSession` reads on the next page load.
        const res = NextResponse.redirect(`${origin}${safeNext}`);
        if (payload?.access_token && payload?.refresh_token) {
          const session = {
            access_token: payload.access_token,
            refresh_token: payload.refresh_token,
            expires_at: payload.expires_at ?? Math.floor(Date.now() / 1000) + 3600,
            token_type: payload.token_type ?? "bearer",
          };
          res.cookies.set("sb-access-token", payload.access_token, {
            httpOnly: false,
            sameSite: "lax",
            secure: origin.startsWith("https"),
            path: "/",
            maxAge: 60 * 60 * 24 * 7,
          });
          res.cookies.set("sb-refresh-token", payload.refresh_token, {
            httpOnly: false,
            sameSite: "lax",
            secure: origin.startsWith("https"),
            path: "/",
            maxAge: 60 * 60 * 24 * 7,
          });
          res.cookies.set("repochat-session", JSON.stringify(session), {
            httpOnly: true,
            sameSite: "lax",
            secure: origin.startsWith("https"),
            path: "/",
            maxAge: 60 * 60 * 24 * 7,
          });
        }
        return res;
      }
    }
  } catch {
    // Fall through to the plain redirect below; the client-side exchange in
    // lib/supabase.ts will handle it.
  }

  return NextResponse.redirect(`${origin}${safeNext}`);
}
