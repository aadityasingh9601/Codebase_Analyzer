import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { REDIRECT_COOKIE_NAME, readRedirectCookie } from "@/lib/auth/redirect";
import { POST_AUTH_REDIRECT } from "@/lib/constants";
import {
  getSupabaseKey,
  getSupabaseUrl,
  isSupabaseConfigured,
  SUPABASE_COOKIE_OPTIONS,
} from "@/lib/supabase/env";

/**
 * Google OAuth landing route.
 *
 * Google → Supabase → here with `?code=`. The browser cannot complete the PKCE
 * exchange itself: the code verifier lives in a cookie written by the browser
 * client at the moment `signInWithOAuth` was called, and this handler is the
 * only place both halves are available. So the exchange happens here, on the
 * server, and the resulting session cookies are set on the redirect response.
 *
 * Exchanging by hand with `fetch(.../token?grant_type=pkce)` — which this route
 * used to do — cannot work: without the verifier Supabase rejects the code, and
 * a browser-readable cookie is the wrong home for a token anyway.
 */
export async function GET(request: NextRequest) {
  const { searchParams, origin } = request.nextUrl;
  const code = searchParams.get("code");
  const providerError = searchParams.get("error_description") ?? searchParams.get("error");

  /** @see lib/auth/redirect.ts */
  const next = readRedirectCookie(request.headers.get("cookie")) ?? POST_AUTH_REDIRECT;

  if (!isSupabaseConfigured()) {
    return redirectToAuth(origin, "Supabase is not configured.");
  }

  // Declined on Google's consent screen, or Google failed: no code to exchange.
  if (!code) {
    return redirectToAuth(origin, providerError);
  }

  // Built before the exchange so `setAll` has a response to write the session
  // cookies onto. Cookies set on a redirect response are still delivered by the
  // browser; mutating `request.cookies` only affects later reads in this same
  // request, which is why we need both.
  const response = redirectTo(next, origin);

  const supabase = createServerClient(getSupabaseUrl(), getSupabaseKey(), {
    cookieOptions: SUPABASE_COOKIE_OPTIONS,
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet, headers) {
        for (const { name, value } of cookiesToSet) {
          request.cookies.set(name, value);
        }

        for (const { name, value, options } of cookiesToSet) {
          response.cookies.set(name, value, options);
        }

        for (const [header, value] of Object.entries(headers)) {
          response.headers.set(header, value);
        }
      },
    },
  });

  const { error } = await supabase.auth.exchangeCodeForSession(code);

  if (error) {
    return redirectToAuth(origin, error.message);
  }

  return response;
}

function redirectTo(path: string, origin: string): NextResponse {
  return forgetRedirect(NextResponse.redirect(`${origin}${path}`));
}

/**
 * Returns to the login page with a reason attached.
 *
 * Also clears the saved destination, otherwise a failed attempt would replay
 * the same stale path on the next successful sign-in.
 */
function redirectToAuth(origin: string, reason?: string | null): NextResponse {
  const target = new URL("/auth", origin);

  if (reason) {
    // Long provider strings would blow up the URL; the login page shows them
    // truncated as a hint, not as a verbatim error.
    target.searchParams.set("error", reason.slice(0, 200));
  }

  return forgetRedirect(NextResponse.redirect(target));
}

/** One-shot destination: expire it on every path out of this route. */
function forgetRedirect(response: NextResponse): NextResponse {
  response.cookies.set(REDIRECT_COOKIE_NAME, "", { path: "/", maxAge: 0 });
  return response;
}
