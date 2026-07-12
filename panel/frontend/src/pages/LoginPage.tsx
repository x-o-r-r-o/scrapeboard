import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api, getToken, setToken, type PublicConfig } from "../api";

declare global {
  interface Window {
    grecaptcha?: {
      ready: (cb: () => void) => void;
      execute: (siteKey: string, opts: { action: string }) => Promise<string>;
      render: (el: string | HTMLElement, opts: Record<string, unknown>) => number;
      getResponse: (widgetId?: number) => string;
    };
  }
}

export default function LoginPage() {
  const nav = useNavigate();
  const [cfg, setCfg] = useState<PublicConfig | null>(null);
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api<PublicConfig>("/api/auth/public-config").then(setCfg).catch(() =>
      setCfg({ registration_enabled: false, recaptcha_mode: "none", recaptcha_site_key: "", totp_required: true })
    );
  }, []);

  useEffect(() => {
    if (!cfg || cfg.recaptcha_mode === "none" || !cfg.recaptcha_site_key) return;
    const id = "recaptcha-script";
    if (document.getElementById(id)) return;
    const s = document.createElement("script");
    s.id = id;
    s.src =
      cfg.recaptcha_mode === "v3"
        ? `https://www.google.com/recaptcha/api.js?render=${cfg.recaptcha_site_key}`
        : "https://www.google.com/recaptcha/api.js";
    s.async = true;
    document.body.appendChild(s);
  }, [cfg]);

  if (getToken()) return <Navigate to="/app" replace />;

  async function getCaptchaToken(): Promise<string | undefined> {
    if (!cfg || cfg.recaptcha_mode === "none" || !cfg.recaptcha_site_key) return undefined;
    if (cfg.recaptcha_mode === "v3") {
      return new Promise((resolve, reject) => {
        window.grecaptcha?.ready(() => {
          window.grecaptcha!
            .execute(cfg.recaptcha_site_key, { action: "login" })
            .then(resolve)
            .catch(reject);
        });
      });
    }
    const token = window.grecaptcha?.getResponse();
    return token || undefined;
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const recaptcha_token = await getCaptchaToken();
      const res = await api<{
        access_token: string;
        must_change_password: boolean;
        must_setup_2fa: boolean;
      }>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({
          username,
          password,
          totp_code: totp || null,
          recaptcha_token: recaptcha_token || null,
        }),
      });
      setToken(res.access_token);
      if (res.must_change_password) nav("/setup/password");
      else if (res.must_setup_2fa) nav("/setup/2fa");
      else nav("/app");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-shell">
      <form className="card login-card" onSubmit={onSubmit}>
        <p className="brand">GMaps Panel</p>
        <h1>Sign in</h1>
        <p className="muted">Invite-only. No self-registration. 2FA required.</p>
        <label>
          Username
          <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        </label>
        <label>
          Password
          <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </label>
        <label>
          2FA code (if enabled)
          <input className="input" value={totp} onChange={(e) => setTotp(e.target.value)} inputMode="numeric" autoComplete="one-time-code" />
        </label>
        {cfg?.recaptcha_mode === "v2" && cfg.recaptcha_site_key ? (
          <div className="g-recaptcha" data-sitekey={cfg.recaptcha_site_key} />
        ) : null}
        {error ? <p className="error">{error}</p> : null}
        <button className="btn" disabled={loading} type="submit">
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <style>{`
        .login-shell { min-height: 100vh; display:grid; place-items:center; padding: 1.5rem; }
        .login-card { width: min(420px, 100%); display:grid; gap: 0.85rem; }
        .brand { margin:0; letter-spacing:0.12em; text-transform:uppercase; color: var(--accent); font-size:0.75rem; font-weight:700; }
        h1 { margin:0; font-size:1.7rem; }
        label { display:grid; gap:0.35rem; font-size:0.85rem; color: var(--muted); }
      `}</style>
    </div>
  );
}
