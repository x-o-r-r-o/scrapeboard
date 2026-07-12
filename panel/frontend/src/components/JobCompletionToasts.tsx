import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type User } from "../api";

type JobRow = {
  id: number;
  public_id: string;
  status: string;
  rows_saved: number;
  owner_username?: string | null;
};

type Toast = {
  id: string;
  title: string;
  detail: string;
  kind: "ok" | "warn" | "danger";
  jobId: number;
};

const TERMINAL = new Set(["completed", "stopped", "failed"]);
const ACTIVE = new Set(["queued", "running"]);

function kindFor(status: string): Toast["kind"] {
  if (status === "completed") return "ok";
  if (status === "stopped") return "warn";
  return "danger";
}

function labelFor(status: string): string {
  if (status === "completed") return "Job completed";
  if (status === "stopped") return "Job stopped";
  return "Job failed";
}

/**
 * Polls /api/jobs and surfaces an in-panel toast when a job the viewer cares
 * about transitions into a terminal status (admin: any; user: own — already
 * scoped by the API).
 */
export function JobCompletionToasts({ user }: { user: User }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const prev = useRef<Map<number, string>>(new Map());
  const primed = useRef(false);

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      try {
        const rows = await api<JobRow[]>("/api/jobs?limit=100");
        if (cancelled) return;
        const next = new Map<number, string>();
        const fresh: Toast[] = [];
        for (const j of rows) {
          next.set(j.id, j.status);
          if (!primed.current) continue;
          const was = prev.current.get(j.id);
          if (was && ACTIVE.has(was) && TERMINAL.has(j.status)) {
            const who =
              user.role === "admin" && j.owner_username ? ` · ${j.owner_username}` : "";
            fresh.push({
              id: `${j.id}-${j.status}-${Date.now()}`,
              title: `${labelFor(j.status)}${who}`,
              detail: `${j.public_id} · ${j.rows_saved} businesses`,
              kind: kindFor(j.status),
              jobId: j.id,
            });
          }
        }
        prev.current = next;
        primed.current = true;
        if (fresh.length) {
          setToasts((t) => [...fresh, ...t].slice(0, 5));
        }
      } catch {
        /* ignore transient poll errors */
      }
    }

    tick();
    const iv = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, [user.role]);

  useEffect(() => {
    if (!toasts.length) return;
    const id = toasts[0].id;
    const t = setTimeout(() => {
      setToasts((list) => list.filter((x) => x.id !== id));
    }, 8000);
    return () => clearTimeout(t);
  }, [toasts[0]?.id]);

  if (!toasts.length) return null;

  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind}`}>
          <div className="toast-body">
            <strong>{t.title}</strong>
            <span className="muted">{t.detail}</span>
            <Link to="/app/jobs" className="toast-link">
              View jobs
            </Link>
          </div>
          <button
            type="button"
            className="toast-dismiss"
            aria-label="Dismiss"
            onClick={() => setToasts((list) => list.filter((x) => x.id !== t.id))}
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
