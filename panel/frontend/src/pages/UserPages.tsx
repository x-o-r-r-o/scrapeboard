import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link, useOutletContext } from "react-router-dom";
import { api, type User } from "../api";

type Job = {
  id: number;
  public_id: string;
  name?: string | null;
  owner_id: number;
  owner_username: string | null;
  owner_telegram_id: string | null;
  status: string;
  threads: number;
  total_searches: number;
  done_searches: number;
  rows_saved: number;
  pct: number;
  result_exists: boolean;
  result_bytes: number | null;
  error: string | null;
  created_at: string;
  started_at?: string | null;
  waiting_for_threads?: boolean;
  blocking_job_public_id?: string | null;
  blocking_job_label?: string | null;
  settings?: { engine?: string; threads?: number };
  chunks_pending?: number | null;
  chunks_leased?: number | null;
  chunks_done?: number | null;
  workers?: Array<{ worker_id: number; worker_name: string; leased_chunks: number; online: boolean }> | null;
};

type ThreadQuota = {
  thread_allowance: number;
  threads_in_use: number;
  threads_free: number;
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
  const [quota, setQuota] = useState<ThreadQuota | null>(null);
  const [owners, setOwners] = useState<Array<{ id: number; username: string }>>([]);
  const [storage, setStorage] = useState<StorageOwner[]>([]);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [engine, setEngine] = useState("chrome");
  const [threads, setThreads] = useState(2);
  const [jobName, setJobName] = useState("");
  const [filterOwner, setFilterOwner] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterQ, setFilterQ] = useState("");
  const [filesFor, setFilesFor] = useState<JobFiles | null>(null);
  const [showStorage, setShowStorage] = useState(false);
  const [editJob, setEditJob] = useState<{
    id: number;
    threads: number;
    engine: string;
    name: string;
    status: string;
  } | null>(null);
  const [detailJob, setDetailJob] = useState<Job | null>(null);

  async function refresh() {
    const params = new URLSearchParams();
    if (isAdmin && filterOwner) params.set("owner_id", filterOwner);
    if (filterStatus) params.set("status", filterStatus);
    if (filterQ.trim()) params.set("q", filterQ.trim());
    params.set("limit", "200");
    const qs = params.toString();
    const [jobRows, q] = await Promise.all([
      api<Job[]>(`/api/jobs${qs ? `?${qs}` : ""}`),
      api<ThreadQuota>("/api/jobs/quota").catch(() => null),
    ]);
    setJobs(jobRows);
    setQuota(q);
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
    setMsg("");
    const form = e.currentTarget;
    const fd = new FormData(form);
    fd.set("engine", engine);
    fd.set("threads", String(threads));
    const trimmedName = jobName.trim();
    if (trimmedName) fd.set("name", trimmedName);
    try {
      const created = await api<Job>("/api/jobs", { method: "POST", body: fd });
      form.reset();
      setJobName("");
      if (created.blocking_job_label || created.blocking_job_public_id) {
        const behind = created.blocking_job_label || created.blocking_job_public_id;
        setMsg(`Job queued — 1 job at a time — waiting for ${behind} to finish.`);
      } else if (created.waiting_for_threads) {
        setMsg(
          `Job queued — waiting for free threads (${created.threads} needed). ` +
            `It starts when capacity frees, or edit threads on the queued job.`,
        );
      } else {
        setMsg(created.name ? `Job queued — ${created.name}.` : "Job queued.");
      }
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  async function saveEditJob(e: FormEvent) {
    e.preventDefault();
    if (!editJob) return;
    setError("");
    try {
      const body: { name: string; threads?: number; engine?: string } = {
        name: editJob.name.trim(),
      };
      if (editJob.status === "queued") {
        body.threads = editJob.threads;
        body.engine = editJob.engine;
      }
      await api(`/api/jobs/${editJob.id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      setMsg("Job updated.");
      setEditJob(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
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
    setError("");
    try {
      await api(`/api/jobs/${jobId}/files`, { method: "DELETE" });
      setMsg("Files purged.");
      setFilesFor(null);
      await refresh();
      if (showStorage) await refreshStorage();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Purge failed");
    }
  }

  async function deleteJob(jobId: number) {
    if (!confirm("Delete this job record and purge its files?")) return;
    setError("");
    try {
      await api(`/api/jobs/${jobId}?purge_files=true`, { method: "DELETE" });
      setMsg("Job deleted.");
      setFilesFor(null);
      await refresh();
      if (showStorage) await refreshStorage();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>Jobs</h1>
          <p className="subtitle">
            {isAdmin
              ? "Each job has a unique ID. One job per owner runs at a time; view worker placement and stop any job from here."
              : "One job runs at a time. Use Telegram /stop to cancel your own queued or running jobs."}
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
        {quota ? (
          <p className="muted" style={{ margin: 0 }}>
            Thread pool: <strong>{quota.threads_in_use}</strong> in use / <strong>{quota.thread_allowance}</strong> allowed
            ({quota.threads_free} free). One job runs at a time — extra jobs stay queued until it finishes.
          </p>
        ) : null}
        <div className="form-grid two">
          <label className="field" style={{ gridColumn: "1 / -1" }}>
            Name <span className="muted">(optional)</span>
            <input
              className="input"
              type="text"
              maxLength={128}
              value={jobName}
              onChange={(e) => setJobName(e.target.value)}
              placeholder="e.g. NYC dentists — March"
            />
          </label>
          <label className="field">
            Keywords file
            <input className="input" type="file" name="keywords" accept=".txt,.csv" required />
          </label>
          <label className="field">
            Locations file
            <input className="input" type="file" name="locations" accept=".txt,.csv" required />
          </label>
          <p className="muted" style={{ margin: 0, gridColumn: "1 / -1" }}>
            UTF-8 <code>.txt</code> / <code>.csv</code>: one keyword or <code>city,state,country</code> per line
            (# comments OK). CSV may use a <code>keyword</code>/<code>query</code> or <code>location</code> header
            column. Invalid files are rejected before the job is queued.
          </p>
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
            Threads {quota ? <span className="muted">(max {quota.thread_allowance})</span> : null}
            <input
              className="input"
              type="number"
              min={1}
              max={quota?.thread_allowance || 64}
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
            Search
            <input
              className="input"
              value={filterQ}
              onChange={(e) => setFilterQ(e.target.value)}
              placeholder="name or public id…"
            />
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
              <th>Job</th>
              {isAdmin ? <th>Owner</th> : null}
              <th>Status</th>
              <th>Threads</th>
              {isAdmin ? <th>Workers</th> : null}
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
                  {j.name ? (
                    <>
                      <strong>{j.name}</strong>
                      <div className="muted" style={{ fontSize: "0.75rem" }}>
                        <code title={`#${j.id}`}>{j.public_id}</code>
                      </div>
                    </>
                  ) : (
                    <code title={`#${j.id}`}>{j.public_id}</code>
                  )}
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
                  {j.blocking_job_label || j.blocking_job_public_id ? (
                    <div className="muted" style={{ fontSize: "0.75rem" }}>
                      1 job at a time — waiting for {j.blocking_job_label || j.blocking_job_public_id} to finish
                    </div>
                  ) : j.waiting_for_threads ? (
                    <div className="muted" style={{ fontSize: "0.75rem" }}>
                      waiting for free threads
                    </div>
                  ) : null}
                  {j.error ? <div className="muted" style={{ fontSize: "0.75rem" }}>{j.error.slice(0, 60)}</div> : null}
                </td>
                <td>{j.threads ?? "—"}</td>
                {isAdmin ? (
                  <td>
                    {j.workers && j.workers.length > 0 ? (
                      j.workers.map((w) => (
                        <div key={w.worker_id} style={{ fontSize: "0.85rem" }}>
                          {w.worker_name}{" "}
                          <span className={`badge ${w.online ? "ok" : ""}`}>{w.online ? "online" : "off"}</span>
                          <span className="muted"> · {w.leased_chunks} chunk{w.leased_chunks === 1 ? "" : "s"}</span>
                        </div>
                      ))
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                ) : null}
                <td>
                  {j.done_searches}/{j.total_searches} ({j.pct.toFixed(1)}%)
                </td>
                <td>{j.rows_saved}</td>
                <td>{j.result_exists ? fmtBytes(j.result_bytes) : "—"}</td>
                <td style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
                  {isAdmin ? (
                    <button className="btn secondary sm" type="button" onClick={() => setDetailJob(j)}>
                      Details
                    </button>
                  ) : null}
                  {j.status === "queued" || j.status === "running" ? (
                    <button
                      className="btn secondary sm"
                      type="button"
                      onClick={() =>
                        setEditJob({
                          id: j.id,
                          threads: j.threads || 1,
                          engine: j.settings?.engine || "chrome",
                          name: j.name || "",
                          status: j.status,
                        })
                      }
                    >
                      Edit
                    </button>
                  ) : null}
                  {isAdmin && (j.status === "running" || j.status === "queued") ? (
                    <button className="btn danger sm" type="button" onClick={() => stop(j.id)}>
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

      {detailJob && isAdmin ? (
        <div className="card">
          <div className="page-header" style={{ marginBottom: "0.5rem" }}>
            <h3 style={{ margin: 0 }}>
              Job details — {detailJob.name ? detailJob.name : detailJob.public_id}
            </h3>
            <button className="btn secondary sm" type="button" onClick={() => setDetailJob(null)}>
              Close
            </button>
          </div>
          <div className="form-grid two" style={{ gap: "0.75rem" }}>
            {detailJob.name ? (
              <div>
                <div className="muted">Name</div>
                <strong>{detailJob.name}</strong>
              </div>
            ) : null}
            <div>
              <div className="muted">Unique job ID</div>
              <code>{detailJob.public_id}</code>
            </div>
            <div>
              <div className="muted">Internal id</div>
              #{detailJob.id}
            </div>
            <div>
              <div className="muted">Owner</div>
              {detailJob.owner_username || `#${detailJob.owner_id}`}
              {detailJob.owner_telegram_id ? ` · tg:${detailJob.owner_telegram_id}` : ""}
            </div>
            <div>
              <div className="muted">Status</div>
              {detailJob.status} · {detailJob.threads} threads · engine {detailJob.settings?.engine || "—"}
            </div>
            <div>
              <div className="muted">Progress</div>
              {detailJob.done_searches}/{detailJob.total_searches} ({detailJob.pct.toFixed(1)}%) · rows {detailJob.rows_saved}
            </div>
            <div>
              <div className="muted">Chunks</div>
              pending {detailJob.chunks_pending ?? "—"} · leased {detailJob.chunks_leased ?? "—"} · done{" "}
              {detailJob.chunks_done ?? "—"}
            </div>
            <div style={{ gridColumn: "1 / -1" }}>
              <div className="muted">Workers running this job</div>
              {detailJob.workers && detailJob.workers.length > 0 ? (
                <ul style={{ margin: "0.35rem 0 0", paddingLeft: "1.1rem" }}>
                  {detailJob.workers.map((w) => (
                    <li key={w.worker_id}>
                      {w.worker_name} (#{w.worker_id}) — {w.leased_chunks} leased chunk
                      {w.leased_chunks === 1 ? "" : "s"} · {w.online ? "online" : "offline"}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted" style={{ margin: "0.35rem 0 0" }}>
                  No active worker leases (queued or between chunks).
                </p>
              )}
            </div>
          </div>
          {(detailJob.status === "running" || detailJob.status === "queued") ? (
            <button className="btn danger" type="button" style={{ marginTop: "0.85rem" }} onClick={() => stop(detailJob.id).then(() => setDetailJob(null))}>
              Stop job
            </button>
          ) : null}
        </div>
      ) : null}

      {editJob ? (
        <form className="card" onSubmit={saveEditJob} style={{ display: "grid", gap: "0.65rem", maxWidth: 420 }}>
          <h3 style={{ margin: 0 }}>
            {editJob.status === "queued" ? "Edit queued job" : "Edit job name"}
          </h3>
          {editJob.status === "queued" ? (
            <p className="muted" style={{ margin: 0 }}>
              One job runs at a time. Threads must be ≤ your allowance (
              {quota?.thread_allowance ?? "—"}). This job starts automatically when
              your current running job finishes
              {quota && quota.threads_free === 0 ? " (slot busy)" : ""}.
            </p>
          ) : (
            <p className="muted" style={{ margin: 0 }}>
              Running jobs can rename only — threads and engine stay locked.
            </p>
          )}
          <label className="field">
            Name <span className="muted">(optional)</span>
            <input
              className="input"
              type="text"
              maxLength={128}
              value={editJob.name}
              onChange={(e) => setEditJob({ ...editJob, name: e.target.value })}
              placeholder="Leave blank to clear"
            />
          </label>
          {editJob.status === "queued" ? (
            <>
              <label className="field">
                Threads
                <input
                  className="input"
                  type="number"
                  min={1}
                  max={quota?.thread_allowance || 64}
                  value={editJob.threads}
                  onChange={(e) => setEditJob({ ...editJob, threads: Number(e.target.value) })}
                />
              </label>
              <label className="field">
                Engine
                <select
                  className="input"
                  value={editJob.engine}
                  onChange={(e) => setEditJob({ ...editJob, engine: e.target.value })}
                >
                  <option value="chrome">chrome</option>
                  <option value="brave">brave</option>
                  <option value="camoufox">camoufox</option>
                  <option value="google-chrome">google-chrome</option>
                  <option value="edge">edge</option>
                </select>
              </label>
            </>
          ) : null}
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button className="btn" type="submit">
              Save
            </button>
            <button className="btn secondary" type="button" onClick={() => setEditJob(null)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}

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
  tier?: number;
} | null;

type LiveWorker = {
  id: number;
  name: string;
  status: string;
  online: boolean;
  is_enabled: boolean;
  is_draining: boolean;
  last_seen_at: string | null;
  cpu_percent: number;
  mem_percent: number;
  disk_percent: number;
  mem_used_gb: number;
  mem_total_gb: number;
  disk_used_gb: number;
  disk_total_gb: number;
  load_avg_1: number;
  load_avg_5?: number;
  load_avg_15?: number;
  host_os: string;
  hostname: string;
  version: string;
  max_browsers: number;
  active_leases: number;
  load_ratio: number;
  proxy_pool_id?: number | null;
  has_proxy_pool?: boolean;
};

type LiveUser = {
  id: number;
  username: string;
  role: string;
  is_active: boolean;
  jobs_queued: number;
  jobs_running: number;
  jobs_completed: number;
  jobs_failed?: number;
  jobs_stopped?: number;
  rows_saved_total: number;
  rows_saved_today: number;
  subscription: string | null;
  subscription_days_left: number | null;
  thread_allowance?: number;
  threads_in_use?: number;
  threads_free?: number;
  dedicated_worker_count?: number;
};

type LiveRecentJob = {
  id: number;
  public_id: string;
  name?: string | null;
  owner_id: number;
  owner_username: string | null;
  status: string;
  rows_saved: number;
  total_searches: number;
  done_searches: number;
  error: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  chunks_pending: number;
  chunks_leased: number;
  chunks_done: number;
};

type LiveSystem = {
  packages_total: number;
  packages_active: number;
  packages_dedicated: number;
  subscriptions_active: number;
  orders_pending: number;
  users_total: number;
  users_active: number;
  users_with_dedicated_workers: number;
  proxy_pools_total: number;
  proxy_pools_active: number;
  proxies_total: number;
  workers_without_pool: number;
  captcha_configured: boolean;
  captcha_provider: string;
  captcha_backup_provider: string;
  bot_enabled: boolean;
  bot_username: string;
  bot_token_configured: boolean;
  bot_commands: number;
  bot_workflows: number;
};

type LiveStats = {
  generated_at: string;
  scope: string;
  quota: {
    thread_allowance: number;
    threads_in_use: number;
    threads_free: number;
  };
  overview: {
    workers_total: number;
    workers_online: number;
    workers_busy: number;
    workers_offline: number;
    workers_draining?: number;
    workers_disabled?: number;
    capacity_total?: number;
    capacity_used?: number;
    avg_cpu: number;
    avg_mem: number;
    avg_disk: number;
    active_leases: number;
    jobs_queued: number;
    jobs_running: number;
    jobs_completed: number;
    jobs_failed: number;
    jobs_stopped?: number;
    jobs_finished_today: number;
    rows_saved_today: number;
    chunks_pending?: number;
    chunks_leased?: number;
    chunks_done?: number;
    users_total: number;
    users_with_running_jobs: number;
    subscriptions_active?: number;
  };
  system: LiveSystem | null;
  recent_jobs: LiveRecentJob[];
  workers: LiveWorker[];
  users: LiveUser[];
};

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms)) return "—";
  const sec = Math.max(0, Math.floor(ms / 1000));
  if (sec < 5) return "just now";
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

function jobStatusBadge(status: string) {
  const cls =
    status === "running" || status === "completed"
      ? "ok"
      : status === "queued"
        ? "warn"
        : status === "failed"
          ? "danger"
          : "";
  return <span className={`badge ${cls}`}>{status}</span>;
}

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
  const isAdmin = user.role === "admin";

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
  const q = live?.quota;
  const sys = live?.system;
  const capacityTotal = o?.capacity_total ?? 0;
  const capacityUsed = o?.capacity_used ?? o?.active_leases ?? 0;
  const capacityPct = capacityTotal > 0 ? (capacityUsed / capacityTotal) * 100 : 0;
  const threadPct =
    q && q.thread_allowance > 0 ? (q.threads_in_use / q.thread_allowance) * 100 : 0;

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>Dashboard</h1>
          <p className="subtitle">
            Live stats · {isAdmin ? "fleet + platform" : "your account"}
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
          {isAdmin && o ? (
            <p className="muted" style={{ marginBottom: 0 }}>
              {o.subscriptions_active ?? 0} active subscriptions platform-wide
            </p>
          ) : null}
        </div>
        <div className="card">
          <h3 style={{ margin: 0 }}>Thread pool</h3>
          <p className="stat-xl">
            {q ? q.threads_in_use : "—"}
            <span className="muted"> / {q ? q.thread_allowance : "—"}</span>
          </p>
          <Meter label="" value={threadPct} detail={q ? `${q.threads_free} free` : undefined} />
        </div>
        <div className="card">
          <h3 style={{ margin: 0 }}>Jobs now</h3>
          <p className="stat-xl">
            {o ? o.jobs_running : "—"} <span className="muted">running</span>
          </p>
          <p className="muted">
            {o
              ? `${o.jobs_queued} queued · ${o.jobs_stopped ?? 0} stopped · ${o.jobs_finished_today} finished today`
              : "…"}
          </p>
        </div>
      </div>

      <div className="grid-cards cols-3">
        <div className="card">
          <h3 style={{ margin: 0 }}>Rows today</h3>
          <p className="stat-xl">{o ? o.rows_saved_today.toLocaleString() : "—"}</p>
          <p className="muted">
            {o ? `${o.jobs_completed} completed · ${o.jobs_failed} failed · ${o.jobs_stopped ?? 0} stopped` : "…"}
          </p>
        </div>
        <div className="card">
          <h3 style={{ margin: 0 }}>Chunks in flight</h3>
          <p className="stat-xl">
            {o ? (o.chunks_leased ?? 0) : "—"} <span className="muted">leased</span>
          </p>
          <p className="muted">
            {o ? `${o.chunks_pending ?? 0} pending · ${(o.chunks_done ?? 0).toLocaleString()} done` : "…"}
          </p>
        </div>
        {isAdmin && o ? (
          <div className="card">
            <h3 style={{ margin: 0 }}>Users</h3>
            <p className="stat-xl">
              {o.users_with_running_jobs}/{o.users_total} <span className="muted">active jobs</span>
            </p>
            <p className="muted">
              {sys ? `${sys.users_active} accounts enabled · ${sys.users_with_dedicated_workers} dedicated pins` : "…"}
            </p>
          </div>
        ) : (
          <div className="card">
            <h3 style={{ margin: 0 }}>Activity</h3>
            <p className="muted" style={{ marginBottom: 0 }}>
              {o
                ? `${o.jobs_completed + (o.jobs_failed || 0) + (o.jobs_stopped ?? 0)} finished jobs all-time · storage on Jobs page`
                : "…"}
            </p>
          </div>
        )}
      </div>

      {isAdmin && o ? (
        <div className="grid-cards cols-3">
          <div className="card">
            <h3 style={{ margin: 0 }}>Workers</h3>
            <p className="stat-xl">
              {o.workers_online}/{o.workers_total} <span className="muted">online</span>
            </p>
            <p className="muted">
              {o.workers_busy} busy · {o.workers_draining ?? 0} draining · {o.workers_disabled ?? 0} disabled ·{" "}
              {o.workers_offline} offline
            </p>
            <Meter
              label="Capacity"
              value={capacityPct}
              detail={`${capacityUsed}/${capacityTotal} leases`}
            />
          </div>
          <div className="card">
            <h3 style={{ margin: 0 }}>Fleet load</h3>
            <Meter label="CPU avg" value={o.avg_cpu} />
            <Meter label="RAM avg" value={o.avg_mem} />
            <Meter label="Disk avg" value={o.avg_disk} />
          </div>
          <div className="card">
            <h3 style={{ margin: 0 }}>Platform</h3>
            {sys ? (
              <>
                <p style={{ margin: "0.35rem 0" }}>
                  Captcha{" "}
                  {sys.captcha_configured ? (
                    <span className="badge ok">
                      {sys.captcha_provider}
                      {sys.captcha_backup_provider !== "none" ? ` + ${sys.captcha_backup_provider}` : ""}
                    </span>
                  ) : (
                    <span className="badge warn">not configured</span>
                  )}
                  {" · "}
                  <Link to="/app/admin/captcha">settings</Link>
                </p>
                <p style={{ margin: "0.35rem 0" }}>
                  Bot{" "}
                  {sys.bot_enabled ? (
                    <span className="badge ok">{sys.bot_username ? `@${sys.bot_username}` : "enabled"}</span>
                  ) : (
                    <span className="badge">off</span>
                  )}
                  {!sys.bot_token_configured ? <span className="muted"> · no token</span> : null}
                  {" · "}
                  {sys.bot_commands} cmds · {sys.bot_workflows} workflows · <Link to="/app/admin/bot">builder</Link>
                </p>
                <p className="muted" style={{ marginBottom: 0 }}>
                  {sys.packages_active}/{sys.packages_total} packages
                  {sys.packages_dedicated ? ` · ${sys.packages_dedicated} dedicated` : ""}
                  {" · "}
                  {sys.proxy_pools_active} pools / {sys.proxies_total} proxies
                  {sys.workers_without_pool ? ` · ${sys.workers_without_pool} workers no pool` : ""}
                  {sys.orders_pending ? (
                    <>
                      {" · "}
                      <Link to="/app/admin/billing">{sys.orders_pending} pending orders</Link>
                    </>
                  ) : (
                    " · no pending orders"
                  )}
                </p>
              </>
            ) : (
              <p className="muted">…</p>
            )}
          </div>
        </div>
      ) : null}

      {isAdmin && live && live.workers.length > 0 ? (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Workers — live</h3>
          <p className="muted" style={{ marginTop: 0 }}>
            Leases = concurrent chunk instances on the worker (capped by max browsers). Job thread count runs inside each
            lease and does not increase this ratio.
          </p>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Worker</th>
                  <th>Status</th>
                  <th>Last seen</th>
                  <th>Leases</th>
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
                        {w.has_proxy_pool === false ? " · no proxy pool" : ""}
                      </div>
                    </td>
                    <td>{statusBadge(w.status)}</td>
                    <td>
                      <span className="muted" style={{ fontSize: "0.85rem" }}>
                        {relativeTime(w.last_seen_at)}
                      </span>
                    </td>
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
                      <Meter
                        label=""
                        value={w.cpu_percent}
                        detail={
                          w.load_avg_1
                            ? `load ${w.load_avg_1}${w.load_avg_5 != null ? ` / ${w.load_avg_5}` : ""}`
                            : undefined
                        }
                      />
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
          <h3 style={{ marginTop: 0 }}>{isAdmin ? "Users — live" : "Your activity"}</h3>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Plan</th>
                  <th>Threads</th>
                  <th>Queued</th>
                  <th>Running</th>
                  <th>Done</th>
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
                        {(u.dedicated_worker_count ?? 0) > 0
                          ? ` · ${u.dedicated_worker_count} dedicated worker${u.dedicated_worker_count === 1 ? "" : "s"}`
                          : ""}
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
                    <td>
                      {u.threads_in_use ?? 0}/{u.thread_allowance ?? "—"}
                      {(u.threads_in_use ?? 0) > 0 ? (
                        <div className="meter" style={{ marginTop: 4 }}>
                          <div className="meter-track">
                            <div
                              className={`meter-fill ${
                                (u.thread_allowance ?? 0) > 0 &&
                                (u.threads_in_use ?? 0) / (u.thread_allowance ?? 1) >= 0.9
                                  ? "danger"
                                  : "ok"
                              }`}
                              style={{
                                width: `${
                                  (u.thread_allowance ?? 0) > 0
                                    ? Math.min(100, ((u.threads_in_use ?? 0) / (u.thread_allowance ?? 1)) * 100)
                                    : 0
                                }%`,
                              }}
                            />
                          </div>
                        </div>
                      ) : null}
                    </td>
                    <td>{u.jobs_queued}</td>
                    <td>
                      {u.jobs_running > 0 ? <span className="badge ok">{u.jobs_running}</span> : u.jobs_running}
                    </td>
                    <td>
                      <span className="muted" style={{ fontSize: "0.85rem" }}>
                        {u.jobs_completed} ok
                        {(u.jobs_failed ?? 0) > 0 ? ` · ${u.jobs_failed} fail` : ""}
                        {(u.jobs_stopped ?? 0) > 0 ? ` · ${u.jobs_stopped} stop` : ""}
                      </span>
                    </td>
                    <td>{u.rows_saved_today.toLocaleString()}</td>
                    <td>{u.rows_saved_total.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {live && live.recent_jobs.length > 0 ? (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Recent jobs</h3>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  {isAdmin ? <th>Owner</th> : null}
                  <th>Job</th>
                  <th>Status</th>
                  <th>Chunks</th>
                  <th>Rows</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {live.recent_jobs.map((j) => (
                  <tr key={j.id}>
                    {isAdmin ? (
                      <td>
                        <strong>{j.owner_username || `user ${j.owner_id}`}</strong>
                      </td>
                    ) : null}
                    <td>
                      {j.name ? <strong style={{ display: "block" }}>{j.name}</strong> : null}
                      <code style={{ fontSize: "0.8rem" }}>{j.public_id}</code>
                      {j.error ? (
                        <div className="muted" style={{ fontSize: "0.75rem", maxWidth: 280 }}>
                          {j.error}
                        </div>
                      ) : null}
                    </td>
                    <td>{jobStatusBadge(j.status)}</td>
                    <td>
                      <span className="muted" style={{ fontSize: "0.85rem" }}>
                        {j.chunks_pending}p / {j.chunks_leased}l / {j.chunks_done}d
                      </span>
                    </td>
                    <td>{j.rows_saved.toLocaleString()}</td>
                    <td>
                      <span className="muted" style={{ fontSize: "0.85rem" }}>
                        {relativeTime(j.finished_at || j.started_at || j.created_at)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted" style={{ marginBottom: 0, marginTop: "0.75rem" }}>
            Full history and storage by user: <Link to="/app/jobs">Jobs</Link>
          </p>
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
      tier?: number;
      description?: string;
      headings?: string[];
      features?: string[];
    }>
  >([]);
  const [billing, setBilling] = useState<{
    enabled: boolean;
    usdt_enabled: boolean;
    usdt_wallet: string;
    usdt_bep20_enabled?: boolean;
    usdt_bep20_wallet?: string;
    networks?: Array<{ key: string; label: string; wallet: string }>;
    manual_enabled: boolean;
    manual_methods: Array<{ name: string; details: string }>;
  } | null>(null);
  const [instructions, setInstructions] = useState("");
  const [txid, setTxid] = useState("");
  const [buyNetwork, setBuyNetwork] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  async function refresh() {
    setSub(await api<Sub>("/api/subscriptions/me").catch(() => null));
    setPackages(await api<Array<{ id: number; slug: string; name: string; price_usdt: number; threads: number; duration_days: number; tier?: number }>>("/api/packages").catch(() => []));
    const b = await api<{
      enabled: boolean;
      usdt_enabled: boolean;
      usdt_wallet: string;
      usdt_bep20_enabled?: boolean;
      usdt_bep20_wallet?: string;
      networks?: Array<{ key: string; label: string; wallet: string }>;
      manual_enabled: boolean;
      manual_methods: Array<{ name: string; details: string }>;
    }>("/api/billing/public").catch(() => null);
    setBilling(b);
    if (b?.networks?.length === 1) setBuyNetwork(b.networks[0].key);
  }

  useEffect(() => {
    refresh().catch(() => undefined);
  }, []);

  async function buy(slug: string) {
    setError("");
    setMsg("");
    try {
      const body: Record<string, string> = { package_slug: slug };
      if (buyNetwork) body.network = buyNetwork;
      const res = await api<{ instructions: string }>("/api/orders/buy", {
        method: "POST",
        body: JSON.stringify(body),
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

  const buyablePackages =
    sub && typeof sub.tier === "number"
      ? packages.filter((p) => (p.tier ?? 0) > (sub.tier ?? 0))
      : packages;

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
          <h3>{sub ? "Upgrade" : "Packages"}</h3>
          {sub && buyablePackages.length === 0 ? (
            <p className="muted">You're on the top plan — no higher packages to upgrade to.</p>
          ) : null}
          {(billing.networks?.length || 0) > 0 && buyablePackages.length > 0 ? (
            <label className="field" style={{ maxWidth: 320, marginBottom: "0.75rem" }}>
              Payment network
              <select className="input" value={buyNetwork} onChange={(e) => setBuyNetwork(e.target.value)}>
                {(billing.networks?.length || 0) > 1 ? <option value="">Select…</option> : null}
                {(billing.networks || []).map((n) => (
                  <option key={n.key} value={n.key}>
                    {n.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {buyablePackages.length > 0 ? (
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
              {buyablePackages.map((p) => (
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
                      {sub ? "Upgrade" : "Buy"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          ) : null}
          {instructions ? <pre style={{ whiteSpace: "pre-wrap", color: "var(--muted)" }}>{instructions}</pre> : null}
          {billing.usdt_enabled || billing.usdt_bep20_enabled ? (
            <form onSubmit={paid} style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", marginTop: "0.75rem" }}>
              <input
                className="input"
                style={{ maxWidth: 360 }}
                placeholder="USDT TxID (TRC-20 or BEP-20)"
                value={txid}
                onChange={(e) => setTxid(e.target.value)}
              />
              <button className="btn" type="submit">
                Verify payment
              </button>
            </form>
          ) : null}
          {billing.usdt_enabled || billing.usdt_bep20_enabled ? (
            <p className="muted" style={{ fontSize: "0.85rem", marginTop: "0.5rem" }}>
              Auto-grant after ≥20 on-chain confirmations. If confirmations are still low, wait and retry.
            </p>
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
