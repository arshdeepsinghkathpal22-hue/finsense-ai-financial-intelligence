// Small fetch wrapper for the FinSense API.
//
// Authentication uses an HttpOnly session cookie set by the server, so no
// token is ever stored in JavaScript. State-changing requests echo the
// readable `fs_csrf` cookie in the X-CSRF-Token header (double-submit CSRF
// protection checked by the backend).

export const API_BASE = "/api/v1";
const CSRF_COOKIE = "fs_csrf";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export interface ApiErrorBody {
  error?: { code?: string; message?: string; details?: Record<string, unknown> };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** Field-level problems from a 422 validation response, if any. */
  get problems(): { location: string[]; message: string }[] {
    const problems = this.details.problems;
    if (!Array.isArray(problems)) return [];
    return problems.filter(
      (p): p is { location: string[]; message: string } =>
        typeof p === "object" && p !== null && "message" in p,
    );
  }
}

type Listener = () => void;
const unauthorizedListeners = new Set<Listener>();

/** Called whenever the API answers 401 (session missing or expired). */
export function onUnauthorized(listener: Listener): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

export function readCookie(name: string): string | null {
  const prefix = `${name}=`;
  for (const part of document.cookie.split(";")) {
    const trimmed = part.trim();
    if (trimmed.startsWith(prefix)) return decodeURIComponent(trimmed.slice(prefix.length));
  }
  return null;
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  form?: FormData;
  signal?: AbortSignal;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = (options.method ?? (options.body !== undefined || options.form ? "POST" : "GET")).toUpperCase();
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (options.form) {
    body = options.form; // the browser sets the multipart boundary
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  if (!SAFE_METHODS.has(method)) {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body,
      credentials: "same-origin",
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "network_error", "Cannot reach the FinSense server. Check that the backend is running.");
  }

  if (response.status === 204) return undefined as T;
  const text = await response.text();
  let data: unknown = undefined;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = undefined;
    }
  }
  if (!response.ok) {
    const envelope = (data ?? {}) as ApiErrorBody;
    const error = new ApiError(
      response.status,
      envelope.error?.code ?? "http_error",
      envelope.error?.message ?? `Request failed with status ${response.status}.`,
      envelope.error?.details ?? {},
    );
    if (response.status === 401) unauthorizedListeners.forEach((listener) => listener());
    throw error;
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => apiRequest<T>(path, { signal }),
  post: <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: "POST", body: body ?? {} }),
  put: <T>(path: string, body: unknown) => apiRequest<T>(path, { method: "PUT", body }),
  patch: <T>(path: string, body: unknown) => apiRequest<T>(path, { method: "PATCH", body }),
  delete: <T = void>(path: string) => apiRequest<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, form: FormData) => apiRequest<T>(path, { method: "POST", form }),
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const problems = error.problems.map((p) => `${p.location.filter((l) => l !== "body").join(".")}: ${p.message}`);
    const extra = Array.isArray(error.details.problems) && typeof error.details.problems[0] === "string"
      ? (error.details.problems as string[])
      : [];
    return [error.message, ...problems, ...extra].join(" ");
  }
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}
