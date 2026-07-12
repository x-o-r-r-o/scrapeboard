const TOKEN_KEY = "panel_token";

export type PublicConfig = {
  registration_enabled: boolean;
  recaptcha_mode: "none" | "v2" | "v3";
  recaptcha_site_key: string;
  totp_required: boolean;
};

export type User = {
  id: number;
  username: string;
  email: string;
  role: "admin" | "user";
  is_active: boolean;
  must_change_password: boolean;
  totp_enabled: boolean;
  telegram_id: string | null;
  perms: Record<string, unknown>;
  worker_ids?: number[];
};

function authHeaders(): HeadersInit {
  const t = localStorage.getItem(TOKEN_KEY);
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const auth = authHeaders();
  Object.entries(auth).forEach(([k, v]) => headers.set(k, v as string));

  const res = await fetch(path, { ...init, headers });
  if (res.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const detail =
      typeof data === "object" && data && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : res.statusText;
    throw new Error(detail);
  }
  return data as T;
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}
