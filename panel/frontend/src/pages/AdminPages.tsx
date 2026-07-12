import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";
import { EngineMultiSelect, PermsEditor, StringListField, usePermSchema } from "../components/PermsEditor";

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
};

export function UsersAdminPage() {
  const { schema, workers } = usePermSchema();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [form, setForm] = useState({
    username: "",
    email: "",
    password: "",
    role: "user",
    telegram_id: "",
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
  } | null>(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  async function refresh() {
    setUsers(await api("/api/users"));
  }
  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (schema && Object.keys(form.perms).length === 0) {
      setForm((f) => ({ ...f, perms: { ...schema.defaults } }));
    }
  }, [schema]);

  async function create(e: FormEvent) {
    e.preventDefault();
    setError("");
    setMsg("");
    try {
      await api("/api/users", {
        method: "POST",
        body: JSON.stringify({
          username: form.username,
          email: form.email,
          password: form.password,
          role: form.role,
          telegram_id: form.telegram_id || null,
          perms: form.perms,
          worker_ids: form.worker_ids,
        }),
      });
      setForm({
        username: "",
        email: "",
        password: "",
        role: "user",
        telegram_id: "",
        perms: schema ? { ...schema.defaults } : {},
        worker_ids: [],
      });
      setMsg("User created (must change password + setup 2FA on first login)");
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
        email: edit.email,
        role: edit.role,
        is_active: edit.is_active,
        telegram_id: edit.telegram_id || null,
        reset_2fa: edit.reset_2fa,
        perms: edit.perms,
        worker_ids: edit.worker_ids,
      };
      if (edit.password) body.password = edit.password;
      await api(`/api/users/${edit.id}`, { method: "PATCH", body: JSON.stringify(body) });
      setMsg("User updated.");
      setEdit(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>Users</h1>
          <p className="subtitle">Create panel/admin accounts and set role permissions. Leave worker assignment empty for dedicated-worker users to use all workers.</p>
        </div>
      </div>
      <form className="card" onSubmit={create} style={{ display: "grid", gap: "0.6rem", gridTemplateColumns: "repeat(auto-fit,minmax(160px,1fr))" }}>
        <h3 style={{ margin: 0, gridColumn: "1 / -1" }}>Create user</h3>
        <input className="input" placeholder="username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required />
        <input className="input" placeholder="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
        <input className="input" placeholder="temp password" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required minLength={8} />
        <select className="input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
          <option value="user">user</option>
          <option value="admin">admin</option>
        </select>
        <input className="input" placeholder="telegram id" value={form.telegram_id} onChange={(e) => setForm({ ...form, telegram_id: e.target.value })} />
        {form.role === "user" ? (
          <PermsEditor
            perms={form.perms}
            workerIds={form.worker_ids}
            onPermsChange={(perms) => setForm({ ...form, perms })}
            onWorkerIdsChange={(worker_ids) => setForm({ ...form, worker_ids })}
            schema={schema}
            workers={workers}
            showWorkers={false}
            title="Role permissions"
          />
        ) : (
          <p className="muted" style={{ gridColumn: "1 / -1" }}>
            Admins have full access; permission matrix applies to user-role accounts.
          </p>
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
                  <div className="muted">{u.email}</div>
                </td>
                <td>{u.role}</td>
                <td>{u.worker_ids?.length ? u.worker_ids.join(", ") : "any"}</td>
                <td>
                  <span className={`badge ${u.is_active ? "ok" : "danger"}`}>{u.is_active ? "active" : "disabled"}</span>
                </td>
                <td>{u.totp_enabled ? "yes" : "pending"}</td>
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
                      })
                    }
                  >
                    Edit
                  </button>
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
      {edit ? (
        <form className="card" onSubmit={saveEdit} style={{ display: "grid", gap: "0.65rem", maxWidth: 720 }}>
          <h3 style={{ margin: 0 }}>Edit {edit.username}</h3>
          <label className="field">
            Email
            <input className="input" value={edit.email} onChange={(e) => setEdit({ ...edit, email: e.target.value })} />
          </label>
          <label className="field">
            Role
            <select className="input" value={edit.role} onChange={(e) => setEdit({ ...edit, role: e.target.value })}>
              <option value="user">user</option>
              <option value="admin">admin</option>
            </select>
          </label>
          <label className="field">
            Telegram ID
            <input className="input" value={edit.telegram_id} onChange={(e) => setEdit({ ...edit, telegram_id: e.target.value })} placeholder="blank to unlink" />
          </label>
          <label className="field">
            New password (optional)
            <input className="input" type="password" value={edit.password} onChange={(e) => setEdit({ ...edit, password: e.target.value })} minLength={8} />
          </label>
          <label>
            <input type="checkbox" checked={edit.is_active} onChange={(e) => setEdit({ ...edit, is_active: e.target.checked })} /> Active
          </label>
          <label>
            <input type="checkbox" checked={edit.reset_2fa} onChange={(e) => setEdit({ ...edit, reset_2fa: e.target.checked })} /> Reset 2FA
          </label>
          {edit.role === "user" ? (
            <PermsEditor
              perms={edit.perms}
              workerIds={edit.worker_ids}
              onPermsChange={(perms) => setEdit({ ...edit, perms })}
              onWorkerIdsChange={(worker_ids) => setEdit({ ...edit, worker_ids })}
              schema={schema}
              workers={workers}
              showWorkers={edit.dedicated_worker}
              title="Role permissions"
            />
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
  scrape_settings_id: number | null;
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
  create_scrape_profile: true,
  scrape_settings_id: "" as number | "",
};

export function PackagesAdminPage() {
  const { schema } = usePermSchema();
  const engines = schema?.engines || ["chrome", "google-chrome", "edge", "brave", "camoufox"];
  const [packages, setPackages] = useState<PackageRow[]>([]);
  const [profiles, setProfiles] = useState<Array<{ id: number; name: string }>>([]);
  const [form, setForm] = useState(EMPTY_PKG_FORM);
  const [edit, setEdit] = useState<PackageRow | null>(null);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  async function refresh() {
    const [pkgs, profs] = await Promise.all([
      api<PackageRow[]>("/api/packages"),
      api<Array<{ id: number; name: string }>>("/api/scrape-profiles").catch(() => []),
    ]);
    setPackages(pkgs);
    setProfiles(profs);
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
        create_scrape_profile: form.create_scrape_profile && form.scrape_settings_id === "",
      };
      if (form.scrape_settings_id !== "") body.scrape_settings_id = Number(form.scrape_settings_id);
      await api("/api/packages", { method: "POST", body: JSON.stringify(body) });
      setForm(EMPTY_PKG_FORM);
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
          scrape_settings_id: edit.scrape_settings_id,
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

  function profileName(id: number | null) {
    if (!id) return "—";
    return profiles.find((p) => p.id === id)?.name || `#${id}`;
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
          <p className="subtitle">Full subscription packages: limits, engines, scrape profile, headings, and feature list.</p>
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
          <input className="input" type="number" value={form.threads} onChange={(e) => setForm({ ...form, threads: Number(e.target.value) })} />
        </label>
        <label className="field">
          Max upload (MB)
          <input className="input" type="number" value={form.max_upload_mb} onChange={(e) => setForm({ ...form, max_upload_mb: Number(e.target.value) })} />
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
        <label className="field" style={{ gridColumn: "1 / -1" }}>
          Scrape profile
          <select
            className="input"
            value={form.scrape_settings_id}
            onChange={(e) =>
              setForm({
                ...form,
                scrape_settings_id: e.target.value === "" ? "" : Number(e.target.value),
                create_scrape_profile: e.target.value === "",
              })
            }
          >
            <option value="">Auto-create new profile from default</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
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
              <th>Scrape profile</th>
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
                <td>{profileName(p.scrape_settings_id)}</td>
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
            <input className="input" type="number" value={edit.threads} onChange={(e) => setEdit({ ...edit, threads: Number(e.target.value) })} />
          </label>
          <label className="field">
            Max upload (MB)
            <input className="input" type="number" value={edit.max_upload_mb} onChange={(e) => setEdit({ ...edit, max_upload_mb: Number(e.target.value) })} />
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
          <label className="field" style={{ gridColumn: "1 / -1" }}>
            Scrape profile
            <select
              className="input"
              value={edit.scrape_settings_id ?? ""}
              onChange={(e) => setEdit({ ...edit, scrape_settings_id: e.target.value === "" ? null : Number(e.target.value) })}
            >
              <option value="">None</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
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
  scrape_settings_id: number | null;
  scrape_settings_name: string | null;
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
  { key: "captcha_provider", label: "Captcha primary", type: "select", options: ["none", "2captcha", "captchaai"] },
  { key: "captcha_key", label: "Captcha key (blank=keep)", type: "text" },
  { key: "captcha_host", label: "Captcha host", type: "text" },
  { key: "captcha_retries", label: "Captcha retries", type: "number" },
  { key: "captcha_backup_provider", label: "Captcha backup", type: "select", options: ["none", "2captcha", "captchaai"] },
  { key: "captcha_backup_key", label: "Backup key (blank=keep)", type: "text" },
  { key: "captcha_backup_host", label: "Backup host", type: "text" },
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
  const [profiles, setProfiles] = useState<Array<{ id: number; name: string }>>([]);
  const [name, setName] = useState("");
  const [createBrowsers, setCreateBrowsers] = useState(2);
  const [createPool, setCreatePool] = useState<number | "">("");
  const [createProfile, setCreateProfile] = useState<number | "">("");
  const [createdToken, setCreatedToken] = useState("");
  const [hint, setHint] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [edit, setEdit] = useState<{
    name: string;
    is_enabled: boolean;
    is_draining: boolean;
    max_browsers: number;
    proxy_pool_id: number | null;
    scrape_settings_id: number | null;
    worker_config: WorkerConfig;
  } | null>(null);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  async function refresh() {
    const [w, p, pr] = await Promise.all([
      api<WorkerRow[]>("/api/workers"),
      api<Array<{ id: number; name: string }>>("/api/proxy-pools"),
      api<Array<{ id: number; name: string }>>("/api/scrape-profiles").catch(() => []),
    ]);
    setWorkers(w);
    setPools(p);
    setProfiles(pr);
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
    delete cfg.captcha_backup_key_configured;
    cfg.captcha_key = "";
    cfg.captcha_backup_key = "";
    setEdit({
      name: w.name,
      is_enabled: w.is_enabled,
      is_draining: w.is_draining,
      max_browsers: w.max_browsers,
      proxy_pool_id: w.proxy_pool_id,
      scrape_settings_id: w.scrape_settings_id,
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
        scrape_settings_id: createProfile === "" ? null : createProfile,
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
        scrape_settings_id: edit.scrape_settings_id,
        worker_config: { ...edit.worker_config },
      };
      const wc = body.worker_config as WorkerConfig;
      if (!wc.captcha_key) delete wc.captcha_key;
      if (!wc.captcha_backup_key) delete wc.captcha_backup_key;
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
      setMsg("Worker config reset from assigned scrape profile.");
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
            Assign a proxy pool and scrape profile per worker. Fine-tune overrides merge on top of the profile for each lease.
          </p>
        </div>
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
          Scrape profile
          <select className="input" value={createProfile} onChange={(e) => setCreateProfile(e.target.value === "" ? "" : Number(e.target.value))}>
            <option value="">Default profile</option>
            {profiles.map((p) => (
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
              <th>Instances</th>
              <th>CPU</th>
              <th>RAM</th>
              <th>Disk</th>
              <th>Host</th>
              <th>Pool / Profile</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {workers.map((w) => {
              const status = !w.is_enabled ? "disabled" : w.is_draining ? "draining" : w.online ? "online" : "offline";
              const statusCls =
                status === "online" ? "ok" : status === "draining" ? "warn" : status === "offline" || status === "disabled" ? "danger" : "";
              const leaseLoad = (w.active_leases || 0) / Math.max(1, w.max_browsers);
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
                        {w.scrape_settings_name || "default profile"}
                      </div>
                  </td>
                  <td>
                    <button className="btn secondary" type="button" onClick={() => openEdit(w)}>
                      Settings
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
              Scrape profile
              <select
                className="input"
                value={edit.scrape_settings_id ?? ""}
                onChange={(e) => setEdit({ ...edit, scrape_settings_id: e.target.value === "" ? null : Number(e.target.value) })}
              >
                <option value="">Default</option>
                {profiles.map((p) => (
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
