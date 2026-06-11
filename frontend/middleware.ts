import type { NextRequest } from "next/server";

import { NextResponse } from "next/server";

// Must mirror backend Settings.session_cookie_name (default "cc_session").
const SESSION_COOKIE = "cc_session";

// Edge middleware can't reach Postgres, so it gates on session-cookie PRESENCE
// (cheap) and then asks the backend /api/auth/me for the authoritative
// identity/role + plan state on EVERY matched route. That same /me round-trip
// is the auth check that triggers backend session revocation on expiry
// (Story 1.4), so the redirect and the invalidation happen together:
//   - no cookie               → /login
//   - 403 code=plan_expired   → /expired (and delete the stale cookie)
//   - any other non-OK / error→ /login (fail safe)
//   - 200 client on /admin/*  → /        (role gate, AC4 of Story 1.3)
//   - otherwise               → continue
export async function middleware(request: NextRequest) {
  const hasSession = request.cookies.has(SESSION_COOKIE);

  if (!hasSession) {
    const loginUrl = new URL("/login", request.url);

    return NextResponse.redirect(loginUrl);
  }

  const meUrl = new URL("/api/auth/me", request.nextUrl.origin);
  const loginRedirect = NextResponse.redirect(new URL("/login", request.url));

  let res: Response;

  try {
    res = await fetch(meUrl, {
      headers: { cookie: request.headers.get("cookie") ?? "" },
    });
  } catch {
    // Backend unreachable → fail safe to /login instead of 500-ing every
    // navigation.
    return loginRedirect;
  }

  // Expired plan: backend has just revoked the session and returned 403
  // plan_expired. Redirect to the public /expired page and drop the now-stale
  // cookie so later navigations don't bounce to /login on a revoked token.
  if (res.status === 403) {
    const body = (await res.json().catch(() => null)) as {
      code?: string;
    } | null;

    if (body?.code === "plan_expired") {
      const redirect = NextResponse.redirect(new URL("/expired", request.url));

      redirect.cookies.delete(SESSION_COOKIE);

      return redirect;
    }

    return loginRedirect; // fail safe for any other 403
  }

  // 401 (stale/revoked) / 5xx / non-JSON → fail safe to /login.
  if (!res.ok) return loginRedirect;

  const me = (await res.json().catch(() => ({}))) as { role?: string };

  // /admin/* is role-gated: a client is redirected away to / (NO blocked
  // screen). admin/owner fall through.
  if (me.role === "client" && request.nextUrl.pathname.startsWith("/admin")) {
    return NextResponse.redirect(new URL("/", request.url));
  }

  return NextResponse.next();
}

export const config = {
  // Run on everything EXCEPT /login, /expired (must be reachable WITHOUT a
  // session — a freshly-locked-out client has had theirs revoked), Next
  // internals, the API (backend owns API auth), and static files. Exclusions
  // are anchored to a path segment (`login$`/`login/`) so unrelated routes like
  // `/logins` or `/api-keys` are still gated.
  matcher: [
    "/((?!login(?:/|$)|expired(?:/|$)|api(?:/|$)|_next/static|_next/image|favicon.ico).*)",
  ],
};
