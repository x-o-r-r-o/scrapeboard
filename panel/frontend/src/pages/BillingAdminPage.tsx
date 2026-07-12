import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";

export function BillingAdminPage() {
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
  const [pending, setPending] = useState<Array<{ id: number; username: string; package_name: string; user_id: number }>>([]);
  const [users, setUsers] = useState<Array<{ id: number; username: string }>>([]);
  const [packages, setPackages] = useState<Array<{ id: number; name: string }>>([]);
  const [grantUser, setGrantUser] = useState("");
  const [grantPkg, setGrantPkg] = useState("");
  const [msg, setMsg] = useState("");

  async function refresh() {
    const s = await api<Record<string, unknown>>("/api/billing/settings");
    setForm({
      enabled: Boolean(s.enabled),
      usdt_enabled: Boolean(s.usdt_enabled),
      usdt_wallet: String(s.usdt_wallet || ""),
      usdt_contract: String(s.usdt_contract || ""),
      usdt_api_base: String(s.usdt_api_base || ""),
      usdt_api_key: "",
      manual_enabled: Boolean(s.manual_enabled),
      manual_methods_json: JSON.stringify(s.manual_methods || [], null, 2),
      max_upload_mb: Number(s.max_upload_mb || 5),
    });
    setPending(await api<Array<{ id: number; username: string; package_name: string; user_id: number }>>("/api/orders/pending").catch(() => []));
    setUsers(await api<Array<{ id: number; username: string }>>("/api/users").catch(() => []));
    setPackages(await api<Array<{ id: number; name: string }>>("/api/packages").catch(() => []));
  }

  useEffect(() => {
    refresh().catch(() => undefined);
  }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    let methods = [];
    try {
      methods = JSON.parse(form.manual_methods_json || "[]");
    } catch {
      setMsg("Invalid manual_methods JSON");
      return;
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
    setMsg("Billing settings saved.");
    await refresh();
  }

  return (
    <div className="stack">
      <h1>Billing</h1>
      <form className="card" onSubmit={save} style={{ display: "grid", gap: "0.7rem", maxWidth: 640 }}>
        <label>
          <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} /> Billing enabled
        </label>
        <label>
          <input type="checkbox" checked={form.usdt_enabled} onChange={(e) => setForm({ ...form, usdt_enabled: e.target.checked })} /> USDT TRC-20
        </label>
        <label>
          Wallet address
          <input className="input" value={form.usdt_wallet} onChange={(e) => setForm({ ...form, usdt_wallet: e.target.value })} />
        </label>
        <label>
          API base
          <input className="input" value={form.usdt_api_base} onChange={(e) => setForm({ ...form, usdt_api_base: e.target.value })} />
        </label>
        <label>
          API key (optional)
          <input className="input" type="password" value={form.usdt_api_key} onChange={(e) => setForm({ ...form, usdt_api_key: e.target.value })} />
        </label>
        <label>
          <input type="checkbox" checked={form.manual_enabled} onChange={(e) => setForm({ ...form, manual_enabled: e.target.checked })} /> Manual payments
        </label>
        <label>
          Manual methods JSON
          <textarea className="input" rows={4} value={form.manual_methods_json} onChange={(e) => setForm({ ...form, manual_methods_json: e.target.value })} />
        </label>
        <button className="btn" type="submit">
          Save
        </button>
      </form>

      <div className="card">
        <h3>Grant subscription</h3>
        <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
          <select className="input" style={{ maxWidth: 200 }} value={grantUser} onChange={(e) => setGrantUser(e.target.value)}>
            <option value="">User</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>
                {u.username}
              </option>
            ))}
          </select>
          <select className="input" style={{ maxWidth: 200 }} value={grantPkg} onChange={(e) => setGrantPkg(e.target.value)}>
            <option value="">Package</option>
            {packages.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <button
            className="btn"
            type="button"
            onClick={async () => {
              await api("/api/subscriptions/grant", {
                method: "POST",
                body: JSON.stringify({ user_id: Number(grantUser), package_id: Number(grantPkg) }),
              });
              setMsg("Granted.");
            }}
          >
            Grant
          </button>
        </div>
      </div>

      <div className="card">
        <h3>Pending orders</h3>
        <table className="table">
          <thead>
            <tr>
              <th>ID</th>
              <th>User</th>
              <th>Package</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {pending.map((o) => (
              <tr key={o.id}>
                <td>{o.id}</td>
                <td>{o.username}</td>
                <td>{o.package_name}</td>
                <td>
                  <button
                    className="btn secondary"
                    type="button"
                    onClick={async () => {
                      await api("/api/orders/approve", { method: "POST", body: JSON.stringify({ order_id: o.id }) });
                      setMsg(`Approved order ${o.id}`);
                      await refresh();
                    }}
                  >
                    Approve
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {msg ? <p className="muted">{msg}</p> : null}
      <style>{`.stack{display:grid;gap:1rem}h1,h3{margin:0}label{display:grid;gap:.3rem;font-size:.85rem;color:var(--muted)}`}</style>
    </div>
  );
}
