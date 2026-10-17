"""
Front/back ID document matching with TypeSafe Jev (danger-zone images).

Each (front, back) pair is sent as one System One request: both images go into
`state` and the model answers a few simple Noul questions (the docs recommend
"the simplest possible decompositions" for images). Every answer is p(SAME);
the script reports precision / recall / F1 / confusion matrix and a threshold sweep.

Labels: 0 = DIFFERENT, 1 = SAME.

Examples:
    # single pair
    python jev_front_back_eval.py --front f.png --back b.png

    # folder with <name>_front.* and <name>_back.*  (every name = one SAME pair)
    python jev_front_back_eval.py --folder data --add-negatives 2

    # CSV with columns front,back[,label]
    python jev_front_back_eval.py --pairs pairs.csv --out results.csv

    # compare Jev 1.13 and 1.14
    python jev_front_back_eval.py --pairs pairs.csv --models jev-1.13.0 jev-1.14.0

API key: env TYPESAFE_API_KEY (or a .env file next to this script).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import requests
from PIL import Image, ImageOps

API_URL = "https://api.typesafe.ai/v1/systemone"
IMAGES_HEADER = {"TypeSafe-Danger-Zone-Images": "true"}

# Image limits from docs.typesafe.ai/images
MIN_SIDE = 280
MAX_SIDE = 1568
MAX_PIXELS = 1568 * 1568

# Primary question + two helper questions for diagnostics.
QUESTIONS = {
    "same_document": {
        "type": "noul",
        "instructions": (
            "`front` is a photo of the front side of an identity document and "
            "`back` is a photo of a back side. Are `front` and `back` the two "
            "sides of the same physical document (same card, same holder)?"
        ),
        "criteria": {
            "true": "Both images show sides of one and the same physical document.",
            "false": "The back belongs to a different document, a different "
                     "document type, a different country or a different person.",
        },
    },
    "same_type": {
        "type": "noul",
        "instructions": (
            "Do `front` and `back` show the same kind of document: same issuing "
            "country, same document type (ID card, driving licence, residence "
            "permit...) and the same design version?"
        ),
    },
    "details_agree": {
        "type": "noul",
        "instructions": (
            "Look only at details printed on both `front` and `back` (document "
            "number, name, dates, MRZ). Do these details agree with each other? "
            "If nothing overlaps, answer no."
        ),
    },
}
PRIMARY = "same_document"


# ---------------------------------------------------------------- images

def prepare_image(path: Path) -> bytes:
    """Load, apply EXIF orientation, fit into the API size limits, return PNG bytes."""
    img = Image.open(path)
    img = ImageOps.exif_transpose(img).convert("RGB")
    w, h = img.size

    scale = 1.0
    if max(w, h) > MAX_SIDE:
        scale = MAX_SIDE / max(w, h)
    if w * h * scale * scale > MAX_PIXELS:
        scale = min(scale, (MAX_PIXELS / (w * h)) ** 0.5)
    if min(w, h) * scale < MIN_SIDE:
        scale = MIN_SIDE / min(w, h)
        # very elongated image: after upscaling the long side may exceed MAX_SIDE
        if max(w, h) * scale > MAX_SIDE:
            raise ValueError(f"{path}: aspect ratio {w}x{h} does not fit {MIN_SIDE}-{MAX_SIDE}px")

    if scale != 1.0:
        img = img.resize((round(w * scale), round(h * scale)), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------- API

@dataclass
class PairResult:
    front: str
    back: str
    label: int | None
    model: str
    scores: dict[str, float] = field(default_factory=dict)
    error: str = ""
    latency_s: float = 0.0
    input_tokens: int = 0


def ask_jev(session: requests.Session, api_key: str, model: str,
            front: Path, back: Path, retries: int = 5) -> tuple[dict, float]:
    front_ref, back_ref = f"img-{uuid.uuid4()}", f"img-{uuid.uuid4()}"
    body = {
        "model": model,
        "state": {"front": front_ref, "back": back_ref},
        "assets": {
            front_ref: {"type": "image", "part": "front"},
            back_ref: {"type": "image", "part": "back"},
        },
        "questions": QUESTIONS,
    }
    # multipart: the JSON body must be the first part, named "request"
    files = {
        "request": (None, json.dumps(body, ensure_ascii=False), "application/json"),
        "front": ("front.png", prepare_image(front), "image/png"),
        "back": ("back.png", prepare_image(back), "image/png"),
    }
    headers = {"Authorization": f"Bearer {api_key}", **IMAGES_HEADER}

    delay = 1.0
    for _ in range(retries):
        t0 = time.perf_counter()
        r = session.post(API_URL, headers=headers, files=files, timeout=120)
        dt = time.perf_counter() - t0
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(float(r.headers.get("retry-after", delay)))
            delay = min(delay * 2, 30)
            continue
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
        return r.json(), dt
    raise RuntimeError(f"gave up after {retries} attempts (last HTTP {r.status_code})")


def run_pair(session, api_key, model, front, back, label) -> PairResult:
    res = PairResult(str(front), str(back), label, model)
    try:
        data, res.latency_s = ask_jev(session, api_key, model, Path(front), Path(back))
        res.scores = {k: float(v["noul"]) for k, v in data["answers"].items()}
        res.input_tokens = int(data.get("usage", {}).get("input_tokens", 0))
    except Exception as e:  # one bad pair must not kill the whole eval
        res.error = str(e)
    return res


# ---------------------------------------------------------------- data

def load_pairs(path: Path) -> list[tuple[str, str, int | None]]:
    base = path.parent
    pairs = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            fr = _resolve(row["front"], base)
            bk = _resolve(row["back"], base)
            lab = row.get("label", "")
            pairs.append((fr, bk, int(lab) if lab not in ("", None) else None))
    return pairs


def load_folder(folder: Path) -> list[tuple[str, str, int | None]]:
    """<name>_front.* + <name>_back.* -> SAME pair."""
    sides: dict[str, dict[str, str]] = {}
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            continue
        stem, _, side = p.stem.rpartition("_")
        if side.lower() in ("front", "back") and stem:
            sides.setdefault(stem, {})[side.lower()] = str(p)
    pairs = []
    for stem, s in sides.items():
        if "front" in s and "back" in s:
            pairs.append((s["front"], s["back"], 1))
        else:
            print(f"skipping {stem}: missing {'back' if 'front' in s else 'front'}")
    return pairs


def _resolve(p: str, base: Path) -> str:
    pp = Path(p)
    return str(pp if pp.is_absolute() else base / pp)


def add_negatives(pairs, per_positive: int, seed: int):
    """For every SAME pair add `per_positive` DIFFERENT pairs: front_i + back_j (j != i)."""
    rng = random.Random(seed)
    positives = [p for p in pairs if p[2] == 1]
    backs = [p[1] for p in positives]
    out = list(pairs)
    for fr, bk, _ in positives:
        candidates = [b for b in backs if b != bk]
        for b in rng.sample(candidates, min(per_positive, len(candidates))):
            out.append((fr, b, 0))
    return out


# ---------------------------------------------------------------- metrics

def metrics(y_true, y_prob, thr):
    tp = sum(1 for y, p in zip(y_true, y_prob) if y == 1 and p >= thr)
    fp = sum(1 for y, p in zip(y_true, y_prob) if y == 0 and p >= thr)
    fn = sum(1 for y, p in zip(y_true, y_prob) if y == 1 and p < thr)
    tn = sum(1 for y, p in zip(y_true, y_prob) if y == 0 and p < thr)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    acc = (tp + tn) / len(y_true) if y_true else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": prec, "recall": rec, "f1": f1, "acc": acc}


def report(results: list[PairResult], threshold: float):
    by_model: dict[str, list[PairResult]] = {}
    for r in results:
        by_model.setdefault(r.model, []).append(r)

    for model, rs in by_model.items():
        ok = [r for r in rs if not r.error]
        errs = len(rs) - len(ok)
        lat = sorted(r.latency_s for r in ok)
        print(f"\n=== {model}  ({len(ok)} ok, {errs} errors)")
        if lat:
            p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
            print(f"latency: median {lat[len(lat)//2]:.2f}s  p95 {p95:.2f}s"
                  f"  | avg input tokens {sum(r.input_tokens for r in ok)/len(ok):.0f}")
        labeled = [r for r in ok if r.label is not None]
        if not labeled:
            for r in ok:
                print(f"  {Path(r.front).name} | {Path(r.back).name} -> "
                      + "  ".join(f"{k}={v:.3f}" for k, v in r.scores.items()))
            continue

        y = [r.label for r in labeled]
        print(f"labeled pairs: {len(y)}  (SAME={sum(y)}, DIFFERENT={len(y)-sum(y)})")

        score_sets = {q: [r.scores.get(q, 0.0) for r in labeled] for q in QUESTIONS}
        score_sets["mean_all"] = [sum(r.scores.values()) / len(r.scores) for r in labeled]

        print(f"\n{'score':<15}{'thr':>6}{'P':>8}{'R':>8}{'F1':>8}{'acc':>8}   TP  FP  FN  TN"
              f"   | best F1 @thr")
        for name, probs in score_sets.items():
            m = metrics(y, probs, threshold)
            best_thr, best = max(((t / 100, metrics(y, probs, t / 100)) for t in range(1, 100)),
                                 key=lambda x: x[1]["f1"])
            print(f"{name:<15}{threshold:>6.2f}{m['precision']:>8.3f}{m['recall']:>8.3f}"
                  f"{m['f1']:>8.3f}{m['acc']:>8.3f}  {m['tp']:>3} {m['fp']:>3} {m['fn']:>3} {m['tn']:>3}"
                  f"   | {best['f1']:.3f} @{best_thr:.2f}")
        print("(best-F1 threshold is picked on the same data -> optimistic, indicative only)")


def write_csv(results: list[PairResult], out: Path):
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["model", "front", "back", "label", *QUESTIONS, "latency_s", "input_tokens", "error"])
        for r in results:
            w.writerow([r.model, Path(r.front).as_posix(), Path(r.back).as_posix(),
                        "" if r.label is None else r.label,
                        *(f"{r.scores[q]:.5f}" if q in r.scores else "" for q in QUESTIONS),
                        f"{r.latency_s:.3f}", r.input_tokens, r.error])
    print(f"\nresults -> {out}")


# ---------------------------------------------------------------- main

def load_dotenv(path: Path):
    """Minimal .env loader (KEY=VALUE); does not override variables already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--pairs", type=Path, help="CSV with columns front,back[,label]")
    src.add_argument("--folder", type=Path, help="folder with <name>_front.* and <name>_back.*")
    src.add_argument("--front", type=Path, help="single front image (with --back)")
    ap.add_argument("--back", type=Path)
    ap.add_argument("--label", type=int, choices=[0, 1])
    ap.add_argument("--models", nargs="+", default=["jev-1.14.0"])
    ap.add_argument("--add-negatives", type=int, default=0,
                    help="for every SAME pair add N DIFFERENT pairs by crossing fronts and backs")
    ap.add_argument("--limit", type=int, help="max number of pairs (after shuffling)")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=Path("jev_results.csv"))
    ap.add_argument("--dry-run", action="store_true", help="only prepare images, do not call the API")
    args = ap.parse_args()

    if args.front:
        if not args.back:
            ap.error("--front requires --back")
        pairs = [(str(args.front), str(args.back), args.label)]
    elif args.folder:
        pairs = load_folder(args.folder)
    else:
        pairs = load_pairs(args.pairs)
    if args.add_negatives:
        pairs = add_negatives(pairs, args.add_negatives, args.seed)
    if args.limit and len(pairs) > args.limit:
        random.Random(args.seed).shuffle(pairs)
        pairs = pairs[: args.limit]

    if args.dry_run:
        for fr, bk, lab in pairs:
            sizes = [Image.open(io.BytesIO(prepare_image(Path(p)))).size for p in (fr, bk)]
            print(f"{Path(fr).name} {sizes[0]} | {Path(bk).name} {sizes[1]} | label={lab}")
        print(f"\n{len(pairs)} pairs x {len(args.models)} models ready")
        return

    load_dotenv(Path(__file__).with_name(".env"))
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        sys.exit("set TYPESAFE_API_KEY (env or .env)")

    jobs = [(m, *p) for m in args.models for p in pairs]
    results: list[PairResult] = []
    with requests.Session() as session, ThreadPoolExecutor(args.workers) as pool:
        futs = [pool.submit(run_pair, session, api_key, m, fr, bk, lab) for m, fr, bk, lab in jobs]
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            results.append(r)
            status = r.error[:80] if r.error else f"{PRIMARY}={r.scores.get(PRIMARY, float('nan')):.3f}"
            print(f"[{i}/{len(jobs)}] {r.model} {Path(r.front).name} | {Path(r.back).name}"
                  f" label={r.label} -> {status}", flush=True)

    results.sort(key=lambda r: (r.model, r.front, r.back))
    write_csv(results, args.out)
    report(results, args.threshold)


if __name__ == "__main__":
    main()
