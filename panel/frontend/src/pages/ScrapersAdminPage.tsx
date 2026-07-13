import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";

type CatalogItem = {
  id: string;
  label: string;
  group: string;
  group_label: string;
  description: string;
  implemented: boolean;
  risk: string;
  inputs: string;
  selectable: boolean;
};

type ScraperSettings = {
  enabled_sources: string[];
  catalog: CatalogItem[];
};

export function ScrapersAdminPage() {
  const [enabled, setEnabled] = useState<string[]>(["gmaps"]);
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  async function load() {
    const s = await api<ScraperSettings>("/api/settings/scrapers");
    setEnabled(s.enabled_sources?.length ? s.enabled_sources : ["gmaps"]);
    setCatalog(s.catalog || []);
  }

  useEffect(() => {
    load().catch((e) => setError(e instanceof Error ? e.message : "Failed to load"));
  }, []);

  function toggle(id: string) {
    if (id === "gmaps") return; // Maps always on
    setEnabled((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const saved = await api<ScraperSettings>("/api/settings/scrapers", {
        method: "PUT",
        body: JSON.stringify({ enabled_sources: enabled.includes("gmaps") ? enabled : ["gmaps", ...enabled] }),
      });
      setEnabled(saved.enabled_sources);
      setCatalog(saved.catalog || []);
      setMsg("Saved. Enabled sources can appear in job menus when also allowed on the package and implemented.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  }

  const groups = new Map<string, CatalogItem[]>();
  for (const s of catalog) {
    const list = groups.get(s.group_label) || [];
    list.push(s);
    groups.set(s.group_label, list);
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>Scrapers</h1>
          <p className="subtitle">
            Global menu kill switches for scraper sources. Google Maps stays enabled. Other sources stay hidden
            from users until enabled here, allowed on a package, and implemented (Phase A: only Maps is
            implemented).
          </p>
        </div>
      </div>

      {error ? <p className="error">{error}</p> : null}
      {msg ? <p className="muted">{msg}</p> : null}

      <form className="card stack" onSubmit={save}>
        {Array.from(groups.entries()).map(([label, items]) => (
          <div key={label} className="stack" style={{ gap: "0.45rem" }}>
            <h3 style={{ margin: 0 }}>{label}</h3>
            {items.map((s) => (
              <label
                key={s.id}
                style={{ display: "flex", gap: "0.6rem", alignItems: "flex-start" }}
              >
                <input
                  type="checkbox"
                  checked={enabled.includes(s.id)}
                  disabled={s.id === "gmaps"}
                  onChange={() => toggle(s.id)}
                  style={{ marginTop: "0.25rem" }}
                />
                <span>
                  <strong>{s.label}</strong>{" "}
                  <span className="muted">
                    ({s.id}
                    {!s.implemented ? " · not implemented yet" : ""}
                    {s.risk ? ` · risk: ${s.risk}` : ""})
                  </span>
                  <div className="muted" style={{ fontSize: "0.85rem" }}>
                    {s.description}
                  </div>
                </span>
              </label>
            ))}
          </div>
        ))}
        <button className="btn" type="submit" style={{ alignSelf: "flex-start" }}>
          Save enabled scrapers
        </button>
      </form>
    </div>
  );
}
