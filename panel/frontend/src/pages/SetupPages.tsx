import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";

export function PasswordSetupPage() {
  const nav = useNavigate();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState("");

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/api/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      nav("/setup/2fa");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <div className="login-shell">
      <form className="card login-card" onSubmit={onSubmit}>
        <h1>Change password</h1>
        <p className="muted">Required before you can use the panel.</p>
        <label>
          Current password
          <input className="input" type="password" value={current} onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <label>
          New password
          <input className="input" type="password" value={next} onChange={(e) => setNext(e.target.value)} minLength={8} />
        </label>
        {error ? <p className="error">{error}</p> : null}
        <button className="btn" type="submit">
          Continue
        </button>
      </form>
      <style>{`.login-shell{min-height:100vh;display:grid;place-items:center;padding:1.5rem}.login-card{width:min(420px,100%);display:grid;gap:.85rem}label{display:grid;gap:.35rem;font-size:.85rem;color:var(--muted)}`}</style>
    </div>
  );
}

export function TotpSetupPage() {
  const nav = useNavigate();
  const [secret, setSecret] = useState("");
  const [uri, setUri] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");

  async function start() {
    setError("");
    try {
      const res = await api<{ secret: string; otpauth_uri: string }>("/api/auth/2fa/setup", { method: "POST" });
      setSecret(res.secret);
      setUri(res.otpauth_uri);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  async function enable(e: FormEvent) {
    e.preventDefault();
    try {
      await api("/api/auth/2fa/enable", { method: "POST", body: JSON.stringify({ code }) });
      nav("/app");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <div className="login-shell">
      <div className="card login-card">
        <h1>Enable 2FA</h1>
        <p className="muted">Mandatory for all accounts. Use an authenticator app.</p>
        {!secret ? (
          <button className="btn" type="button" onClick={start}>
            Generate secret
          </button>
        ) : (
          <form onSubmit={enable} style={{ display: "grid", gap: "0.85rem" }}>
            <p className="muted" style={{ wordBreak: "break-all" }}>
              Secret: <code>{secret}</code>
            </p>
            <p className="muted" style={{ fontSize: "0.8rem", wordBreak: "break-all" }}>
              URI: {uri}
            </p>
            <label>
              Enter code from app
              <input className="input" value={code} onChange={(e) => setCode(e.target.value)} />
            </label>
            <button className="btn" type="submit">
              Enable 2FA
            </button>
          </form>
        )}
        {error ? <p className="error">{error}</p> : null}
      </div>
      <style>{`.login-shell{min-height:100vh;display:grid;place-items:center;padding:1.5rem}.login-card{width:min(480px,100%);display:grid;gap:.85rem}label{display:grid;gap:.35rem;font-size:.85rem;color:var(--muted)}`}</style>
    </div>
  );
}
