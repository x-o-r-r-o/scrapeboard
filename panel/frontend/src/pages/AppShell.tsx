import { useEffect, useState } from "react";
import { Link, Navigate, Outlet, useNavigate } from "react-router-dom";
import { api, getToken, setToken, type User } from "../api";

export default function AppShell() {
  const nav = useNavigate();
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!getToken()) return;
    api<User>("/api/auth/me")
      .then((u) => {
        if (u.must_change_password) nav("/setup/password");
        else if (!u.totp_enabled) nav("/setup/2fa");
        else setUser(u);
      })
      .catch((e) => setError(e.message));
  }, [nav]);

  if (!getToken()) return <Navigate to="/login" replace />;
  if (error) return <p className="error">{error}</p>;
  if (!user) return <p className="muted" style={{ padding: "2rem" }}>Loading…</p>;

  const isAdmin = user.role === "admin";

  return (
    <div className="shell">
      <aside>
        <div className="brand">GMaps Panel</div>
        <nav>
          <Link to="/app">Dashboard</Link>
          <Link to="/app/jobs">Jobs</Link>
          <Link to="/app/subscription">Subscription</Link>
          {isAdmin ? (
            <>
              <div className="nav-label">Admin</div>
              <Link to="/app/admin/users">Users</Link>
              <Link to="/app/admin/packages">Packages</Link>
              <Link to="/app/admin/billing">Billing</Link>
              <Link to="/app/admin/proxies">Proxy pools</Link>
              <Link to="/app/admin/workers">Workers</Link>
              <Link to="/app/admin/scrape">Scrape settings</Link>
              <Link to="/app/admin/security">Security</Link>
              <Link to="/app/admin/bot">Bot builder</Link>
            </>
          ) : null}
        </nav>
        <button
          className="btn secondary"
          type="button"
          onClick={() => {
            setToken(null);
            nav("/login");
          }}
        >
          Sign out ({user.username})
        </button>
      </aside>
      <main>
        <Outlet context={{ user }} />
      </main>
      <style>{`
        .shell { display:grid; grid-template-columns: 240px 1fr; min-height:100vh; }
        aside { border-right:1px solid var(--line); padding:1.25rem; display:flex; flex-direction:column; gap:1rem; background: color-mix(in srgb, var(--bg2) 80%, transparent); }
        .brand { font-weight:700; letter-spacing:0.08em; text-transform:uppercase; color:var(--accent); font-size:0.8rem; }
        nav { display:flex; flex-direction:column; gap:0.35rem; flex:1; }
        nav a { color:var(--text); padding:0.45rem 0.55rem; border-radius:8px; }
        nav a:hover { background:#24313a; }
        .nav-label { margin-top:0.8rem; font-size:0.7rem; text-transform:uppercase; letter-spacing:0.08em; color:var(--muted); }
        main { padding:1.5rem; }
        @media (max-width: 800px) { .shell { grid-template-columns: 1fr; } aside { border-right:0; border-bottom:1px solid var(--line); } }
      `}</style>
    </div>
  );
}
