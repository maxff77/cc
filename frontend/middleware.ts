import type { NextRequest } from "next/server";

import { NextResponse } from "next/server";

// Must mirror backend Settings.session_cookie_name (default "cc_session").
const SESSION_COOKIE = "cc_session";

// Edge middleware can't reach Postgres, so it gates on session-cookie PRESENCE
// (cheap) and then asks the backend /api/auth/me for the authoritative
// identity/role + plan state on real navigations. The /me round-trip is also
// the auth check that revokes a BLOCKED user's session (Story 1.4/1.5); plan
// expiry no longer revokes (it returns a repeatable 403 the client recovers
// from on renewal), so for expiry the redirect carries no invalidation:
//   - no cookie               → /login
//   - prefetch                → continue (don't fire a redirect speculatively)
//   - 403 code=plan_expired   → /expired (KEEP the cookie — repeatable 403;
//                               /expired polls /me to auto-recover on renewal)
//   - 401 / other 403         → /login   (and delete the stale cookie)
//   - backend unreachable/5xx → not authoritative: continue, except /admin/*
//                               where the role gate fails closed to /login
//   - 200 client on /admin/*  → /        (role gate, AC4 of Story 1.3)
//   - otherwise               → continue
export async function middleware(request: NextRequest) {
  if (!request.cookies.has(SESSION_COOKIE)) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  // Link prefetches re-run middleware. Let prefetches through on cookie
  // presence alone — a speculative prefetch shouldn't fire a redirect, and
  // skipping the /me round-trip keeps hover/viewport prefetching from
  // multiplying backend load. (plan_expired is a repeatable 403 now, so even if
  // a prefetch consumed it nothing is lost; the real navigation re-runs and
  // gets the redirect.)
  const isPrefetch =
    request.headers.has("next-router-prefetch") ||
    request.headers.get("purpose") === "prefetch" ||
    (request.headers.get("sec-purpose") ?? "").includes("prefetch");

  if (isPrefetch) return NextResponse.next();

  const isAdminPath = request.nextUrl.pathname.startsWith("/admin");

  // Authoritative "this token is dead" answer → drop the cookie so later
  // navigations short-circuit on the no-cookie branch instead of re-hitting
  // the backend with a revoked token.
  const staleSessionRedirect = () => {
    const redirect = NextResponse.redirect(new URL("/login", request.url));

    redirect.cookies.delete(SESSION_COOKIE);

    return redirect;
  };

  // Backend gave no authoritative answer (unreachable, 5xx). The session may
  // be perfectly valid, so don't log everyone out: fail open outside /admin
  // (pages surface their own API errors) and fail closed on the role gate.
  const backendDownResponse = () =>
    isAdminPath
      ? NextResponse.redirect(new URL("/login", request.url))
      : NextResponse.next();

  // Talk to uvicorn directly over loopback. Using the request's public origin
  // would hairpin through Caddy/TLS, which the Next middleware fetch fails on
  // (observed: every /admin/* bounced to /login, no /me ever hit the backend).
  // The backend lives on 127.0.0.1:8000 in BOTH environments (next.config dev
  // rewrites and the prod Caddy route both target it); override only if that
  // ever changes. NOTE: middleware runs in the edge runtime, so this env is
  // inlined at BUILD time — set it before `next build`, not just at start.
  const backendBase =
    process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000";
  const meUrl = new URL("/api/auth/me", backendBase);

  let res: Response;

  try {
    res = await fetch(meUrl, {
      headers: { cookie: request.headers.get("cookie") ?? "" },
    });
  } catch {
    return backendDownResponse();
  }

  // Expired plan: backend returned 403 plan_expired. Redirect to the public
  // /expired page but KEEP the cookie — plan_expired is a repeatable 403 (the
  // backend no longer revokes the session on expiry), and /expired polls /me to
  // auto-recover the client the moment an admin renews the plan. Deleting the
  // cookie would strip the identity that poll needs. The session can do nothing
  // while expired (every gated call 403s) and dies on its own at SESSION_TTL.
  if (res.status === 403) {
    const body = (await res.json().catch(() => null)) as {
      code?: string;
    } | null;

    if (body?.code === "plan_expired") {
      return NextResponse.redirect(new URL("/expired", request.url));
    }

    // Forced password change (Story 1.6): the session is VALID — the user
    // needs it to complete the change — so unlike plan_expired the cookie is
    // KEPT. /change-password itself must pass through (redirect-loop guard).
    if (body?.code === "password_change_required") {
      if (request.nextUrl.pathname === "/change-password") {
        return NextResponse.next();
      }

      return NextResponse.redirect(new URL("/change-password", request.url));
    }

    return staleSessionRedirect(); // fail safe for any other 403
  }

  // 401: stale/revoked token — authoritative, clear it.
  if (res.status === 401) return staleSessionRedirect();

  // 5xx / anything else non-OK: backend unhealthy, not authoritative.
  if (!res.ok) return backendDownResponse();

  // 200 with an unparseable body: fail CLOSED — the role gate below cannot run
  // without a role, and an undefined role must not fall through to next().
  // (Keep the cookie: the backend said 200, the session itself is fine.)
  let me: { role?: string };

  try {
    me = (await res.json()) as { role?: string };
  } catch {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  // /admin/* is role-gated: a client is redirected away to / (NO blocked
  // screen). admin/owner fall through.
  if (me.role === "client" && isAdminPath) {
    return NextResponse.redirect(new URL("/", request.url));
  }

  // /admin/gates is owner-only (Story 2.1): an admin is redirected to their
  // own surface, /admin/users. Clients were already bounced to / above.
  // Segment-anchored so siblings like /admin/gatesfoo fall through to 404.
  const isGatesPath =
    request.nextUrl.pathname === "/admin/gates" ||
    request.nextUrl.pathname.startsWith("/admin/gates/");

  if (isGatesPath && me.role !== "owner") {
    return NextResponse.redirect(new URL("/admin/users", request.url));
  }

  // /admin/plans is owner-only (pricing-plan catalog): same gate as
  // /admin/gates — an admin is redirected to /admin/users; clients were
  // already bounced to / above. Segment-anchored so /admin/plansfoo 404s.
  const isPlansPath =
    request.nextUrl.pathname === "/admin/plans" ||
    request.nextUrl.pathname.startsWith("/admin/plans/");

  if (isPlansPath && me.role !== "owner") {
    return NextResponse.redirect(new URL("/admin/users", request.url));
  }

  // /admin/destinos is owner-only (multi-target sending): the send-target list
  // is a shared-account safety surface, same gate as /admin/gates. Admins are
  // redirected to their own surface; clients were already bounced to / above.
  const isDestinosPath =
    request.nextUrl.pathname === "/admin/destinos" ||
    request.nextUrl.pathname.startsWith("/admin/destinos/");

  if (isDestinosPath && me.role !== "owner") {
    return NextResponse.redirect(new URL("/admin/users", request.url));
  }

  return NextResponse.next();
}

export const config = {
  // Run on everything EXCEPT /login, /expired (must be reachable WITHOUT a
  // session — a freshly-locked-out client has had theirs revoked), Next
  // internals, the API (backend owns API auth), /ws (the backend owns WS auth
  // via the cookie handshake — middleware must not consume a /me round-trip
  // or interfere with the upgrade, Story 2.2), and static files — a KNOWN
  // asset extension, so /public assets never burn a backend /me call. The
  // exclusion is an explicit extension list, NOT `.+\.\w+$`: dynamic segments
  // like /admin/tenants/1.2 contain a dot, and a bare any-extension exclusion
  // would skip the /admin role gate on them (Story 3.6 review).
  // Exclusions are anchored to a path segment (`login$`/`login/`) so unrelated
  // routes like `/logins` or `/api-keys` are still gated.
  matcher: [
    "/((?!login(?:/|$)|expired(?:/|$)|api(?:/|$)|ws(?:/|$)|_next/|.+\\.(?:js|css|map|json|png|jpe?g|gif|svg|ico|webp|avif|woff2?|ttf|txt|xml)$).*)",
  ],
};
