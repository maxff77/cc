import type { NextRequest } from "next/server";

import { NextResponse } from "next/server";

// Must mirror backend Settings.session_cookie_name (default "cc_session").
const SESSION_COOKIE = "cc_session";

// Edge middleware can't reach Postgres, so it only gates on session-cookie
// PRESENCE (cheap). The authoritative identity/role check is /api/auth/me,
// which each protected page/server component calls. An unauthenticated visitor
// (no cookie) is redirected to /login. A stale/revoked cookie still reaches the
// page, where /api/auth/me returns 401 and the page handles it.
export function middleware(request: NextRequest) {
  const hasSession = request.cookies.has(SESSION_COOKIE);

  if (!hasSession) {
    const loginUrl = new URL("/login", request.url);

    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  // Run on everything EXCEPT /login, Next internals, the API (backend owns API
  // auth), and static files. Exclusions are anchored to a path segment
  // (`login$`/`login/`) so unrelated routes like `/logins` or `/api-keys` are
  // still gated.
  matcher: [
    "/((?!login(?:/|$)|api(?:/|$)|_next/static|_next/image|favicon.ico).*)",
  ],
};
