---
name: spec-drawings-crosscheck
description: Cross-check a construction specification book against its structural drawing set to automatically catch coordination mismatches — concrete strength (f'c), masonry strength (f'm), rebar spec/grade, and external standards (ASTM, ACI, AWS, CSA, ANSI). Use when a user has both a spec PDF and a drawing PDF and wants to know where the two documents disagree before bidding or before construction. Handles US CSI MasterFormat specs and Canadian municipal specs, imperial (psi, Grade 60) and metric (MPa, Grade 400W, CSA G30.18), and cross-unit projects (metric drawings bid against an imperial spec). Every finding is page-anchored on both sides.
---

# Spec vs Drawings Cross-Check

Contractors and estimators get handed a spec book and a drawing set. They almost never agree perfectly. The concrete section in the spec says `Minimum f'c = 30 MPa`, the structural drawings' f'c table shows 25/30/35 MPa mixes for different elements, the drawings reference `ASTM A307` for anchor bolts but the spec never mentions A307 — every one of those is a real coordination gap that becomes a change order.

This skill indexes both documents into SQLite (using the sister skills' indexers), then joins the two databases fact-by-fact and reports MATCH / MISMATCH / VERIFY / SPEC-ONLY / DRAWING-ONLY, page-anchored on both sides.

**Why this beats reading both documents by hand**
- A 500-page spec + 50-sheet drawing set is 4-6 hours of eyeball comparison for one estimator.
- Missed coordination items become change orders — costs thousands, sometimes tens of thousands.
- A SQL join of the two indexes runs in under a second and cites the exact page on each side.

## The reliability rule

Fact extraction is regex-based — high recall, some noise. Say so.

| Category | Confidence | Notes |
|---|---|---|
| **Rebar spec / grade** (ASTM A615, Grade 60, CSA G30.18, Grade 400W) | **high** | Verbatim strings from labelled schedules |
| **Concrete f'c, Masonry f'm** | **high** | Reads labelled `F'c = X psi` / `X MPa`; normalizes MPa → psi (× 145.038) |
| **External standards coverage** (ASTM/ACI/AWS/CSA) | **high** | Canonicalized so `ACI 318-14` matches `ACI 318`, and `ASTM A615/A615M` matches `ASTM A615` |
| Numeric extractions from prose | **medium** | Verify against the source page before quoting |

Every row carries a page number on both sides. When you hand a mismatch to the user, cite **both** page numbers so they can verify against the source PDFs.

## Workflow

Two documents in, one report out. Scripts live in `scripts/`.

1. **Extract both PDFs to text** (Poppler `pdftotext` preserves layout and page breaks with `\f`):
   ```
   pdftotext -layout inputs/spec.pdf work/spec_full.txt
   pdftotext -layout inputs/drawings.pdf work/plans_full.txt
   ```

2. **Build the spec index** — US CSI or Canadian municipal:
   ```
   py scripts/build_spec_index_us.py work/spec_full.txt outputs/spec_index.db   # US
   py scripts/build_spec_index.py     work/spec_full.txt outputs/spec_index.db   # Canadian
   ```

3. **Build the drawings index** (extracts sheets, notes, standard refs, and normalized `facts` — concrete f'c, masonry f'm, rebar spec/grade, all stored in psi):
   ```
   py scripts/build_plans_index.py work/plans_full.txt outputs/plans_index.db
   ```

4. **Run the cross-check**:
   ```
   py scripts/cross_check.py outputs/spec_index.db outputs/plans_index.db
   ```

## How to use this as Claude

- When a user asks about coordination between a spec and a drawing set, **check whether `outputs/spec_index.db` and `outputs/plans_index.db` both exist**. If yes, run `cross_check.py` — don't re-read the PDFs.
- If one or both indexes are missing, build them first with the steps above, then run the check.
- Every mismatch you cite must include **the spec page and the drawing page**. Example: *"Concrete f'c: spec §32 13 15 p87 requires min 4351 psi; drawings sheet S-311 p5 show 2500 psi mix for foundations. Verify which elements the section governs."*
- When the check reports a `⚠ VERIFY`, don't call it a defect — call it a coordination item to confirm. The tool flags the join; the human decides.
- If the drawing set has no vector text (image-only PDF), the drawings index will show 0 sheets with text. Tell the user to OCR the PDF first.

## Tuning per project

The two spec indexers are already tuned for US CSI MasterFormat (`SECTION XX XX XX` headers) and Canadian municipal (`DIVISION N` + `Item NNN`). For other spec families (Australian, UK, ME), swap the section-header regex in whichever indexer is closer — the rest of the pipeline is universal.

The drawings indexer's fact extraction handles both imperial and metric out of the box. If a project uses non-standard notation for concrete strength (e.g. `30 N/mm²` instead of `30 MPa`), add one more `re.finditer` to `build_plans_index.py` — five minutes of work.

Test by running `build_plans_index.py` and eyeballing the "Facts by category" summary. If the expected values (f'c mixes, rebar spec, grade) are all there, the cross-check will work.
