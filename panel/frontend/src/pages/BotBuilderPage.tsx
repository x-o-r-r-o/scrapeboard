import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";

type BotSettings = Record<string, unknown>;
type Command = {
  id: number;
  command: string;
  title: string;
  enabled: boolean;
  audience: string;
};
type Workflow = {
  id: number;
  name: string;
  description: string;
  enabled: boolean;
  is_demo: boolean;
};

type Tab = "connection" | "commands" | "workflows";

export default function BotBuilderPage() {
  const [settings, setSettings] = useState<BotSettings>({});
  const [token, setToken] = useState("");
  const [commands, setCommands] = useState<Command[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("connection");
  const [saving, setSaving] = useState(false);

  async function refresh() {
    const [s, c, w] = await Promise.all([
      api<BotSettings>("/api/bot/settings"),
      api<Command[]>("/api/bot/commands"),
      api<Workflow[]>("/api/bot/workflows"),
    ]);
    setSettings(s);
    setCommands(c);
    setWorkflows(w);
  }

  useEffect(() => {
    refresh().catch((e) => setError(e instanceof Error ? e.message : "Failed to load"));
  }, []);

  async function saveSettings(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    setMsg("");
    try {
      const body: Record<string, unknown> = {
        enabled: Boolean(settings.enabled),
        username: settings.username,
        welcome_text: settings.welcome_text,
        notify_interval_sec: Number(settings.notify_interval_sec || 300),
        support_enabled: Boolean(settings.support_enabled),
        support_chat_id: settings.support_chat_id,
        public_packages: Boolean(settings.public_packages),
        deliver_results_telegram: Boolean(settings.deliver_results_telegram),
        admin_commands_enabled: Boolean(settings.admin_commands_enabled),
      };
      if (token) body.token = token;
      await api("/api/bot/settings", { method: "PUT", body: JSON.stringify(body) });
      setToken("");
      setMsg("Bot settings saved; runtime restarted.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  const enabled = Boolean(settings.enabled);
  const tokenOk = Boolean(settings.token_configured);
  const username = String(settings.username || "");

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>Bot builder</h1>
          <p className="subtitle">
            Connect your Telegram bot (BotFather token), toggle commands, and manage demo workflows — same control
            surface style as Omnidesk bots.
          </p>
        </div>
        <div className="page-actions">
          <button
            className="btn secondary"
            type="button"
            onClick={async () => {
              setMsg("");
              await api("/api/bot/install-demos", { method: "POST" });
              setMsg("Demo commands & workflows installed.");
              await refresh();
            }}
          >
            Import demos
          </button>
          <button
            className="btn secondary"
            type="button"
            onClick={async () => {
              setMsg("");
              await api("/api/bot/restart", { method: "POST" });
              setMsg("Bot runtime restarted.");
            }}
          >
            Restart runtime
          </button>
        </div>
      </div>

      <div className="card hero-bot">
        <div className="bot-avatar" aria-hidden>
          ✈
        </div>
        <div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.45rem", alignItems: "center" }}>
            <strong style={{ fontSize: "1.05rem" }}>{username ? `@${username.replace(/^@/, "")}` : "Telegram bot"}</strong>
            <span className={`badge ${enabled ? "ok" : ""}`}>{enabled ? "Enabled" : "Disabled"}</span>
            <span className={`badge ${tokenOk ? "ok" : "warn"}`}>{tokenOk ? "Token set" : "Token missing"}</span>
            <span className="badge">Telegram</span>
          </div>
          <p className="muted" style={{ margin: "0.45rem 0 0", fontSize: "0.9rem" }}>
            {String(settings.welcome_text || "No welcome text yet — set one in Connection.")}
          </p>
          <div className="item-card-meta" style={{ marginTop: "0.55rem" }}>
            <span className="chip">{commands.filter((c) => c.enabled).length}/{commands.length} commands on</span>
            <span className="chip">{workflows.filter((w) => w.enabled).length}/{workflows.length} workflows on</span>
            <span className="chip">notify {String(settings.notify_interval_sec || 300)}s</span>
          </div>
        </div>
        <label className="check-row" style={{ justifyContent: "flex-end" }}>
          <span className="switch">
            <input
              type="checkbox"
              checked={enabled}
              onChange={async (e) => {
                const next = e.target.checked;
                setSettings({ ...settings, enabled: next });
                try {
                  await api("/api/bot/settings", {
                    method: "PUT",
                    body: JSON.stringify({ enabled: next }),
                  });
                  setMsg(next ? "Bot enabled." : "Bot disabled.");
                  await refresh();
                } catch (err) {
                  setError(err instanceof Error ? err.message : "Toggle failed");
                }
              }}
            />
            <span />
          </span>
          Live
        </label>
      </div>

      <div className="tabs" role="tablist">
        {(
          [
            ["connection", "Connection"],
            ["commands", "Commands"],
            ["workflows", "Workflows"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            className={`tab ${tab === id ? "active" : ""}`}
            aria-selected={tab === id}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {error ? <p className="error">{error}</p> : null}
      {msg ? <p className="muted">{msg}</p> : null}

      {tab === "connection" ? (
        <form className="card stack" onSubmit={saveSettings}>
          <div className="form-grid two">
            <label className="field full">
              BotFather token {tokenOk ? <span className="badge ok">configured</span> : null}
              <input
                className="input"
                type="password"
                placeholder="Paste new token to update"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                autoComplete="off"
              />
            </label>
            <label className="field">
              Username
              <input
                className="input"
                value={String(settings.username || "")}
                onChange={(e) => setSettings({ ...settings, username: e.target.value })}
                placeholder="my_scrapeboard_bot"
              />
            </label>
            <label className="field">
              Notify interval (sec)
              <input
                className="input"
                type="number"
                min={30}
                value={Number(settings.notify_interval_sec || 300)}
                onChange={(e) => setSettings({ ...settings, notify_interval_sec: Number(e.target.value) || 300 })}
              />
            </label>
            <label className="field full">
              Welcome text
              <textarea
                className="input"
                rows={3}
                value={String(settings.welcome_text || "")}
                onChange={(e) => setSettings({ ...settings, welcome_text: e.target.value })}
              />
            </label>
            <label className="field">
              Support chat id
              <input
                className="input"
                value={String(settings.support_chat_id || "")}
                onChange={(e) => setSettings({ ...settings, support_chat_id: e.target.value })}
                placeholder="-100…"
              />
            </label>
            <div className="field" style={{ gap: "0.55rem" }}>
              <span>Options</span>
              <label className="check-row">
                <input
                  type="checkbox"
                  checked={Boolean(settings.support_enabled)}
                  onChange={(e) => setSettings({ ...settings, support_enabled: e.target.checked })}
                />
                Support via Telegram
              </label>
              <label className="check-row">
                <input
                  type="checkbox"
                  checked={Boolean(settings.admin_commands_enabled)}
                  onChange={(e) => setSettings({ ...settings, admin_commands_enabled: e.target.checked })}
                />
                Admin Telegram commands
              </label>
              <label className="check-row">
                <input
                  type="checkbox"
                  checked={Boolean(settings.public_packages)}
                  onChange={(e) => setSettings({ ...settings, public_packages: e.target.checked })}
                />
                Public /packages
              </label>
              <label className="check-row">
                <input
                  type="checkbox"
                  checked={Boolean(settings.deliver_results_telegram)}
                  onChange={(e) => setSettings({ ...settings, deliver_results_telegram: e.target.checked })}
                />
                Deliver results on Telegram
              </label>
            </div>
          </div>
          <div className="page-actions">
            <button className="btn" type="submit" disabled={saving}>
              {saving ? "Saving…" : "Save connection"}
            </button>
          </div>
        </form>
      ) : null}

      {tab === "commands" ? (
        <div className="stack">
          {commands.length === 0 ? (
            <div className="card empty-state">
              <strong>No commands yet</strong>
              <p className="muted">Import demos to load /start, /buy, /run, and more.</p>
            </div>
          ) : (
            <div className="grid-cards">
              {commands.map((c) => (
                <div key={c.id} className="card item-card">
                  <div className="item-card-top">
                    <div>
                      <code>/{c.command.replace(/^\//, "")}</code>
                      <div style={{ marginTop: "0.25rem", fontWeight: 600 }}>{c.title}</div>
                    </div>
                    <label className="switch" title={c.enabled ? "Disable" : "Enable"}>
                      <input
                        type="checkbox"
                        checked={c.enabled}
                        onChange={async () => {
                          await api(`/api/bot/commands/${c.id}`, {
                            method: "PATCH",
                            body: JSON.stringify({ enabled: !c.enabled }),
                          });
                          await refresh();
                        }}
                      />
                      <span />
                    </label>
                  </div>
                  <div className="item-card-meta">
                    <span className="chip">{c.audience}</span>
                    <span className={`badge ${c.enabled ? "ok" : ""}`}>{c.enabled ? "On" : "Off"}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}

      {tab === "workflows" ? (
        <div className="stack">
          {workflows.length === 0 ? (
            <div className="card empty-state">
              <strong>No workflows yet</strong>
              <p className="muted">Import demos for onboarding, payments, jobs, and support flows.</p>
            </div>
          ) : (
            <div className="grid-cards">
              {workflows.map((w) => (
                <div key={w.id} className="card item-card">
                  <div className="item-card-top">
                    <div>
                      <div style={{ fontWeight: 650 }}>{w.name}</div>
                      <p className="muted" style={{ margin: "0.35rem 0 0", fontSize: "0.88rem", lineHeight: 1.4 }}>
                        {w.description || "No description"}
                      </p>
                    </div>
                    <label className="switch">
                      <input
                        type="checkbox"
                        checked={w.enabled}
                        onChange={async () => {
                          await api(`/api/bot/workflows/${w.id}/toggle`, { method: "POST" });
                          await refresh();
                        }}
                      />
                      <span />
                    </label>
                  </div>
                  <div className="item-card-meta">
                    {w.is_demo ? <span className="chip">demo</span> : <span className="chip">custom</span>}
                    <span className={`badge ${w.enabled ? "ok" : ""}`}>{w.enabled ? "Active" : "Off"}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
