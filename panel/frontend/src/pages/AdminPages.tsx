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

export function WorkersAdminPage() {
  const [workers, setWorkers] = useState<Array<{ id: number; name: string; token_prefix: string; online: boolean; is_enabled: boolean; is_draining: boolean; cpu_percent: number; mem_percent: number; max_browsers: number }>>([]);
  const [name, setName] = useState("");
  const [createdToken, setCreatedToken] = useState("");
  const [hint, setHint] = useState("");

  async function refresh() {
    setWorkers(await api("/api/workers"));
  }
  useEffect(() => {
    refresh().catch(() => undefined);
    const t = setInterval(() => refresh().catch(() => undefined), 5000);
    return () => clearInterval(t);
  }, []);

  async function create(e: FormEvent) {
    e.preventDefault();
    const res = await api<{ token: string; install_hint: string }>("/api/workers", {
      method: "POST",
      body: JSON.stringify({ name, max_browsers: 2 }),
    });
    setCreatedToken(res.token);
    setHint(res.install_hint);
    setName("");
    await refresh();
  }

  return (
    <div className="stack">
      <h1>Workers</h1>
      <form className="card" onSubmit={create} style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
        <input className="input" style={{ maxWidth: 280 }} placeholder="Worker name" value={name} onChange={(e) => setName(e.target.value)} required />
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
          <p className="muted">{hint}</p>
        </div>
      ) : null}
      <div className="card">
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Status</th>
              <th>CPU/Mem</th>
              <th>Browsers</th>
              <th>Token</th>
            </tr>
          </thead>
          <tbody>
            {workers.map((w) => (
              <tr key={w.id}>
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
                <td>
                  <code>{w.token_prefix}…</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <style>{`.stack{display:grid;gap:1rem}h1{margin:0}`}</style>
    </div>
  );
}

export function ScrapeAdminPage() {
  const [form, setForm] = useState<Record<string, string | number>>({});
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api<Record<string, unknown>>("/api/settings/scrape").then((s) => {
      const next: Record<string, string | number> = {};
      Object.entries(s).forEach(([k, v]) => {
        if (typeof v === "boolean") return;
        next[k] = v as string | number;
      });
      setForm(next);
    });
  }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    const body = { ...form };
    delete body.captcha_key_configured;
    await api("/api/settings/scrape", { method: "PUT", body: JSON.stringify(body) });
    setSaved(true);
  }

  return (
    <div className="stack">
      <h1>Scrape settings</h1>
      <form className="card" onSubmit={save} style={{ display: "grid", gap: "0.65rem", gridTemplateColumns: "repeat(auto-fit,minmax(200px,1fr))" }}>
        {Object.keys(form).map((k) => (
          <label key={k}>
            {k}
            <input
              className="input"
              value={form[k] ?? ""}
              onChange={(e) => setForm({ ...form, [k]: e.target.value })}
            />
          </label>
        ))}
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

export function BotBuilderPage() {
  const [settings, setSettings] = useState<Record<string, unknown>>({});
  const [token, setToken] = useState("");
  const [commands, setCommands] = useState<Array<{ id: number; command: string; title: string; enabled: boolean; audience: string }>>([]);
  const [workflows, setWorkflows] = useState<Array<{ id: number; name: string; description: string; enabled: boolean; is_demo: boolean }>>([]);
  const [msg, setMsg] = useState("");

  async function refresh() {
    setSettings(await api("/api/bot/settings"));
    setCommands(await api("/api/bot/commands"));
    setWorkflows(await api("/api/bot/workflows"));
  }

  useEffect(() => {
    refresh().catch(() => undefined);
  }, []);

  async function saveSettings(e: FormEvent) {
    e.preventDefault();
    const body: Record<string, unknown> = {
      enabled: Boolean(settings.enabled),
      username: settings.username,
      welcome_text: settings.welcome_text,
      notify_interval_sec: Number(settings.notify_interval_sec || 300),
      support_enabled: Boolean(settings.support_enabled),
      support_chat_id: settings.support_chat_id,
      public_packages: Boolean(settings.public_packages),
      deliver_results_telegram: Boolean(settings.deliver_results_telegram),
      admin_commands_enabled: Boolean(settings.admin_commands_enabled),
    };
    if (token) body.token = token;
    await api("/api/bot/settings", { method: "PUT", body: JSON.stringify(body) });
    setToken("");
    setMsg("Bot settings saved; runtime restarted.");
    await refresh();
  }

  return (
    <div className="stack">
      <h1>Bot builder</h1>
      <p className="muted">Connect BotFather token, toggle commands, manage demo workflows, support chat.</p>
      <form className="card" onSubmit={saveSettings} style={{ display: "grid", gap: "0.7rem", maxWidth: 640 }}>
        <label>
          <input type="checkbox" checked={Boolean(settings.enabled)} onChange={(e) => setSettings({ ...settings, enabled: e.target.checked })} /> Enabled
        </label>
        <label>
          Bot token {settings.token_configured ? <span className="badge ok">configured</span> : null}
          <input className="input" type="password" placeholder="Paste new token to update" value={token} onChange={(e) => setToken(e.target.value)} />
        </label>
        <label>
          Username
          <input className="input" value={String(settings.username || "")} onChange={(e) => setSettings({ ...settings, username: e.target.value })} />
        </label>
        <label>
          Welcome text
          <textarea className="input" rows={3} value={String(settings.welcome_text || "")} onChange={(e) => setSettings({ ...settings, welcome_text: e.target.value })} />
        </label>
        <label>
          <input type="checkbox" checked={Boolean(settings.support_enabled)} onChange={(e) => setSettings({ ...settings, support_enabled: e.target.checked })} /> Support via Telegram
        </label>
        <label>
          Support chat id
          <input className="input" value={String(settings.support_chat_id || "")} onChange={(e) => setSettings({ ...settings, support_chat_id: e.target.value })} />
        </label>
        <label>
          <input type="checkbox" checked={Boolean(settings.admin_commands_enabled)} onChange={(e) => setSettings({ ...settings, admin_commands_enabled: e.target.checked })} /> Admin Telegram commands
        </label>
        <label>
          <input type="checkbox" checked={Boolean(settings.public_packages)} onChange={(e) => setSettings({ ...settings, public_packages: e.target.checked })} /> Public /packages
        </label>
        <button className="btn" type="submit">
          Save bot settings
        </button>
      </form>

      <div className="card" style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
        <button
          className="btn secondary"
          type="button"
          onClick={async () => {
            await api("/api/bot/install-demos", { method: "POST" });
            setMsg("Demo commands & workflows installed.");
            await refresh();
          }}
        >
          Install / refresh demos
        </button>
        <button
          className="btn secondary"
          type="button"
          onClick={async () => {
            await api("/api/bot/restart", { method: "POST" });
            setMsg("Bot runtime restarted.");
          }}
        >
          Restart bot runtime
        </button>
      </div>
      {msg ? <p className="muted">{msg}</p> : null}

      <div className="card">
        <h3>Commands</h3>
        <table className="table">
          <thead>
            <tr>
              <th>Command</th>
              <th>Title</th>
              <th>Audience</th>
              <th>Enabled</th>
            </tr>
          </thead>
          <tbody>
            {commands.map((c) => (
              <tr key={c.id}>
                <td>
                  <code>{c.command}</code>
                </td>
                <td>{c.title}</td>
                <td>{c.audience}</td>
                <td>
                  <button
                    className="btn secondary"
                    type="button"
                    onClick={async () => {
                      await api(`/api/bot/commands/${c.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !c.enabled }) });
                      await refresh();
                    }}
                  >
                    {c.enabled ? "On" : "Off"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>Workflows (incl. demos)</h3>
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Description</th>
              <th>Demo</th>
              <th>Enabled</th>
            </tr>
          </thead>
          <tbody>
            {workflows.map((w) => (
              <tr key={w.id}>
                <td>{w.name}</td>
                <td className="muted">{w.description}</td>
                <td>{w.is_demo ? "yes" : "no"}</td>
                <td>
                  <button
                    className="btn secondary"
                    type="button"
                    onClick={async () => {
                      await api(`/api/bot/workflows/${w.id}/toggle`, { method: "POST" });
                      await refresh();
                    }}
                  >
                    {w.enabled ? "On" : "Off"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <style>{`.stack{display:grid;gap:1rem}h1,h3{margin:0}label{display:grid;gap:.3rem;font-size:.85rem;color:var(--muted)}`}</style>
    </div>
  );
}
