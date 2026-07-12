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

export function DashboardPage() {
  const { user } = useOutletContext<{ user: User }>();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [sub, setSub] = useState<Sub>(null);

  useEffect(() => {
    api<Job[]>("/api/jobs").then(setJobs).catch(() => setJobs([]));
    api<Sub>("/api/subscriptions/me").then(setSub).catch(() => setSub(null));
  }, []);

  return (
    <div className="stack">
      <h1>Dashboard</h1>
      <p className="muted">
        Signed in as <strong>{user.username}</strong> ({user.role}). Stats below are{" "}
        {user.role === "admin" ? "global" : "only yours"}.
      </p>
      <div className="grid">
        <div className="card">
          <h3>Subscription</h3>
          {sub ? (
            <p>
              {sub.package_name} · {sub.threads} threads · {sub.days_left.toFixed(1)} days left
            </p>
          ) : (
            <p className="muted">No active subscription</p>
          )}
        </div>
        <div className="card">
          <h3>Jobs</h3>
          <p>{jobs.length} total · {jobs.filter((j) => j.status === "running").length} running</p>
        </div>
      </div>
      <style>{`.stack{display:grid;gap:1rem}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem}h1,h3{margin:0}`}</style>
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
      <h1>Jobs</h1>
      <p className="muted">You only see jobs you own (admins see all).</p>
      <form className="card" onSubmit={onCreate} style={{ display: "grid", gap: "0.75rem" }}>
        <h3>New job</h3>
        <label>
          Keywords file
          <input className="input" type="file" name="keywords" accept=".txt,.csv" required />
        </label>
        <label>
          Locations file
          <input className="input" type="file" name="locations" accept=".txt,.csv" required />
        </label>
        <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
          <label>
            Engine
            <select className="input" value={engine} onChange={(e) => setEngine(e.target.value)}>
              <option value="chrome">chrome</option>
              <option value="brave">brave</option>
              <option value="camoufox">camoufox</option>
              <option value="google-chrome">google-chrome</option>
              <option value="edge">edge</option>
            </select>
          </label>
          <label>
            Threads
            <input className="input" type="number" min={1} value={threads} onChange={(e) => setThreads(Number(e.target.value))} />
          </label>
        </div>
        {error ? <p className="error">{error}</p> : null}
        <button className="btn" type="submit">
          Queue job
        </button>
      </form>
      <div className="card">
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
