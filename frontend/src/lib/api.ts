/** Thin fetch wrapper for the backend. All paths are relative (`/api/...`) and proxied by Next.js. */

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail?: unknown,
  ) {
    super(message);
  }
}

/** Auth endpoints report their own 401s (wrong password); everything else means "your session ended". */
const AUTH_PATH = "/api/auth/";

async function endSession() {
  await fetch("/api/auth/logout", { method: "POST" }).catch(() => undefined); // clears the stale cookie
  const here = window.location.pathname + window.location.search;
  window.location.assign(`/login?next=${encodeURIComponent(here)}`);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { Accept: "application/json", ...init?.headers } });
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith(AUTH_PATH) && typeof window !== "undefined") {
      await endSession();
    }
    let detail: unknown;
    try {
      detail = (await response.json()).detail;
    } catch {
      /* body was not JSON */
    }
    const message = typeof detail === "string" ? detail : `Request failed (${response.status})`;
    throw new ApiError(message, response.status, detail);
  }
  return (await response.json()) as T;
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  upload: <T,>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form }),
};
