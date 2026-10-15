let token: string | null = null;
const apiBase = (import.meta.env.VITE_API_BASE_URL || "/api").replace(
  /\/$/,
  "",
);
export function setAccessToken(value: string | null) {
  token = value;
}

async function request(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${apiBase}${path}`, {
    credentials: "include",
    ...options,
    headers,
  });
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try {
      const error = await response.json();
      if (typeof error.detail === "string") message = error.detail;
      else if (Array.isArray(error.detail))
        message = error.detail
          .map((item: { msg?: string }) => item.msg || "Invalid request")
          .join("; ");
    } catch {
      /* Keep the useful HTTP status for non-JSON proxy errors. */
    }
    throw new Error(message);
  }
  return response;
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await request(path, options);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function apiText(
  path: string,
  options: RequestInit = {},
): Promise<string> {
  const headers = new Headers(options.headers);
  if (!headers.has("Accept")) headers.set("Accept", "text/markdown");
  const response = await request(path, { ...options, headers });
  return response.text();
}

export function friendlyError(error: unknown) {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}
export function safeExternalUrl(
  value: string | null | undefined,
): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:"
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}
