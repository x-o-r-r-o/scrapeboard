import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";

type BotSettings = Record<string, unknown>;
type Command = {
  id: number;
  key: string;
  command: string;
  title: string;
  description: string;
  response_text: string;
  enabled: boolean;
  audience: string;
  sort_order: number;
  is_builtin?: boolean;
  handler?: "builtin" | "static";
};
type WorkflowStep = Record<string, string>;
type Workflow = {
  id: number;
  key: string;
  name: string;
  description: string;
  enabled: boolean;
  is_demo: boolean;
  sort_order: number;
  definition: {
    trigger?: string;
    steps?: WorkflowStep[];
    [k: string]: unknown;
  };
};

type WorkflowDraft = {
  key: string;
  name: string;
  description: string;
  enabled: boolean;
  sort_order: number;
  trigger: string;
  steps: WorkflowStep[];
};

type CommandDraft = {
  key: string;
  command: string;
  title: string;
  description: string;
  response_text: string;
  enabled: boolean;
  audience: string;
  sort_order: number;
};

type Tab = "connection" | "commands" | "workflows";

const AUDIENCES = ["everyone", "users", "subscribers", "admins"] as const;
const PROTECTED_COMMAND_KEYS = new Set(["start", "stop", "help"]);

const EMPTY_DRAFT: WorkflowDraft = {
  key: "",
  name: "",
  description: "",
  enabled: true,
  sort_order: 100,
  trigger: "command:/start",
  steps: [{ action: "" }],
};

const EMPTY_CMD_DRAFT: CommandDraft = {
  key: "",
  command: "",
  title: "",
  description: "",
  response_text: "",
  enabled: true,
  audience: "everyone",
  sort_order: 100,
};

const STEP_KINDS = ["action", "say", "if", "wait"] as const;
type StepKind = (typeof STEP_KINDS)[number];

function stepKind(step: WorkflowStep): StepKind {
  for (const k of STEP_KINDS) {
    if (k in step) return k;
  }
  return "action";
}

function stepValue(step: WorkflowStep): string {
  const k = stepKind(step);
  return String(step[k] ?? "");
}

function toDraft(w: Workflow): WorkflowDraft {
  const steps = Array.isArray(w.definition?.steps) && w.definition.steps.length
    ? w.definition.steps.map((s) => ({ ...s }))
    : [{ action: "" }];
  return {
    key: w.key,
    name: w.name,
    description: w.description || "",
    enabled: w.enabled,
    sort_order: w.sort_order ?? 0,
    trigger: String(w.definition?.trigger || "command:/start"),
    steps,
  };
}

function toCommandDraft(c: Command): CommandDraft {
  return {
    key: c.key,
    command: c.command,
    title: c.title || "",
    description: c.description || "",
    response_text: c.response_text || "",
    enabled: c.enabled,
    audience: c.audience || "everyone",
    sort_order: c.sort_order ?? 0,
  };
}

function draftDefinition(d: WorkflowDraft) {
  return {
    trigger: d.trigger.trim() || "command:/start",
    steps: d.steps
      .map((s) => {
        const kind = stepKind(s);
        const val = String(s[kind] ?? "").trim();
        if (!val) return null;
        const out: WorkflowStep = { [kind]: val };
        // preserve companion "say" when kind is "if"
        if (kind === "if" && s.say) out.say = String(s.say);
        return out;
      })
      .filter(Boolean) as WorkflowStep[],
  };
}

function updateStep(draft: WorkflowDraft, index: number, patch: { kind?: StepKind; value?: string; say?: string }) {
  const steps = draft.steps.map((s, i) => {
    if (i !== index) return s;
    if (patch.kind) {
      const next: WorkflowStep = { [patch.kind]: patch.value ?? stepValue(s) };
      if (patch.kind === "if") {
        const say = patch.say !== undefined ? patch.say : s.say;
        if (say) next.say = String(say);
      }
      return next;
    }
    const kind = stepKind(s);
    const next: WorkflowStep = { [kind]: patch.value !== undefined ? patch.value : stepValue(s) };
    if (kind === "if" || patch.say !== undefined) {
      const say = patch.say !== undefined ? patch.say : s.say;
      if (say) next.say = String(say);
    }
    return next;
  });
  return { ...draft, steps };
}

function StepEditor({
  draft,
  onChange,
}: {
  draft: WorkflowDraft;
  onChange: (d: WorkflowDraft) => void;
}) {
  return (
    <div className="stack" style={{ gap: "0.65rem" }}>
      <label className="field">
        Trigger
        <input
          className="input"
          value={draft.trigger}
          onChange={(e) => onChange({ ...draft, trigger: e.target.value })}
          placeholder="command:/start · cron:daily · event:worker_offline"
        />
      </label>
      <div>
        <div className="field" style={{ marginBottom: "0.4rem" }}>
          Steps
        </div>
        <p className="muted" style={{ margin: "0 0 0.55rem", fontSize: "0.85rem" }}>
          Each step is an action, message (say), condition (if), or wait — matching the Telegram bot workflow model.
        </p>
        <div className="stack" style={{ gap: "0.5rem" }}>
          {draft.steps.map((step, i) => {
            const kind = stepKind(step);
            return (
              <div key={i} className="card" style={{ padding: "0.75rem", display: "grid", gap: "0.45rem", gridTemplateColumns: "120px 1fr auto" }}>
                <select
                  className="input"
                  value={kind}
                  onChange={(e) => onChange(updateStep(draft, i, { kind: e.target.value as StepKind }))}
                >
                  {STEP_KINDS.map((k) => (
                    <option key={k} value={k}>
                      {k}
                    </option>
                  ))}
                </select>
                <input
                  className="input"
                  value={stepValue(step)}
                  onChange={(e) => onChange(updateStep(draft, i, { value: e.target.value }))}
                  placeholder={
                    kind === "action"
                      ? "create_order"
                      : kind === "say"
                        ? "Message text"
                        : kind === "if"
                          ? "no_subscription"
                          : "command:/paid"
                  }
                />
                <button
                  className="btn secondary sm"
                  type="button"
                  onClick={() => onChange({ ...draft, steps: draft.steps.filter((_, j) => j !== i) })}
                  disabled={draft.steps.length <= 1}
                >
                  Remove
                </button>
                {kind === "if" ? (
                  <label className="field" style={{ gridColumn: "1 / -1" }}>
                    Message when condition matches (say)
                    <input
                      className="input"
                      value={String(step.say || "")}
                      onChange={(e) => onChange(updateStep(draft, i, { say: e.target.value }))}
                      placeholder="Optional reply text"
                    />
                  </label>
                ) : null}
              </div>
            );
          })}
        </div>
        <div className="page-actions" style={{ marginTop: "0.55rem" }}>
          <button
            className="btn secondary sm"
            type="button"
            onClick={() => onChange({ ...draft, steps: [...draft.steps, { action: "" }] })}
          >
            Add step
          </button>
          <button
            className="btn secondary sm"
            type="button"
            onClick={() => {
              const steps = [...draft.steps];
              if (steps.length < 2) return;
              const last = steps.pop()!;
              steps.unshift(last);
              onChange({ ...draft, steps });
            }}
          >
            Rotate order
          </button>
        </div>
      </div>
    </div>
  );
}

function CommandFormFields({
  draft,
  onChange,
  lockKey,
  lockCommand,
}: {
  draft: CommandDraft;
  onChange: (d: CommandDraft) => void;
  lockKey?: boolean;
  lockCommand?: boolean;
}) {
  return (
    <div className="form-grid two">
      <label className="field">
        Key
        <input
          className="input"
          value={draft.key}
          onChange={(e) => onChange({ ...draft, key: e.target.value })}
          placeholder="hello (optional — derived from command)"
          disabled={lockKey}
        />
      </label>
      <label className="field">
        Command
        <input
          className="input"
          required
          value={draft.command}
          onChange={(e) => onChange({ ...draft, command: e.target.value })}
          placeholder="/hello"
          disabled={lockCommand}
        />
      </label>
      <label className="field">
        Title
        <input
          className="input"
          required
          value={draft.title}
          onChange={(e) => onChange({ ...draft, title: e.target.value })}
        />
      </label>
      <label className="field">
        Audience
        <select
          className="input"
          value={draft.audience}
          onChange={(e) => onChange({ ...draft, audience: e.target.value })}
        >
          {AUDIENCES.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
      </label>
      <label className="field full">
        Description
        <textarea
          className="input"
          rows={2}
          value={draft.description}
          onChange={(e) => onChange({ ...draft, description: e.target.value })}
          placeholder="Shown in Bot Builder and help context"
        />
      </label>
      <label className="field full">
        Response text
        <textarea
          className="input"
          rows={3}
          value={draft.response_text}
          onChange={(e) => onChange({ ...draft, response_text: e.target.value })}
          placeholder={
            lockCommand
              ? "Optional fallback / usage hint (built-in handler still runs)"
              : "Message Telegram sends for this custom command"
          }
        />
      </label>
      <label className="field">
        Sort order
        <input
          className="input"
          type="number"
          value={draft.sort_order}
          onChange={(e) => onChange({ ...draft, sort_order: Number(e.target.value) || 0 })}
        />
      </label>
      <label className="check-row" style={{ alignItems: "center" }}>
        <input
          type="checkbox"
          checked={draft.enabled}
          onChange={(e) => onChange({ ...draft, enabled: e.target.checked })}
        />
        Enabled
      </label>
    </div>
  );
}

export default function BotBuilderPage() {
  const [settings, setSettings] = useState<BotSettings>({});
  const [token, setToken] = useState("");
  const [commands, setCommands] = useState<Command[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("connection");
  const [saving, setSaving] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createDraft, setCreateDraft] = useState<WorkflowDraft>(EMPTY_DRAFT);
  const [editId, setEditId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<WorkflowDraft | null>(null);
  const [cmdCreating, setCmdCreating] = useState(false);
  const [cmdCreateDraft, setCmdCreateDraft] = useState<CommandDraft>(EMPTY_CMD_DRAFT);
  const [cmdEditId, setCmdEditId] = useState<number | null>(null);
  const [cmdEditDraft, setCmdEditDraft] = useState<CommandDraft | null>(null);

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

  async function createWorkflow(e: FormEvent) {
    e.preventDefault();
    setError("");
    setMsg("");
    try {
      await api("/api/bot/workflows", {
        method: "POST",
        body: JSON.stringify({
          key: createDraft.key,
          name: createDraft.name,
          description: createDraft.description,
          enabled: createDraft.enabled,
          sort_order: createDraft.sort_order,
          definition: draftDefinition(createDraft),
        }),
      });
      setCreateDraft(EMPTY_DRAFT);
      setCreating(false);
      setMsg("Workflow created.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
    }
  }

  async function saveWorkflow(e: FormEvent) {
    e.preventDefault();
    if (editId == null || !editDraft) return;
    setError("");
    setMsg("");
    try {
      await api(`/api/bot/workflows/${editId}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: editDraft.name,
          description: editDraft.description,
          enabled: editDraft.enabled,
          sort_order: editDraft.sort_order,
          definition: draftDefinition(editDraft),
        }),
      });
      setEditId(null);
      setEditDraft(null);
      setMsg("Workflow updated.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  }

  async function createCommand(e: FormEvent) {
    e.preventDefault();
    setError("");
    setMsg("");
    try {
      await api("/api/bot/commands", {
        method: "POST",
        body: JSON.stringify({
          key: cmdCreateDraft.key,
          command: cmdCreateDraft.command,
          title: cmdCreateDraft.title,
          description: cmdCreateDraft.description,
          response_text: cmdCreateDraft.response_text,
          enabled: cmdCreateDraft.enabled,
          audience: cmdCreateDraft.audience,
          sort_order: cmdCreateDraft.sort_order,
        }),
      });
      setCmdCreateDraft(EMPTY_CMD_DRAFT);
      setCmdCreating(false);
      setMsg("Command created. Runtime picks up enabled commands on the next message.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
    }
  }

  async function saveCommand(e: FormEvent) {
    e.preventDefault();
    if (cmdEditId == null || !cmdEditDraft) return;
    setError("");
    setMsg("");
    try {
      const body: Record<string, unknown> = {
        title: cmdEditDraft.title,
        description: cmdEditDraft.description,
        response_text: cmdEditDraft.response_text,
        enabled: cmdEditDraft.enabled,
        audience: cmdEditDraft.audience,
        sort_order: cmdEditDraft.sort_order,
      };
      const editing = commands.find((c) => c.id === cmdEditId);
      if (editing && !editing.is_builtin) {
        body.command = cmdEditDraft.command;
      }
      await api(`/api/bot/commands/${cmdEditId}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      setCmdEditId(null);
      setCmdEditDraft(null);
      setMsg("Command updated.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
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
            Connect your Telegram bot (BotFather token), manage commands, and edit workflows — same control surface
            style as Omnidesk bots.
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
          <div className="page-actions">
            <button
              className="btn"
              type="button"
              onClick={() => {
                setCmdCreating(true);
                setCmdCreateDraft({
                  ...EMPTY_CMD_DRAFT,
                  sort_order: (commands.reduce((m, c) => Math.max(m, c.sort_order || 0), 0) || 0) + 10,
                });
                setCmdEditId(null);
                setCmdEditDraft(null);
              }}
            >
              Add command
            </button>
          </div>

          {cmdCreating ? (
            <form className="card stack" onSubmit={createCommand}>
              <h3 style={{ margin: 0 }}>New command</h3>
              <p className="muted" style={{ margin: 0, fontSize: "0.88rem" }}>
                Custom commands reply with <strong>response text</strong>. Built-in handlers (/start, /run, …) stay
                code-backed — add those via Import demos if missing.
              </p>
              <CommandFormFields draft={cmdCreateDraft} onChange={setCmdCreateDraft} />
              <div className="page-actions">
                <button className="btn" type="submit">
                  Create command
                </button>
                <button className="btn secondary" type="button" onClick={() => setCmdCreating(false)}>
                  Cancel
                </button>
              </div>
            </form>
          ) : null}

          {cmdEditId != null && cmdEditDraft ? (
            <form className="card stack" onSubmit={saveCommand}>
              <h3 style={{ margin: 0 }}>
                Edit command · <code>{cmdEditDraft.key}</code>
              </h3>
              {commands.find((c) => c.id === cmdEditId)?.is_builtin ? (
                <p className="muted" style={{ margin: 0, fontSize: "0.88rem" }}>
                  Built-in handler — you can edit title, description, audience, response hint, and enable/disable. The
                  slash trigger is fixed.
                </p>
              ) : null}
              <CommandFormFields
                draft={cmdEditDraft}
                onChange={setCmdEditDraft}
                lockKey
                lockCommand={Boolean(commands.find((c) => c.id === cmdEditId)?.is_builtin)}
              />
              <div className="page-actions">
                <button className="btn" type="submit">
                  Save command
                </button>
                <button
                  className="btn secondary"
                  type="button"
                  onClick={() => {
                    setCmdEditId(null);
                    setCmdEditDraft(null);
                  }}
                >
                  Cancel
                </button>
              </div>
            </form>
          ) : null}

          {commands.length === 0 && !cmdCreating ? (
            <div className="card empty-state">
              <strong>No commands yet</strong>
              <p className="muted">Import demos to load /start, /buy, /run, and more — or add a custom reply command.</p>
            </div>
          ) : (
            <div className="grid-cards">
              {commands.map((c) => {
                const protectedCmd = PROTECTED_COMMAND_KEYS.has(c.key);
                return (
                  <div key={c.id} className="card item-card">
                    <div className="item-card-top">
                      <div>
                        <code>/{c.command.replace(/^\//, "")}</code>
                        <div style={{ marginTop: "0.25rem", fontWeight: 600 }}>{c.title}</div>
                        {c.description ? (
                          <p className="muted" style={{ margin: "0.35rem 0 0", fontSize: "0.88rem", lineHeight: 1.4 }}>
                            {c.description}
                          </p>
                        ) : null}
                      </div>
                      <label className="switch" title={c.enabled ? "Disable" : "Enable"}>
                        <input
                          type="checkbox"
                          checked={c.enabled}
                          onChange={async () => {
                            await api(`/api/bot/commands/${c.id}/toggle`, { method: "POST" });
                            await refresh();
                          }}
                        />
                        <span />
                      </label>
                    </div>
                    <div className="item-card-meta">
                      <span className="chip">{c.audience}</span>
                      <span className="chip">order {c.sort_order ?? 0}</span>
                      {c.is_builtin ? <span className="chip">built-in</span> : <span className="chip">custom</span>}
                      <span className="chip">{c.handler === "builtin" ? "code handler" : "static reply"}</span>
                      <span className={`badge ${c.enabled ? "ok" : ""}`}>{c.enabled ? "On" : "Off"}</span>
                    </div>
                    <div className="page-actions" style={{ marginTop: "0.65rem" }}>
                      <button
                        className="btn secondary sm"
                        type="button"
                        onClick={() => {
                          setCmdCreating(false);
                          setCmdEditId(c.id);
                          setCmdEditDraft(toCommandDraft(c));
                        }}
                      >
                        Edit
                      </button>
                      <button
                        className="btn secondary sm"
                        type="button"
                        disabled={protectedCmd}
                        onClick={async () => {
                          if (protectedCmd) return;
                          const warn = c.is_builtin
                            ? `Delete built-in “${c.title}” (${c.command})? Import demos can restore it later.`
                            : `Delete command “${c.title}” (${c.command})?`;
                          if (!confirm(warn)) return;
                          setError("");
                          try {
                            await api(`/api/bot/commands/${c.id}`, { method: "DELETE" });
                            if (cmdEditId === c.id) {
                              setCmdEditId(null);
                              setCmdEditDraft(null);
                            }
                            setMsg("Command deleted.");
                            await refresh();
                          } catch (err) {
                            setError(err instanceof Error ? err.message : "Delete failed");
                          }
                        }}
                        title={
                          protectedCmd
                            ? "Critical built-in — disable or edit settings instead of delete"
                            : "Delete command"
                        }
                      >
                        {protectedCmd ? "Protected" : "Delete"}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      ) : null}

      {tab === "workflows" ? (
        <div className="stack">
          <div className="page-actions">
            <button
              className="btn"
              type="button"
              onClick={() => {
                setCreating(true);
                setCreateDraft({
                  ...EMPTY_DRAFT,
                  sort_order: (workflows.reduce((m, w) => Math.max(m, w.sort_order || 0), 0) || 0) + 10,
                });
                setEditId(null);
                setEditDraft(null);
              }}
            >
              Add workflow
            </button>
          </div>

          {creating ? (
            <form className="card stack" onSubmit={createWorkflow}>
              <h3 style={{ margin: 0 }}>New workflow</h3>
              <div className="form-grid two">
                <label className="field">
                  Key
                  <input
                    className="input"
                    required
                    value={createDraft.key}
                    onChange={(e) => setCreateDraft({ ...createDraft, key: e.target.value })}
                    placeholder="onboarding_custom"
                  />
                </label>
                <label className="field">
                  Name
                  <input
                    className="input"
                    required
                    value={createDraft.name}
                    onChange={(e) => setCreateDraft({ ...createDraft, name: e.target.value })}
                  />
                </label>
                <label className="field full">
                  Description
                  <textarea
                    className="input"
                    rows={2}
                    value={createDraft.description}
                    onChange={(e) => setCreateDraft({ ...createDraft, description: e.target.value })}
                  />
                </label>
                <label className="field">
                  Sort order
                  <input
                    className="input"
                    type="number"
                    value={createDraft.sort_order}
                    onChange={(e) => setCreateDraft({ ...createDraft, sort_order: Number(e.target.value) || 0 })}
                  />
                </label>
                <label className="check-row" style={{ alignItems: "center" }}>
                  <input
                    type="checkbox"
                    checked={createDraft.enabled}
                    onChange={(e) => setCreateDraft({ ...createDraft, enabled: e.target.checked })}
                  />
                  Enabled
                </label>
              </div>
              <StepEditor draft={createDraft} onChange={setCreateDraft} />
              <div className="page-actions">
                <button className="btn" type="submit">
                  Create workflow
                </button>
                <button className="btn secondary" type="button" onClick={() => setCreating(false)}>
                  Cancel
                </button>
              </div>
            </form>
          ) : null}

          {editId != null && editDraft ? (
            <form className="card stack" onSubmit={saveWorkflow}>
              <h3 style={{ margin: 0 }}>Edit workflow · <code>{editDraft.key}</code></h3>
              <div className="form-grid two">
                <label className="field">
                  Name
                  <input
                    className="input"
                    required
                    value={editDraft.name}
                    onChange={(e) => setEditDraft({ ...editDraft, name: e.target.value })}
                  />
                </label>
                <label className="field">
                  Sort order
                  <input
                    className="input"
                    type="number"
                    value={editDraft.sort_order}
                    onChange={(e) => setEditDraft({ ...editDraft, sort_order: Number(e.target.value) || 0 })}
                  />
                </label>
                <label className="field full">
                  Description
                  <textarea
                    className="input"
                    rows={2}
                    value={editDraft.description}
                    onChange={(e) => setEditDraft({ ...editDraft, description: e.target.value })}
                  />
                </label>
                <label className="check-row" style={{ alignItems: "center" }}>
                  <input
                    type="checkbox"
                    checked={editDraft.enabled}
                    onChange={(e) => setEditDraft({ ...editDraft, enabled: e.target.checked })}
                  />
                  Enabled
                </label>
              </div>
              <StepEditor draft={editDraft} onChange={setEditDraft} />
              <div className="page-actions">
                <button className="btn" type="submit">
                  Save workflow
                </button>
                <button
                  className="btn secondary"
                  type="button"
                  onClick={() => {
                    setEditId(null);
                    setEditDraft(null);
                  }}
                >
                  Cancel
                </button>
              </div>
            </form>
          ) : null}

          {workflows.length === 0 && !creating ? (
            <div className="card empty-state">
              <strong>No workflows yet</strong>
              <p className="muted">Add a custom workflow or import demos for onboarding, payments, jobs, and support flows.</p>
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
                    <span className="chip">{w.key}</span>
                    {w.is_demo ? <span className="chip">demo</span> : <span className="chip">custom</span>}
                    <span className="chip">order {w.sort_order ?? 0}</span>
                    <span className="chip">{String(w.definition?.trigger || "—")}</span>
                    <span className="chip">{Array.isArray(w.definition?.steps) ? w.definition.steps.length : 0} steps</span>
                    <span className={`badge ${w.enabled ? "ok" : ""}`}>{w.enabled ? "Active" : "Off"}</span>
                  </div>
                  <div className="page-actions" style={{ marginTop: "0.65rem" }}>
                    <button
                      className="btn secondary sm"
                      type="button"
                      onClick={() => {
                        setCreating(false);
                        setEditId(w.id);
                        setEditDraft(toDraft(w));
                      }}
                    >
                      Edit
                    </button>
                    <button
                      className="btn secondary sm"
                      type="button"
                      onClick={async () => {
                        if (!confirm(`Delete workflow “${w.name}”?`)) return;
                        setError("");
                        try {
                          await api(`/api/bot/workflows/${w.id}`, { method: "DELETE" });
                          if (editId === w.id) {
                            setEditId(null);
                            setEditDraft(null);
                          }
                          setMsg("Workflow deleted.");
                          await refresh();
                        } catch (err) {
                          setError(err instanceof Error ? err.message : "Delete failed");
                        }
                      }}
                    >
                      Delete
                    </button>
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
