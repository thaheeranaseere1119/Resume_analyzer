/**
 * Typed fetch wrapper for the FastAPI backend.
 * - Sends cookies (httpOnly access/refresh tokens) and the double-submit CSRF header.
 * - On a 401 it tries one silent refresh, then retries the request once.
 */
export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;
  requestId?: string;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}, requestId?: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

const BASE = "/api/v1";

function csrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(/(?:^|;\s*)ats_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

let refreshing: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  if (!refreshing) {
    refreshing = fetch(`${BASE}/auth/refresh`, { method: "POST", credentials: "include" })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => setTimeout(() => (refreshing = null), 0));
  }
  return refreshing;
}

type Options = Omit<RequestInit, "body"> & { body?: unknown; raw?: boolean; noRefresh?: boolean };

export async function api<T = unknown>(path: string, opts: Options = {}): Promise<T> {
  const { body, raw, noRefresh, headers, ...rest } = opts;
  const method = (rest.method || (body !== undefined ? "POST" : "GET")).toUpperCase();
  const h = new Headers(headers);
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  if (body !== undefined && !isForm) h.set("Content-Type", "application/json");
  if (method !== "GET") {
    const t = csrfToken();
    if (t) h.set("X-CSRF-Token", t);
  }
  const res = await fetch(`${BASE}${path}`, {
    ...rest,
    method,
    headers: h,
    credentials: "include",
    body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
  });
  if (res.status === 401 && !noRefresh && !path.startsWith("/auth/")) {
    if (await tryRefresh()) return api<T>(path, { ...opts, noRefresh: true });
  }
  if (!res.ok) {
    let payload: { error?: { code?: string; message?: string; details?: Record<string, unknown>; request_id?: string } } = {};
    try {
      payload = await res.json();
    } catch {
      /* non-JSON error */
    }
    const e = payload.error || {};
    let message = e.message || `Request failed (${res.status})`;
    // Turn backend field validation errors into a readable sentence ("email: value is not a valid email address").
    const fieldErrors = (e.details?.errors as { loc?: string[]; msg?: string }[] | undefined) ?? [];
    if (e.code === "validation_error" && fieldErrors.length) {
      message = fieldErrors.map((f) => {
        const field = (f.loc ?? []).filter((x) => x !== "body").join(".");
        return `${field ? field.replace(/_/g, " ") + ": " : ""}${(f.msg ?? "").replace(/^Value error, /, "")}`;
      }).join("; ");
    }
    throw new ApiError(res.status, e.code || "http_error", message, e.details, e.request_id);
  }
  if (raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong.";
}
