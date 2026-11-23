import type { components } from "./api-schema";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
let csrfToken: string | null = null;

export async function ensureCsrfToken(): Promise<string> {
  if (csrfToken) return csrfToken;
  const response = await fetch(`${API_BASE_URL}/api/v1/auth/csrf`, {
    credentials: "include",
  });
  if (!response.ok) throw new ApiError(response.status);
  const body = (await response.json()) as components["schemas"]["CsrfResponse"];
  csrfToken = body.csrf_token;
  return csrfToken;
}

export function clearCsrfToken() {
  csrfToken = null;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code?: string,
  ) {
    super(code ?? "API_REQUEST_FAILED");
  }
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && method !== "GET") {
    headers.set("Content-Type", "application/json");
  }
  if (method !== "GET" && method !== "HEAD" && method !== "OPTIONS") {
    headers.set("X-CSRF-Token", await ensureCsrfToken());
  }

  const response = await fetch(`${API_BASE_URL}/api/v1${path}`, {
    ...init,
    credentials: "include",
    headers,
  });

  if (!response.ok) {
    // Not trusted to match the schema: a proxy in between may answer instead.
    const body = (await response.json().catch(() => null)) as Partial<
      components["schemas"]["ErrorResponse"]
    > | null;
    throw new ApiError(response.status, body?.error?.code);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const body = (await response.json()) as T & {
    csrf_token?: string;
  };
  if (body.csrf_token) {
    csrfToken = body.csrf_token;
  }
  return body;
}
