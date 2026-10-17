# Jev front/back ID matching

Can [TypeSafe Jev](https://typesafe.ai/blog/jev-1-14) tell whether a **front** and a **back** image belong to the **same identity card**?

Jev 1.13 and 1.14 accept images behind the experimental `TypeSafe-Danger-Zone-Images: true` header (released 14.10.2026). This repo is a small, reproducible test of that feature: 56 European ID cards, 154 front/back pairs, both model versions, with accuracy, latency and cost.

> **TL;DR**
> - Jev **never** confused cards from different countries (112/112 rejected).
> - Jev accepts almost every genuine pair (55/56 on both versions).
> - The hard case is a front and back from **two versions of the same country's ID**. At threshold 0.5, Jev wrongly says SAME ~60% of the time.
> - **1.14 separates the hard cases better** (AUC 0.858 vs 0.809), but only once you raise the threshold to ~0.8.
> - Whole experiment: **310 API calls, 544k input tokens, ≈ $0.023**. One pair costs ≈ $0.00007 and takes ~0.4 s.

---

## How it works

Each pair is **one** request to `POST /v1/systemone`. Both images go into `state`, and the model answers three yes/no ([Noul](https://docs.typesafe.ai/primitives/noul)) questions. TypeSafe recommends breaking image questions into the simplest possible parts.

| Question id | What it asks |
|---|---|
| `same_document` (primary) | Are `front` and `back` the two sides of the same physical document (same card, same holder)? |
| `same_type` | Same issuing country, same document type and same design version? |
| `details_agree` | Do details printed on both sides (number, name, dates, MRZ) agree? |

```jsonc
{
  "model": "jev-1.14.0",
  "state": { "front": "img-<uuid>", "back": "img-<uuid>" },
  "assets": {
    "img-<uuid>": { "type": "image", "part": "front" },   // multipart file parts
    "img-<uuid>": { "type": "image", "part": "back" }
  },
  "questions": {
    "same_document": { "type": "noul", "instructions": "...", "criteria": { "true": "...", "false": "..." } },
    "same_type":     { "type": "noul", "instructions": "..." },
    "details_agree": { "type": "noul", "instructions": "..." }
  }
}
```

Images are sent as `multipart/form-data`. The docs recommend this because base64 inflates every image by about a third. Before sending, each image is EXIF-rotated, converted to RGB and resized into the API limits (280 to 1568 px per side).

Each answer is `noul ∈ [0, 1]`, which this repo reads as **p(SAME)**. Labels: `1 = SAME`, `0 = DIFFERENT`.

## Dataset

There is no public dataset of real ID cards with both sides, for obvious privacy reasons. This test uses **specimen images from [PRADO](https://www.consilium.europa.eu/prado/)**, the EU Council's public register of identity and travel documents.

- **56 identity cards from 28 countries**: AUT, BEL, BGR, CHE, CYP, CZE, ESP, EST, FIN, FRA, HRV, HUN, ITA, LTU, LUX, LVA, MDA, MLT, MNE, NLD, NOR, POL, PRT, ROU, SVK, SVN, SWE, XKX.
- Each card has a *Recto* (front) and a *Verso* (back) image, about 600 × 380 px.
- **154 pairs**, in three kinds:

| Kind | n | Label | Example |
|---|---:|:---:|---|
| `same` | 56 | 1 | HRV 2021 front + HRV 2021 back |
| `diff_country` | 56 | 0 | HRV 2021 front + NLD 2021 back |
| `diff_version_same_country` (**hard**) | 42 | 0 | AUT 2010 front + AUT 2021 back |

The images are **not redistributed** here. `tools/` contains everything needed to download them again (see [Reproduce](#reproduce)). The exact pairs are in [`results/prado_pairs.csv`](results/prado_pairs.csv).

## Results

Raw per-pair scores: [`results/prado_results.csv`](results/prado_results.csv). Run date: 17.10.2026.

### Overall (`same_document`, threshold 0.5)

| | jev-1.13.0 | jev-1.14.0 |
|---|---:|---:|
| Precision | 0.671 | 0.679 |
| Recall | 0.982 | 0.982 |
| **F1** | **0.797** | **0.803** |
| Accuracy | 0.818 | 0.825 |
| TP / FP / FN / TN | 55 / 27 / 1 / 71 | 55 / 26 / 1 / 72 |
| Best F1 (threshold picked on the same data*) | 0.824 @ 0.71 | 0.821 @ 0.79 |
| **ROC AUC** | 0.918 | **0.939** |

\* The best-F1 threshold is optimistic and only indicative. Pick a threshold on a separate validation set.

### By pair kind (`same_document`)

| Kind | jev-1.13.0 mean p(SAME) | 1.13 correct @0.5 | jev-1.14.0 mean p(SAME) | 1.14 correct @0.5 |
|---|---:|---:|---:|---:|
| `same` | 0.88 | 55/56 | 0.82 | 55/56 |
| `diff_country` | 0.03 | **56/56** | 0.06 | **56/56** |
| `diff_version_same_country` | 0.59 | 15/42 ❌ | 0.56 | 16/42 ❌ |

### The threshold matters, especially on 1.14

SAME accepted, hard pairs rejected, and cross-country pairs rejected at different thresholds:

| Threshold | 1.13 SAME ✓ | 1.13 hard ✓ | 1.13 cross ✓ | 1.14 SAME ✓ | 1.14 hard ✓ | 1.14 cross ✓ |
|---:|---:|---:|---:|---:|---:|---:|
| 0.5 | 55/56 | 15/42 | 56/56 | 55/56 | 16/42 | 56/56 |
| 0.7 | 54/56 | 20/42 | 56/56 | 51/56 | 21/42 | 56/56 |
| **0.8** | 51/56 | 22/42 | 56/56 | **45/56** | **33/42** | 56/56 |
| 0.9 | 34/56 | 32/42 | 56/56 | 6/56 | 42/42 | 56/56 |

AUC of `same_document` on **same vs. hard pairs only** is 0.809 for 1.13 and **0.858** for 1.14. The newer model ranks the hard pairs better, but its scores are compressed into a narrow band (0.7 to 0.9). At the default 0.5 threshold that gain is invisible.

### Helper questions

| Question | AUC 1.13 (all / hard) | AUC 1.14 (all / hard) | Note |
|---|---|---|---|
| `same_document` | 0.918 / 0.809 | **0.939 / 0.858** | best single signal on 1.14 |
| `same_type` | 0.919 / 0.810 | 0.906 / 0.781 | |
| `details_agree` | 0.917 / 0.821 | 0.906 / 0.793 | on 1.14 almost always < 0.5: recall 0.20 @0.5, best F1 @ 0.14 |

`details_agree` is calibrated very differently between versions. Never reuse a threshold across model versions.

### Latency and cost

| | jev-1.13.0 | jev-1.14.0 |
|---|---:|---:|
| Latency mean / median | 0.44 s / 0.39 s | 0.45 s / 0.38 s |
| Latency p95 | 0.87 s | 0.85 s |
| Input tokens per pair (2 images + 3 questions) | ~1 751 | ~1 750 |
| Cost per pair | ≈ $0.000074 | ≈ $0.000074 |
| Cost for this run (154 pairs) | $0.0113 | $0.0113 |

Pricing is **$0.042 per million input tokens**, and output tokens are free ([models](https://docs.typesafe.ai/models)). Latency was measured client-side from Europe with 6 parallel workers. All 310 requests succeeded (no errors, no retries).

What it would cost at scale, per model:

| Pairs | Tokens | Cost |
|---:|---:|---:|
| 1 000 | 1.75 M | $0.07 |
| 10 000 | 17.5 M | $0.74 |
| 100 000 | 175 M | $7.35 |
| 1 000 000 | 1.75 B | $73.50 |

## Takeaways

1. **Cross-country mismatches are solved.** 112/112 rejected with p(SAME) ≈ 0.03–0.06. A cheap Jev call is a reliable "is this even the same kind of card?" filter.
2. **Same-country, different-version mismatches are not.** At 0.5 Jev accepts ~60% of them, which is the dangerous direction (a false SAME). Raising the threshold helps 1.14 a lot (33/42 rejected at 0.8) but costs recall (45/56).
3. **1.14 vs 1.13.** The overall F1 is the same, but 1.14 ranks better (AUC 0.939 vs 0.918, hard-only 0.858 vs 0.809). It needs a recalibrated threshold.
4. **Ask one simple question per signal.** Splitting into `same_document`, `same_type` and `details_agree` costs nothing extra: the images are read once per request and you get three scores you can threshold separately.
5. **Danger zone means danger zone.** TypeSafe explicitly says image support is not production-ready. Use it for prototyping, labelling help, triage or a second opinion, not as an automatic gate.

## Limitations

- **Specimens, not real photos.** PRADO images are clean, flat and well lit. Real phone or scanner captures (glare, blur, perspective, cropping) will be harder.
- **Hard pairs are partly ambiguous.** Within a country the specimens often show the same fictitious holder, and some versions differ only slightly (e.g. BEL 10002 vs 11005). Some "errors" in the hard bucket are hard even for a human.
- **Small sample.** 154 pairs, one prompt wording, one run. No variance across runs or prompt variants was measured.
- **Thresholds were chosen on the test data.** Treat every "best" number as optimistic.
- **Privacy.** Sending real identity documents to any third-party API is processing of personal data. Check GDPR, your contracts and TypeSafe's data policy before doing it.

## Reproduce

```bash
pip install -r requirements.txt
cp .env.example .env            # put your TYPESAFE_API_KEY here
```

**1. Download the PRADO specimens.** PRADO is behind a browser check, so the download runs inside your browser.

```bash
python tools/receive.py data_prado 18765        # tiny local receiver
```

Open any PRADO page (e.g. <https://www.consilium.europa.eu/prado/EN/HRV-BO-04001/index.html>), open DevTools → Console and paste [`tools/prado_download.js`](tools/prado_download.js). It finds all ID cards with both a Recto and a Verso image and posts them to the receiver. The set can change over time as PRADO is updated.

**2. Build pairs and run.**

```bash
python tools/make_pairs.py data_prado            # -> data_prado/pairs.csv
python jev_front_back_eval.py --pairs data_prado/pairs.csv \
       --models jev-1.13.0 jev-1.14.0 --workers 6 --out results/prado_results.csv
python tools/breakdown.py results/prado_results.csv data_prado/pairs.csv
```

**Your own images.** Name them `<name>_front.jpg` / `<name>_back.jpg` in one folder:

```bash
python jev_front_back_eval.py --folder my_ids --add-negatives 2 --models jev-1.14.0
python jev_front_back_eval.py --front f.jpg --back b.jpg          # single pair
python jev_front_back_eval.py --folder my_ids --dry-run           # check resizing, no API calls
```

## Repo layout

```
jev_front_back_eval.py     # main script: requests, retries, metrics, CSV output
tools/
  prado_download.js        # browser-console downloader for PRADO specimens
  receive.py               # local receiver used by the downloader
  make_pairs.py            # same / diff_country / diff_version_same_country pairs
  breakdown.py             # per-kind stats, AUC, threshold table, cost
results/
  prado_pairs.csv          # the 154 pairs used
  prado_results.csv        # raw scores for both models
```

## Links

- Jev 1.14 announcement: <https://typesafe.ai/blog/jev-1-14>
- Images docs: <https://docs.typesafe.ai/images>
- Models and pricing: <https://docs.typesafe.ai/models>
- PRADO register: <https://www.consilium.europa.eu/prado/>

Specimen images © their issuing authorities, published via PRADO by the Council of the European Union. They are not included in this repository.
