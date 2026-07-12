import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";

type TicketListItem = {
  id: number;
  user_id: number | null;
  telegram_id: string;
  message: string;
  status: string;
  created_at: string;
  updated_at: string | null;
  closed_at: string | null;
  message_count: number;
};

type TicketMessage = {
  id: number;
  ticket_id: number;
  sender: string;
  admin_user_id: number | null;
  body: string;
  created_at: string;
};

type TicketDetail = {
  id: number;
  user_id: number | null;
  telegram_id: string;
  message: string;
  status: string;
  created_at: string;
  updated_at: string | null;
  closed_at: string | null;
  closed_by_id: number | null;
  messages: TicketMessage[];
};

type StatusFilter = "open" | "closed" | "all";

function preview(text: string, n = 80) {
  const one = (text || "").replace(/\s+/g, " ").trim();
  return one.length > n ? `${one.slice(0, n)}…` : one;
}

export function SupportAdminPage() {
  const [status, setStatus] = useState<StatusFilter>("open");
  const [tickets, setTickets] = useState<TicketListItem[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<TicketDetail | null>(null);
  const [reply, setReply] = useState("");
  const [closeReason, setCloseReason] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function loadList(filter: StatusFilter = status) {
    setError("");
    const rows = await api<TicketListItem[]>(`/api/support/tickets?status=${filter}&limit=100`);
    setTickets(rows);
  }

  async function loadDetail(id: number) {
    setError("");
    const t = await api<TicketDetail>(`/api/support/tickets/${id}`);
    setDetail(t);
    setSelectedId(id);
  }

  useEffect(() => {
    loadList("open").catch((e) => setError(e instanceof Error ? e.message : "Failed to load"));
  }, []);

  async function changeFilter(next: StatusFilter) {
    setStatus(next);
    setMsg("");
    try {
      await loadList(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
  }

  async function selectTicket(id: number) {
    setMsg("");
    setReply("");
    setCloseReason("");
    try {
      await loadDetail(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load ticket");
    }
  }

  async function sendReply(e: FormEvent) {
    e.preventDefault();
    if (!selectedId || !reply.trim()) return;
    setBusy(true);
    setError("");
    setMsg("");
    try {
      const t = await api<TicketDetail>(`/api/support/tickets/${selectedId}/reply`, {
        method: "POST",
        body: JSON.stringify({ message: reply.trim() }),
      });
      setDetail(t);
      setReply("");
      setMsg("Reply sent — user notified on Telegram.");
      await loadList(status);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reply failed");
    } finally {
      setBusy(false);
    }
  }

  async function closeTicket() {
    if (!selectedId) return;
    setBusy(true);
    setError("");
    setMsg("");
    try {
      const t = await api<TicketDetail>(`/api/support/tickets/${selectedId}/close`, {
        method: "POST",
        body: JSON.stringify({ reason: closeReason.trim() }),
      });
      setDetail(t);
      setCloseReason("");
      setMsg("Ticket closed — user notified on Telegram.");
      await loadList(status);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Close failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h2 style={{ margin: 0 }}>Support tickets</h2>
          <p className="muted" style={{ margin: "0.35rem 0 0", fontSize: "0.9rem" }}>
            Users open tickets with <code>/support</code> in Telegram. Replies and closes notify them instantly.
          </p>
        </div>
      </div>

      {error ? <p className="error">{error}</p> : null}
      {msg ? <p className="muted">{msg}</p> : null}

      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
        {(["open", "closed", "all"] as StatusFilter[]).map((s) => (
          <button
            key={s}
            type="button"
            className={status === s ? "btn sm" : "btn secondary sm"}
            onClick={() => changeFilter(s)}
          >
            {s}
          </button>
        ))}
        <button
          type="button"
          className="btn secondary sm"
          onClick={() => loadList(status).catch((e) => setError(e instanceof Error ? e.message : "Refresh failed"))}
        >
          Refresh
        </button>
      </div>

      <div className="form-grid two" style={{ alignItems: "start" }}>
        <div className="card">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Status</th>
                  <th>Telegram</th>
                  <th>Message</th>
                  <th>Replies</th>
                </tr>
              </thead>
              <tbody>
                {tickets.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="muted">
                      No tickets.
                    </td>
                  </tr>
                ) : (
                  tickets.map((t) => (
                    <tr
                      key={t.id}
                      style={{
                        cursor: "pointer",
                        background: selectedId === t.id ? "var(--surface-2, #f3f4f6)" : undefined,
                      }}
                      onClick={() => selectTicket(t.id)}
                    >
                      <td>{t.id}</td>
                      <td>{t.status}</td>
                      <td>{t.telegram_id}</td>
                      <td>{preview(t.message)}</td>
                      <td>{t.message_count}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card stack">
          {!detail ? (
            <p className="muted">Select a ticket to view the thread, reply, or close.</p>
          ) : (
            <>
              <div>
                <strong>
                  Ticket #{detail.id} · {detail.status}
                </strong>
                <div className="muted" style={{ fontSize: "0.85rem", marginTop: "0.25rem" }}>
                  tg={detail.telegram_id}
                  {detail.user_id != null ? ` · user_id=${detail.user_id}` : ""} · opened{" "}
                  {new Date(detail.created_at).toLocaleString()}
                  {detail.closed_at ? ` · closed ${new Date(detail.closed_at).toLocaleString()}` : ""}
                </div>
              </div>

              <div
                className="stack"
                style={{
                  maxHeight: "22rem",
                  overflow: "auto",
                  gap: "0.65rem",
                  padding: "0.5rem 0",
                  borderTop: "1px solid var(--border, #e5e7eb)",
                  borderBottom: "1px solid var(--border, #e5e7eb)",
                }}
              >
                <div>
                  <div className="muted" style={{ fontSize: "0.75rem" }}>
                    user · {new Date(detail.created_at).toLocaleString()}
                  </div>
                  <div style={{ whiteSpace: "pre-wrap" }}>{detail.message}</div>
                </div>
                {detail.messages.map((m) => (
                  <div key={m.id}>
                    <div className="muted" style={{ fontSize: "0.75rem" }}>
                      {m.sender}
                      {m.admin_user_id != null ? ` #${m.admin_user_id}` : ""} ·{" "}
                      {new Date(m.created_at).toLocaleString()}
                    </div>
                    <div style={{ whiteSpace: "pre-wrap" }}>{m.body}</div>
                  </div>
                ))}
              </div>

              {detail.status === "open" ? (
                <>
                  <form className="stack" onSubmit={sendReply}>
                    <label className="field">
                      Reply (sent instantly to the user on Telegram)
                      <textarea
                        className="input"
                        rows={4}
                        value={reply}
                        onChange={(e) => setReply(e.target.value)}
                        placeholder="Type your reply…"
                        required
                      />
                    </label>
                    <button className="btn" type="submit" disabled={busy || !reply.trim()}>
                      Send reply
                    </button>
                  </form>
                  <label className="field">
                    Close reason (optional)
                    <input
                      className="input"
                      value={closeReason}
                      onChange={(e) => setCloseReason(e.target.value)}
                      placeholder="Resolved / duplicate / …"
                    />
                  </label>
                  <button className="btn secondary" type="button" disabled={busy} onClick={closeTicket}>
                    Close ticket
                  </button>
                </>
              ) : (
                <p className="muted">This ticket is closed. The user can open a new one with /support.</p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
