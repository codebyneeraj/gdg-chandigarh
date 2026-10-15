"""Capture README/demo screenshots with headless Chrome via the DevTools protocol.

Requires a running server (python run_demo.py --no-browser) and Chrome/Edge.
Usage: python scripts/capture_screens.py [--base http://localhost:8000] [--upload data/bench/images/cm017.jpg]
Writes PNGs to docs/screenshots/ and prints page width checks (no horizontal scroll).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome") or "", shutil.which("chromium") or "",
]


class Page:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    def call(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == self.n:
                if "error" in r:
                    raise RuntimeError(r["error"])
                return r.get("result", {})

    def js(self, expr: str):
        return self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)["result"].get("value")

    def wait(self, expr: str, timeout: float = 240) -> None:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.js(expr):
                return
            time.sleep(0.5)
        raise TimeoutError(expr)

    def shot(self, name: str, w: int, h: int, full: bool = False) -> None:
        height = min(self.js("document.documentElement.scrollHeight"), 5000) if full else h
        data = self.call("Page.captureScreenshot", format="png", captureBeyondViewport=full,
                         clip={"x": 0, "y": 0 if full else self.js("scrollY"), "width": w, "height": height, "scale": 1})["data"]
        (OUT / f"{name}.png").write_bytes(base64.b64decode(data))
        print("saved", name, "| innerWidth/scrollWidth:", self.js("[innerWidth, document.documentElement.scrollWidth]"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000/")
    ap.add_argument("--upload", default=str(ROOT / "data" / "bench" / "images" / "cm017.jpg"))
    ap.add_argument("--sector", default="26")
    ap.add_argument("--lang", default="pa")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    chrome = next(c for c in CANDIDATES if c and os.path.exists(c))
    profile = tempfile.mkdtemp(prefix="saaf-chrome-", dir=ROOT / ".tmp" if (ROOT / ".tmp").exists() else None)
    proc = subprocess.Popen([chrome, "--headless=new", "--disable-gpu", f"--user-data-dir={profile}",
                             "--remote-debugging-port=9334", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                tabs = httpx.get("http://127.0.0.1:9334/json").json()
                break
            except httpx.HTTPError:
                time.sleep(0.25)
        ws_url = next(t for t in tabs if t["type"] == "page")["webSocketDebuggerUrl"]
        with connect(ws_url, max_size=100_000_000) as ws:
            p = Page(ws)
            p.call("Page.enable")
            p.call("DOM.enable")

            # 1. Desktop: routes planned with naive overlay
            p.call("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)
            p.call("Page.navigate", url=args.base + "#plan")
            p.wait("document.querySelector('#savings .stat') !== null", 60)
            time.sleep(2.5)
            p.shot("dashboard_routes", 1440, 900)
            p.js("document.querySelector('#showBase').click()")
            time.sleep(1)
            p.js("document.querySelector('#savings').scrollIntoView({block:'end'})")
            time.sleep(0.5)
            p.shot("routes_vs_naive", 1440, 900)

            # 2. Real upload through the UI (live models)
            p.call("Page.navigate", url=args.base)
            p.wait("document.querySelectorAll('#sector option').length > 50", 30)
            p.js(f"document.querySelector('#sector').value='{args.sector}'; document.querySelector('#lang').value='{args.lang}'; "
                 "document.querySelector('#official').value='JE Demo'; true")
            node = p.call("DOM.querySelector", nodeId=p.call("DOM.getDocument")["root"]["nodeId"], selector="#photo")["nodeId"]
            p.call("DOM.setFileInputFiles", nodeId=node, files=[str(Path(args.upload).resolve())])
            p.js("document.querySelector('#photo').dispatchEvent(new Event('change')); "
                 "document.querySelector('#reportForm').requestSubmit(); true")
            t0 = time.time()
            p.wait("document.querySelector('#submitBtn').disabled === false && document.querySelector('#formMsg').textContent.startsWith('Done') "
                   "|| document.querySelector('#formMsg').classList.contains('err')", 300)
            print("upload took", round(time.time() - t0, 1), "s:", p.js("document.querySelector('#formMsg').textContent"))
            time.sleep(2)
            p.js("window.scrollTo(0,0)")
            p.shot("upload_result", 1440, 900)
            p.js("document.querySelector('.ticket.focus')?.scrollIntoView({block:'center'})")
            time.sleep(1)
            p.shot("ticket_review", 1440, 900)

            # 3. Phone
            p.call("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
            p.call("Page.navigate", url=args.base)
            p.wait("document.querySelectorAll('.ticket').length > 0", 30)
            time.sleep(2)
            p.shot("mobile", 390, 844)
            p.shot("mobile_full", 390, 844, full=True)
    finally:
        proc.kill()


if __name__ == "__main__":
    main()
