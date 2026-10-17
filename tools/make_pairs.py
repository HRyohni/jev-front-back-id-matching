"""Build pairs.csv from a folder of <DOC>_front.jpg / <DOC>_back.jpg (PRADO naming, e.g. HRVBO04001).

Per document:
  same                       front + its own back                               label 1
  diff_country               front + back of a document from another country    label 0
  diff_version_same_country  front + back of another ID version, same country   label 0 (hard)

    python tools/make_pairs.py data_prado
"""
import csv
import random
import sys
from collections import Counter
from pathlib import Path

folder = Path(sys.argv[1])
rng = random.Random(42)
docs = sorted({p.stem.rsplit("_", 1)[0] for p in folder.glob("*_front.jpg")
               if (folder / p.name.replace("_front", "_back")).exists()})
rows = []
for d in docs:
    rows.append((f"{d}_front.jpg", f"{d}_back.jpg", 1, "same"))
    other = rng.choice([o for o in docs if o[:3] != d[:3]])
    rows.append((f"{d}_front.jpg", f"{other}_back.jpg", 0, "diff_country"))
    same_country = [o for o in docs if o[:3] == d[:3] and o != d]
    if same_country:
        rows.append((f"{d}_front.jpg", f"{rng.choice(same_country)}_back.jpg", 0, "diff_version_same_country"))

with open(folder / "pairs.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["front", "back", "label", "kind"])
    w.writerows(rows)
print(len(docs), "documents ->", dict(Counter(r[3] for r in rows)))
