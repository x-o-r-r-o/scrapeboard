import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";
import { EngineMultiSelect, PermsEditor, StringListField, usePermSchema } from "../components/PermsEditor";

type ScrapeConfig = Record<string, string | number | boolean>;

const SCRAPE_FLAG_FIELDS: Array<{ key: string; label: string; type: "text" | "number" | "bool" | "select"; options?: string[] }> = [
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

const DEFAULT_SCRAPE_CONFIG: ScrapeConfig = {
  engine: "chrome",
  threads: 2,
  block_resources: "media",
  scrape_websites: "yes",
  max_results: 0,
  min_delay: 2,
  max_delay: 5,
  cooldown_every: 25,
  cooldown_min: 25,
  cooldown_max: 60,
  nav_timeout: 45,
  proxy_attempts: 3,
  browser_path: "",
  preflight_timeout: 12,
  headless: true,
  no_stealth: false,
  geoip: false,
  no_preflight: false,
  fresh: false,
  debug: false,
};

function coerceConfigValue(type: string, raw: string): string | number | boolean {
  if (type === "bool") return raw === "true" || raw === "1";
  if (type === "number") {
    const n = Number(raw);
    return Number.isFinite(n) ? n : 0;
  }
  return raw;
}

function ScrapeFlagsEditor({
  value,
  onChange,
}: {
  value: ScrapeConfig;
  onChange: (next: ScrapeConfig) => void;
}) {
  return (
    <div style={{ display: "grid", gap: "0.65rem", gridTemplateColumns: "repeat(auto-fit,minmax(200px,1fr))", gridColumn: "1 / -1" }}>
      {SCRAPE_FLAG_FIELDS.map((f) => (
        <label key={f.key} className="field">
          {f.label}
          {f.type === "bool" ? (
            <select
              className="input"
              value={value[f.key] === true || value[f.key] === "true" || value[f.key] === 1 ? "true" : "false"}
              onChange={(e) => onChange({ ...value, [f.key]: e.target.value === "true" })}
            >
              <option value="true">yes</option>
              <option value="false">no</option>
            </select>
          ) : f.type === "select" ? (
            <select
              className="input"
              value={String(value[f.key] ?? "")}
              onChange={(e) => onChange({ ...value, [f.key]: e.target.value })}
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
              value={String(value[f.key] ?? "")}
              onChange={(e) => onChange({ ...value, [f.key]: coerceConfigValue(f.type, e.target.value) })}
            />
          )}
        </label>
      ))}
    </div>
  );
}

type AdminUser = {
  id: number;
  username: string;
  email: string;
  role: string;
  is_active: boolean;
  telegram_id: string | null;
  totp_enabled: boolean;
  perms: Record<string, unknown>;
  worker_ids: number[];
  dedicated_worker: boolean;
  subscription_package: string | null;
  subscription_id: number | null;
  subscription_expires_at: string | null;
  has_active_subscription: boolean;
};

type PackageLite = {
  id: number;
  name: string;
  duration_days: number;
  threads: number;
  is_active: boolean;
  dedicated_worker?: boolean;
};

function isTelegramUser(u: { role: string; telegram_id?: string | null; perms?: Record<string, unknown> }) {
  return u.role === "user" && (Boolean(u.telegram_id) || Boolean(u.perms?.telegram_user));
}

export function UsersAdminPage() {
  const { schema, workers } = usePermSchema();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [packages, setPackages] = useState<PackageLite[]>([]);
  const [form, setForm] = useState({
    username: "",
    email: "",
    password: "",
    role: "user",
    telegram_id: "",
    package_id: "",
    duration_days: "",
    notify: true,
    perms: {} as Record<string, unknown>,
    worker_ids: [] as number[],
  });
  const [edit, setEdit] = useState<{
    id: number;
    username: string;
    email: string;
    role: string;
    is_active: boolean;
    telegram_id: string;
    password: string;
    reset_2fa: boolean;
    perms: Record<string, unknown>;
    worker_ids: number[];
    dedicated_worker: boolean;
    subscription_package: string | null;
    has_active_subscription: boolean;
  } | null>(null);
  const [assign, setAssign] = useState<{
    user_id: number;
    username: string;
    current_package: string | null;
    package_id: string;
    duration_days: string;
    notify: boolean;
  } | null>(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const activePackages = packages.filter((p) => p.is_active);

  async function refresh() {
    const [userRows, pkgs] = await Promise.all([
      api<AdminUser[]>("/api/users"),
      api<PackageLite[]>("/api/packages").catch(() => [] as PackageLite[]),
    ]);
    setUsers(userRows);
    setPackages(pkgs);
  }
  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (schema && Object.keys(form.perms).length === 0) {
      setForm((f) => ({
        ...f,
        perms: { ...schema.defaults, ...(f.role === "user" ? { telegram_user: true } : {}) },
      }));
    }
  }, [schema]);

  async function create(e: FormEvent) {
    e.preventDefault();
    setError("");
    setMsg("");
    try {
      const body: Record<string, unknown> = {
        role: form.role,
        perms: form.role === "user" ? { ...form.perms, telegram_user: true } : form.perms,
        worker_ids: form.worker_ids,
      };
      if (form.role === "user") {
        body.telegram_id = form.telegram_id.trim();
        if (form.username.trim()) body.username = form.username.trim();
        if (form.package_id) {
          body.package_id = Number(form.package_id);
          body.notify = form.notify;
          if (form.duration_days) body.duration_days = Number(form.duration_days);
        }
      } else {
        body.username = form.username.trim();
        body.email = form.email.trim();
        body.password = form.password;
        if (form.telegram_id.trim()) body.telegram_id = form.telegram_id.trim();
      }
      const createdRole = form.role;
      await api("/api/users", { method: "POST", body: JSON.stringify(body) });
      setForm({
        username: "",
        email: "",
        password: "",
        role: "user",
        telegram_id: "",
        package_id: "",
        duration_days: "",
        notify: true,
        perms: schema ? { ...schema.defaults, telegram_user: true } : {},
        worker_ids: [],
      });
      setMsg(
        createdRole === "admin"
          ? "Admin created (must change password + setup 2FA on first login)"
          : "Telegram user created"
      );
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  async function saveEdit(e: FormEvent) {
    e.preventDefault();
    if (!edit) return;
    setError("");
    try {
      const body: Record<string, unknown> = {
        role: edit.role,
        is_active: edit.is_active,
        telegram_id: edit.telegram_id || null,
        reset_2fa: edit.reset_2fa,
        perms: edit.role === "user" ? { ...edit.perms, telegram_user: true } : edit.perms,
        worker_ids: edit.worker_ids,
      };
      if (edit.role === "admin") {
        body.email = edit.email;
        if (edit.password) body.password = edit.password;
      } else if (edit.username.trim()) {
        body.username = edit.username.trim();
      }
      await api(`/api/users/${edit.id}`, { method: "PATCH", body: JSON.stringify(body) });
      setMsg("User updated.");
      setEdit(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  }

  async function saveAssign(e: FormEvent) {
    e.preventDefault();
    if (!assign) return;
    setError("");
    try {
      const body: Record<string, unknown> = {
        user_id: assign.user_id,
        package_id: Number(assign.package_id),
        notify: assign.notify,
      };
      if (assign.duration_days) body.duration_days = Number(assign.duration_days);
      await api("/api/subscriptions/grant", { method: "POST", body: JSON.stringify(body) });
      setMsg(`Package assigned to ${assign.username}.`);
      setAssign(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Assign failed");
    }
  }

  function openAssign(u: AdminUser) {
    setAssign({
      user_id: u.id,
      username: u.username,
      current_package: u.has_active_subscription ? u.subscription_package : null,
      package_id: "",
      duration_days: "",
      notify: true,
    });
    setEdit(null);
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>Users</h1>
          <p className="subtitle">
            Default users are Telegram-linked (no panel login). Admins need username, email, and a temporary password.
            Assign packages from the user row or edit form.
          </p>
        </div>
      </div>
      <form className="card" onSubmit={create} style={{ display: "grid", gap: "0.6rem", gridTemplateColumns: "repeat(auto-fit,minmax(160px,1fr))" }}>
        <h3 style={{ margin: 0, gridColumn: "1 / -1" }}>Create user</h3>
        <label className="field" style={{ gridColumn: "1 / -1", maxWidth: 280 }}>
          Role
          <select
            className="input"
            value={form.role}
            onChange={(e) => {
              const role = e.target.value;
              setForm({
                ...form,
                role,
                perms:
                  role === "user"
                    ? { ...(schema?.defaults || {}), telegram_user: true }
                    : { ...(schema?.defaults || {}) },
              });
            }}
          >
            <option value="user">user (Telegram)</option>
            <option value="admin">admin (panel login)</option>
          </select>
        </label>
        {form.role === "user" ? (
          <>
            <label className="field">
              Telegram ID
              <input
                className="input"
                placeholder="numeric id"
                value={form.telegram_id}
                onChange={(e) => setForm({ ...form, telegram_id: e.target.value })}
                required
              />
            </label>
            <label className="field">
              Display name (optional)
              <input
                className="input"
                placeholder="defaults to tg_&lt;id&gt;"
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
              />
            </label>
            <label className="field">
              Package (optional)
              <select className="input" value={form.package_id} onChange={(e) => setForm({ ...form, package_id: e.target.value })}>
                <option value="">None</option>
                {activePackages.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.duration_days}d · {p.threads} threads)
                  </option>
                ))}
              </select>
            </label>
            {form.package_id ? (
              <>
                <label className="field">
                  Days override
                  <input
                    className="input"
                    type="number"
                    min={1}
                    placeholder="package default"
                    value={form.duration_days}
                    onChange={(e) => setForm({ ...form, duration_days: e.target.value })}
                  />
                </label>
                <label style={{ alignSelf: "end", paddingBottom: "0.4rem" }}>
                  <input type="checkbox" checked={form.notify} onChange={(e) => setForm({ ...form, notify: e.target.checked })} /> Notify on Telegram
                </label>
              </>
            ) : null}
            <PermsEditor
              perms={form.perms}
              workerIds={form.worker_ids}
              onPermsChange={(perms) => setForm({ ...form, perms: { ...perms, telegram_user: true } })}
              onWorkerIdsChange={(worker_ids) => setForm({ ...form, worker_ids })}
              schema={schema}
              workers={workers}
              showWorkers={Boolean(
                form.package_id && activePackages.find((p) => String(p.id) === String(form.package_id))?.dedicated_worker
              )}
              title="Role permissions"
            />
          </>
        ) : (
          <>
            <input className="input" placeholder="username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required />
            <input className="input" placeholder="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
            <input
              className="input"
              placeholder="temp password"
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              required
              minLength={8}
            />
            <input
              className="input"
              placeholder="telegram numeric id (for /admin)"
              value={form.telegram_id}
              onChange={(e) => setForm({ ...form, telegram_id: e.target.value })}
            />
            <p className="muted" style={{ gridColumn: "1 / -1" }}>
              Admins sign in with username/email + password + 2FA. Set Telegram numeric id (from bot /whoami) to use
              /admin; also enable Admin commands in Bot Builder. Packages are not required.
            </p>
          </>
        )}
        <button className="btn" type="submit">
          Create user
        </button>
      </form>
      {error ? <p className="error">{error}</p> : null}
      {msg ? <p className="muted">{msg}</p> : null}
      <div className="card table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>ID</th>
              <th>User</th>
              <th>Role</th>
              <th>Plan</th>
              <th>Workers</th>
              <th>Status</th>
              <th>2FA</th>
              <th>Telegram</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.id}</td>
                <td>
                  {u.username}
                  {u.role === "admin" || !isTelegramUser(u) ? <div className="muted">{u.email}</div> : null}
                </td>
                <td>{u.role === "user" ? "user (Telegram)" : u.role}</td>
                <td>
                  {u.has_active_subscription && u.subscription_package ? (
                    <>
                      {u.subscription_package}
                      {u.subscription_expires_at ? (
                        <div className="muted" style={{ fontSize: "0.75rem" }}>
                          until {new Date(u.subscription_expires_at).toLocaleDateString()}
                        </div>
                      ) : null}
                    </>
                  ) : (
                    <span className="muted">{u.role === "admin" ? "n/a" : "none"}</span>
                  )}
                </td>
                <td>{u.worker_ids?.length ? u.worker_ids.join(", ") : "any"}</td>
                <td>
                  <span className={`badge ${u.is_active ? "ok" : "danger"}`}>{u.is_active ? "active" : "disabled"}</span>
                </td>
                <td>{u.role === "admin" ? (u.totp_enabled ? "yes" : "pending") : "—"}</td>
                <td>{u.telegram_id || "—"}</td>
                <td style={{ display: "flex", gap: "0.35rem", flexWrap: "wrap" }}>
                  <button
                    className="btn secondary sm"
                    type="button"
                    onClick={() =>
                      setEdit({
                        id: u.id,
                        username: u.username,
                        email: u.email,
                        role: u.role,
                        is_active: u.is_active,
                        telegram_id: u.telegram_id || "",
                        password: "",
                        reset_2fa: false,
                        perms: { ...(schema?.defaults || {}), ...(u.perms || {}) },
                        worker_ids: [...(u.worker_ids || [])],
                        dedicated_worker: Boolean(u.dedicated_worker),
                        subscription_package: u.has_active_subscription ? u.subscription_package : null,
                        has_active_subscription: u.has_active_subscription,
                      })
                    }
                  >
                    Edit
                  </button>
                  {u.role === "user" ? (
                    <button className="btn secondary sm" type="button" onClick={() => openAssign(u)}>
                      {u.has_active_subscription ? "Change plan" : "Assign package"}
                    </button>
                  ) : null}
                  <button
                    className="btn secondary sm"
                    type="button"
                    onClick={async () => {
                      await api(`/api/users/${u.id}`, {
                        method: "PATCH",
                        body: JSON.stringify({ is_active: !u.is_active }),
                      });
                      await refresh();
                    }}
                  >
                    {u.is_active ? "Disable" : "Enable"}
                  </button>
                  <button
                    className="btn danger sm"
                    type="button"
                    onClick={async () => {
                      if (!confirm(`Delete ${u.username}?`)) return;
                      try {
                        await api(`/api/users/${u.id}`, { method: "DELETE" });
                        setMsg("Deleted.");
                        await refresh();
                      } catch (err) {
                        setError(err instanceof Error ? err.message : "Delete failed");
                      }
                    }}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {assign ? (
        <form className="card" onSubmit={saveAssign} style={{ display: "grid", gap: "0.65rem", maxWidth: 480 }}>
          <h3 style={{ margin: 0 }}>
            {assign.current_package ? "Change plan" : "Assign package"} — {assign.username}
          </h3>
          {assign.current_package ? <p className="muted" style={{ margin: 0 }}>Current: {assign.current_package}</p> : null}
          <label className="field">
            Package
            <select className="input" required value={assign.package_id} onChange={(e) => setAssign({ ...assign, package_id: e.target.value })}>
              <option value="">Select…</option>
              {packages.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.duration_days}d · {p.threads} threads){!p.is_active ? " (disabled)" : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Days (optional)
            <input
              className="input"
              type="number"
              min={1}
              placeholder="package default"
              value={assign.duration_days}
              onChange={(e) => setAssign({ ...assign, duration_days: e.target.value })}
            />
          </label>
          <label>
            <input type="checkbox" checked={assign.notify} onChange={(e) => setAssign({ ...assign, notify: e.target.checked })} /> Notify on Telegram
          </label>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button className="btn" type="submit">
              {assign.current_package ? "Change plan" : "Assign package"}
            </button>
            <button className="btn secondary" type="button" onClick={() => setAssign(null)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
      {edit ? (
        <form className="card" onSubmit={saveEdit} style={{ display: "grid", gap: "0.65rem", maxWidth: 720 }}>
          <h3 style={{ margin: 0 }}>Edit {edit.username}</h3>
          <label className="field">
            Role
            <select className="input" value={edit.role} onChange={(e) => setEdit({ ...edit, role: e.target.value })}>
              <option value="user">user (Telegram)</option>
              <option value="admin">admin</option>
            </select>
          </label>
          <label className="field">
            Telegram ID
            <input
              className="input"
              value={edit.telegram_id}
              onChange={(e) => setEdit({ ...edit, telegram_id: e.target.value })}
              placeholder={
                edit.role === "user"
                  ? "required numeric Telegram user id"
                  : "numeric id from bot /whoami (for /admin)"
              }
              required={edit.role === "user"}
            />
            {edit.role === "admin" ? (
              <span className="muted" style={{ fontSize: "0.8rem", marginTop: "0.25rem" }}>
                Required for Telegram /admin. Use the numeric id from /whoami (not @username). Also enable Admin
                commands in Bot Builder.
              </span>
            ) : null}
          </label>
          {edit.role === "user" ? (
            <label className="field">
              Display name
              <input className="input" value={edit.username} onChange={(e) => setEdit({ ...edit, username: e.target.value })} />
            </label>
          ) : (
            <>
              <label className="field">
                Email
                <input className="input" value={edit.email} onChange={(e) => setEdit({ ...edit, email: e.target.value })} />
              </label>
              <label className="field">
                New password (optional)
                <input
                  className="input"
                  type="password"
                  value={edit.password}
                  onChange={(e) => setEdit({ ...edit, password: e.target.value })}
                  minLength={8}
                />
              </label>
            </>
          )}
          <label>
            <input type="checkbox" checked={edit.is_active} onChange={(e) => setEdit({ ...edit, is_active: e.target.checked })} /> Active
          </label>
          {edit.role === "admin" ? (
            <label>
              <input type="checkbox" checked={edit.reset_2fa} onChange={(e) => setEdit({ ...edit, reset_2fa: e.target.checked })} /> Reset 2FA
            </label>
          ) : null}
          {edit.role === "user" ? (
            <>
              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center" }}>
                <span className="muted">
                  Plan: {edit.has_active_subscription && edit.subscription_package ? edit.subscription_package : "none"}
                </span>
                <button
                  className="btn secondary sm"
                  type="button"
                  onClick={() =>
                    openAssign({
                      id: edit.id,
                      username: edit.username,
                      email: edit.email,
                      role: edit.role,
                      is_active: edit.is_active,
                      telegram_id: edit.telegram_id || null,
                      totp_enabled: false,
                      perms: edit.perms,
                      worker_ids: edit.worker_ids,
                      dedicated_worker: edit.dedicated_worker,
                      subscription_package: edit.subscription_package,
                      subscription_id: null,
                      subscription_expires_at: null,
                      has_active_subscription: edit.has_active_subscription,
                    })
                  }
                >
                  {edit.has_active_subscription ? "Change plan" : "Assign package"}
                </button>
              </div>
              <PermsEditor
                perms={edit.perms}
                workerIds={edit.worker_ids}
                onPermsChange={(perms) => setEdit({ ...edit, perms: { ...perms, telegram_user: true } })}
                onWorkerIdsChange={(worker_ids) => setEdit({ ...edit, worker_ids })}
                schema={schema}
                workers={workers}
                showWorkers={edit.dedicated_worker}
                title="Role permissions"
              />
            </>
          ) : null}
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button className="btn" type="submit">
              Save
            </button>
            <button className="btn secondary" type="button" onClick={() => setEdit(null)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

type PackageRow = {
  id: number;
  slug: string;
  name: string;
  tier: number;
  price_usdt: number;
  duration_days: number;
  threads: number;
  max_upload_mb: number;
  allowed_engines: string[];
  description: string;
  headings: string[];
  features: string[];
  dedicated_worker: boolean;
  scrape_defaults: ScrapeConfig;
  chunk_size: number;
  is_active: boolean;
};

const EMPTY_PKG_FORM = {
  slug: "",
  name: "",
  tier: 1,
  price_usdt: 10,
  duration_days: 30,
  threads: 2,
  max_upload_mb: 5,
  allowed_engines: ["all"] as string[],
  description: "",
  headings: [] as string[],
  features: [] as string[],
  dedicated_worker: false,
  scrape_defaults: { ...DEFAULT_SCRAPE_CONFIG } as ScrapeConfig,
  chunk_size: 500,
};

export function PackagesAdminPage() {
  const { schema } = usePermSchema();
  const engines = schema?.engines || ["chrome", "google-chrome", "edge", "brave", "camoufox"];
  const [packages, setPackages] = useState<PackageRow[]>([]);
  const [form, setForm] = useState(EMPTY_PKG_FORM);
  const [edit, setEdit] = useState<PackageRow | null>(null);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  async function refresh() {
    setPackages(await api<PackageRow[]>("/api/packages"));
  }
  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, []);

  function normalizeEngines(ae: string[] | "all"): string[] {
    if (ae === "all" || (Array.isArray(ae) && (ae.length === 0 || ae.includes("all")))) return ["all"];
    return ae;
  }

  async function create(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const body: Record<string, unknown> = {
        slug: form.slug,
        name: form.name,
        tier: form.tier,
        price_usdt: form.price_usdt,
        duration_days: form.duration_days,
        threads: form.threads,
        max_upload_mb: form.max_upload_mb,
        allowed_engines: normalizeEngines(form.allowed_engines as string[] | "all"),
        description: form.description,
        headings: form.headings.map((s) => s.trim()).filter(Boolean),
        features: form.features.map((s) => s.trim()).filter(Boolean),
        dedicated_worker: form.dedicated_worker,
        scrape_defaults: { ...form.scrape_defaults, threads: form.threads },
        chunk_size: form.chunk_size,
      };
      await api("/api/packages", { method: "POST", body: JSON.stringify(body) });
      setForm({ ...EMPTY_PKG_FORM, scrape_defaults: { ...DEFAULT_SCRAPE_CONFIG } });
      setMsg("Package created.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
    }
  }

  async function saveEdit(e: FormEvent) {
    e.preventDefault();
    if (!edit) return;
    try {
      await api(`/api/packages/${edit.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: edit.name,
          tier: edit.tier,
          price_usdt: edit.price_usdt,
          duration_days: edit.duration_days,
          threads: edit.threads,
          max_upload_mb: edit.max_upload_mb,
          allowed_engines: normalizeEngines(edit.allowed_engines as string[] | "all"),
          description: edit.description || "",
          headings: (edit.headings || []).map((s) => s.trim()).filter(Boolean),
          features: (edit.features || []).map((s) => s.trim()).filter(Boolean),
          dedicated_worker: Boolean(edit.dedicated_worker),
          scrape_defaults: { ...(edit.scrape_defaults || DEFAULT_SCRAPE_CONFIG), threads: edit.threads },
          chunk_size: edit.chunk_size || 500,
          is_active: edit.is_active,
        }),
      });
      setMsg("Package updated.");
      setEdit(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  }

  function enginesLabel(ae: string[] | undefined) {
    if (!ae || ae.includes("all") || ae.length === 0) return "all";
    return ae.join(", ");
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>Packages</h1>
          <p className="subtitle">
            Subscription packages: limits, engines, default scrape flags (applied on leases before per-worker
            overrides), headings, and features.
          </p>
        </div>
      </div>
      <form className="card" onSubmit={create} style={{ display: "grid", gap: "0.6rem", gridTemplateColumns: "repeat(auto-fit,minmax(140px,1fr))" }}>
        <h3 style={{ margin: 0, gridColumn: "1 / -1" }}>Create package</h3>
        {(["slug", "name"] as const).map((k) => (
          <label key={k} className="field">
            {k === "slug" ? "Slug" : "Name"}
            <input className="input" placeholder={k} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} required />
          </label>
        ))}
        <label className="field">
          Tier
          <input className="input" type="number" value={form.tier} onChange={(e) => setForm({ ...form, tier: Number(e.target.value) })} />
        </label>
        <label className="field">
          Price (USDT)
          <input className="input" type="number" value={form.price_usdt} onChange={(e) => setForm({ ...form, price_usdt: Number(e.target.value) })} />
        </label>
        <label className="field">
          Duration (days)
          <input className="input" type="number" value={form.duration_days} onChange={(e) => setForm({ ...form, duration_days: Number(e.target.value) })} />
        </label>
        <label className="field">
          Threads
          <input
            className="input"
            type="number"
            value={form.threads}
            onChange={(e) => {
              const threads = Number(e.target.value);
              setForm({ ...form, threads, scrape_defaults: { ...form.scrape_defaults, threads } });
            }}
          />
        </label>
        <label className="field">
          Max upload (MB)
          <input className="input" type="number" value={form.max_upload_mb} onChange={(e) => setForm({ ...form, max_upload_mb: Number(e.target.value) })} />
        </label>
        <label className="field">
          Chunk size
          <input className="input" type="number" min={1} value={form.chunk_size} onChange={(e) => setForm({ ...form, chunk_size: Number(e.target.value) || 500 })} />
        </label>
        <label className="field" style={{ gridColumn: "1 / -1" }}>
          Description
          <textarea className="input" rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Short package summary shown in billing / bot" />
        </label>
        <StringListField label="Headings" hint="One heading per line (marketing / bot display)" value={form.headings} onChange={(headings) => setForm({ ...form, headings })} placeholder={"Premium scraping\nPriority workers"} />
        <StringListField label="Features" hint="One feature bullet per line" value={form.features} onChange={(features) => setForm({ ...form, features })} placeholder={"Up to 4 threads\nAll engines\nTelegram delivery"} />
        <EngineMultiSelect
          value={form.allowed_engines.includes("all") ? "all" : form.allowed_engines}
          onChange={(v) => setForm({ ...form, allowed_engines: v === "all" ? ["all"] : v })}
          engines={engines}
        />
        <label style={{ gridColumn: "1 / -1", display: "flex", gap: "0.45rem", alignItems: "center" }}>
          <input type="checkbox" checked={form.dedicated_worker} onChange={(e) => setForm({ ...form, dedicated_worker: e.target.checked })} />
          Dedicated worker package (subscribers can optionally pin workers; leave empty = all workers)
        </label>
        <h4 style={{ margin: "0.4rem 0 0", gridColumn: "1 / -1", fontSize: "0.9rem", color: "var(--muted)" }}>
          Default scrape settings (package → worker → job)
        </h4>
        <ScrapeFlagsEditor
          value={form.scrape_defaults}
          onChange={(scrape_defaults) => setForm({ ...form, scrape_defaults, threads: Number(scrape_defaults.threads) || form.threads })}
        />
        <button className="btn" type="submit">
          Create package
        </button>
      </form>
      {error ? <p className="error">{error}</p> : null}
      {msg ? <p className="muted">{msg}</p> : null}
      <div className="card table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Slug</th>
              <th>Name</th>
              <th>Price</th>
              <th>Days</th>
              <th>Threads</th>
              <th>Engines</th>
              <th>Dedicated</th>
              <th>Features</th>
              <th>Active</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {packages.map((p) => (
              <tr key={p.id}>
                <td>{p.slug}</td>
                <td>
                  {p.name}
                  {p.description ? <div className="muted" style={{ fontSize: "0.8rem" }}>{p.description.slice(0, 80)}</div> : null}
                </td>
                <td>{p.price_usdt}</td>
                <td>{p.duration_days}</td>
                <td>{p.threads}</td>
                <td>{enginesLabel(p.allowed_engines)}</td>
                <td>{p.dedicated_worker ? <span className="badge ok">yes</span> : "—"}</td>
                <td>{(p.features || []).length || (p.headings || []).length || "—"}</td>
                <td>
                  <span className={`badge ${p.is_active ? "ok" : "danger"}`}>{p.is_active ? "yes" : "no"}</span>
                </td>
                <td style={{ display: "flex", gap: "0.35rem" }}>
                  <button
                    className="btn secondary sm"
                    type="button"
                    onClick={() =>
                      setEdit({
                        ...p,
                        description: p.description || "",
                        headings: p.headings || [],
                        features: p.features || [],
                        allowed_engines: p.allowed_engines?.length ? p.allowed_engines : ["all"],
                        dedicated_worker: Boolean(p.dedicated_worker),
                        scrape_defaults: { ...DEFAULT_SCRAPE_CONFIG, ...(p.scrape_defaults || {}), threads: p.threads },
                        chunk_size: p.chunk_size || 500,
                      })
                    }
                  >
                    Edit
                  </button>
                  <button
                    className="btn secondary sm"
                    type="button"
                    onClick={async () => {
                      if (p.is_active) await api(`/api/packages/${p.id}`, { method: "DELETE" });
                      else await api(`/api/packages/${p.id}`, { method: "PATCH", body: JSON.stringify({ is_active: true }) });
                      await refresh();
                    }}
                  >
                    {p.is_active ? "Disable" : "Enable"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {edit ? (
        <form className="card" onSubmit={saveEdit} style={{ display: "grid", gap: "0.6rem", gridTemplateColumns: "repeat(auto-fit,minmax(140px,1fr))" }}>
          <h3 style={{ margin: 0, gridColumn: "1 / -1" }}>Edit {edit.slug}</h3>
          <label className="field">
            Name
            <input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
          </label>
          <label className="field">
            Tier
            <input className="input" type="number" value={edit.tier} onChange={(e) => setEdit({ ...edit, tier: Number(e.target.value) })} />
          </label>
          <label className="field">
            Price (USDT)
            <input className="input" type="number" value={edit.price_usdt} onChange={(e) => setEdit({ ...edit, price_usdt: Number(e.target.value) })} />
          </label>
          <label className="field">
            Duration (days)
            <input className="input" type="number" value={edit.duration_days} onChange={(e) => setEdit({ ...edit, duration_days: Number(e.target.value) })} />
          </label>
          <label className="field">
            Threads
            <input
              className="input"
              type="number"
              value={edit.threads}
              onChange={(e) => {
                const threads = Number(e.target.value);
                setEdit({ ...edit, threads, scrape_defaults: { ...edit.scrape_defaults, threads } });
              }}
            />
          </label>
          <label className="field">
            Max upload (MB)
            <input className="input" type="number" value={edit.max_upload_mb} onChange={(e) => setEdit({ ...edit, max_upload_mb: Number(e.target.value) })} />
          </label>
          <label className="field">
            Chunk size
            <input className="input" type="number" min={1} value={edit.chunk_size || 500} onChange={(e) => setEdit({ ...edit, chunk_size: Number(e.target.value) || 500 })} />
          </label>
          <label className="field" style={{ gridColumn: "1 / -1" }}>
            Description
            <textarea className="input" rows={2} value={edit.description || ""} onChange={(e) => setEdit({ ...edit, description: e.target.value })} />
          </label>
          <StringListField label="Headings" value={edit.headings || []} onChange={(headings) => setEdit({ ...edit, headings })} />
          <StringListField label="Features" value={edit.features || []} onChange={(features) => setEdit({ ...edit, features })} />
          <EngineMultiSelect
            value={(edit.allowed_engines || ["all"]).includes("all") ? "all" : edit.allowed_engines}
            onChange={(v) => setEdit({ ...edit, allowed_engines: v === "all" ? ["all"] : v })}
            engines={engines}
          />
          <label style={{ gridColumn: "1 / -1", display: "flex", gap: "0.45rem", alignItems: "center" }}>
            <input type="checkbox" checked={Boolean(edit.dedicated_worker)} onChange={(e) => setEdit({ ...edit, dedicated_worker: e.target.checked })} />
            Dedicated worker package (subscribers can optionally pin workers; leave empty = all workers)
          </label>
          <h4 style={{ margin: "0.4rem 0 0", gridColumn: "1 / -1", fontSize: "0.9rem", color: "var(--muted)" }}>
            Default scrape settings
          </h4>
          <ScrapeFlagsEditor
            value={edit.scrape_defaults || DEFAULT_SCRAPE_CONFIG}
            onChange={(scrape_defaults) => setEdit({ ...edit, scrape_defaults, threads: Number(scrape_defaults.threads) || edit.threads })}
          />
          <label>
            <input type="checkbox" checked={edit.is_active} onChange={(e) => setEdit({ ...edit, is_active: e.target.checked })} /> Active
          </label>
          <div style={{ display: "flex", gap: "0.5rem", gridColumn: "1 / -1" }}>
            <button className="btn" type="submit">
              Save
            </button>
            <button className="btn secondary" type="button" onClick={() => setEdit(null)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
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

type WorkerUpdateInfo = {
  status: string;
  ref: string;
  message: string;
  requested_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
};

type WorkerRow = {
  id: number;
  name: string;
  token_prefix: string;
  online: boolean;
  is_enabled: boolean;
  is_draining: boolean;
  cpu_percent: number;
  mem_percent: number;
  disk_percent: number;
  mem_used_gb: number;
  mem_total_gb: number;
  disk_used_gb: number;
  disk_total_gb: number;
  load_avg_1: number;
  load_avg_5: number;
  load_avg_15: number;
  host_os: string;
  hostname: string;
  version: string;
  max_browsers: number;
  active_leases: number;
  proxy_pool_id: number | null;
  proxy_pool_name: string | null;
  worker_config: ScrapeConfig;
  update?: WorkerUpdateInfo;
};

export function WorkersAdminPage() {
  const [workers, setWorkers] = useState<WorkerRow[]>([]);
  const [pools, setPools] = useState<Array<{ id: number; name: string }>>([]);
  const [packages, setPackages] = useState<Array<{ id: number; name: string }>>([]);
  const [name, setName] = useState("");
  const [createBrowsers, setCreateBrowsers] = useState(2);
  const [createPool, setCreatePool] = useState<number | "">("");
  const [createPackage, setCreatePackage] = useState<number | "">("");
  const [createdToken, setCreatedToken] = useState("");
  const [hint, setHint] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [edit, setEdit] = useState<{
    name: string;
    is_enabled: boolean;
    is_draining: boolean;
    max_browsers: number;
    proxy_pool_id: number | null;
    worker_config: ScrapeConfig;
  } | null>(null);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [logLines, setLogLines] = useState<string[]>([]);
  const [logUpdatedAt, setLogUpdatedAt] = useState<string | null>(null);
  const [logError, setLogError] = useState("");
  const [logAuto, setLogAuto] = useState(true);
  const [updateRef, setUpdateRef] = useState("main");
  const [updateBusy, setUpdateBusy] = useState(false);

  async function refresh() {
    const [w, p, pkgs] = await Promise.all([
      api<WorkerRow[]>("/api/workers"),
      api<Array<{ id: number; name: string }>>("/api/proxy-pools"),
      api<Array<{ id: number; name: string }>>("/api/packages").catch(() => []),
    ]);
    setWorkers(w);
    setPools(p);
    setPackages(pkgs);
  }

  useEffect(() => {
    refresh().catch((e) => setError(e.message));
    const t = setInterval(() => refresh().catch(() => undefined), 5000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (selectedId == null || !logAuto) return;
    let cancelled = false;
    async function pullLogs() {
      try {
        const data = await api<{ lines: string[]; updated_at?: string | null }>(
          `/api/workers/${selectedId}/logs`,
        );
        if (cancelled) return;
        setLogLines(Array.isArray(data.lines) ? data.lines.map(String) : []);
        setLogUpdatedAt(data.updated_at || null);
        setLogError("");
      } catch (e) {
        if (!cancelled) setLogError(e instanceof Error ? e.message : "Failed to load logs");
      }
    }
    pullLogs();
    const t = setInterval(pullLogs, 3000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [selectedId, logAuto]);

  function openEdit(w: WorkerRow) {
    setSelectedId(w.id);
    setMsg("");
    setError("");
    setLogLines([]);
    setLogUpdatedAt(null);
    setLogError("");
    setLogAuto(true);
    const cfg = { ...(w.worker_config || {}) };
    delete cfg.captcha_key_configured;
    delete cfg.captcha_backup_key_configured;
    delete cfg.captcha_key;
    delete cfg.captcha_backup_key;
    delete cfg.captcha_provider;
    delete cfg.captcha_host;
    delete cfg.captcha_retries;
    delete cfg.captcha_backup_provider;
    delete cfg.captcha_backup_host;
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
    const body: Record<string, unknown> = {
      name,
      max_browsers: createBrowsers,
      proxy_pool_id: createPool === "" ? null : createPool,
    };
    if (createPackage !== "") body.seed_from_package_id = createPackage;
    const res = await api<{ token: string; install_hint: string; worker: WorkerRow }>("/api/workers", {
      method: "POST",
      body: JSON.stringify(body),
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
      await api(`/api/workers/${selectedId}`, { method: "PATCH", body: JSON.stringify(body) });
      setMsg("Worker settings saved. Online workers pick them up on the next heartbeat/lease.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  }

  async function resetToDefaults() {
    if (!selectedId) return;
    setError("");
    try {
      const w = await api<WorkerRow>(`/api/workers/${selectedId}`, {
        method: "PATCH",
        body: JSON.stringify({ reset_config_to_defaults: true }),
      });
      openEdit(w);
      setMsg("Worker config reset to built-in defaults.");
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

  async function requestFleetUpdate(workerIds?: number[]) {
    const ref = updateRef.trim() || "main";
    const scope = workerIds?.length ? `${workerIds.length} worker(s)` : "all workers";
    if (
      !confirm(
        `Queue git update (ref=${ref}) for ${scope}?\nOnline agents pull on the next heartbeat, then restart.`,
      )
    ) {
      return;
    }
    setError("");
    setMsg("");
    setUpdateBusy(true);
    try {
      const body: Record<string, unknown> = { ref };
      if (workerIds?.length) body.worker_ids = workerIds;
      const res = await api<{ queued: number; ref: string }>("/api/workers/request-update", {
        method: "POST",
        body: JSON.stringify(body),
      });
      setMsg(
        `Queued update for ${res.queued} worker(s) at ref=${res.ref}. Status updates as agents heartbeat.`,
      );
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update request failed");
    } finally {
      setUpdateBusy(false);
    }
  }

  function updateBadge(u?: WorkerUpdateInfo) {
    const status = (u?.status || "idle").toLowerCase();
    const cls =
      status === "success"
        ? "ok"
        : status === "failed"
          ? "danger"
          : status === "pending" || status === "updating"
            ? "warn"
            : "";
    return { status, cls, message: u?.message || "", ref: u?.ref || "main" };
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>Workers</h1>
          <p className="subtitle">
            Per-worker scrape flags override package defaults on each lease. Optionally seed a new worker from a
            package. 2captcha / CaptchaAI solvers are global under Admin → 2captcha / CaptchaAI.
          </p>
        </div>
      </div>
      <div className="card" style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem", alignItems: "end" }}>
        <label className="field" style={{ minWidth: 160 }}>
          Git ref
          <input
            className="input"
            value={updateRef}
            onChange={(e) => setUpdateRef(e.target.value)}
            placeholder="main or latest"
            disabled={updateBusy}
          />
        </label>
        <button
          className="btn"
          type="button"
          disabled={updateBusy || workers.length === 0}
          onClick={() => requestFleetUpdate()}
        >
          Update all workers
        </button>
        <p className="muted" style={{ margin: 0, flex: "1 1 220px", fontSize: "0.85rem" }}>
          After you push to GitHub, queue a fleet update here. Online workers run the fixed{" "}
          <code>install.py --role worker --update</code> path on the next heartbeat (no SSH per VPS).
        </p>
      </div>
      <form className="card form-grid two" onSubmit={create} style={{ alignItems: "end" }}>
        <label className="field">
          Name
          <input className="input" placeholder="Worker name" value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label className="field">
          Max concurrent instances
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
        <label className="field">
          Seed from package (optional)
          <select className="input" value={createPackage} onChange={(e) => setCreatePackage(e.target.value === "" ? "" : Number(e.target.value))}>
            <option value="">Built-in defaults</option>
            {packages.map((p) => (
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
              <th>Update</th>
              <th>Instances</th>
              <th>CPU</th>
              <th>RAM</th>
              <th>Disk</th>
              <th>Host</th>
              <th>Pool / Scrape</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {workers.map((w) => {
              const status = !w.is_enabled ? "disabled" : w.is_draining ? "draining" : w.online ? "online" : "offline";
              const statusCls =
                status === "online" ? "ok" : status === "draining" ? "warn" : status === "offline" || status === "disabled" ? "danger" : "";
              const leaseLoad = (w.active_leases || 0) / Math.max(1, w.max_browsers);
              const upd = updateBadge(w.update);
              return (
                <tr key={w.id} style={{ background: selectedId === w.id ? "color-mix(in srgb, var(--accent) 12%, transparent)" : undefined }}>
                  <td>
                    <strong>{w.name}</strong>
                    <div className="muted" style={{ fontSize: "0.8rem" }}>
                      v{w.version || "—"} · {w.token_prefix}…
                    </div>
                  </td>
                  <td>
                    <span className={`badge ${statusCls}`}>{status}</span>
                  </td>
                  <td>
                    <span className={`badge ${upd.cls}`} title={upd.message || undefined}>
                      {upd.status}
                    </span>
                    {upd.status !== "idle" ? (
                      <div className="muted" style={{ fontSize: "0.75rem" }}>
                        {upd.ref}
                        {upd.message ? ` · ${upd.message.slice(0, 80)}` : ""}
                      </div>
                    ) : null}
                  </td>
                  <td>
                    {w.active_leases ?? 0}/{w.max_browsers}
                    <div className="meter" style={{ marginTop: 4, minWidth: 72 }}>
                      <div className="meter-track">
                        <div
                          className={`meter-fill ${leaseLoad >= 0.9 ? "danger" : leaseLoad >= 0.7 ? "warn" : "ok"}`}
                          style={{ width: `${Math.min(100, leaseLoad * 100)}%` }}
                        />
                      </div>
                    </div>
                  </td>
                  <td>
                    {w.cpu_percent.toFixed(0)}%
                    {w.load_avg_1 ? <div className="muted" style={{ fontSize: "0.75rem" }}>load {w.load_avg_1}</div> : null}
                  </td>
                  <td>
                    {w.mem_percent.toFixed(0)}%
                    {w.mem_total_gb ? (
                      <div className="muted" style={{ fontSize: "0.75rem" }}>
                        {w.mem_used_gb}/{w.mem_total_gb} GB
                      </div>
                    ) : null}
                  </td>
                  <td>
                    {(w.disk_percent || 0).toFixed(0)}%
                    {w.disk_total_gb ? (
                      <div className="muted" style={{ fontSize: "0.75rem" }}>
                        {w.disk_used_gb}/{w.disk_total_gb} GB
                      </div>
                    ) : null}
                  </td>
                  <td>
                    <code>{w.hostname || "—"}</code>
                    <div className="muted" style={{ fontSize: "0.75rem" }}>
                      {w.host_os || "—"}
                    </div>
                  </td>
                  <td>
                      <code>{w.proxy_pool_name || "—"}</code>
                      <div className="muted" style={{ fontSize: "0.75rem" }}>
                        {String(w.worker_config?.engine || "chrome")} · {String(w.worker_config?.threads ?? "—")} thr
                      </div>
                  </td>
                  <td style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
                    <button className="btn secondary" type="button" onClick={() => openEdit(w)}>
                      Settings
                    </button>
                    <button
                      className="btn secondary"
                      type="button"
                      disabled={updateBusy}
                      onClick={() => requestFleetUpdate([w.id])}
                    >
                      Request update
                    </button>
                  </td>
                </tr>
              );
            })}
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
              Max concurrent instances
              <input
                className="input"
                type="number"
                min={1}
                max={64}
                value={edit.max_browsers}
                onChange={(e) => setEdit({ ...edit, max_browsers: Number(e.target.value) || 1 })}
              />
              <span className="muted" style={{ fontSize: "0.75rem" }}>
                How many user scrape instances this worker may run at once
              </span>
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

          <h3 style={{ margin: "0.4rem 0 0", fontSize: "0.9rem", color: "var(--muted)" }}>
            Scrape flags (machine overrides — merge after package defaults)
          </h3>
          <ScrapeFlagsEditor
            value={edit.worker_config}
            onChange={(worker_config) => setEdit({ ...edit, worker_config })}
          />
          <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
            <button className="btn" type="submit">
              Save worker settings
            </button>
            <button className="btn secondary" type="button" onClick={resetToDefaults}>
              Reset to built-in defaults
            </button>
            <button className="btn secondary" type="button" onClick={rotateToken}>
              Rotate token
            </button>
          </div>
        </form>
      ) : null}

      {selectedId != null ? (
        <div className="card worker-log-panel">
          <div className="worker-log-meta">
            <div>
              <h2 style={{ margin: 0, fontSize: "1.05rem" }}>Live worker logs — #{selectedId}</h2>
              <p className="muted" style={{ margin: "0.25rem 0 0", fontSize: "0.8rem" }}>
                Recent lines pushed by the agent (from logs/worker.log or stdout).
                {logUpdatedAt ? ` · updated ${new Date(logUpdatedAt).toLocaleTimeString()}` : ""}
              </p>
            </div>
            <label className="muted" style={{ display: "inline-flex", gap: "0.4rem", alignItems: "center", fontSize: "0.85rem" }}>
              <input type="checkbox" checked={logAuto} onChange={(e) => setLogAuto(e.target.checked)} />
              Auto-refresh
            </label>
          </div>
          {logError ? <p className="error">{logError}</p> : null}
          <pre className="worker-log-pre">
            {logLines.length ? logLines.join("\n") : "Waiting for worker log lines…"}
          </pre>
        </div>
      ) : null}
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
