import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";

type CaptchaForm = {
  captcha_provider: string;
  captcha_key: string;
  captcha_host: string;
  captcha_retries: number;
  captcha_backup_provider: string;
  captcha_backup_key: string;
  captcha_backup_host: string;
  captcha_key_configured?: boolean;
  captcha_backup_key_configured?: boolean;
};

/** Provider ids accepted by the API / worker (2captcha-compatible APIs only). */
const CAPTCHA: { value: string; label: string }[] = [
  { value: "none", label: "none" },
  { value: "2captcha", label: "2captcha" },
  { value: "captchaai", label: "CaptchaAI" },
];

/** Matches worker CaptchaSolver.HOSTS — used when host override is blank. */
const DEFAULT_HOSTS: Record<string, string> = {
  "2captcha": "https://2captcha.com",
  captchaai: "https://ocr.captchaai.com",
};

function defaultHostFor(provider: string): string | null {
  return DEFAULT_HOSTS[provider] ?? null;
}

function hostOverrideHint(provider: string): string {
  const def = defaultHostFor(provider);
  if (def) return `Leave blank for default: ${def}`;
  return "Leave blank for the selected provider’s default URL";
}

const emptyForm = (): CaptchaForm => ({
  captcha_provider: "none",
  captcha_key: "",
  captcha_host: "",
  captcha_retries: 2,
  captcha_backup_provider: "none",
  captcha_backup_key: "",
  captcha_backup_host: "",
});

export function CaptchaAdminPage() {
  const [form, setForm] = useState(emptyForm());
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api<CaptchaForm>("/api/settings/captcha")
      .then((s) =>
        setForm({
          captcha_provider: s.captcha_provider || "none",
          captcha_key: "",
          captcha_host: s.captcha_host || "",
          captcha_retries: s.captcha_retries ?? 2,
          captcha_backup_provider: s.captcha_backup_provider || "none",
          captcha_backup_key: "",
          captcha_backup_host: s.captcha_backup_host || "",
          captcha_key_configured: s.captcha_key_configured,
          captcha_backup_key_configured: s.captcha_backup_key_configured,
        })
      )
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"));
  }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const body: Record<string, unknown> = {
        captcha_provider: form.captcha_provider,
        captcha_host: form.captcha_host,
        captcha_retries: form.captcha_retries,
        captcha_backup_provider: form.captcha_backup_provider,
        captcha_backup_host: form.captcha_backup_host,
      };
      if (form.captcha_key.trim()) body.captcha_key = form.captcha_key.trim();
      if (form.captcha_backup_key.trim()) body.captcha_backup_key = form.captcha_backup_key.trim();
      const saved = await api<CaptchaForm>("/api/settings/captcha", {
        method: "PUT",
        body: JSON.stringify(body),
      });
      setForm({
        captcha_provider: saved.captcha_provider || "none",
        captcha_key: "",
        captcha_host: saved.captcha_host || "",
        captcha_retries: saved.captcha_retries ?? 2,
        captcha_backup_provider: saved.captcha_backup_provider || "none",
        captcha_backup_key: "",
        captcha_backup_host: saved.captcha_backup_host || "",
        captcha_key_configured: saved.captcha_key_configured,
        captcha_backup_key_configured: saved.captcha_backup_key_configured,
      });
      setMsg("Saved. 2captcha / CaptchaAI settings apply to all scrape jobs and workers.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>2captcha / CaptchaAI</h1>
          <p className="subtitle">
            Global scrape solvers (primary + backup). Set once here — injected into every job lease; not
            configured per worker or package.
          </p>
        </div>
      </div>

      {error ? <p className="error">{error}</p> : null}
      {msg ? <p className="muted">{msg}</p> : null}

      <form className="card" onSubmit={save} style={{ display: "grid", gap: "0.85rem", maxWidth: 640 }}>
        <p className="muted" style={{ margin: 0 }}>
          Typical setup: primary <code>2captcha</code>, backup <code>captchaai</code> (or the reverse). Backup
          runs only if primary fails. Host override fields are optional free-text API base URLs — leave them
          blank unless you use a mirror, proxy, or self-hosted compatible endpoint. Login reCAPTCHA is separate
          under Security.
        </p>
        <div className="form-grid two">
          <label className="field">
            Primary provider
            <select
              className="input"
              value={form.captcha_provider}
              onChange={(e) => setForm({ ...form, captcha_provider: e.target.value })}
            >
              {CAPTCHA.map((x) => (
                <option key={x.value} value={x.value}>
                  {x.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Primary API key {form.captcha_key_configured ? <span className="badge ok">set</span> : null}
            <input
              className="input"
              type="password"
              value={form.captcha_key}
              onChange={(e) => setForm({ ...form, captcha_key: e.target.value })}
              placeholder="blank = keep"
            />
          </label>
          <label className="field" style={{ gridColumn: "1 / -1" }}>
            Primary API host override
            <input
              className="input"
              value={form.captcha_host}
              onChange={(e) => setForm({ ...form, captcha_host: e.target.value })}
              placeholder={hostOverrideHint(form.captcha_provider)}
              autoComplete="off"
              spellCheck={false}
            />
            <span className="muted" style={{ fontSize: "0.75rem", fontWeight: 400 }}>
              Optional custom API base URL (type a full URL — not a dropdown). Leave blank to use the provider
              default
              {defaultHostFor(form.captcha_provider) ? (
                <>
                  {" "}
                  (<code>{defaultHostFor(form.captcha_provider)}</code>)
                </>
              ) : (
                " (pick a provider above)"
              )}
              . Rarely needed — only for a mirror, proxy, or self-hosted 2captcha-compatible endpoint.
            </span>
          </label>
          <label className="field">
            Solver retries
            <input
              className="input"
              type="number"
              min={0}
              value={form.captcha_retries}
              onChange={(e) => setForm({ ...form, captcha_retries: Number(e.target.value) })}
            />
          </label>
          <div aria-hidden="true" />
          <label className="field">
            Backup provider
            <select
              className="input"
              value={form.captcha_backup_provider}
              onChange={(e) => setForm({ ...form, captcha_backup_provider: e.target.value })}
            >
              {CAPTCHA.map((x) => (
                <option key={x.value} value={x.value}>
                  {x.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Backup API key {form.captcha_backup_key_configured ? <span className="badge ok">set</span> : null}
            <input
              className="input"
              type="password"
              value={form.captcha_backup_key}
              onChange={(e) => setForm({ ...form, captcha_backup_key: e.target.value })}
              placeholder="blank = keep"
            />
          </label>
          <label className="field" style={{ gridColumn: "1 / -1" }}>
            Backup API host override
            <input
              className="input"
              value={form.captcha_backup_host}
              onChange={(e) => setForm({ ...form, captcha_backup_host: e.target.value })}
              placeholder={hostOverrideHint(form.captcha_backup_provider)}
              autoComplete="off"
              spellCheck={false}
            />
            <span className="muted" style={{ fontSize: "0.75rem", fontWeight: 400 }}>
              Same as primary: optional free-text base URL for the backup solver. Leave blank for
              {defaultHostFor(form.captcha_backup_provider) ? (
                <>
                  {" "}
                  <code>{defaultHostFor(form.captcha_backup_provider)}</code>
                </>
              ) : (
                " the selected backup provider’s default"
              )}
              . No options list — paste a URL only if you need a non-default endpoint.
            </span>
          </label>
        </div>
        <button className="btn" type="submit">
          Save
        </button>
      </form>
    </div>
  );
}
