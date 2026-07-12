#!/usr/bin/env python3
"""
Worker-only agent for the GMaps Scraper Control Panel.

Connects to the panel Worker API, leases job chunks, scrapes via gmaps_scraper,
uploads result ZIPs, and acknowledges. No Telegram, billing, or user management.

Usage:
  python agent.py --panel-url http://127.0.0.1:8000 --token YOUR_WORKER_TOKEN
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VERSION = "0.2.0"


def _cpu_mem():
    try:
        import psutil
        return psutil.cpu_percent(interval=None), psutil.virtual_memory().percent
    except Exception:
        return 0.0, 0.0


class PanelClient:
    def __init__(self, base: str, token: str):
        self.base = base.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}

    def heartbeat(self):
        cpu, mem = _cpu_mem()
        r = requests.post(
            f"{self.base}/api/worker-api/heartbeat",
            json={"cpu": cpu, "mem": mem, "version": VERSION},
            headers=self.headers,
            timeout=20,
        )
        r.raise_for_status()
        return r.json()

    def lease(self):
        r = requests.post(f"{self.base}/api/worker-api/lease", headers=self.headers, timeout=30)
        r.raise_for_status()
        return r.json()

    def upload(self, job_id: str, chunk_id: int, zip_path: Path):
        with open(zip_path, "rb") as fh:
            r = requests.post(
                f"{self.base}/api/worker-api/upload",
                params={"job_id": job_id, "chunk_id": chunk_id},
                headers=self.headers,
                files={"file": (zip_path.name, fh, "application/zip")},
                timeout=300,
            )
        r.raise_for_status()
        return r.json()

    def ack(self, job_id: str, chunk_id: int, rows: int):
        r = requests.post(
            f"{self.base}/api/worker-api/ack",
            json={"job_id": job_id, "chunk_id": chunk_id, "rows": rows},
            headers=self.headers,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()


def _zip_dir(src: Path, dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in src.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(src).as_posix())
    return dest


def run_chunk(job: dict, chunk: dict, work_dir: Path) -> tuple[int, Path | None]:
    import gmaps_scraper as gs

    keywords = job.get("keywords") or []
    locations = job.get("locations") or []
    settings = job.get("settings") or {}
    proxies_text = job.get("proxies_text") or ""

    proxies_path = work_dir / "proxies.txt"
    proxies_path.write_text(proxies_text, encoding="utf-8")

    args = gs.build_args_from_settings(settings)
    args.proxies = str(proxies_path)
    args.no_proxy = not bool(proxies_text.strip())
    args.threads = max(1, int(settings.get("threads") or 1))

    out_dir = work_dir / "out" / str(chunk["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    ts = str(job.get("ts") or "run")

    rows, _failed = gs.execute_index_batch(
        args,
        keywords,
        [gs.format_location(l) for l in locations],
        int(chunk["start"]),
        int(chunk["end"]),
        str(out_dir),
        ts,
        stop,
    )
    zip_path = work_dir / f"chunk_{chunk['id']}.zip"
    if any(out_dir.rglob("*.csv")):
        _zip_dir(out_dir, zip_path)
        return int(rows or 0), zip_path
    return int(rows or 0), None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="GMaps panel worker agent")
    p.add_argument("--panel-url", required=True, help="Control panel base URL")
    p.add_argument("--token", required=True, help="Worker token from the control panel")
    p.add_argument("--work-dir", default="", help="Scratch directory for chunk work")
    args = p.parse_args(argv)

    client = PanelClient(args.panel_url, args.token)
    work_root = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="gmaps_worker_"))
    work_root.mkdir(parents=True, exist_ok=True)
    print(f"[worker] v{VERSION} → {args.panel_url}  work={work_root}", flush=True)

    while True:
        try:
            hb = client.heartbeat()
            if not hb.get("enabled", True):
                print("[worker] disabled by panel; sleeping", flush=True)
                time.sleep(10)
                continue
            if hb.get("drain"):
                print("[worker] draining; no new leases", flush=True)
                time.sleep(5)
                continue
            lease = client.lease()
            chunk = lease.get("chunk")
            if not chunk:
                time.sleep(2)
                continue
            job = lease["job"]
            print(
                f"[worker] leased job={job['job_id']} chunk={chunk['id']} "
                f"[{chunk['start']}:{chunk['end']}]",
                flush=True,
            )
            try:
                rows, zip_path = run_chunk(job, chunk, work_root / job["job_id"])
                if zip_path and zip_path.exists():
                    client.upload(job["job_id"], chunk["id"], zip_path)
                    print(f"[worker] uploaded chunk={chunk['id']}", flush=True)
            except Exception as e:
                print(f"[worker] chunk error: {e}", flush=True)
                rows = 0
            client.ack(job["job_id"], chunk["id"], rows)
            print(f"[worker] ack chunk={chunk['id']} rows={rows}", flush=True)
        except KeyboardInterrupt:
            print("[worker] stop", flush=True)
            return 0
        except Exception as e:
            print(f"[worker] loop error: {e}; retry in 5s", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
