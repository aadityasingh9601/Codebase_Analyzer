/** App-wide constants. */

export const APP_NAME = "RepoChat";

/** The login page. Public: `proxy.ts` bounces signed-in visitors away from it. */
export const AUTH_PATH = "/auth";

/** Where OAuth returns: exchanges `?code=` for a session. */
export const AUTH_CALLBACK_PATH = `${AUTH_PATH}/callback`;

/** Where users land after signing in. */
export const POST_AUTH_REDIRECT = "/get-started";
