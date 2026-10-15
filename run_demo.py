"""One-command demo: python run_demo.py [--offline] [--reset] [--port 8000]

Creates .venv (first run), installs pinned requirements, seeds synthetic demo
data if the database is empty, starts the server and opens the browser.

--offline : no network models. Vision replays outputs recorded from Gemma 4
            during benchmarking (benchmark photos only; other photos go to
            "needs human review"), drafting uses built-in templates.
--reset   : wipe reports and re-seed the synthetic demo data.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(*args: str, env: dict | None = None) -> None:
    subprocess.check_call([str(a) for a in args], cwd=ROOT, env=env)


def load_dotenv(env: dict) -> None:
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if v.strip():
                    env.setdefault(k.strip(), v.strip())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if not PY.exists():
        print("Creating virtual environment in .venv ...")
        venv.create(VENV, with_pip=True)
    marker = VENV / ".deps-installed"
    req = ROOT / "requirements.txt"
    if not marker.exists() or marker.stat().st_mtime < req.stat().st_mtime:
        print("Installing pinned dependencies ...")
        run(PY, "-m", "pip", "install", "-q", "-r", req)
        marker.touch()

    env = dict(os.environ)
    load_dotenv(env)
    if args.offline:
        env["SAAFROUTE_VISION_PROVIDER"] = "replay"
        env["SAAFROUTE_DRAFT_PROVIDER"] = "template"
    elif not (env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY")):
        print("\nGEMINI_API_KEY is not set (environment or .env). Starting in --offline mode instead.\n")
        env["SAAFROUTE_VISION_PROVIDER"] = "replay"
        env["SAAFROUTE_DRAFT_PROVIDER"] = "template"

    db = Path(env.get("SAAFROUTE_DB", ROOT / "data" / "saafroute.db"))
    if args.reset or not db.exists():
        run(PY, "scripts/seed_demo.py", "--reset", env=env)

    url = f"http://localhost:{args.port}/"
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(3), webbrowser.open(url)), daemon=True).start()
    print(f"\nSaafRoute running at {url}  (Ctrl+C to stop)\n")
    try:
        run(PY, "-m", "uvicorn", "saafroute.main:app", "--host", "127.0.0.1", "--port", str(args.port), env=env)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
