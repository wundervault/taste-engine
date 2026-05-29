#!/usr/bin/env python3
"""Resume polling + downloading a Bright Data snapshot that was triggered
earlier but the original poller died (timeout, network hiccup, etc.).

Usage:
    python scripts/resume_snapshot.py <snapshot_id>
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / ".env")
API_TOKEN = os.environ["API_TOKEN"]
OUT = ROOT / "data" / "maps_reviews_raw"
OUT.mkdir(parents=True, exist_ok=True)
BD_HOST = "api.brightdata.com"

POLL_INTERVAL = 30
POLL_TIMEOUT = 60 * 90  # 90 min


def resolve_ip():
    r = subprocess.run(["dig", "+short", "@1.1.1.1", BD_HOST],
                       check=True, capture_output=True, text=True)
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()][0]


BD_IP = resolve_ip()


def get_status_or_download(sid: str, out_path: Path) -> str | None:
    """Returns 'building' / 'ready' / 'failed' / 'unknown', and writes file if ready.
    The same endpoint returns either a status JSON OR the full JSONL stream when ready."""
    url = f"https://{BD_HOST}/datasets/v3/snapshot/{sid}?format=json"
    cmd = [
        "curl", "-sS",
        "--resolve", f"{BD_HOST}:443:{BD_IP}",
        "-H", f"Authorization: Bearer {API_TOKEN}",
        "-H", "Accept: application/json",
        "-o", str(out_path),
        "-w", "%{http_code}",
        "--max-time", "3600",  # 1 hour for downloads
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return "unknown"
    http_status = proc.stdout.strip()
    # Inspect body to decide if it's a status JSON or the real data
    size = out_path.stat().st_size
    if size < 1000:
        try:
            body = json.loads(out_path.read_text())
            if isinstance(body, dict):
                st = body.get("status")
                if st == "building": return "building"
                if st == "failed":   return "failed"
                if st == "ready":    return "ready"
        except Exception:
            pass
    # Larger file → assume data payload
    return "ready" if size > 1000 else "unknown"


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: resume_snapshot.py <snapshot_id>")
    sid = sys.argv[1]
    out_path = OUT / f"reviews_{sid}.jsonl"
    print(f"polling snapshot {sid}, writing to {out_path}")
    started = time.time()
    while time.time() - started < POLL_TIMEOUT:
        elapsed = int(time.time() - started)
        state = get_status_or_download(sid, out_path)
        size = out_path.stat().st_size if out_path.exists() else 0
        print(f"  [{elapsed:>4}s] state={state}  file={size} bytes", flush=True)
        if state == "ready" and size > 1000:
            print(f"DONE → {out_path} ({size} bytes)")
            return
        if state == "failed":
            sys.exit("snapshot failed on BD side")
        time.sleep(POLL_INTERVAL)
    sys.exit(f"timeout after {POLL_TIMEOUT}s")


if __name__ == "__main__":
    main()
