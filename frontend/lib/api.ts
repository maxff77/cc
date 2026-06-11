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
