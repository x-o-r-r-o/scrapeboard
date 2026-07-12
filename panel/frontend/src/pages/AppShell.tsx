import { useEffect, useState } from "react";
import { Link, Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, getToken, setToken, type User } from "../api";

type NavLink = { to: string; label: string };

const USER_LINKS: NavLink[] = [
  { to: "/app", label: "Dashboard" },
  { to: "/app/jobs", label: "Jobs" },
  { to: "/app/subscription", label: "Subscription" },
];

const ADMIN_LINKS: NavLink[] = [
  { to: "/app/admin/users", label: "Users" },
  { to: "/app/admin/packages", label: "Packages" },
  { to: "/app/admin/billing", label: "Billing & Telegram" },
  { to: "/app/admin/proxies", label: "Proxy pools" },
  { to: "/app/admin/workers", label: "Workers" },
  { to: "/app/admin/scrape", label: "Scrape profiles" },
  { to: "/app/admin/security", label: "Security" },
  { to: "/app/admin/bot", label: "Bot builder" },
];

function NavLinks({
  links,
  onNavigate,
  pathname,
}: {
  links: NavLink[];
  onNavigate?: () => void;
  pathname: string;
}) {
  return (
    <>
      {links.map((l) => {
        const active = pathname === l.to || (l.to !== "/app" && pathname.startsWith(l.to));
        return (
          <Link key={l.to} to={l.to} className={active ? "active" : undefined} onClick={onNavigate}>
            {l.label}
          </Link>
        );
      })}
    </>
  );
}

function SidebarBody({
  user,
  pathname,
  onNavigate,
  onSignOut,
}: {
  user: User;
  pathname: string;
  onNavigate?: () => void;
  onSignOut: () => void;
}) {
  const isAdmin = user.role === "admin";
  return (
    <>
      <div>
        <div className="shell-brand">Scrapeboard</div>
        <div className="shell-brand-sub">Maps control panel</div>
      </div>
      <nav className="shell-nav">
        <NavLinks links={USER_LINKS} pathname={pathname} onNavigate={onNavigate} />
        {isAdmin ? (
          <>
            <div className="nav-label">Admin</div>
            <NavLinks links={ADMIN_LINKS} pathname={pathname} onNavigate={onNavigate} />
          </>
        ) : null}
      </nav>
      <div style={{ display: "grid", gap: "0.45rem" }}>
        <div className="muted" style={{ fontSize: "0.8rem", padding: "0 0.25rem" }}>
          {user.username} · {user.role}
        </div>
        <button className="btn secondary" type="button" onClick={onSignOut}>
          Sign out
        </button>
      </div>
    </>
  );
}

export default function AppShell() {
  const nav = useNavigate();
  const { pathname } = useLocation();
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState("");
  const [mobileOpen, setMobileOpen] = useState(false);

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

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  if (!getToken()) return <Navigate to="/login" replace />;
  if (error) return <p className="error" style={{ padding: "1.5rem" }}>{error}</p>;
  if (!user) return <p className="muted" style={{ padding: "2rem" }}>Loading…</p>;

  const signOut = () => {
    setToken(null);
    nav("/login");
  };

  const pageTitle =
    [...USER_LINKS, ...ADMIN_LINKS].find((l) => pathname === l.to || (l.to !== "/app" && pathname.startsWith(l.to)))
      ?.label || "Scrapeboard";

  return (
    <div className="shell">
      <aside className="shell-aside">
        <SidebarBody user={user} pathname={pathname} onSignOut={signOut} />
      </aside>

      {mobileOpen ? (
        <>
          <div className="drawer-backdrop" onClick={() => setMobileOpen(false)} aria-hidden />
          <aside className="drawer" role="dialog" aria-label="Navigation">
            <SidebarBody
              user={user}
              pathname={pathname}
              onNavigate={() => setMobileOpen(false)}
              onSignOut={signOut}
            />
          </aside>
        </>
      ) : null}

      <div className="shell-main-col">
        <header className="shell-topbar">
          <button
            className="btn secondary icon"
            type="button"
            aria-label="Open menu"
            onClick={() => setMobileOpen(true)}
          >
            ☰
          </button>
          <span className="title">{pageTitle}</span>
        </header>
        <main className="shell-content">
          <Outlet context={{ user }} />
        </main>
      </div>
    </div>
  );
}
