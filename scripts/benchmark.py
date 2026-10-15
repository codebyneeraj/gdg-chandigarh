"""Benchmark vision models on the hand-labelled set in data/bench/labels.csv.

Each model runs on its own (no fallback chain) through the production pipeline:
same image normalisation, prompt, schema validation and single retry.
Raw results go to data/bench/results/<model>.jsonl (resumable: finished ids are
skipped on re-run); a summary table is printed and written to
data/bench/results/summary.json.

Usage:
  python scripts/benchmark.py --models gemma-4-31b-it gemma-4-26b-a4b-it gemini-3.5-flash
  python scripts/benchmark.py --summary-only
  python scripts/benchmark.py --record gemma-4-31b-it   # write replay file for offline mode
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saafroute.config import load_settings  # noqa: E402
from saafroute.vision.gemini_hosted import HostedGemmaVision  # noqa: E402
from saafroute.vision.pipeline import analyze_photo, normalise_image  # noqa: E402

BENCH = ROOT / "data" / "bench"
RESULTS = BENCH / "results"
OPEN_WEIGHT = {"gemma-4-31b-it", "gemma-4-26b-a4b-it"}


def load_labels() -> list[dict]:
    with open(BENCH / "labels.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def run_model(model: str, labels: list[dict], api_key: str, pause_s: float) -> None:
    out = RESULTS / f"{model}.jsonl"
    done = set()
    if out.exists():
        done = {json.loads(line)["id"] for line in out.read_text(encoding="utf-8").splitlines() if line.strip()}
    vm = HostedGemmaVision([model], api_key, timeout_s=120)
    for row in labels:
        if row["id"] in done:
            continue
        img = normalise_image((BENCH / "images" / f"{row['id']}.jpg").read_bytes())
        raw_holder: list[str] = []
        orig = vm.generate

        def capture(image_jpeg: bytes, prompt: str) -> str:
            raw = orig(image_jpeg, prompt)
            raw_holder.append(raw)
            return raw

        vm.generate = capture  # type: ignore[method-assign]
        res = analyze_photo(img, vm)
        vm.generate = orig  # type: ignore[method-assign]
        rec = {"id": row["id"], "sha256": hashlib.sha256(img).hexdigest(), "status": res.status,
               "attempts": res.attempts, "latency_s": res.latency_s, "error": res.error,
               "analysis": res.analysis.model_dump(mode="json") if res.analysis else None,
               "raw": raw_holder[-1] if raw_holder else None}
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[{model}] {row['id']} {res.status} a={res.attempts} {res.latency_s}s "
              f"{(res.analysis.kind.value + '/' + res.analysis.waste_type.value + '/' + str(res.analysis.severity)) if res.analysis else res.error}",
              flush=True)
        time.sleep(pause_s)


def summarise(models: list[str], labels: list[dict]) -> dict:
    lab = {r["id"]: r for r in labels}
    summary = {}
    for model in models:
        path = RESULTS / f"{model}.jsonl"
        if not path.exists():
            continue
        recs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        recs = [r for r in recs if r["id"] in lab]
        n = len(recs)
        ok = [r for r in recs if r["status"] == "ok"]
        first_try = [r for r in ok if r["attempts"] == 1]
        # Accuracy is computed over ALL images: a needs_review result counts as wrong.
        kind = sum(1 for r in ok if r["analysis"]["kind"] == lab[r["id"]]["kind"])
        wtype = sum(1 for r in ok if r["analysis"]["waste_type"] == lab[r["id"]]["waste_type"])
        sev_exact = sum(1 for r in ok if r["analysis"]["severity"] == int(lab[r["id"]]["severity"]))
        sev_pm1 = sum(1 for r in ok if abs(r["analysis"]["severity"] - int(lab[r["id"]]["severity"])) <= 1)
        # "Needs collection" = anything that is not clean; the decision that actually triggers a ticket.
        actionable = sum(1 for r in ok if (r["analysis"]["kind"] == "clean") == (lab[r["id"]]["kind"] == "clean"))
        lat = sorted(r["latency_s"] for r in ok)
        api_err = sum(1 for r in recs if r["status"] != "ok" and (r["error"] or "").startswith("model error"))
        invalid = sum(1 for r in recs if r["status"] != "ok" and (r["error"] or "").startswith("invalid output"))
        k = len(ok)

        def frac(x: int, d: int) -> float | None:
            return round(x / d, 3) if d else None

        summary[model] = {
            "open_weight": model in OPEN_WEIGHT,
            "n": n,
            "answered": k,
            "api_errors": api_err,              # 429/5xx/timeouts after the retry: provider availability
            "invalid_output": invalid,          # model answered but failed schema twice
            "ok_on_first_attempt": frac(len(first_try), n),  # first call returned schema-valid JSON (API error or invalid JSON = miss)
            # Accuracy over ALL n images (a failure counts as wrong) ...
            "acc_kind": frac(kind, n),
            "acc_waste_type": frac(wtype, n),
            "acc_severity_exact": frac(sev_exact, n),
            "acc_severity_within_1": frac(sev_pm1, n),
            "acc_clean_vs_needs_collection": frac(actionable, n),
            # ... and over answered images only (model quality when the API responds).
            "answered_acc_kind": frac(kind, k),
            "answered_acc_waste_type": frac(wtype, k),
            "answered_acc_severity_exact": frac(sev_exact, k),
            "answered_acc_severity_within_1": frac(sev_pm1, k),
            "answered_acc_clean_vs_needs_collection": frac(actionable, k),
            "latency_median_s": lat[len(lat) // 2] if lat else None,
            "latency_p90_s": lat[max(0, int(len(lat) * 0.9) - 1)] if lat else None,
        }
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["gemma-4-31b-it", "gemma-4-26b-a4b-it", "gemini-3.5-flash"])
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between calls per model (rate limits)")
    ap.add_argument("--summary-only", action="store_true")
    ap.add_argument("--record", help="write data/bench/recorded_outputs.json from this model's results")
    args = ap.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    labels = load_labels()

    if args.record:
        recs = [json.loads(line) for line in (RESULTS / f"{args.record}.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        replay = {r["sha256"]: {"id": r["id"], "model": f"gemini-api:{args.record}", "raw": r["raw"]}
                  for r in recs if r["status"] == "ok" and r["raw"]}
        (BENCH / "recorded_outputs.json").write_text(json.dumps(replay, indent=1, ensure_ascii=False), encoding="utf-8")
        print("recorded", len(replay))
        return

    if not args.summary_only:
        key = load_settings().gemini_api_key
        if not key:
            sys.exit("GEMINI_API_KEY not set")
        threads = [threading.Thread(target=run_model, args=(m, labels, key, args.pause)) for m in args.models]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    summary = summarise(args.models, labels)
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
