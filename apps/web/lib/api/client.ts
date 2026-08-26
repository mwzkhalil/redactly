/**
 * Same-origin client for the policy service.
 *
 * Requests go through the Next.js rewrite at `/api/backend/*`, so the session
 * cookie stays `SameSite=Strict` and `HttpOnly`: no script on this page can read
 * it, including anything an agent injects.
 */

const BASE_PATH = "/api/backend";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly payload: unknown,
  ) {
    super(`request failed with status ${status}`);
    this.name = "ApiError";
  }
}

async function request<T>(
  method: "GET" | "POST",
  path: string,
  options: { body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const response = await fetch(`${BASE_PATH}${path}`, {
    method,
    credentials: "same-origin",
    headers: options.body === undefined ? undefined : { "content-type": "application/json" },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
    cache: "no-store",
  });

  const text = await response.text();
  const payload = text ? (JSON.parse(text) as unknown) : null;
  if (!response.ok) {
    throw new ApiError(response.status, payload);
  }
  return payload as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>("GET", path, { signal }),
  post: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    request<T>("POST", path, { body, signal }),
};

export async function ensureSession(): Promise<void> {
  const status = await api.get<{ active: boolean }>("/session");
  if (!status.active) {
    await api.post("/session");
  }
}
