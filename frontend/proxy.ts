import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import type { CookieOptions } from "@supabase/ssr";
import { AUTH_PATH, POST_AUTH_REDIRECT } from "@/lib/constants";
import {
  getSupabaseKey,
  getSupabaseUrl,
  hasSessionCookie,
  isSupabaseConfigured,
  SUPABASE_COOKIE_OPTIONS,
} from "@/lib/supabase/env";

type CookieWrite = { name: string; value: string; options: CookieOptions };

/**
 * Gate that runs before any page renders: nobody reaches the app without a
 * verified Supabase session, and a signed-in visitor never sits on the login
 * page.
 *
 * This is the file convention Next 16 renamed `middleware.ts` to; see
 * `node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/proxy.md`.
 * It runs on the Node runtime by default, which is what lets us verify a JWT
 * here rather than only on the client.
 *
 * Client-side guarding alone would not be enough: static HTML is served
 * before any client JS runs, so protected pages would flash (and leak) to
 * signed-out visitors.
 */
export async function proxy(request: NextRequest) {
  // Nothing to enforce until a project is configured. Blocking now would make
  // the app permanently unreachable, since signing in is impossible without keys.
  if (!isSupabaseConfigured()) return NextResponse.next();

  const { pathname } = request.nextUrl;

  // Collected rather than written immediately: Supabase may refresh the token
  // mid-check, and those cookies have to land on whichever response we return
  // — including a redirect.
  const pendingWrites: CookieWrite[] = [];
  let cacheHeaders: Record<string, string> = {};

  const supabase = createServerClient(getSupabaseUrl(), getSupabaseKey(), {
    cookieOptions: SUPABASE_COOKIE_OPTIONS,
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet, headers) {
        // Mirror onto the request too, so anything reading cookies later in
        // this same request sees the refreshed values.
        for (const { name, value } of cookiesToSet) {
          request.cookies.set(name, value);
        }

        pendingWrites.push(...cookiesToSet);
        cacheHeaders = { ...cacheHeaders, ...headers };
      },
    },
  });

  // `getClaims()` verifies the JWT signature, so a hand-crafted cookie does not
  // get in. It is also cheaper than `getUser()`, which round-trips to the Auth
  // server on every request.
  let signedIn = false;
  try {
    const { data } = await supabase.auth.getClaims();
    signedIn = Boolean(data?.claims);
  } catch {
    // Treat an unreadable token as signed out rather than failing the request.
    signedIn = false;
  }

  // A dead access token next to a live refresh token is not a signed-out user.
  // Redirecting them would log them out over a token they can still renew, so
  // we let the request through: `AuthGuard` attempts one refresh and only then
  // sends them to the login page.
  const canRefresh = !signedIn && hasSessionCookie(request.cookies.getAll());

  const isAuthRoute = pathname === AUTH_PATH || pathname.startsWith(`${AUTH_PATH}/`);

  let response: NextResponse;

  if (isAuthRoute) {
    // Deliberately no redirect for a stale session here. Bouncing
    // /auth → /chat → (refresh fails) → /auth would loop forever.
    response = signedIn
      ? NextResponse.redirect(new URL(POST_AUTH_REDIRECT, request.url))
      : NextResponse.next();
  } else if (signedIn || canRefresh) {
    response = NextResponse.next();
  } else {
    const target = new URL(AUTH_PATH, request.url);
    // Relative path only, so the sign-in flow can send people back to exactly
    // where they were headed.
    target.searchParams.set("next", `${pathname}${request.nextUrl.search}`);

    response = NextResponse.redirect(target);
  }

  for (const { name, value, options } of pendingWrites) {
    response.cookies.set(name, value, options);
  }

  for (const [header, value] of Object.entries(cacheHeaders)) {
    response.headers.set(header, value);
  }

  return response;
}

export const config = {
  // Skip static assets; auth only ever gates real pages. Excluding them also
  // keeps the redirect off image and font requests, which would otherwise fail
  // with a CORS error on a cross-origin <img>.
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|css|js|map|woff2?)$).*)",
  ],
};
