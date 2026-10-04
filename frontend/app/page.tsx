import { redirect } from "next/navigation";
import { AUTH_PATH, POST_AUTH_REDIRECT } from "@/lib/constants";
import { isSupabaseConfigured } from "@/lib/supabase/env";
import { getServerUser } from "@/lib/supabase/server";

/**
 * Root route: sign in first, then go to the app.
 *
 * A Server Component rather than a client-side check so the redirect is part
 * of the server response — no flash of the wrong page, and no prerendered HTML
 * that has to be corrected after hydration.
 */
export default async function IndexPage() {
  // With no Supabase project there is nobody to sign in, so show the app rather
  // than a login screen that cannot work. See `isSupabaseConfigured`.
  if (!isSupabaseConfigured()) {
    redirect(POST_AUTH_REDIRECT);
  }

  const user = await getServerUser();

  redirect(user ? POST_AUTH_REDIRECT : AUTH_PATH);
}
