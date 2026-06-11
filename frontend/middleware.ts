import type { NextRequest } from "next/server";

import { NextResponse } from "next/server";

// Must mirror backend Settings.session_cookie_name (default "cc_session").
const SESSION_COOKIE = "cc_session";

// Edge middleware can't reach Postgres, so it only gates on session-cookie
// PRESENCE (cheap). The authoritative identity/role check is /api/auth/me,
// which each protected page/server component calls. An unauthenticated visitor
// (no cookie) is redirected to /login. A stale/revoked cookie still reaches the
// page, where /api/auth/me returns 401 and the page handles it.
export async function middleware(request: NextRequest) {
  const hasSession = request.cookies.has(SESSION_COOKIE);

  if (!hasSession) {
    const loginUrl = new URL("/login", request.url);

    return NextResponse.redirect(loginUrl);
  }

  // /admin/* is role-gated (AC4). The Edge/Node middleware can't reach Postgres,
  // so it resolves the role authoritatively by asking the backend, forwarding
  // the inbound session cookie. A 401 (stale/revoked) → /login; a client → /
  // (redirected away, NO blocked screen). admin/owner fall through. Non-/admin
  // protected paths keep the cheap cookie-presence gate above. The page/server
  // components still call /api/auth/me for role-conditional UI — belt and
  // suspenders, this is not the only check.
  if (request.nextUrl.pathname.startsWith("/admin")) {
    const meUrl = new URL("/api/auth/me", request.nextUrl.origin);
    const loginRedirect = NextResponse.redirect(new URL("/login", request.url));

    let me: { role?: string };
    try {
      const res = await fetch(meUrl, {
        headers: { cookie: request.headers.get("cookie") ?? "" },
      });

      // 401 (stale/revoked) → /login. Any non-OK status is treated the same:
      // fail safe rather than fall through and expose the gated page.
      if (!res.ok) return loginRedirect;

      me = (await res.json()) as { role?: string };
    } catch {
      // Backend unreachable / non-JSON body → fail safe to /login instead of
      // throwing (which would 500 every /admin navigation).
      return loginRedirect;
    }

    if (me.role === "client") {
      return NextResponse.redirect(new URL("/", request.url));
    }
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
