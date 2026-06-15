// Fetch wrapper + error-contract parsing shared by every client-side call.
//
// Backend success bodies are the payload directly (no {success,data} wrapper);
// errors are {code, message} with a meaningful HTTP status. We surface that as
// a typed ApiError so the UI can branch on `code` (and fall back to `message`).

export interface ApiErrorBody {
  code: string;
  message: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code;
  }
}

// Shared error-contract handling for any non-ok response (extracted so
// downloadFile gets the same normalization and lockout redirects as request —
// byte-for-byte the behavior that lived inline in `request`).
async function toApiError(res: Response): Promise<ApiError> {
  let body: ApiErrorBody;

  // Normalize non-contract bodies (e.g. FastAPI 422 {detail: [...]}) so
  // `message` is never empty — the UI renders it directly.
  try {
    const parsed = (await res.json()) as Partial<ApiErrorBody>;

    body = {
      code: typeof parsed.code === "string" ? parsed.code : "unknown_error",
      message:
        typeof parsed.message === "string" && parsed.message !== ""
          ? parsed.message
          : "Ocurrió un error inesperado.",
    };
  } catch {
    body = { code: "unknown_error", message: "Ocurrió un error inesperado." };
  }

  // Plan lockout (Story 1.4): the backend answers 403 plan_expired on EVERY
  // request (repeatable — the session is NOT revoked, so the /expired page can
  // poll /me and auto-recover the client on renewal). Any consuming call routes
  // to the lockout page instead of surfacing a per-page error. Skip when already
  // there: the /expired page probes /me itself and branches on the error code.
  if (
    res.status === 403 &&
    body.code === "plan_expired" &&
    window.location.pathname !== "/expired"
  ) {
    window.location.assign("/expired");
  }

  // Forced password change (Story 1.6): any client-side call from a flagged
  // user's open tab routes to the one allowed page. Repeatable 403 (the
  // session survives), so no cookie/state cleanup is needed here.
  if (
    res.status === 403 &&
    body.code === "password_change_required" &&
    window.location.pathname !== "/change-password"
  ) {
    window.location.assign("/change-password");
  }

  return new ApiError(res.status, body);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    // Send/receive the httpOnly session cookie on same-origin requests.
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!res.ok) throw await toApiError(res);

  if (res.status === 204) return undefined as T;

  return (await res.json()) as T;
}

// Browser download via fetch+blob (Story 3.5) — NOT a direct <a href>: an
// anchor hitting a 401/403/404 would navigate to raw error JSON (a dead-end,
// UX-DR16) and skip the plan_expired/password_change redirects toApiError
// guarantees on every call. The backend is the SINGLE authority on the
// filename (Content-Disposition; same-origin fetch exposes every header —
// the exposure restrictions are CORS-only).
export async function downloadFile(path: string): Promise<void> {
  // GET with no body — no Content-Type header on purpose. cache: "no-store"
  // mirrors the backend's Cache-Control: the export is generated on the fly
  // and must never be served from any cache (the body carries CC data).
  const res = await fetch(path, { credentials: "include", cache: "no-store" });

  if (!res.ok) throw await toApiError(res);

  const disposition = res.headers.get("Content-Disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "export.txt";

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);

  try {
    const anchor = document.createElement("a");

    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}

export const api = {
  get: <T>(path: string) => request<T>(path, { method: "GET" }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PUT",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PATCH",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};
