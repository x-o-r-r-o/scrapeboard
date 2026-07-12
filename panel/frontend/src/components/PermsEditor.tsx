import { useEffect, useState } from "react";
import { api } from "../api";

export type PermSchema = {
  keys: Array<{
    key: string;
    label: string;
    type: "bool" | "number" | "engines";
    group: string;
    min?: number;
    max?: number;
    options?: string[];
  }>;
  defaults: Record<string, unknown>;
  engines: string[];
};

export type WorkerLite = { id: number; name: string; online?: boolean; is_enabled?: boolean };

type Props = {
  perms: Record<string, unknown>;
  workerIds: number[];
  onPermsChange: (perms: Record<string, unknown>) => void;
  onWorkerIdsChange: (ids: number[]) => void;
  schema?: PermSchema | null;
  workers?: WorkerLite[];
  title?: string;
  /** Only show worker pin UI for dedicated-worker packages */
  showWorkers?: boolean;
};

export function usePermSchema() {
  const [schema, setSchema] = useState<PermSchema | null>(null);
  const [workers, setWorkers] = useState<WorkerLite[]>([]);
  useEffect(() => {
    api<PermSchema>("/api/users/perm-schema")
      .then(setSchema)
      .catch(() => undefined);
    api<WorkerLite[]>("/api/workers")
      .then((rows) => setWorkers(rows.map((w) => ({ id: w.id, name: w.name, online: w.online, is_enabled: w.is_enabled }))))
      .catch(() => undefined);
  }, []);
  return { schema, workers };
}

export function PermsEditor({
  perms,
  workerIds,
  onPermsChange,
  onWorkerIdsChange,
  schema,
  workers = [],
  title = "Role permissions & workers",
  showWorkers = false,
}: Props) {
  if (!schema) {
    return <p className="muted">Loading permission schema…</p>;
  }

  const groups = Array.from(new Set(schema.keys.map((k) => k.group)));

  function setKey(key: string, value: unknown) {
    onPermsChange({ ...perms, [key]: value });
  }

  const enginesVal = perms.allowed_engines;
  const allEngines = enginesVal === "all" || (Array.isArray(enginesVal) && enginesVal.includes("all"));
  const selectedEngines = Array.isArray(enginesVal) ? enginesVal.filter((e) => e !== "all") : [];

  function toggleWorker(id: number) {
    if (workerIds.includes(id)) onWorkerIdsChange(workerIds.filter((x) => x !== id));
    else onWorkerIdsChange([...workerIds, id]);
  }

  return (
    <div className="card" style={{ gridColumn: "1 / -1", display: "grid", gap: "0.85rem" }}>
      <h4 style={{ margin: 0 }}>{title}</h4>
      {groups.map((g) => (
        <div key={g}>
          <div className="muted" style={{ textTransform: "uppercase", fontSize: "0.72rem", letterSpacing: "0.04em", marginBottom: "0.4rem" }}>
            {g}
          </div>
          <div style={{ display: "grid", gap: "0.55rem", gridTemplateColumns: "repeat(auto-fit,minmax(200px,1fr))" }}>
            {schema.keys
              .filter((k) => k.group === g)
              .map((k) => {
                if (k.type === "bool") {
                  return (
                    <label key={k.key} style={{ display: "flex", gap: "0.45rem", alignItems: "center" }}>
                      <input type="checkbox" checked={Boolean(perms[k.key] ?? schema.defaults[k.key])} onChange={(e) => setKey(k.key, e.target.checked)} />
                      {k.label}
                    </label>
                  );
                }
                if (k.type === "number") {
                  return (
                    <label key={k.key} className="field">
                      {k.label}
                      <input
                        className="input"
                        type="number"
                        min={k.min}
                        max={k.max}
                        value={Number(perms[k.key] ?? schema.defaults[k.key] ?? 0)}
                        onChange={(e) => setKey(k.key, Number(e.target.value))}
                      />
                    </label>
                  );
                }
                if (k.type === "engines") {
                  return (
                    <div key={k.key} style={{ gridColumn: "1 / -1" }}>
                      <div className="field" style={{ marginBottom: "0.4rem" }}>
                        {k.label}
                      </div>
                      <label style={{ display: "flex", gap: "0.45rem", alignItems: "center", marginBottom: "0.35rem" }}>
                        <input
                          type="checkbox"
                          checked={allEngines}
                          onChange={(e) => setKey("allowed_engines", e.target.checked ? "all" : selectedEngines.length ? selectedEngines : [schema.engines[0]])}
                        />
                        All engines
                      </label>
                      {!allEngines ? (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.65rem" }}>
                          {(k.options || schema.engines).map((eng) => {
                            const on = selectedEngines.includes(eng);
                            return (
                              <label key={eng} style={{ display: "flex", gap: "0.35rem", alignItems: "center" }}>
                                <input
                                  type="checkbox"
                                  checked={on}
                                  onChange={() => {
                                    const next = on ? selectedEngines.filter((x) => x !== eng) : [...selectedEngines, eng];
                                    setKey("allowed_engines", next.length ? next : "all");
                                  }}
                                />
                                {eng}
                              </label>
                            );
                          })}
                        </div>
                      ) : null}
                    </div>
                  );
                }
                return null;
              })}
          </div>
        </div>
      ))}
      {showWorkers ? (
        <div>
          <div className="field" style={{ marginBottom: "0.35rem" }}>
            Dedicated worker assignment (optional)
          </div>
          <p className="muted" style={{ margin: "0 0 0.5rem", fontSize: "0.85rem" }}>
            Leave empty = all enabled workers (shared pool). Select one or more to pin this user&apos;s jobs. Only applies with a dedicated-worker package; without it, assignments are ignored at lease time.
          </p>
          {workers.length === 0 ? (
            <p className="muted">No workers registered yet.</p>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.65rem" }}>
              {workers.map((w) => (
                <label key={w.id} style={{ display: "flex", gap: "0.35rem", alignItems: "center" }}>
                  <input type="checkbox" checked={workerIds.includes(w.id)} onChange={() => toggleWorker(w.id)} />
                  {w.name}
                  {w.online ? <span className="badge ok">online</span> : <span className="badge">offline</span>}
                </label>
              ))}
            </div>
          )}
          {workerIds.length > 0 ? (
            <button className="btn secondary sm" type="button" style={{ marginTop: "0.5rem" }} onClick={() => onWorkerIdsChange([])}>
              Clear assignments (any worker)
            </button>
          ) : null}
        </div>
      ) : (
        <p className="muted" style={{ margin: 0, fontSize: "0.85rem" }}>
          No dedicated-worker package — jobs use the shared pool (all enabled workers). Worker pin UI appears when this user has a dedicated-worker package.
        </p>
      )}
    </div>
  );
}

export function StringListField({
  label,
  hint,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  hint?: string;
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
}) {
  return (
    <label className="field" style={{ gridColumn: "1 / -1" }}>
      {label}
      {hint ? <span className="muted" style={{ fontWeight: 400 }}>{hint}</span> : null}
      <textarea
        className="input"
        rows={4}
        placeholder={placeholder || "One item per line"}
        value={value.join("\n")}
        onChange={(e) => onChange(e.target.value.split("\n"))}
      />
    </label>
  );
}

export function EngineMultiSelect({
  value,
  onChange,
  engines,
}: {
  value: string[] | "all";
  onChange: (v: string[] | "all") => void;
  engines: string[];
}) {
  const all = value === "all" || (Array.isArray(value) && (value.length === 0 || value.includes("all")));
  const selected = Array.isArray(value) ? value.filter((e) => e !== "all") : [];
  return (
    <div style={{ gridColumn: "1 / -1" }}>
      <div className="field" style={{ marginBottom: "0.35rem" }}>
        Allowed engines
      </div>
      <label style={{ display: "flex", gap: "0.45rem", alignItems: "center", marginBottom: "0.35rem" }}>
        <input type="checkbox" checked={all} onChange={(e) => onChange(e.target.checked ? "all" : [engines[0] || "chrome"])} />
        All engines
      </label>
      {!all ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.65rem" }}>
          {engines.map((eng) => {
            const on = selected.includes(eng);
            return (
              <label key={eng} style={{ display: "flex", gap: "0.35rem", alignItems: "center" }}>
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => {
                    const next = on ? selected.filter((x) => x !== eng) : [...selected, eng];
                    onChange(next.length ? next : "all");
                  }}
                />
                {eng}
              </label>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
