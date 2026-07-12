import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";

type Pool = {
  id: number;
  name: string;
  description: string;
  proxy_count: number;
  is_active: boolean;
  worker_ids: number[];
  worker_names: string[];
  proxies_text?: string;
};

type WorkerLite = { id: number; name: string; proxy_pool_id: number | null; online: boolean };

export function ProxiesAdminPage() {
  const [pools, setPools] = useState<Pool[]>([]);
  const [workers, setWorkers] = useState<WorkerLite[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [form, setForm] = useState({ name: "", description: "", proxies_text: "", is_active: true });
  const [assignIds, setAssignIds] = useState<number[]>([]);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);

  async function refresh() {
    const [p, w] = await Promise.all([
      api<Pool[]>("/api/proxy-pools"),
      api<WorkerLite[]>("/api/workers"),
    ]);
    setPools(p);
    setWorkers(w);
    if (selectedId != null) {
      const cur = p.find((x) => x.id === selectedId);
      if (cur) setAssignIds(cur.worker_ids || []);
    }
  }

  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, []);

  async function openPool(id: number) {
    setError("");
    setMsg("");
    setCreating(false);
    const detail = await api<Pool>(`/api/proxy-pools/${id}`);
    setSelectedId(id);
    setForm({
      name: detail.name,
      description: detail.description || "",
      proxies_text: detail.proxies_text || "",
      is_active: detail.is_active,
    });
    setAssignIds(detail.worker_ids || []);
  }

  async function savePool(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      if (creating || selectedId == null) {
        const created = await api<Pool>("/api/proxy-pools", {
          method: "POST",
          body: JSON.stringify(form),
        });
        setMsg("Pool created.");
        await refresh();
        await openPool(created.id);
        return;
      }
      await api(`/api/proxy-pools/${selectedId}`, { method: "PATCH", body: JSON.stringify(form) });
      await api(`/api/proxy-pools/${selectedId}/assign`, {
        method: "POST",
        body: JSON.stringify({ worker_ids: assignIds }),
      });
      setMsg("Pool saved and workers assigned.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  }

  function toggleWorker(id: number) {
    setAssignIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>Proxy pools</h1>
          <p className="subtitle">Create pools, edit proxy lists, and assign multiple workers to each pool.</p>
        </div>
        <div className="page-actions">
          <button
            className="btn"
            type="button"
            onClick={() => {
              setCreating(true);
              setSelectedId(null);
              setForm({ name: "", description: "", proxies_text: "", is_active: true });
              setAssignIds([]);
              setMsg("");
            }}
          >
            New pool
          </button>
        </div>
      </div>

      {error ? <p className="error">{error}</p> : null}
      {msg ? <p className="muted">{msg}</p> : null}

      <div className="grid-cards cols-3">
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Pools</h3>
          <div className="stack" style={{ gap: "0.55rem" }}>
            {pools.length === 0 ? <p className="muted">No pools yet.</p> : null}
            {pools.map((p) => (
              <button
                key={p.id}
                type="button"
                className={`item-card ${selectedId === p.id ? "active" : ""}`}
                onClick={() => openPool(p.id).catch((e) => setError(e.message))}
                style={{ textAlign: "left", cursor: "pointer", width: "100%" }}
              >
                <div className="item-card-title">
                  {p.name}
                  <span className={`badge ${p.is_active ? "ok" : "danger"}`}>{p.is_active ? "active" : "off"}</span>
                </div>
                <div className="muted" style={{ fontSize: "0.8rem" }}>
                  {p.proxy_count} proxies · {p.worker_ids?.length || 0} workers
                  {p.worker_names?.length ? ` · ${p.worker_names.join(", ")}` : ""}
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="card" style={{ gridColumn: "span 2" }}>
          {creating || selectedId != null ? (
            <form className="stack" onSubmit={savePool}>
              <h3 style={{ marginTop: 0 }}>{creating ? "Create proxy pool" : `Edit pool #${selectedId}`}</h3>
              <div className="form-grid two">
                <label className="field">
                  Name
                  <input className="input" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
                </label>
                <label className="field">
                  Status
                  <select
                    className="input"
                    value={form.is_active ? "1" : "0"}
                    onChange={(e) => setForm({ ...form, is_active: e.target.value === "1" })}
                  >
                    <option value="1">Active</option>
                    <option value="0">Disabled</option>
                  </select>
                </label>
              </div>
              <label className="field">
                Description
                <input className="input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
              </label>
              <label className="field">
                Proxies (one per line: host:port or host:port:user:pass)
                <textarea
                  className="input"
                  rows={10}
                  value={form.proxies_text}
                  onChange={(e) => setForm({ ...form, proxies_text: e.target.value })}
                  placeholder={"1.2.3.4:8080\n1.2.3.4:8080:user:pass"}
                />
              </label>

              {!creating ? (
                <div>
                  <h4 style={{ margin: "0 0 0.5rem" }}>Assign workers</h4>
                  <p className="muted" style={{ marginTop: 0 }}>
                    A pool can serve many workers. Unchecking removes the worker from this pool.
                  </p>
                  <div className="stack" style={{ gap: "0.35rem" }}>
                    {workers.map((w) => (
                      <label key={w.id} style={{ display: "flex", gap: "0.55rem", alignItems: "center" }}>
                        <input type="checkbox" checked={assignIds.includes(w.id)} onChange={() => toggleWorker(w.id)} />
                        <span>
                          {w.name}{" "}
                          <span className={`badge ${w.online ? "ok" : ""}`}>{w.online ? "online" : "offline"}</span>
                          {w.proxy_pool_id && w.proxy_pool_id !== selectedId ? (
                            <span className="muted"> · currently pool #{w.proxy_pool_id}</span>
                          ) : null}
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
              ) : null}

              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                <button className="btn" type="submit">
                  {creating ? "Create" : "Save pool & assignments"}
                </button>
                {!creating && selectedId != null ? (
                  <button
                    className="btn danger"
                    type="button"
                    onClick={async () => {
                      if (!confirm("Delete this proxy pool? Workers will be unassigned.")) return;
                      await api(`/api/proxy-pools/${selectedId}`, { method: "DELETE" });
                      setSelectedId(null);
                      setCreating(false);
                      setMsg("Pool deleted.");
                      await refresh();
                    }}
                  >
                    Delete
                  </button>
                ) : null}
              </div>
            </form>
          ) : (
            <div className="empty-state">
              <h3>Select a pool</h3>
              <p className="muted">Or create a new pool to manage proxies and worker assignments.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
