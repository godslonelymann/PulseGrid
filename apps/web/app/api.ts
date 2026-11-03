export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public fields: Record<string, string> = {},
  ) {
    super(message);
  }
}
function csrf() {
  return document.cookie
    .split("; ")
    .find((c) => c.startsWith("pg_csrf="))
    ?.split("=")[1];
}
let initializing: Promise<void> | null = null;
async function ensureCsrf() {
  if (csrf()) return;
  initializing ??= fetch("/api/v1/auth/csrf", { credentials: "same-origin" })
    .then((r) => {
      if (!r.ok) throw new Error("API unavailable");
    })
    .finally(() => {
      initializing = null;
    });
  await initializing;
}
async function raw(path: string, method = "GET", body?: unknown) {
  if (method !== "GET") await ensureCsrf();
  return fetch("/api/v1" + path, {
    method,
    credentials: "same-origin",
    headers: {
      ...(method !== "GET" ? { "X-CSRF-Token": csrf() || "" } : {}),
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
}
async function refreshOnce() {
  const check = await raw("/me");
  if (check.ok) return;
  if (check.status !== 401)
    throw new ApiError(
      check.status,
      "Connection interrupted. Please try again.",
    );
  const result = await raw("/auth/refresh", "POST");
  if (!result.ok)
    throw new ApiError(
      result.status,
      result.status === 401
        ? "Session expired. Sign in again."
        : "Connection interrupted. Please try again.",
    );
}
export async function api<T = Record<string, unknown>>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  let r = await raw(path, method, body);
  if (
    r.status === 401 &&
    !["/auth/login", "/auth/verify", "/auth/refresh", "/auth/csrf"].includes(
      path,
    ) &&
    path !== "/me"
  ) {
    if (navigator.locks)
      await navigator.locks.request("pulsegrid-refresh", refreshOnce);
    else await refreshOnce();
    r = await raw(path, method, body);
  }
  if (!r.ok) {
    const data = await r
      .json()
      .catch(() => ({ detail: "API temporarily unavailable" }));
    const issues: { loc?: string[]; msg: string }[] = Array.isArray(data.detail)
      ? data.detail
      : [];
    const detail =
      typeof data.detail === "string"
        ? data.detail
        : issues.map((issue) => issue.msg).join("; ");
    const fields = Object.fromEntries(
      issues.flatMap((issue) => {
        const field = issue.loc?.[1];
        return field ? [[field, issue.msg]] : [];
      }),
    );
    throw new ApiError(
      r.status,
      detail || `Request failed (${r.status})`,
      fields,
    );
  }
  return r.json();
}
export async function currentUser<T>(): Promise<T> {
  try {
    return await api<T>("/me");
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      if (navigator.locks)
        await navigator.locks.request("pulsegrid-refresh", refreshOnce);
      else await refreshOnce();
      return api<T>("/me");
    }
    throw e;
  }
}
