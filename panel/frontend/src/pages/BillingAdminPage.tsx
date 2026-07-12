import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";
import { PermsEditor, usePermSchema } from "../components/PermsEditor";

type Tab = "telegram" | "subscriptions" | "orders" | "settings";

type PackageRow = {
  id: number;
  slug: string;
  name: string;
  tier: number;
  price_usdt: number;
  duration_days: number;
  threads: number;
  max_upload_mb: number;
  is_active: boolean;
  dedicated_worker?: boolean;
};

type SubRow = {
  id: number;
  user_id: number;
  username: string;
  telegram_id: string | null;
  package_id: number | null;
  package_name: string;
  threads: number;
  max_upload_mb: number;
  tier: number;
  starts_at: string;
  expires_at: string;
  is_active: boolean;
  days_left: number;
  user_is_active: boolean;
};

type Subscriber = {
  user_id: number;
  username: string;
  email: string;
  role: string;
  is_active: boolean;
  telegram_id: string | null;
  totp_enabled: boolean;
  created_at: string;
  subscription: SubRow | null;
  has_active_subscription: boolean;
  perms: Record<string, unknown>;
  worker_ids: number[];
  dedicated_worker: boolean;
};

type OrderRow = {
  id: number;
  user_id: number;
  username: string;
  telegram_id: string | null;
  package_name: string;
  status: string;
  payment_method: string;
  created_at: string;
};

type UserLite = { id: number; username: string; telegram_id: string | null };

export function BillingAdminPage() {
  const { schema, workers } = usePermSchema();
  const [tab, setTab] = useState<Tab>("telegram");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [subscribers, setSubscribers] = useState<Subscriber[]>([]);
  const [subs, setSubs] = useState<SubRow[]>([]);
  const [packages, setPackages] = useState<PackageRow[]>([]);
  const [users, setUsers] = useState<UserLite[]>([]);
  const [pending, setPending] = useState<OrderRow[]>([]);
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [tgOnly, setTgOnly] = useState(true);
  const [activeOnly, setActiveOnly] = useState(false);

  const [form, setForm] = useState({
    enabled: false,
    usdt_enabled: false,
    usdt_wallet: "",
    usdt_contract: "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
    usdt_api_base: "https://apilist.tronscanapi.com",
    usdt_api_key: "",
    manual_enabled: false,
    manual_methods_json: "[]",
    max_upload_mb: 5,
  });

  const [addTg, setAddTg] = useState({
    telegram_id: "",
    username: "",
    package_id: "",
    duration_days: "",
    is_active: true,
    notify: true,
    perms: {} as Record<string, unknown>,
    worker_ids: [] as number[],
  });

  const [grant, setGrant] = useState({
    mode: "user" as "user" | "telegram",
    user_id: "",
    telegram_id: "",
    package_id: "",
    duration_days: "",
    notify: true,
  });

  const [editUser, setEditUser] = useState<Subscriber | null>(null);
  const [editDraft, setEditDraft] = useState({
    username: "",
    telegram_id: "",
    email: "",
    is_active: true,
    perms: {} as Record<string, unknown>,
    worker_ids: [] as number[],
  });

  const [editSub, setEditSub] = useState<SubRow | null>(null);
  const [subDraft, setSubDraft] = useState({
    package_id: "",
    threads: 2,
    max_upload_mb: 5,
    expires_at: "",
    is_active: true,
    extend_days: "30",
  });

  const activePackages = useMemo(() => packages.filter((p) => p.is_active), [packages]);

  async function refresh() {
    setError("");
    const [subsList, subRows, pkgs, userRows, pendingRows, orderRows, settings] = await Promise.all([
      api<Subscriber[]>(`/api/billing/subscribers?telegram_only=${tgOnly}`),
      api<SubRow[]>(`/api/subscriptions?active_only=${activeOnly}`),
      api<PackageRow[]>("/api/packages"),
      api<UserLite[]>("/api/users"),
      api<OrderRow[]>("/api/orders/pending"),
      api<OrderRow[]>("/api/orders?limit=40"),
      api<Record<string, unknown>>("/api/billing/settings"),
    ]);
    setSubscribers(subsList);
    setSubs(subRows);
    setPackages(pkgs);
    setUsers(userRows);
    setPending(pendingRows);
    setOrders(orderRows);
    setForm({
      enabled: Boolean(settings.enabled),
      usdt_enabled: Boolean(settings.usdt_enabled),
      usdt_wallet: String(settings.usdt_wallet || ""),
      usdt_contract: String(settings.usdt_contract || ""),
      usdt_api_base: String(settings.usdt_api_base || ""),
      usdt_api_key: "",
      manual_enabled: Boolean(settings.manual_enabled),
      manual_methods_json: JSON.stringify(settings.manual_methods || [], null, 2),
      max_upload_mb: Number(settings.max_upload_mb || 5),
    });
  }

  useEffect(() => {
    refresh().catch((e) => setError(e instanceof Error ? e.message : "Load failed"));
  }, [tgOnly, activeOnly]);

  useEffect(() => {
    if (schema && Object.keys(addTg.perms).length === 0) {
      setAddTg((a) => ({ ...a, perms: { ...schema.defaults, telegram_user: true } }));
    }
  }, [schema]);

  function flash(ok: string) {
    setMsg(ok);
    setError("");
  }

  async function run(action: () => Promise<void>, okMsg: string) {
    try {
      setError("");
      await action();
      flash(okMsg);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    }
  }

  async function saveSettings(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      let methods = [];
      try {
        methods = JSON.parse(form.manual_methods_json || "[]");
      } catch {
        throw new Error("Invalid manual_methods JSON");
      }
      const body: Record<string, unknown> = {
        enabled: form.enabled,
        usdt_enabled: form.usdt_enabled,
        usdt_wallet: form.usdt_wallet,
        usdt_contract: form.usdt_contract,
        usdt_api_base: form.usdt_api_base,
        manual_enabled: form.manual_enabled,
        manual_methods: methods,
        max_upload_mb: form.max_upload_mb,
      };
      if (form.usdt_api_key) body.usdt_api_key = form.usdt_api_key;
      await api("/api/billing/settings", { method: "PUT", body: JSON.stringify(body) });
    }, "Billing settings saved.");
  }

  async function createTelegramUser(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      const body: Record<string, unknown> = {
        telegram_id: addTg.telegram_id.trim(),
        is_active: addTg.is_active,
        notify: addTg.notify,
        perms: { ...addTg.perms, telegram_user: true },
        worker_ids: addTg.worker_ids,
      };
      if (addTg.username.trim()) body.username = addTg.username.trim();
      if (addTg.package_id) body.package_id = Number(addTg.package_id);
      if (addTg.duration_days) body.duration_days = Number(addTg.duration_days);
      await api("/api/billing/telegram-users", { method: "POST", body: JSON.stringify(body) });
      setAddTg({
        telegram_id: "",
        username: "",
        package_id: "",
        duration_days: "",
        is_active: true,
        notify: true,
        perms: schema ? { ...schema.defaults, telegram_user: true } : {},
        worker_ids: [],
      });
    }, "Telegram user added.");
  }

  async function grantSub(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      const body: Record<string, unknown> = {
        package_id: Number(grant.package_id),
        notify: grant.notify,
      };
      if (grant.mode === "user") body.user_id = Number(grant.user_id);
      else body.telegram_id = grant.telegram_id.trim();
      if (grant.duration_days) body.duration_days = Number(grant.duration_days);
      await api("/api/subscriptions/grant", { method: "POST", body: JSON.stringify(body) });
    }, "Subscription granted.");
  }

  function openEditUser(s: Subscriber) {
    setEditUser(s);
    setEditDraft({
      username: s.username,
      telegram_id: s.telegram_id || "",
      email: s.email,
      is_active: s.is_active,
      perms: { ...(schema?.defaults || {}), ...(s.perms || {}), telegram_user: true },
      worker_ids: [...(s.worker_ids || [])],
    });
  }

  function openEditSub(s: SubRow) {
    setEditSub(s);
    setSubDraft({
      package_id: s.package_id != null ? String(s.package_id) : "",
      threads: s.threads,
      max_upload_mb: s.max_upload_mb,
      expires_at: s.expires_at.slice(0, 16),
      is_active: s.is_active,
      extend_days: "30",
    });
  }

  const tabs: Array<{ id: Tab; label: string }> = [
    { id: "telegram", label: "Telegram users" },
    { id: "subscriptions", label: "Subscriptions" },
    { id: "orders", label: "Orders" },
    { id: "settings", label: "Settings" },
  ];

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>Billing & Telegram</h1>
          <p className="subtitle">Manage Telegram-linked users, grants, subscriptions, and payment settings. Dedicated-worker users with no assignment use all workers.</p>
        </div>
      </div>

      <div className="tab-bar">
        {tabs.map((t) => (
          <button key={t.id} type="button" className={`tab-btn ${tab === t.id ? "active" : ""}`} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>

      {error ? <p className="error">{error}</p> : null}
      {msg ? <p className="muted">{msg}</p> : null}

      {tab === "telegram" ? (
        <>
          <form className="card" onSubmit={createTelegramUser}>
            <h3 style={{ marginTop: 0 }}>Add Telegram user</h3>
            <p className="muted" style={{ marginTop: 0 }}>
              Creates a panel account linked to a Telegram id. Optionally grant a package immediately.
            </p>
            <div className="form-grid two">
              <label className="field">
                Telegram ID
                <input
                  className="input"
                  required
                  value={addTg.telegram_id}
                  onChange={(e) => setAddTg({ ...addTg, telegram_id: e.target.value })}
                  placeholder="numeric id from /whoami"
                />
              </label>
              <label className="field">
                Username (optional)
                <input
                  className="input"
                  value={addTg.username}
                  onChange={(e) => setAddTg({ ...addTg, username: e.target.value })}
                  placeholder="defaults to tg_&lt;id&gt;"
                />
              </label>
              <label className="field">
                Package grant
                <select className="input" value={addTg.package_id} onChange={(e) => setAddTg({ ...addTg, package_id: e.target.value })}>
                  <option value="">None (access only)</option>
                  {activePackages.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} ({p.duration_days}d)
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                Duration override (days)
                <input
                  className="input"
                  type="number"
                  min={1}
                  value={addTg.duration_days}
                  onChange={(e) => setAddTg({ ...addTg, duration_days: e.target.value })}
                  placeholder="package default"
                />
              </label>
            </div>
            <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", marginTop: "0.75rem" }}>
              <label>
                <input type="checkbox" checked={addTg.is_active} onChange={(e) => setAddTg({ ...addTg, is_active: e.target.checked })} /> Active
              </label>
              <label>
                <input type="checkbox" checked={addTg.notify} onChange={(e) => setAddTg({ ...addTg, notify: e.target.checked })} /> Notify on Telegram
              </label>
            </div>
            <div style={{ marginTop: "0.85rem" }}>
              <PermsEditor
                perms={addTg.perms}
                workerIds={addTg.worker_ids}
                onPermsChange={(perms) => setAddTg({ ...addTg, perms: { ...perms, telegram_user: true } })}
                onWorkerIdsChange={(worker_ids) => setAddTg({ ...addTg, worker_ids })}
                schema={schema}
                workers={workers}
                title="Permissions"
                showWorkers={Boolean(
                  addTg.package_id && activePackages.find((p) => String(p.id) === String(addTg.package_id))?.dedicated_worker
                )}
              />
            </div>
            <button className="btn" type="submit" style={{ marginTop: "0.75rem" }}>
              Add user
            </button>
          </form>

          <form className="card" onSubmit={grantSub}>
            <h3 style={{ marginTop: 0 }}>Grant / allow subscription</h3>
            <div className="form-grid two">
              <label className="field">
                Target
                <select className="input" value={grant.mode} onChange={(e) => setGrant({ ...grant, mode: e.target.value as "user" | "telegram" })}>
                  <option value="user">Panel user</option>
                  <option value="telegram">Telegram ID</option>
                </select>
              </label>
              {grant.mode === "user" ? (
                <label className="field">
                  User
                  <select className="input" required value={grant.user_id} onChange={(e) => setGrant({ ...grant, user_id: e.target.value })}>
                    <option value="">Select…</option>
                    {users.map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.username}
                        {u.telegram_id ? ` (tg ${u.telegram_id})` : ""}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <label className="field">
                  Telegram ID
                  <input className="input" required value={grant.telegram_id} onChange={(e) => setGrant({ ...grant, telegram_id: e.target.value })} />
                </label>
              )}
              <label className="field">
                Package
                <select className="input" required value={grant.package_id} onChange={(e) => setGrant({ ...grant, package_id: e.target.value })}>
                  <option value="">Select…</option>
                  {packages.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} {!p.is_active ? "(disabled)" : ""}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                Days (optional)
                <input className="input" type="number" min={1} value={grant.duration_days} onChange={(e) => setGrant({ ...grant, duration_days: e.target.value })} />
              </label>
            </div>
            <div style={{ display: "flex", gap: "1rem", marginTop: "0.75rem", alignItems: "center" }}>
              <label>
                <input type="checkbox" checked={grant.notify} onChange={(e) => setGrant({ ...grant, notify: e.target.checked })} /> Notify user
              </label>
              <button className="btn" type="submit">
                Grant
              </button>
            </div>
          </form>

          <div className="card">
            <div className="page-header" style={{ marginBottom: "0.75rem" }}>
              <h3 style={{ margin: 0 }}>Telegram / subscriber directory</h3>
              <label>
                <input type="checkbox" checked={tgOnly} onChange={(e) => setTgOnly(e.target.checked)} /> Telegram-linked only
              </label>
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>User</th>
                    <th>Telegram</th>
                    <th>Workers</th>
                    <th>Status</th>
                    <th>Subscription</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {subscribers.map((s) => (
                    <tr key={s.user_id}>
                      <td>
                        <strong>{s.username}</strong>
                        <div className="muted" style={{ fontSize: "0.8rem" }}>
                          #{s.user_id} · {s.role}
                        </div>
                      </td>
                      <td>
                        <code>{s.telegram_id || "—"}</code>
                      </td>
                      <td>{s.worker_ids?.length ? s.worker_ids.join(", ") : "any"}</td>
                      <td>
                        <span className={`badge ${s.is_active ? "ok" : "danger"}`}>{s.is_active ? "active" : "disabled"}</span>
                      </td>
                      <td>
                        {s.has_active_subscription && s.subscription ? (
                          <>
                            {s.subscription.package_name}
                            <div className="muted" style={{ fontSize: "0.8rem" }}>
                              {s.subscription.days_left.toFixed(1)}d left · {s.subscription.threads} threads
                            </div>
                          </>
                        ) : (
                          <span className="muted">No active plan</span>
                        )}
                      </td>
                      <td style={{ display: "flex", gap: "0.35rem", flexWrap: "wrap" }}>
                        <button className="btn secondary sm" type="button" onClick={() => openEditUser(s)}>
                          Edit
                        </button>
                        <button
                          className="btn secondary sm"
                          type="button"
                          onClick={() =>
                            run(
                              async () => {
                                await api(`/api/billing/telegram-users/${s.user_id}`, {
                                  method: "PATCH",
                                  body: JSON.stringify({ is_active: !s.is_active }),
                                });
                              },
                              s.is_active ? "User disabled." : "User enabled."
                            )
                          }
                        >
                          {s.is_active ? "Disable" : "Enable"}
                        </button>
                        {s.subscription?.is_active ? (
                          <button
                            className="btn secondary sm"
                            type="button"
                            onClick={() =>
                              run(async () => {
                                await api(`/api/subscriptions/${s.subscription!.id}/revoke`, { method: "POST", body: "{}" });
                              }, "Subscription revoked.")
                            }
                          >
                            Revoke plan
                          </button>
                        ) : null}
                        <button
                          className="btn secondary sm"
                          type="button"
                          onClick={() =>
                            run(async () => {
                              await api(`/api/billing/telegram-users/${s.user_id}?unlink_only=true`, { method: "DELETE" });
                            }, "Telegram unlinked.")
                          }
                        >
                          Unlink
                        </button>
                        <button
                          className="btn danger sm"
                          type="button"
                          onClick={() => {
                            if (!confirm(`Delete user ${s.username}? This cannot be undone.`)) return;
                            run(async () => {
                              await api(`/api/billing/telegram-users/${s.user_id}`, { method: "DELETE" });
                            }, "User deleted.");
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
          </div>

          {editUser ? (
            <form
              className="card"
              onSubmit={(e) => {
                e.preventDefault();
                run(async () => {
                  await api(`/api/billing/telegram-users/${editUser.user_id}`, {
                    method: "PATCH",
                    body: JSON.stringify({
                      username: editDraft.username,
                      email: editDraft.email,
                      telegram_id: editDraft.telegram_id || null,
                      is_active: editDraft.is_active,
                      unlink_telegram: !editDraft.telegram_id,
                      perms: { ...editDraft.perms, telegram_user: true },
                      worker_ids: editDraft.worker_ids,
                    }),
                  });
                  setEditUser(null);
                }, "User updated.");
              }}
            >
              <h3 style={{ marginTop: 0 }}>Edit — {editUser.username}</h3>
              <div className="form-grid two">
                <label className="field">
                  Username
                  <input className="input" value={editDraft.username} onChange={(e) => setEditDraft({ ...editDraft, username: e.target.value })} />
                </label>
                <label className="field">
                  Email
                  <input className="input" value={editDraft.email} onChange={(e) => setEditDraft({ ...editDraft, email: e.target.value })} />
                </label>
                <label className="field">
                  Telegram ID
                  <input
                    className="input"
                    value={editDraft.telegram_id}
                    onChange={(e) => setEditDraft({ ...editDraft, telegram_id: e.target.value })}
                    placeholder="blank = unlink"
                  />
                </label>
                <label className="field">
                  Status
                  <select
                    className="input"
                    value={editDraft.is_active ? "1" : "0"}
                    onChange={(e) => setEditDraft({ ...editDraft, is_active: e.target.value === "1" })}
                  >
                    <option value="1">Active</option>
                    <option value="0">Disabled</option>
                  </select>
                </label>
              </div>
              <div style={{ marginTop: "0.85rem" }}>
                <PermsEditor
                  perms={editDraft.perms}
                  workerIds={editDraft.worker_ids}
                  onPermsChange={(perms) => setEditDraft({ ...editDraft, perms: { ...perms, telegram_user: true } })}
                  onWorkerIdsChange={(worker_ids) => setEditDraft({ ...editDraft, worker_ids })}
                  schema={schema}
                  workers={workers}
                  title="Permissions"
                  showWorkers={Boolean(editUser.dedicated_worker)}
                />
              </div>
              <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.75rem" }}>
                <button className="btn" type="submit">
                  Save
                </button>
                <button className="btn secondary" type="button" onClick={() => setEditUser(null)}>
                  Cancel
                </button>
              </div>
            </form>
          ) : null}
        </>
      ) : null}

      {tab === "subscriptions" ? (
        <>
          <div className="card">
            <div className="page-header" style={{ marginBottom: "0.75rem" }}>
              <h3 style={{ margin: 0 }}>All subscriptions</h3>
              <label>
                <input type="checkbox" checked={activeOnly} onChange={(e) => setActiveOnly(e.target.checked)} /> Active only
              </label>
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>User</th>
                    <th>Package</th>
                    <th>Threads</th>
                    <th>Expires</th>
                    <th>Status</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {subs.map((s) => (
                    <tr key={s.id}>
                      <td>{s.id}</td>
                      <td>
                        {s.username}
                        <div className="muted" style={{ fontSize: "0.8rem" }}>
                          tg {s.telegram_id || "—"}
                        </div>
                      </td>
                      <td>{s.package_name}</td>
                      <td>
                        {s.threads} / {s.max_upload_mb}MB
                      </td>
                      <td>
                        {new Date(s.expires_at).toLocaleString()}
                        <div className="muted" style={{ fontSize: "0.8rem" }}>
                          {s.days_left.toFixed(1)}d left
                        </div>
                      </td>
                      <td>
                        <span className={`badge ${s.is_active ? "ok" : "danger"}`}>{s.is_active ? "active" : "inactive"}</span>
                      </td>
                      <td style={{ display: "flex", gap: "0.35rem", flexWrap: "wrap" }}>
                        <button className="btn secondary sm" type="button" onClick={() => openEditSub(s)}>
                          Edit
                        </button>
                        <button
                          className="btn secondary sm"
                          type="button"
                          onClick={() =>
                            run(async () => {
                              await api(`/api/subscriptions/${s.id}/extend`, {
                                method: "POST",
                                body: JSON.stringify({ days: 30 }),
                              });
                            }, "Extended +30 days.")
                          }
                        >
                          +30d
                        </button>
                        {s.is_active ? (
                          <button
                            className="btn danger sm"
                            type="button"
                            onClick={() =>
                              run(async () => {
                                await api(`/api/subscriptions/${s.id}/revoke`, { method: "POST", body: "{}" });
                              }, "Revoked.")
                            }
                          >
                            Revoke
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {editSub ? (
            <form
              className="card"
              onSubmit={(e) => {
                e.preventDefault();
                run(async () => {
                  const body: Record<string, unknown> = {
                    threads: Number(subDraft.threads),
                    max_upload_mb: Number(subDraft.max_upload_mb),
                    is_active: subDraft.is_active,
                  };
                  if (subDraft.package_id) body.package_id = Number(subDraft.package_id);
                  if (subDraft.expires_at) body.expires_at = new Date(subDraft.expires_at).toISOString();
                  await api(`/api/subscriptions/${editSub.id}`, { method: "PATCH", body: JSON.stringify(body) });
                  setEditSub(null);
                }, "Subscription updated.");
              }}
            >
              <h3 style={{ marginTop: 0 }}>
                Edit subscription #{editSub.id} — {editSub.username}
              </h3>
              <div className="form-grid two">
                <label className="field">
                  Package
                  <select className="input" value={subDraft.package_id} onChange={(e) => setSubDraft({ ...subDraft, package_id: e.target.value })}>
                    <option value="">Keep current name</option>
                    {packages.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  Expires
                  <input
                    className="input"
                    type="datetime-local"
                    value={subDraft.expires_at}
                    onChange={(e) => setSubDraft({ ...subDraft, expires_at: e.target.value })}
                  />
                </label>
                <label className="field">
                  Threads
                  <input
                    className="input"
                    type="number"
                    min={1}
                    value={subDraft.threads}
                    onChange={(e) => setSubDraft({ ...subDraft, threads: Number(e.target.value) })}
                  />
                </label>
                <label className="field">
                  Max upload MB
                  <input
                    className="input"
                    type="number"
                    min={1}
                    value={subDraft.max_upload_mb}
                    onChange={(e) => setSubDraft({ ...subDraft, max_upload_mb: Number(e.target.value) })}
                  />
                </label>
                <label className="field">
                  Active
                  <select
                    className="input"
                    value={subDraft.is_active ? "1" : "0"}
                    onChange={(e) => setSubDraft({ ...subDraft, is_active: e.target.value === "1" })}
                  >
                    <option value="1">Yes</option>
                    <option value="0">No</option>
                  </select>
                </label>
                <label className="field">
                  Extend by days
                  <div style={{ display: "flex", gap: "0.5rem" }}>
                    <input
                      className="input"
                      type="number"
                      min={1}
                      value={subDraft.extend_days}
                      onChange={(e) => setSubDraft({ ...subDraft, extend_days: e.target.value })}
                    />
                    <button
                      className="btn secondary"
                      type="button"
                      onClick={() =>
                        run(async () => {
                          await api(`/api/subscriptions/${editSub.id}/extend`, {
                            method: "POST",
                            body: JSON.stringify({ days: Number(subDraft.extend_days) || 30 }),
                          });
                          setEditSub(null);
                        }, "Extended.")
                      }
                    >
                      Extend
                    </button>
                  </div>
                </label>
              </div>
              <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.75rem" }}>
                <button className="btn" type="submit">
                  Save
                </button>
                <button className="btn secondary" type="button" onClick={() => setEditSub(null)}>
                  Cancel
                </button>
              </div>
            </form>
          ) : null}
        </>
      ) : null}

      {tab === "orders" ? (
        <>
          <div className="card">
            <h3 style={{ marginTop: 0 }}>Pending orders</h3>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>User</th>
                    <th>Package</th>
                    <th>Method</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {pending.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="muted">
                        No pending orders
                      </td>
                    </tr>
                  ) : (
                    pending.map((o) => (
                      <tr key={o.id}>
                        <td>{o.id}</td>
                        <td>
                          {o.username}
                          <div className="muted" style={{ fontSize: "0.8rem" }}>
                            tg {o.telegram_id || "—"}
                          </div>
                        </td>
                        <td>{o.package_name}</td>
                        <td>{o.payment_method || "—"}</td>
                        <td style={{ display: "flex", gap: "0.35rem" }}>
                          <button
                            className="btn sm"
                            type="button"
                            onClick={() =>
                              run(async () => {
                                await api("/api/orders/approve", { method: "POST", body: JSON.stringify({ order_id: o.id }) });
                              }, `Approved #${o.id}`)
                            }
                          >
                            Approve
                          </button>
                          <button
                            className="btn danger sm"
                            type="button"
                            onClick={() =>
                              run(async () => {
                                await api("/api/orders/reject", {
                                  method: "POST",
                                  body: JSON.stringify({ order_id: o.id, reason: "Rejected by admin" }),
                                });
                              }, `Rejected #${o.id}`)
                            }
                          >
                            Reject
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
          <div className="card">
            <h3 style={{ marginTop: 0 }}>Recent orders</h3>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>User</th>
                    <th>Package</th>
                    <th>Status</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.map((o) => (
                    <tr key={o.id}>
                      <td>{o.id}</td>
                      <td>{o.username}</td>
                      <td>{o.package_name}</td>
                      <td>
                        <span className={`badge ${o.status === "approved" || o.status === "paid" ? "ok" : o.status === "pending" ? "warn" : ""}`}>
                          {o.status}
                        </span>
                      </td>
                      <td>{new Date(o.created_at).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      ) : null}

      {tab === "settings" ? (
        <form className="card" onSubmit={saveSettings} style={{ display: "grid", gap: "0.7rem", maxWidth: 640 }}>
          <h3 style={{ marginTop: 0 }}>Payment settings</h3>
          <label>
            <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} /> Billing enabled
          </label>
          <label>
            <input type="checkbox" checked={form.usdt_enabled} onChange={(e) => setForm({ ...form, usdt_enabled: e.target.checked })} /> USDT TRC-20
          </label>
          <label className="field">
            Wallet address
            <input className="input" value={form.usdt_wallet} onChange={(e) => setForm({ ...form, usdt_wallet: e.target.value })} />
          </label>
          <label className="field">
            API base
            <input className="input" value={form.usdt_api_base} onChange={(e) => setForm({ ...form, usdt_api_base: e.target.value })} />
          </label>
          <label className="field">
            API key (optional)
            <input
              className="input"
              type="password"
              value={form.usdt_api_key}
              onChange={(e) => setForm({ ...form, usdt_api_key: e.target.value })}
            />
          </label>
          <label>
            <input type="checkbox" checked={form.manual_enabled} onChange={(e) => setForm({ ...form, manual_enabled: e.target.checked })} /> Manual
            payments
          </label>
          <label className="field">
            Manual methods JSON
            <textarea
              className="input"
              rows={4}
              value={form.manual_methods_json}
              onChange={(e) => setForm({ ...form, manual_methods_json: e.target.value })}
            />
          </label>
          <button className="btn" type="submit">
            Save settings
          </button>
        </form>
      ) : null}
    </div>
  );
}
