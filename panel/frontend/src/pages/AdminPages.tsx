import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";

export function UsersAdminPage() {
  const [users, setUsers] = useState<Array<{ id: number; username: string; email: string; role: string; is_active: boolean; telegram_id: string | null; totp_enabled: boolean }>>([]);
  const [form, setForm] = useState({ username: "", email: "", password: "", role: "user", telegram_id: "" });
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  async function refresh() {
    setUsers(await api("/api/users"));
  }
  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, []);

  async function create(e: FormEvent) {
    e.preventDefault();
    setError("");
    setMsg("");
    try {
      await api("/api/users", {
        method: "POST",
        body: JSON.stringify({
          ...form,
          telegram_id: form.telegram_id || null,
          perms: {},
        }),
      });
      setForm({ username: "", email: "", password: "", role: "user", telegram_id: "" });
      setMsg("User created (must change password + setup 2FA on first login)");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <div className="stack">
      <h1>Users</h1>
      <p className="muted">Admin-create only. No public registration.</p>
      <form className="card" onSubmit={create} style={{ display: "grid", gap: "0.6rem", gridTemplateColumns: "repeat(auto-fit,minmax(160px,1fr))" }}>
        <input className="input" placeholder="username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required />
        <input className="input" placeholder="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
        <input className="input" placeholder="temp password" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required minLength={8} />
        <select className="input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
          <option value="user">user</option>
          <option value="admin">admin</option>
        </select>
        <input className="input" placeholder="telegram id" value={form.telegram_id} onChange={(e) => setForm({ ...form, telegram_id: e.target.value })} />
        <button className="btn" type="submit">
          Create user
        </button>
      </form>
      {error ? <p className="error">{error}</p> : null}
      {msg ? <p className="muted">{msg}</p> : null}
      <div className="card">
        <table className="table">
          <thead>
            <tr>
              <th>ID</th>
              <th>User</th>
              <th>Role</th>
              <th>2FA</th>
              <th>Telegram</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.id}</td>
                <td>
                  {u.username}
                  <div className="muted">{u.email}</div>
                </td>
                <td>{u.role}</td>
                <td>{u.totp_enabled ? "yes" : "pending"}</td>
                <td>{u.telegram_id || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <style>{`.stack{display:grid;gap:1rem}h1{margin:0}`}</style>
    </div>
  );
}

export function PackagesAdminPage() {
  const [packages, setPackages] = useState<Array<{ id: number; slug: string; name: string; tier: number; price_usdt: number; duration_days: number; threads: number; max_upload_mb: number; is_active: boolean }>>([]);
  const [form, setForm] = useState({ slug: "", name: "", tier: 1, price_usdt: 10, duration_days: 30, threads: 2, max_upload_mb: 5 });

  async function refresh() {
    setPackages(await api("/api/packages"));
  }
  useEffect(() => {
    refresh().catch(() => undefined);
  }, []);

  async function create(e: FormEvent) {
    e.preventDefault();
    await api("/api/packages", { method: "POST", body: JSON.stringify(form) });
    await refresh();
  }

  return (
    <div className="stack">
      <h1>Packages</h1>
      <form className="card" onSubmit={create} style={{ display: "grid", gap: "0.6rem", gridTemplateColumns: "repeat(auto-fit,minmax(140px,1fr))" }}>
        {(["slug", "name"] as const).map((k) => (
          <input key={k} className="input" placeholder={k} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} required />
        ))}
        <input className="input" type="number" placeholder="tier" value={form.tier} onChange={(e) => setForm({ ...form, tier: Number(e.target.value) })} />
        <input className="input" type="number" placeholder="price USDT" value={form.price_usdt} onChange={(e) => setForm({ ...form, price_usdt: Number(e.target.value) })} />
        <input className="input" type="number" placeholder="days" value={form.duration_days} onChange={(e) => setForm({ ...form, duration_days: Number(e.target.value) })} />
        <input className="input" type="number" placeholder="threads" value={form.threads} onChange={(e) => setForm({ ...form, threads: Number(e.target.value) })} />
        <input className="input" type="number" placeholder="upload MB" value={form.max_upload_mb} onChange={(e) => setForm({ ...form, max_upload_mb: Number(e.target.value) })} />
        <button className="btn" type="submit">
          Create
        </button>
      </form>
      <div className="card">
        <table className="table">
          <thead>
            <tr>
              <th>Slug</th>
              <th>Name</th>
              <th>Price</th>
              <th>Threads</th>
              <th>Active</th>
            </tr>
          </thead>
          <tbody>
            {packages.map((p) => (
              <tr key={p.id}>
                <td>{p.slug}</td>
                <td>{p.name}</td>
                <td>{p.price_usdt}</td>
                <td>{p.threads}</td>
                <td>{p.is_active ? "yes" : "no"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <style>{`.stack{display:grid;gap:1rem}h1{margin:0}`}</style>
    </div>
  );
}

export function ProxiesAdminPage() {
  const [pools, setPools] = useState<Array<{ id: number; name: string; description: string; proxy_count: number; is_active: boolean }>>([]);
  const [name, setName] = useState("");
  const [proxiesText, setProxiesText] = useState("");

  async function refresh() {
    setPools(await api("/api/proxy-pools"));
  }
  useEffect(() => {
    refresh().catch(() => undefined);
  }, []);

  async function create(e: FormEvent) {
    e.preventDefault();
    await api("/api/proxy-pools", { method: "POST", body: JSON.stringify({ name, proxies_text: proxiesText, description: "" }) });
    setName("");
    setProxiesText("");
    await refresh();
  }

  return (
    <div className="stack">
      <h1>Proxy pools</h1>
      <p className="muted">Admin-managed pools assigned to workers.</p>
      <form className="card" onSubmit={create} style={{ display: "grid", gap: "0.75rem" }}>
        <input className="input" placeholder="Pool name" value={name} onChange={(e) => setName(e.target.value)} required />
        <textarea className="input" rows={8} placeholder={"host:port\nhost:port:user:pass"} value={proxiesText} onChange={(e) => setProxiesText(e.target.value)} />
        <button className="btn" type="submit">
          Create pool
        </button>
      </form>
      <div className="card">
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Proxies</th>
              <th>Active</th>
            </tr>
          </thead>
          <tbody>
            {pools.map((p) => (
              <tr key={p.id}>
                <td>{p.name}</td>
                <td>{p.proxy_count}</td>
                <td>{p.is_active ? "yes" : "no"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <style>{`.stack{display:grid;gap:1rem}h1{margin:0}`}</style>
    </div>
  );
}

type WorkerConfig = Record<string, string | number | boolean>;

type WorkerRow = {
  id: number;
  name: string;
  token_prefix: string;
  online: boolean;
  is_enabled: boolean;
  is_draining: boolean;
  cpu_percent: number;
  mem_percent: number;
  max_browsers: number;
  proxy_pool_id: number | null;
  worker_config: WorkerConfig;
};

const WORKER_FLAG_FIELDS: Array<{ key: string; label: string; type: "text" | "number" | "bool" | "select"; options?: string[] }> = [
  { key: "engine", label: "Engine", type: "select", options: ["chrome", "google-chrome", "edge", "brave", "camoufox"] },
  { key: "threads", label: "Threads", type: "number" },
  { key: "block_resources", label: "Block resources", type: "select", options: ["none", "images", "media", "all"] },
  { key: "scrape_websites", label: "Scrape websites", type: "select", options: ["yes", "no"] },
  { key: "max_results", label: "Max results (0=all)", type: "number" },
  { key: "min_delay", label: "Min delay", type: "number" },
  { key: "max_delay", label: "Max delay", type: "number" },
  { key: "cooldown_every", label: "Cooldown every N", type: "number" },
  { key: "cooldown_min", label: "Cooldown min", type: "number" },
  { key: "cooldown_max", label: "Cooldown max", type: "number" },
  { key: "captcha_provider", label: "Captcha provider", type: "select", options: ["none", "2captcha", "captchaai"] },
  { key: "captcha_key", label: "Captcha key (blank=keep)", type: "text" },
  { key: "captcha_host", label: "Captcha host", type: "text" },
  { key: "captcha_retries", label: "Captcha retries", type: "number" },
  { key: "nav_timeout", label: "Nav timeout (s)", type: "number" },
  { key: "proxy_attempts", label: "Proxy attempts", type: "number" },
  { key: "browser_path", label: "Browser path (optional)", type: "text" },
  { key: "preflight_timeout", label: "Preflight timeout", type: "number" },
  { key: "headless", label: "Headless", type: "bool" },
  { key: "no_stealth", label: "Disable stealth", type: "bool" },
  { key: "geoip", label: "GeoIP (Camoufox)", type: "bool" },
  { key: "no_preflight", label: "Skip preflight", type: "bool" },
  { key: "fresh", label: "Fresh profile", type: "bool" },
  { key: "debug", label: "Debug", type: "bool" },
];

function coerceConfigValue(type: string, raw: string): string | number | boolean {
  if (type === "bool") return raw === "true" || raw === "1";
  if (type === "number") {
    const n = Number(raw);
    return Number.isFinite(n) ? n : 0;
  }
  return raw;
}

export function WorkersAdminPage() {
  const [workers, setWorkers] = useState<WorkerRow[]>([]);
  const [pools, setPools] = useState<Array<{ id: number; name: string }>>([]);
  const [name, setName] = useState("");
  const [createBrowsers, setCreateBrowsers] = useState(2);
  const [createPool, setCreatePool] = useState<number | "">("");
  const [createdToken, setCreatedToken] = useState("");
  const [hint, setHint] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [edit, setEdit] = useState<{
    name: string;
    is_enabled: boolean;
    is_draining: boolean;
    max_browsers: number;
    proxy_pool_id: number | null;
    worker_config: WorkerConfig;
  } | null>(null);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  async function refresh() {
    const [w, p] = await Promise.all([
      api<WorkerRow[]>("/api/workers"),
      api<Array<{ id: number; name: string }>>("/api/proxy-pools"),
    ]);
    setWorkers(w);
    setPools(p);
    if (selectedId != null) {
      const cur = w.find((x) => x.id === selectedId);
      if (cur && edit) {
        // keep editing unless selection changed externally
      }
    }
  }

  useEffect(() => {
    refresh().catch((e) => setError(e.message));
    const t = setInterval(() => refresh().catch(() => undefined), 5000);
    return () => clearInterval(t);
  }, []);

  function openEdit(w: WorkerRow) {
    setSelectedId(w.id);
    setMsg("");
    setError("");
    const cfg = { ...(w.worker_config || {}) };
    delete cfg.captcha_key_configured;
    cfg.captcha_key = "";
    setEdit({
      name: w.name,
      is_enabled: w.is_enabled,
      is_draining: w.is_draining,
      max_browsers: w.max_browsers,
      proxy_pool_id: w.proxy_pool_id,
      worker_config: cfg,
    });
  }

  async function create(e: FormEvent) {
    e.preventDefault();
    setError("");
    const res = await api<{ token: string; install_hint: string; worker: WorkerRow }>("/api/workers", {
      method: "POST",
      body: JSON.stringify({
        name,
        max_browsers: createBrowsers,
        proxy_pool_id: createPool === "" ? null : createPool,
        use_global_scrape_defaults: true,
      }),
    });
    setCreatedToken(res.token);
    setHint(res.install_hint);
    setName("");
    await refresh();
    openEdit(res.worker);
  }

  async function saveEdit(e: FormEvent) {
    e.preventDefault();
    if (!selectedId || !edit) return;
    setError("");
    setMsg("");
    try {
      const body: Record<string, unknown> = {
        name: edit.name,
        is_enabled: edit.is_enabled,
        is_draining: edit.is_draining,
        max_browsers: edit.max_browsers,
        proxy_pool_id: edit.proxy_pool_id,
        worker_config: { ...edit.worker_config },
      };
      const wc = body.worker_config as WorkerConfig;
      if (!wc.captcha_key) delete wc.captcha_key;
      await api(`/api/workers/${selectedId}`, { method: "PATCH", body: JSON.stringify(body) });
      setMsg("Worker settings saved. Online workers pick them up on the next heartbeat/lease.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  }

  async function resetFromGlobal() {
    if (!selectedId) return;
    setError("");
    try {
      const w = await api<WorkerRow>(`/api/workers/${selectedId}`, {
        method: "PATCH",
        body: JSON.stringify({ reset_config_from_global: true }),
      });
      openEdit(w);
      setMsg("Worker config reset from global Scrape settings.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reset failed");
    }
  }

  async function rotateToken() {
    if (!selectedId) return;
    if (!confirm("Rotate token? The old token stops working immediately.")) return;
    const res = await api<{ token: string; install_hint: string }>("/api/workers/" + selectedId + "/rotate-token", {
      method: "POST",
      body: "{}",
    });
    setCreatedToken(res.token);
    setHint(res.install_hint);
    await refresh();
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>Workers</h1>
          <p className="subtitle">
            Create workers, assign proxy pools, and set per-worker scrape flags. Those flags are merged into each lease
            and synced into the agent&apos;s local worker config.
          </p>
        </div>
      </div>
      <form className="card form-grid two" onSubmit={create} style={{ alignItems: "end" }}>
        <label className="field">
          Name
          <input className="input" placeholder="Worker name" value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label className="field">
          Max browsers
          <input className="input" type="number" min={1} max={64} value={createBrowsers} onChange={(e) => setCreateBrowsers(Number(e.target.value) || 1)} />
        </label>
        <label className="field">
          Proxy pool
          <select className="input" value={createPool} onChange={(e) => setCreatePool(e.target.value === "" ? "" : Number(e.target.value))}>
            <option value="">None</option>
            {pools.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <button className="btn" type="submit">
          Create worker
        </button>
      </form>
      {createdToken ? (
        <div className="card">
          <p>
            <strong>Copy this token now</strong> (shown once):
          </p>
          <code>{createdToken}</code>
          <pre className="muted" style={{ whiteSpace: "pre-wrap" }}>
            {hint}
          </pre>
        </div>
      ) : null}
      {error ? <p className="error">{error}</p> : null}
      {msg ? <p className="muted">{msg}</p> : null}
      <div className="card">
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Status</th>
              <th>CPU/Mem</th>
              <th>Browsers</th>
              <th>Pool</th>
              <th>Engine</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {workers.map((w) => (
              <tr key={w.id} style={{ background: selectedId === w.id ? "color-mix(in srgb, var(--accent) 12%, transparent)" : undefined }}>
                <td>{w.name}</td>
                <td>
                  <span className={`badge ${w.online ? "ok" : ""}`}>{w.online ? "online" : "offline"}</span>
                  {w.is_draining ? " draining" : ""}
                  {!w.is_enabled ? " disabled" : ""}
                </td>
                <td>
                  {w.cpu_percent.toFixed(0)}% / {w.mem_percent.toFixed(0)}%
                </td>
                <td>{w.max_browsers}</td>
                <td>{w.proxy_pool_id ?? "—"}</td>
                <td>
                  <code>{String(w.worker_config?.engine || "—")}</code>
                </td>
                <td>
                  <button className="btn secondary" type="button" onClick={() => openEdit(w)}>
                    Settings
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {edit && selectedId != null ? (
        <form className="card" onSubmit={saveEdit} style={{ display: "grid", gap: "0.85rem" }}>
          <h2 style={{ margin: 0, fontSize: "1.05rem" }}>Worker settings — #{selectedId}</h2>
          <div style={{ display: "grid", gap: "0.65rem", gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))" }}>
            <label>
              Name
              <input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
            </label>
            <label>
              Max browsers (thread cap)
              <input
                className="input"
                type="number"
                min={1}
                max={64}
                value={edit.max_browsers}
                onChange={(e) => setEdit({ ...edit, max_browsers: Number(e.target.value) || 1 })}
              />
            </label>
            <label>
              Proxy pool
              <select
                className="input"
                value={edit.proxy_pool_id ?? ""}
                onChange={(e) => setEdit({ ...edit, proxy_pool_id: e.target.value === "" ? null : Number(e.target.value) })}
              >
                <option value="">None</option>
                {pools.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Enabled
              <select className="input" value={edit.is_enabled ? "1" : "0"} onChange={(e) => setEdit({ ...edit, is_enabled: e.target.value === "1" })}>
                <option value="1">yes</option>
                <option value="0">no</option>
              </select>
            </label>
            <label>
              Draining
              <select className="input" value={edit.is_draining ? "1" : "0"} onChange={(e) => setEdit({ ...edit, is_draining: e.target.value === "1" })}>
                <option value="0">no</option>
                <option value="1">yes</option>
              </select>
            </label>
          </div>

          <h3 style={{ margin: "0.4rem 0 0", fontSize: "0.9rem", color: "var(--muted)" }}>Scrape flags (written into worker config)</h3>
          <div style={{ display: "grid", gap: "0.65rem", gridTemplateColumns: "repeat(auto-fit,minmax(200px,1fr))" }}>
            {WORKER_FLAG_FIELDS.map((f) => (
              <label key={f.key}>
                {f.label}
                {f.type === "bool" ? (
                  <select
                    className="input"
                    value={
                      edit.worker_config[f.key] === true ||
                      edit.worker_config[f.key] === "true" ||
                      edit.worker_config[f.key] === 1
                        ? "true"
                        : "false"
                    }
                    onChange={(e) =>
                      setEdit({
                        ...edit,
                        worker_config: { ...edit.worker_config, [f.key]: e.target.value === "true" },
                      })
                    }
                  >
                    <option value="true">yes</option>
                    <option value="false">no</option>
                  </select>
                ) : f.type === "select" ? (
                  <select
                    className="input"
                    value={String(edit.worker_config[f.key] ?? "")}
                    onChange={(e) =>
                      setEdit({
                        ...edit,
                        worker_config: { ...edit.worker_config, [f.key]: e.target.value },
                      })
                    }
                  >
                    {(f.options || []).map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    className="input"
                    type={f.type === "number" ? "number" : "text"}
                    value={String(edit.worker_config[f.key] ?? "")}
                    onChange={(e) =>
                      setEdit({
                        ...edit,
                        worker_config: {
                          ...edit.worker_config,
                          [f.key]: coerceConfigValue(f.type, e.target.value),
                        },
                      })
                    }
                  />
                )}
              </label>
            ))}
          </div>
          <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
            <button className="btn" type="submit">
              Save worker settings
            </button>
            <button className="btn secondary" type="button" onClick={resetFromGlobal}>
              Reset from global scrape settings
            </button>
            <button className="btn secondary" type="button" onClick={rotateToken}>
              Rotate token
            </button>
          </div>
        </form>
      ) : null}
      <style>{`.stack{display:grid;gap:1rem}h1{margin:0}label{display:grid;gap:.3rem;font-size:.8rem;color:var(--muted)}`}</style>
    </div>
  );
}

export function ScrapeAdminPage() {
  const [form, setForm] = useState<Record<string, string | number | boolean>>({});
  const [saved, setSaved] = useState(false);
  const [captchaKey, setCaptchaKey] = useState("");

  const boolKeys = new Set(["headless", "no_stealth", "geoip", "no_preflight", "fresh", "debug"]);

  useEffect(() => {
    api<Record<string, unknown>>("/api/settings/scrape").then((s) => {
      const next: Record<string, string | number | boolean> = {};
      Object.entries(s).forEach(([k, v]) => {
        if (k === "captcha_key_configured") return;
        next[k] = v as string | number | boolean;
      });
      setForm(next);
    });
  }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    const body: Record<string, unknown> = { ...form };
    if (captchaKey.trim()) body.captcha_key = captchaKey.trim();
    await api("/api/settings/scrape", { method: "PUT", body: JSON.stringify(body) });
    setSaved(true);
    setCaptchaKey("");
  }

  return (
    <div className="stack">
      <h1>Scrape settings</h1>
      <p className="muted">Global defaults. New workers copy these into their worker config; per-worker Settings can override.</p>
      <form className="card" onSubmit={save} style={{ display: "grid", gap: "0.65rem", gridTemplateColumns: "repeat(auto-fit,minmax(200px,1fr))" }}>
        {Object.keys(form).map((k) =>
          boolKeys.has(k) ? (
            <label key={k}>
              {k}
              <select className="input" value={form[k] ? "true" : "false"} onChange={(e) => setForm({ ...form, [k]: e.target.value === "true" })}>
                <option value="true">yes</option>
                <option value="false">no</option>
              </select>
            </label>
          ) : (
            <label key={k}>
              {k}
              <input className="input" value={String(form[k] ?? "")} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
            </label>
          ),
        )}
        <label>
          captcha_key (blank = keep)
          <input className="input" type="password" value={captchaKey} onChange={(e) => setCaptchaKey(e.target.value)} placeholder="••••••" />
        </label>
        <button className="btn" type="submit">
          Save
        </button>
      </form>
      {saved ? <p className="muted">Saved.</p> : null}
      <style>{`.stack{display:grid;gap:1rem}h1{margin:0}label{display:grid;gap:.3rem;font-size:.8rem;color:var(--muted)}`}</style>
    </div>
  );
}

export function SecurityAdminPage() {
  const [form, setForm] = useState({
    recaptcha_mode: "none",
    recaptcha_site_key: "",
    recaptcha_secret_key: "",
    recaptcha_v3_min_score: 0.5,
    max_login_failures: 5,
    lockout_minutes: 15,
  });
  const [msg, setMsg] = useState("");

  useEffect(() => {
    api<typeof form & { recaptcha_secret_configured: boolean }>("/api/settings/security").then((s) =>
      setForm({
        recaptcha_mode: s.recaptcha_mode,
        recaptcha_site_key: s.recaptcha_site_key,
        recaptcha_secret_key: "",
        recaptcha_v3_min_score: s.recaptcha_v3_min_score,
        max_login_failures: s.max_login_failures,
        lockout_minutes: s.lockout_minutes,
      })
    );
  }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    const body: Record<string, unknown> = { ...form };
    if (!form.recaptcha_secret_key) delete body.recaptcha_secret_key;
    await api("/api/settings/security", { method: "PUT", body: JSON.stringify(body) });
    setMsg("Saved. Only one reCAPTCHA mode is active at a time (none / v2 / v3).");
  }

  return (
    <div className="stack">
      <h1>Security</h1>
      <form className="card" onSubmit={save} style={{ display: "grid", gap: "0.75rem", maxWidth: 520 }}>
        <label>
          reCAPTCHA mode (one at a time)
          <select className="input" value={form.recaptcha_mode} onChange={(e) => setForm({ ...form, recaptcha_mode: e.target.value })}>
            <option value="none">none</option>
            <option value="v2">v2 checkbox</option>
            <option value="v3">v3 score</option>
          </select>
        </label>
        <label>
          Site key
          <input className="input" value={form.recaptcha_site_key} onChange={(e) => setForm({ ...form, recaptcha_site_key: e.target.value })} />
        </label>
        <label>
          Secret key (leave blank to keep)
          <input className="input" value={form.recaptcha_secret_key} onChange={(e) => setForm({ ...form, recaptcha_secret_key: e.target.value })} />
        </label>
        <label>
          v3 min score
          <input className="input" type="number" step="0.1" min={0} max={1} value={form.recaptcha_v3_min_score} onChange={(e) => setForm({ ...form, recaptcha_v3_min_score: Number(e.target.value) })} />
        </label>
        <label>
          Max login failures
          <input className="input" type="number" value={form.max_login_failures} onChange={(e) => setForm({ ...form, max_login_failures: Number(e.target.value) })} />
        </label>
        <label>
          Lockout minutes
          <input className="input" type="number" value={form.lockout_minutes} onChange={(e) => setForm({ ...form, lockout_minutes: Number(e.target.value) })} />
        </label>
        <button className="btn" type="submit">
          Save
        </button>
      </form>
      {msg ? <p className="muted">{msg}</p> : null}
      <style>{`.stack{display:grid;gap:1rem}h1{margin:0}label{display:grid;gap:.3rem;font-size:.85rem;color:var(--muted)}`}</style>
    </div>
  );
}
