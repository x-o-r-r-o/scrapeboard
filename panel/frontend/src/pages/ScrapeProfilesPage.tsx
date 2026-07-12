import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

type Profile = {
  id: number;
  name: string;
  slug: string;
  description: string;
  is_default: boolean;
  is_active: boolean;
  engine: string;
  threads: number;
  block_resources: string;
  scrape_websites: string;
  max_results: number;
  chunk_size: number;
  min_delay: number;
  max_delay: number;
  cooldown_every: number;
  cooldown_min: number;
  cooldown_max: number;
  nav_timeout: number;
  proxy_attempts: number;
  headless: boolean;
  no_stealth: boolean;
  browser_path: string;
  geoip: boolean;
  preflight_timeout: number;
  no_preflight: boolean;
  fresh: boolean;
  debug: boolean;
  worker_count: number;
  package_count: number;
};

const ENGINES = ["chrome", "google-chrome", "edge", "brave", "camoufox"];

const emptyForm = (): Partial<Profile> & { apply_to_workers: boolean } => ({
  name: "",
  slug: "",
  description: "",
  is_default: false,
  is_active: true,
  engine: "chrome",
  threads: 2,
  block_resources: "media",
  scrape_websites: "yes",
  max_results: 0,
  chunk_size: 500,
  min_delay: 2,
  max_delay: 5,
  cooldown_every: 25,
  cooldown_min: 25,
  cooldown_max: 60,
  nav_timeout: 45,
  proxy_attempts: 3,
  headless: true,
  no_stealth: false,
  browser_path: "",
  geoip: false,
  preflight_timeout: 12,
  no_preflight: false,
  fresh: false,
  debug: false,
  apply_to_workers: false,
});

export function ScrapeAdminPage() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [form, setForm] = useState(emptyForm());
  const [creating, setCreating] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  async function refresh() {
    const rows = await api<Profile[]>("/api/scrape-profiles");
    setProfiles(rows);
  }

  useEffect(() => {
    refresh()
      .then((_) => undefined)
      .catch((e) => setError(e.message));
  }, []);

  function openProfile(p: Profile) {
    setCreating(false);
    setSelectedId(p.id);
    setForm({
      ...p,
      apply_to_workers: false,
    });
    setMsg("");
    setError("");
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      if (creating) {
        const created = await api<Profile>("/api/scrape-profiles", {
          method: "POST",
          body: JSON.stringify({
            name: form.name,
            slug: form.slug || undefined,
            description: form.description || "",
            clone_from_id: profiles.find((p) => p.is_default)?.id ?? profiles[0]?.id,
            is_default: form.is_default,
            is_active: form.is_active,
          }),
        });
        await api(`/api/scrape-profiles/${created.id}`, {
          method: "PATCH",
          body: JSON.stringify(buildPatch(form)),
        });
        setMsg("Profile created.");
        await refresh();
        const rows = await api<Profile[]>("/api/scrape-profiles");
        const cur = rows.find((x) => x.id === created.id);
        if (cur) openProfile(cur);
        return;
      }
      if (selectedId == null) return;
      await api(`/api/scrape-profiles/${selectedId}`, {
        method: "PATCH",
        body: JSON.stringify(buildPatch(form)),
      });
      setMsg(form.apply_to_workers ? "Profile saved and pushed to assigned workers." : "Profile saved.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>Scrape profiles</h1>
          <p className="subtitle">
            Browser, pacing, and scrape flags per package or worker. Captcha solvers are configured once under{" "}
            <Link to="/app/admin/captcha">Captcha</Link>.
          </p>
        </div>
        <div className="page-actions">
          <button
            className="btn"
            type="button"
            onClick={() => {
              setCreating(true);
              setSelectedId(null);
              setForm(emptyForm());
            }}
          >
            New profile
          </button>
        </div>
      </div>

      {error ? <p className="error">{error}</p> : null}
      {msg ? <p className="muted">{msg}</p> : null}

      <div className="grid-cards cols-3">
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Profiles</h3>
          <div className="stack" style={{ gap: "0.55rem" }}>
            {profiles.map((p) => (
              <button
                key={p.id}
                type="button"
                className={`item-card ${selectedId === p.id ? "active" : ""}`}
                onClick={() => openProfile(p)}
                style={{ textAlign: "left", width: "100%", cursor: "pointer" }}
              >
                <div className="item-card-title">
                  {p.name}
                  {p.is_default ? <span className="badge ok">default</span> : null}
                  <span className={`badge ${p.is_active ? "ok" : "danger"}`}>{p.is_active ? "on" : "off"}</span>
                </div>
                <div className="muted" style={{ fontSize: "0.8rem" }}>
                  {p.engine} · {p.threads} threads
                  <br />
                  {p.worker_count} workers · {p.package_count} packages
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="card" style={{ gridColumn: "span 2" }}>
          {creating || selectedId != null ? (
            <form className="stack" onSubmit={save}>
              <h3 style={{ marginTop: 0 }}>{creating ? "Create scrape profile" : `Edit — ${form.name}`}</h3>
              <div className="form-grid two">
                <label className="field">
                  Name
                  <input className="input" required value={form.name || ""} onChange={(e) => setForm({ ...form, name: e.target.value })} />
                </label>
                <label className="field">
                  Slug
                  <input className="input" value={form.slug || ""} onChange={(e) => setForm({ ...form, slug: e.target.value })} placeholder="auto from name" />
                </label>
              </div>
              <label className="field">
                Description
                <input className="input" value={form.description || ""} onChange={(e) => setForm({ ...form, description: e.target.value })} />
              </label>

              <h4 style={{ margin: "0.25rem 0 0" }}>Browser & pace</h4>
              <div className="form-grid two">
                <label className="field">
                  Engine
                  <select className="input" value={form.engine} onChange={(e) => setForm({ ...form, engine: e.target.value })}>
                    {ENGINES.map((x) => (
                      <option key={x} value={x}>
                        {x}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  Threads
                  <input className="input" type="number" min={1} value={form.threads} onChange={(e) => setForm({ ...form, threads: Number(e.target.value) })} />
                </label>
                <label className="field">
                  Block resources
                  <select className="input" value={form.block_resources} onChange={(e) => setForm({ ...form, block_resources: e.target.value })}>
                    <option value="none">none — load everything</option>
                    <option value="images">images — block images only</option>
                    <option value="media">media — block images + media (default)</option>
                    <option value="all">all — block images, media, fonts, styles</option>
                  </select>
                </label>
                <label className="field">
                  Scrape websites
                  <select className="input" value={form.scrape_websites} onChange={(e) => setForm({ ...form, scrape_websites: e.target.value })}>
                    <option value="yes">yes</option>
                    <option value="no">no</option>
                  </select>
                </label>
                <label className="field">
                  Max results (0=all)
                  <input className="input" type="number" min={0} value={form.max_results} onChange={(e) => setForm({ ...form, max_results: Number(e.target.value) })} />
                </label>
                <label className="field">
                  Chunk size
                  <input className="input" type="number" min={1} value={form.chunk_size} onChange={(e) => setForm({ ...form, chunk_size: Number(e.target.value) })} />
                </label>
                <label className="field">
                  Min delay
                  <input className="input" type="number" step="0.1" value={form.min_delay} onChange={(e) => setForm({ ...form, min_delay: Number(e.target.value) })} />
                </label>
                <label className="field">
                  Max delay
                  <input className="input" type="number" step="0.1" value={form.max_delay} onChange={(e) => setForm({ ...form, max_delay: Number(e.target.value) })} />
                </label>
                <label className="field">
                  Cooldown every N
                  <input className="input" type="number" value={form.cooldown_every} onChange={(e) => setForm({ ...form, cooldown_every: Number(e.target.value) })} />
                </label>
                <label className="field">
                  Cooldown min/max
                  <div style={{ display: "flex", gap: "0.4rem" }}>
                    <input className="input" type="number" value={form.cooldown_min} onChange={(e) => setForm({ ...form, cooldown_min: Number(e.target.value) })} />
                    <input className="input" type="number" value={form.cooldown_max} onChange={(e) => setForm({ ...form, cooldown_max: Number(e.target.value) })} />
                  </div>
                </label>
                <label className="field">
                  Nav timeout (seconds)
                  <input className="input" type="number" value={form.nav_timeout} onChange={(e) => setForm({ ...form, nav_timeout: Number(e.target.value) })} />
                </label>
                <label className="field">
                  Proxy attempts
                  <input className="input" type="number" value={form.proxy_attempts} onChange={(e) => setForm({ ...form, proxy_attempts: Number(e.target.value) })} />
                </label>
                <label className="field">
                  Browser path (optional)
                  <input
                    className="input"
                    value={form.browser_path || ""}
                    onChange={(e) => setForm({ ...form, browser_path: e.target.value })}
                    placeholder="blank = engine default binary"
                  />
                </label>
                <label className="field">
                  Preflight timeout (seconds)
                  <input
                    className="input"
                    type="number"
                    step="0.1"
                    min={1}
                    value={form.preflight_timeout ?? 12}
                    onChange={(e) => setForm({ ...form, preflight_timeout: Number(e.target.value) })}
                  />
                </label>
              </div>

              <h4 style={{ margin: "0.25rem 0 0" }}>Flags</h4>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "0.85rem" }}>
                {(
                  [
                    ["headless", "Headless"],
                    ["no_stealth", "Disable stealth"],
                    ["geoip", "GeoIP"],
                    ["no_preflight", "Skip preflight"],
                    ["fresh", "Fresh profile"],
                    ["debug", "Debug"],
                    ["is_default", "Default profile"],
                    ["is_active", "Active"],
                    ["apply_to_workers", "Push to assigned workers on save"],
                  ] as const
                ).map(([key, label]) => (
                  <label key={key}>
                    <input
                      type="checkbox"
                      checked={Boolean(form[key])}
                      onChange={(e) => setForm({ ...form, [key]: e.target.checked })}
                    />{" "}
                    {label}
                  </label>
                ))}
              </div>

              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                <button className="btn" type="submit">
                  Save
                </button>
                {!creating && selectedId != null && !form.is_default ? (
                  <button
                    className="btn danger"
                    type="button"
                    onClick={async () => {
                      if (!confirm("Delete this profile? Workers/packages fall back to default.")) return;
                      await api(`/api/scrape-profiles/${selectedId}`, { method: "DELETE" });
                      setSelectedId(null);
                      setMsg("Profile deleted.");
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
              <h3>Select a profile</h3>
              <p className="muted">Manage engines, delays, and scrape flags per package or worker.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function buildPatch(form: ReturnType<typeof emptyForm>) {
  return {
    name: form.name,
    slug: form.slug || undefined,
    description: form.description,
    is_default: form.is_default,
    is_active: form.is_active,
    engine: form.engine,
    threads: form.threads,
    block_resources: form.block_resources,
    scrape_websites: form.scrape_websites,
    max_results: form.max_results,
    chunk_size: form.chunk_size,
    min_delay: form.min_delay,
    max_delay: form.max_delay,
    cooldown_every: form.cooldown_every,
    cooldown_min: form.cooldown_min,
    cooldown_max: form.cooldown_max,
    nav_timeout: form.nav_timeout,
    proxy_attempts: form.proxy_attempts,
    headless: form.headless,
    no_stealth: form.no_stealth,
    browser_path: form.browser_path,
    geoip: form.geoip,
    preflight_timeout: form.preflight_timeout,
    no_preflight: form.no_preflight,
    fresh: form.fresh,
    debug: form.debug,
    apply_to_workers: form.apply_to_workers,
  };
}
