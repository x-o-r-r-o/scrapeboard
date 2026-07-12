import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useOutletContext } from "react-router-dom";
import { api, type User } from "../api";

type Job = {
  id: number;
  public_id: string;
  status: string;
  total_searches: number;
  done_searches: number;
  rows_saved: number;
  pct: number;
  created_at: string;
};

type Sub = {
  package_name: string;
  threads: number;
  max_upload_mb: number;
  days_left: number;
  is_active: boolean;
  expires_at: string;
} | null;

type LiveWorker = {
  id: number;
  name: string;
  status: string;
  online: boolean;
  cpu_percent: number;
  mem_percent: number;
  disk_percent: number;
  mem_used_gb: number;
  mem_total_gb: number;
  disk_used_gb: number;
  disk_total_gb: number;
  load_avg_1: number;
  host_os: string;
  hostname: string;
  version: string;
  max_browsers: number;
  active_leases: number;
  load_ratio: number;
};

type LiveUser = {
  id: number;
  username: string;
  role: string;
  is_active: boolean;
  jobs_queued: number;
  jobs_running: number;
  jobs_completed: number;
  rows_saved_total: number;
  rows_saved_today: number;
  subscription: string | null;
  subscription_days_left: number | null;
};

type LiveStats = {
  generated_at: string;
  scope: string;
  overview: {
    workers_total: number;
    workers_online: number;
    workers_busy: number;
    workers_offline: number;
    avg_cpu: number;
    avg_mem: number;
    avg_disk: number;
    active_leases: number;
    jobs_queued: number;
    jobs_running: number;
    jobs_completed: number;
    jobs_failed: number;
    jobs_finished_today: number;
    rows_saved_today: number;
    users_total: number;
    users_with_running_jobs: number;
  };
  workers: LiveWorker[];
  users: LiveUser[];
};

function Meter({ label, value, detail }: { label: string; value: number; detail?: string }) {
  const pct = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  const tone = pct >= 90 ? "danger" : pct >= 75 ? "warn" : "ok";
  return (
    <div className="meter">
      {label ? (
        <div className="meter-head">
          <span>{label}</span>
          <span>
            {pct.toFixed(0)}%{detail ? ` · ${detail}` : ""}
          </span>
        </div>
      ) : (
        <div className="meter-head">
          <span>
            {pct.toFixed(0)}%{detail ? ` · ${detail}` : ""}
          </span>
        </div>
      )}
      <div className="meter-track">
        <div className={`meter-fill ${tone}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function statusBadge(status: string) {
  const cls =
    status === "online" ? "ok" : status === "draining" ? "warn" : status === "disabled" || status === "offline" ? "danger" : "";
  return <span className={`badge ${cls}`}>{status}</span>;
}

export function DashboardPage() {
  const { user } = useOutletContext<{ user: User }>();
  const [live, setLive] = useState<LiveStats | null>(null);
  const [sub, setSub] = useState<Sub>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const data = await api<LiveStats>("/api/stats/live");
        if (!cancelled) {
          setLive(data);
          setError("");
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load live stats");
      }
    }
    tick();
    api<Sub>("/api/subscriptions/me").then(setSub).catch(() => setSub(null));
    const t = setInterval(tick, 4000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  const o = live?.overview;

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>Dashboard</h1>
          <p className="subtitle">
            Live stats · {user.role === "admin" ? "fleet + all users" : "your account"}
            {live?.generated_at ? ` · updated ${new Date(live.generated_at).toLocaleTimeString()}` : ""}
          </p>
        </div>
        <span className="badge ok live-pulse">LIVE</span>
      </div>
      {error ? <p className="error">{error}</p> : null}

      <div className="grid-cards cols-3">
        <div className="card">
          <h3 style={{ margin: 0 }}>Subscription</h3>
          {sub ? (
            <p>
              {sub.package_name} · {sub.threads} threads · {sub.days_left.toFixed(1)} days left
            </p>
          ) : (
            <p className="muted">No active subscription</p>
          )}
        </div>
        <div className="card">
          <h3 style={{ margin: 0 }}>Jobs now</h3>
          <p className="stat-xl">
            {o ? o.jobs_running : "—"} <span className="muted">running</span>
          </p>
          <p className="muted">{o ? `${o.jobs_queued} queued · ${o.jobs_finished_today} finished today` : "…"}</p>
        </div>
        <div className="card">
          <h3 style={{ margin: 0 }}>Rows today</h3>
          <p className="stat-xl">{o ? o.rows_saved_today.toLocaleString() : "—"}</p>
          <p className="muted">{o ? `${o.jobs_completed} completed · ${o.jobs_failed} failed` : "…"}</p>
        </div>
      </div>

      {user.role === "admin" && o ? (
        <div className="grid-cards cols-3">
          <div className="card">
            <h3 style={{ margin: 0 }}>Workers</h3>
            <p className="stat-xl">
              {o.workers_online}/{o.workers_total} <span className="muted">online</span>
            </p>
            <p className="muted">
              {o.workers_busy} busy · {o.active_leases} leases · {o.workers_offline} offline
            </p>
          </div>
          <div className="card">
            <h3 style={{ margin: 0 }}>Fleet load</h3>
            <Meter label="CPU avg" value={o.avg_cpu} />
            <Meter label="RAM avg" value={o.avg_mem} />
            <Meter label="Disk avg" value={o.avg_disk} />
          </div>
          <div className="card">
            <h3 style={{ margin: 0 }}>Users</h3>
            <p className="stat-xl">
              {o.users_with_running_jobs}/{o.users_total} <span className="muted">active</span>
            </p>
            <p className="muted">Users with at least one running job</p>
          </div>
        </div>
      ) : null}

      {user.role === "admin" && live && live.workers.length > 0 ? (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Workers — live</h3>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Worker</th>
                  <th>Status</th>
                  <th>Load</th>
                  <th>CPU</th>
                  <th>RAM</th>
                  <th>Disk</th>
                  <th>Host</th>
                </tr>
              </thead>
              <tbody>
                {live.workers.map((w) => (
                  <tr key={w.id}>
                    <td>
                      <strong>{w.name}</strong>
                      <div className="muted" style={{ fontSize: "0.8rem" }}>
                        v{w.version || "—"}
                      </div>
                    </td>
                    <td>{statusBadge(w.status)}</td>
                    <td>
                      {w.active_leases}/{w.max_browsers}
                      <div className="meter" style={{ marginTop: 4 }}>
                        <div className="meter-track">
                          <div
                            className={`meter-fill ${w.load_ratio >= 0.9 ? "danger" : w.load_ratio >= 0.7 ? "warn" : "ok"}`}
                            style={{ width: `${Math.min(100, w.load_ratio * 100)}%` }}
                          />
                        </div>
                      </div>
                    </td>
                    <td>
                      <Meter label="" value={w.cpu_percent} detail={w.load_avg_1 ? `load ${w.load_avg_1}` : undefined} />
                    </td>
                    <td>
                      <Meter
                        label=""
                        value={w.mem_percent}
                        detail={w.mem_total_gb ? `${w.mem_used_gb}/${w.mem_total_gb} GB` : undefined}
                      />
                    </td>
                    <td>
                      <Meter
                        label=""
                        value={w.disk_percent}
                        detail={w.disk_total_gb ? `${w.disk_used_gb}/${w.disk_total_gb} GB` : undefined}
                      />
                    </td>
                    <td>
                      <code>{w.hostname || "—"}</code>
                      <div className="muted" style={{ fontSize: "0.8rem" }}>
                        {w.host_os || "—"}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {live && live.users.length > 0 ? (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>{user.role === "admin" ? "Users — live" : "Your activity"}</h3>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Plan</th>
                  <th>Queued</th>
                  <th>Running</th>
                  <th>Completed</th>
                  <th>Rows today</th>
                  <th>Rows total</th>
                </tr>
              </thead>
              <tbody>
                {live.users.map((u) => (
                  <tr key={u.id}>
                    <td>
                      <strong>{u.username}</strong>
                      <div className="muted" style={{ fontSize: "0.8rem" }}>
                        {u.role}
                        {!u.is_active ? " · inactive" : ""}
                      </div>
                    </td>
                    <td>
                      {u.subscription ? (
                        <>
                          {u.subscription}
                          {u.subscription_days_left != null ? (
                            <div className="muted" style={{ fontSize: "0.8rem" }}>
                              {u.subscription_days_left.toFixed(1)}d left
                            </div>
                          ) : null}
                        </>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>{u.jobs_queued}</td>
                    <td>
                      {u.jobs_running > 0 ? <span className="badge ok">{u.jobs_running}</span> : u.jobs_running}
                    </td>
                    <td>{u.jobs_completed}</td>
                    <td>{u.rows_saved_today.toLocaleString()}</td>
                    <td>{u.rows_saved_total.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState("");
  const [engine, setEngine] = useState("chrome");
  const [threads, setThreads] = useState(2);

  async function refresh() {
    setJobs(await api<Job[]>("/api/jobs"));
  }

  useEffect(() => {
    refresh().catch((e) => setError(e.message));
    const t = setInterval(() => refresh().catch(() => undefined), 5000);
    return () => clearInterval(t);
  }, []);

  async function onCreate(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    const fd = new FormData(e.currentTarget);
    fd.set("engine", engine);
    fd.set("threads", String(threads));
    try {
      await api("/api/jobs", { method: "POST", body: fd });
      e.currentTarget.reset();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  async function stop(id: number) {
    await api(`/api/jobs/${id}/stop`, { method: "POST" });
    await refresh();
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>Jobs</h1>
          <p className="subtitle">You only see jobs you own (admins see all).</p>
        </div>
      </div>
      <form className="card stack" onSubmit={onCreate}>
        <h3 style={{ margin: 0 }}>New job</h3>
        <div className="form-grid two">
          <label className="field">
            Keywords file
            <input className="input" type="file" name="keywords" accept=".txt,.csv" required />
          </label>
          <label className="field">
            Locations file
            <input className="input" type="file" name="locations" accept=".txt,.csv" required />
          </label>
          <label className="field">
            Engine
            <select className="input" value={engine} onChange={(e) => setEngine(e.target.value)}>
              <option value="chrome">chrome</option>
              <option value="brave">brave</option>
              <option value="camoufox">camoufox</option>
              <option value="google-chrome">google-chrome</option>
              <option value="edge">edge</option>
            </select>
          </label>
          <label className="field">
            Threads
            <input
              className="input"
              type="number"
              min={1}
              value={threads}
              onChange={(e) => setThreads(Number(e.target.value))}
            />
          </label>
        </div>
        {error ? <p className="error">{error}</p> : null}
        <button className="btn" type="submit">
          Queue job
        </button>
      </form>
      <div className="card table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Status</th>
              <th>Progress</th>
              <th>Rows</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>
                  <code>{j.public_id}</code>
                </td>
                <td>
                  <span className={`badge ${j.status === "completed" ? "ok" : j.status === "running" ? "warn" : ""}`}>{j.status}</span>
                </td>
                <td>
                  {j.done_searches}/{j.total_searches} ({j.pct.toFixed(1)}%)
                </td>
                <td>{j.rows_saved}</td>
                <td style={{ display: "flex", gap: "0.4rem" }}>
                  {j.status === "running" || j.status === "queued" ? (
                    <button className="btn secondary" type="button" onClick={() => stop(j.id)}>
                      Stop
                    </button>
                  ) : null}
                  {j.status === "completed" || j.status === "stopped" ? (
                    <a className="btn secondary" href={`/api/jobs/${j.id}/download`} onClick={(e) => {
                      e.preventDefault();
                      const t = localStorage.getItem("panel_token");
                      fetch(`/api/jobs/${j.id}/download`, { headers: t ? { Authorization: `Bearer ${t}` } : {} })
                        .then((r) => {
                          if (!r.ok) throw new Error("Download failed");
                          return r.blob();
                        })
                        .then((blob) => {
                          const url = URL.createObjectURL(blob);
                          const a = document.createElement("a");
                          a.href = url;
                          a.download = `${j.public_id}.zip`;
                          a.click();
                          URL.revokeObjectURL(url);
                        })
                        .catch((err) => setError(err.message));
                    }}>
                      Download
                    </a>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <style>{`.stack{display:grid;gap:1rem}h1,h3{margin:0}label{display:grid;gap:.35rem;font-size:.85rem;color:var(--muted)}`}</style>
    </div>
  );
}

export function SubscriptionPage() {
  const [sub, setSub] = useState<Sub>(null);
  const [packages, setPackages] = useState<Array<{ id: number; slug: string; name: string; price_usdt: number; threads: number; duration_days: number }>>([]);
  const [billing, setBilling] = useState<{ enabled: boolean; usdt_enabled: boolean; usdt_wallet: string; manual_enabled: boolean; manual_methods: Array<{ name: string; details: string }> } | null>(null);
  const [instructions, setInstructions] = useState("");
  const [txid, setTxid] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  async function refresh() {
    setSub(await api<Sub>("/api/subscriptions/me").catch(() => null));
    setPackages(await api<Array<{ id: number; slug: string; name: string; price_usdt: number; threads: number; duration_days: number }>>("/api/packages").catch(() => []));
    setBilling(await api<{ enabled: boolean; usdt_enabled: boolean; usdt_wallet: string; manual_enabled: boolean; manual_methods: Array<{ name: string; details: string }> }>("/api/billing/public").catch(() => null));
  }

  useEffect(() => {
    refresh().catch(() => undefined);
  }, []);

  async function buy(slug: string) {
    setError("");
    setMsg("");
    try {
      const res = await api<{ instructions: string }>("/api/orders/buy", {
        method: "POST",
        body: JSON.stringify({ package_slug: slug }),
      });
      setInstructions(res.instructions);
      setMsg("Order created. Follow payment instructions below.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Buy failed");
    }
  }

  async function paid(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const res = await api<{ detail: string }>("/api/orders/paid", {
        method: "POST",
        body: JSON.stringify({ txid }),
      });
      setMsg(res.detail);
      setTxid("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Verify failed");
    }
  }

  return (
    <div className="stack">
      <h1>Subscription</h1>
      <div className="card">
        {sub ? (
          <p>
            <strong>{sub.package_name}</strong> — expires {new Date(sub.expires_at).toLocaleDateString()} ({sub.days_left.toFixed(1)} days) ·{" "}
            {sub.threads} threads · {sub.max_upload_mb} MB uploads
          </p>
        ) : (
          <p className="muted">No subscription. Buy a package below (or ask admin to grant one).</p>
        )}
      </div>
      {billing?.enabled ? (
        <div className="card">
          <h3>Packages</h3>
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Price</th>
                <th>Days</th>
                <th>Threads</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {packages.map((p) => (
                <tr key={p.id}>
                  <td>
                    {p.name} <span className="muted">({p.slug})</span>
                  </td>
                  <td>{p.price_usdt} USDT</td>
                  <td>{p.duration_days}</td>
                  <td>{p.threads}</td>
                  <td>
                    <button className="btn secondary" type="button" onClick={() => buy(p.slug)}>
                      Buy
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {instructions ? <pre style={{ whiteSpace: "pre-wrap", color: "var(--muted)" }}>{instructions}</pre> : null}
          {billing.usdt_enabled ? (
            <form onSubmit={paid} style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", marginTop: "0.75rem" }}>
              <input className="input" style={{ maxWidth: 360 }} placeholder="USDT TxID" value={txid} onChange={(e) => setTxid(e.target.value)} />
              <button className="btn" type="submit">
                Verify payment
              </button>
            </form>
          ) : null}
        </div>
      ) : (
        <div className="card">
          <h3>Packages</h3>
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Price</th>
                <th>Days</th>
                <th>Threads</th>
              </tr>
            </thead>
            <tbody>
              {packages.map((p) => (
                <tr key={p.id}>
                  <td>
                    {p.name} <span className="muted">({p.slug})</span>
                  </td>
                  <td>{p.price_usdt} USDT</td>
                  <td>{p.duration_days}</td>
                  <td>{p.threads}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted">Billing is disabled — ask an admin to grant a plan.</p>
        </div>
      )}
      {error ? <p className="error">{error}</p> : null}
      {msg ? <p className="muted">{msg}</p> : null}
      <style>{`.stack{display:grid;gap:1rem}h1,h3{margin:0}`}</style>
    </div>
  );
}
