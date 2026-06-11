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

  if (!res.ok) {
    let body: ApiErrorBody;

    try {
      body = (await res.json()) as ApiErrorBody;
    } catch {
      body = { code: "unknown_error", message: "Ocurrió un error inesperado." };
    }

    // Total lockout (Story 1.4): the backend answers 403 plan_expired exactly
    // ONCE — it revokes the session as it does — so whichever call consumes it
    // must route to the lockout page instead of surfacing a per-page error.
    // (Skip when already there: the /expired page itself probes /me.)
    if (
      res.status === 403 &&
      body.code === "plan_expired" &&
      window.location.pathname !== "/expired"
    ) {
      window.location.assign("/expired");
    }

    throw new ApiError(res.status, body);
  }

  if (res.status === 204) return undefined as T;

  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path, { method: "GET" }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};
