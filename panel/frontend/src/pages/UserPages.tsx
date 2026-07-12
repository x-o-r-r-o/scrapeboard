import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useOutletContext } from "react-router-dom";
import { api, type User } from "../api";

type Job = {
  id: number;
  public_id: string;
  owner_id: number;
  owner_username: string | null;
  owner_telegram_id: string | null;
  status: string;
  total_searches: number;
  done_searches: number;
  rows_saved: number;
  pct: number;
  result_exists: boolean;
  result_bytes: number | null;
  error: string | null;
  created_at: string;
};

type JobFiles = {
  job_id: number;
  public_id: string;
  files: Array<{ name: string; path: string; size_bytes: number; kind: string }>;
  total_bytes: number;
};

type StorageOwner = {
  user_id: number;
  username: string;
  telegram_id: string | null;
  uploads_bytes: number;
  results_bytes: number;
  job_count: number;
};

function fmtBytes(n: number | null | undefined) {
  if (n == null || n <= 0) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function JobsPage() {
  const { user } = useOutletContext<{ user: User }>();
  const isAdmin = user.role === "admin";
  const [jobs, setJobs] = useState<Job[]>([]);
  const [owners, setOwners] = useState<Array<{ id: number; username: string }>>([]);
  const [storage, setStorage] = useState<StorageOwner[]>([]);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [engine, setEngine] = useState("chrome");
  const [threads, setThreads] = useState(2);
  const [filterOwner, setFilterOwner] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterQ, setFilterQ] = useState("");
  const [filesFor, setFilesFor] = useState<JobFiles | null>(null);
  const [showStorage, setShowStorage] = useState(false);

  async function refresh() {
    const params = new URLSearchParams();
    if (isAdmin && filterOwner) params.set("owner_id", filterOwner);
    if (filterStatus) params.set("status", filterStatus);
    if (filterQ.trim()) params.set("q", filterQ.trim());
    params.set("limit", "200");
    const qs = params.toString();
    setJobs(await api<Job[]>(`/api/jobs${qs ? `?${qs}` : ""}`));
  }

  async function refreshStorage() {
    if (!isAdmin) return;
    setStorage(await api<StorageOwner[]>("/api/jobs/admin/storage"));
  }

  useEffect(() => {
    refresh().catch((e) => setError(e.message));
    const t = setInterval(() => refresh().catch(() => undefined), 5000);
    return () => clearInterval(t);
  }, [filterOwner, filterStatus, filterQ, isAdmin]);

  useEffect(() => {
    if (!isAdmin) return;
    api<Array<{ id: number; username: string }>>("/api/users")
      .then(setOwners)
      .catch(() => undefined);
  }, [isAdmin]);

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

  async function downloadJob(j: Job) {
    const t = localStorage.getItem("panel_token");
    try {
      const r = await fetch(`/api/jobs/${j.id}/download`, { headers: t ? { Authorization: `Bearer ${t}` } : {} });
      if (!r.ok) {
        const text = await r.text();
        throw new Error(text || "Download failed");
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${j.public_id}.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Download failed");
    }
  }

  async function openFiles(jobId: number) {
    setError("");
    try {
      setFilesFor(await api<JobFiles>(`/api/jobs/${jobId}/files`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to list files");
    }
  }

  async function purgeFiles(jobId: number) {
    if (!confirm("Purge all result/upload files for this job?")) return;
    await api(`/api/jobs/${jobId}/files`, { method: "DELETE" });
    setMsg("Files purged.");
    setFilesFor(null);
    await refresh();
    if (showStorage) await refreshStorage();
  }

  async function deleteJob(jobId: number) {
    if (!confirm("Delete this job record and purge its files?")) return;
    await api(`/api/jobs/${jobId}?purge_files=true`, { method: "DELETE" });
    setMsg("Job deleted.");
    setFilesFor(null);
    await refresh();
    if (showStorage) await refreshStorage();
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>Jobs</h1>
          <p className="subtitle">
            {isAdmin
              ? "View, download, and manage scrape jobs and files for every user."
              : "You only see jobs you own."}
          </p>
        </div>
        {isAdmin ? (
          <button
            className="btn secondary"
            type="button"
            onClick={async () => {
              setShowStorage((v) => !v);
              if (!showStorage) await refreshStorage().catch((e) => setError(e.message));
            }}
          >
            {showStorage ? "Hide storage" : "Storage by user"}
          </button>
        ) : null}
      </div>

      {isAdmin && showStorage ? (
        <div className="card table-wrap">
          <h3 style={{ marginTop: 0 }}>User data & files</h3>
          <p className="muted">Upload and result storage footprint per account.</p>
          <table className="table">
            <thead>
              <tr>
                <th>User</th>
                <th>Telegram</th>
                <th>Jobs</th>
                <th>Uploads</th>
                <th>Results</th>
                <th>Total</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {storage.map((s) => (
                <tr key={s.user_id}>
                  <td>
                    {s.username} <span className="muted">#{s.user_id}</span>
                  </td>
                  <td>{s.telegram_id || "—"}</td>
                  <td>{s.job_count}</td>
                  <td>{fmtBytes(s.uploads_bytes)}</td>
                  <td>{fmtBytes(s.results_bytes)}</td>
                  <td>{fmtBytes(s.uploads_bytes + s.results_bytes)}</td>
                  <td>
                    <button className="btn secondary sm" type="button" onClick={() => setFilterOwner(String(s.user_id))}>
                      Filter jobs
                    </button>
                  </td>
                </tr>
              ))}
              {storage.length === 0 ? (
                <tr>
                  <td colSpan={7} className="muted">
                    No stored data yet.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      ) : null}

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
        {msg ? <p className="muted">{msg}</p> : null}
        <button className="btn" type="submit">
          Queue job
        </button>
      </form>

      {isAdmin || filterStatus || filterQ ? (
        <div className="card" style={{ display: "flex", gap: "0.65rem", flexWrap: "wrap", alignItems: "end" }}>
          {isAdmin ? (
            <label className="field" style={{ minWidth: 160 }}>
              Owner
              <select className="input" value={filterOwner} onChange={(e) => setFilterOwner(e.target.value)}>
                <option value="">All users</option>
                {owners.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.username}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label className="field" style={{ minWidth: 140 }}>
            Status
            <select className="input" value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
              <option value="">Any</option>
              <option value="queued">queued</option>
              <option value="running">running</option>
              <option value="completed">completed</option>
              <option value="stopped">stopped</option>
              <option value="failed">failed</option>
            </select>
          </label>
          <label className="field" style={{ minWidth: 180, flex: 1 }}>
            Job ID search
            <input className="input" value={filterQ} onChange={(e) => setFilterQ(e.target.value)} placeholder="public id…" />
          </label>
          <button
            className="btn secondary"
            type="button"
            onClick={() => {
              setFilterOwner("");
              setFilterStatus("");
              setFilterQ("");
            }}
          >
            Clear
          </button>
        </div>
      ) : null}

      <div className="card table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>ID</th>
              {isAdmin ? <th>Owner</th> : null}
              <th>Status</th>
              <th>Progress</th>
              <th>Rows</th>
              <th>Result</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>
                  <code>{j.public_id}</code>
                </td>
                {isAdmin ? (
                  <td>
                    {j.owner_username || `#${j.owner_id}`}
                    {j.owner_telegram_id ? <div className="muted" style={{ fontSize: "0.75rem" }}>tg:{j.owner_telegram_id}</div> : null}
                  </td>
                ) : null}
                <td>
                  <span className={`badge ${j.status === "completed" ? "ok" : j.status === "running" ? "warn" : j.status === "failed" ? "danger" : ""}`}>
                    {j.status}
                  </span>
                  {j.error ? <div className="muted" style={{ fontSize: "0.75rem" }}>{j.error.slice(0, 60)}</div> : null}
                </td>
                <td>
                  {j.done_searches}/{j.total_searches} ({j.pct.toFixed(1)}%)
                </td>
                <td>{j.rows_saved}</td>
                <td>{j.result_exists ? fmtBytes(j.result_bytes) : "—"}</td>
                <td style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
                  {j.status === "running" || j.status === "queued" ? (
                    <button className="btn secondary sm" type="button" onClick={() => stop(j.id)}>
                      Stop
                    </button>
                  ) : null}
                  {j.result_exists || j.status === "completed" || j.status === "stopped" ? (
                    <button className="btn secondary sm" type="button" onClick={() => downloadJob(j)}>
                      Download
                    </button>
                  ) : null}
                  <button className="btn secondary sm" type="button" onClick={() => openFiles(j.id)}>
                    Files
                  </button>
                  {isAdmin && j.status !== "running" && j.status !== "queued" ? (
                    <>
                      <button className="btn secondary sm" type="button" onClick={() => purgeFiles(j.id)}>
                        Purge
                      </button>
                      <button className="btn danger sm" type="button" onClick={() => deleteJob(j.id)}>
                        Delete
                      </button>
                    </>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {filesFor ? (
        <div className="card">
          <div className="page-header" style={{ marginBottom: "0.5rem" }}>
            <h3 style={{ margin: 0 }}>Files — {filesFor.public_id}</h3>
            <button className="btn secondary sm" type="button" onClick={() => setFilesFor(null)}>
              Close
            </button>
          </div>
          <p className="muted">Total {fmtBytes(filesFor.total_bytes)}</p>
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Kind</th>
                <th>Size</th>
              </tr>
            </thead>
            <tbody>
              {filesFor.files.map((f) => (
                <tr key={f.name + f.kind}>
                  <td>
                    <code>{f.name}</code>
                  </td>
                  <td>{f.kind}</td>
                  <td>{fmtBytes(f.size_bytes)}</td>
                </tr>
              ))}
              {filesFor.files.length === 0 ? (
                <tr>
                  <td colSpan={3} className="muted">
                    No files on disk.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
          {isAdmin ? (
            <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.75rem" }}>
              <button className="btn secondary" type="button" onClick={() => purgeFiles(filesFor.job_id)}>
                Purge files
              </button>
              <button className="btn" type="button" onClick={() => {
                const j = jobs.find((x) => x.id === filesFor.job_id);
                if (j) void downloadJob(j);
              }}>
                Download zip
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      <style>{`.stack{display:grid;gap:1rem}h1,h3{margin:0}label{display:grid;gap:.35rem;font-size:.85rem;color:var(--muted)}`}</style>
    </div>
  );
}

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

export function SubscriptionPage() {
  const [sub, setSub] = useState<Sub>(null);
  const [packages, setPackages] = useState<
    Array<{
      id: number;
      slug: string;
      name: string;
      price_usdt: number;
      threads: number;
      duration_days: number;
      description?: string;
      headings?: string[];
      features?: string[];
    }>
  >([]);
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
                    {p.description ? <div className="muted" style={{ fontSize: "0.8rem" }}>{p.description}</div> : null}
                    {(p.headings || []).length ? (
                      <div style={{ marginTop: "0.25rem", fontSize: "0.85rem" }}>{p.headings!.join(" · ")}</div>
                    ) : null}
                    {(p.features || []).length ? (
                      <ul style={{ margin: "0.35rem 0 0", paddingLeft: "1.1rem", fontSize: "0.8rem", color: "var(--muted)" }}>
                        {p.features!.map((f) => (
                          <li key={f}>{f}</li>
                        ))}
                      </ul>
                    ) : null}
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
