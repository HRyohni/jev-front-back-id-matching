"""Per-pair-type breakdown, ROC AUC and threshold trade-offs for a results CSV.

    python tools/breakdown.py results/prado_results.csv results/prado_pairs.csv
"""
import csv
import sys
from pathlib import Path

results_csv, pairs_csv = sys.argv[1], sys.argv[2]
KINDS = ["same", "diff_country", "diff_version_same_country"]
QUESTIONS = ["same_document", "same_type", "details_agree"]
PRICE_PER_MTOK = 0.042


def key(front, back):
    return Path(front).name, Path(back).name


def auc(y, s):
    pos = [b for a, b in zip(y, s) if a == 1]
    neg = [b for a, b in zip(y, s) if a == 0]
    return sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))


kind = {key(r["front"], r["back"]): r["kind"] for r in csv.DictReader(open(pairs_csv))}
rows = [r for r in csv.DictReader(open(results_csv, encoding="utf-8")) if not r["error"]]
for r in rows:
    r["kind"] = kind[key(r["front"], r["back"])]

for model in sorted({r["model"] for r in rows}):
    rs = [r for r in rows if r["model"] == model]
    print(f"\n=== {model}")
    for k in KINDS:
        sd = [float(r["same_document"]) for r in rs if r["kind"] == k]
        print(f"  {k:<27} n={len(sd):>3}  mean p(SAME)={sum(sd)/len(sd):.2f}"
              f"  >=0.5: {sum(s >= .5 for s in sd)}/{len(sd)}")

    hard = [r for r in rs if r["kind"] != "diff_country"]
    for q in QUESTIONS:
        a_all = auc([int(r["label"]) for r in rs], [float(r[q]) for r in rs])
        a_hard = auc([int(r["label"]) for r in hard], [float(r[q]) for r in hard])
        print(f"  AUC {q:<14} all={a_all:.3f}  same-vs-hard={a_hard:.3f}")

    by = {k: [float(r["same_document"]) for r in rs if r["kind"] == k] for k in KINDS}
    for t in (0.5, 0.7, 0.8, 0.9):
        print(f"  thr {t}: SAME accepted {sum(s >= t for s in by['same'])}/{len(by['same'])}"
              f"  hard rejected {sum(s < t for s in by['diff_version_same_country'])}"
              f"/{len(by['diff_version_same_country'])}"
              f"  cross-country rejected {sum(s < t for s in by['diff_country'])}/{len(by['diff_country'])}")

    lat = sorted(float(r["latency_s"]) for r in rs)
    tok = sum(int(r["input_tokens"]) for r in rs)
    print(f"  latency mean {sum(lat)/len(lat):.2f}s median {lat[len(lat)//2]:.2f}s"
          f" p95 {lat[int(len(lat)*.95)]:.2f}s | tokens {tok} (~${tok * PRICE_PER_MTOK / 1e6:.4f})")
