# Spec vs Drawings Cross-Check

**Automatically catch coordination mismatches between a project's specification book and its structural drawings — before they become change orders.**

Point it at a spec PDF and a drawing set. Two minutes later you have a page-anchored report telling you exactly where the two documents disagree on concrete strength, rebar spec, masonry grade, and external standards (ASTM, ACI, AWS, CSA...).

Same idea as the [Drawing Takeoff](https://github.com/hamzaabduljabbar/autoConst-drawing-takeoff-claude) and [Spec Index](https://github.com/hamzaabduljabbar/autoConst-spec-index-claude) tools — index each document once, then run the join to find the gaps.

Works on **US CSI MasterFormat** specs and **Canadian municipal** specs. Handles imperial (psi, Grade 60, ASTM A615) and metric (MPa, Grade 400W, CSA G30.18) — and cross-unit projects (metric drawing bid against an imperial spec, or vice versa).

---

## What this looks like in practice

Real output from a US project (California DGS — San Gorgonio Pass):

```
■ Reinforcing bar standard (main)
   spec (p32):     ASTM A615
   drawings (p5):  ASTM A615
   VERDICT: ✅ MATCH

■ Reinforcing bar grade
   spec (p32):     Grade 60
   drawings (p5):  Grade 60
   VERDICT: ✅ MATCH

■ Concrete compressive strength f'c (psi)
   spec:     4351 psi
   drawings: 2500, 3000, 4000 psi
   VERDICT: ⚠ VERIFY — spec requires min 4351 psi (§32 13 15);
            drawing shows 2500 psi for some elements. Confirm those
            elements aren't governed by §32 13 15.

■ External standards referenced by the drawings — is each in the spec?
   ✅ ASTM A615      → also in spec
   ✅ ACI 318        → also in spec  (matched despite "ACI 318-14" vs "ACI 318")
   ⚠  ASTM A307     → NOT found in spec  ← real coordination gap
   ⚠  ASTM A53      → NOT found in spec  ← real coordination gap
```

Every row is page-anchored on both sides. You can verify each finding against the source PDF in seconds.

---

## Setting it up (step by step, no prior git/python needed)

### Step 1 — Install prerequisites

**Windows:**
```powershell
winget install --id Python.Python.3.12 -e
winget install --id oschwartz10612.Poppler -e
```

**Mac:**
```bash
brew install python@3.12
brew install poppler
```

Close and reopen your terminal. Verify:
```
py --version         # Windows
python3 --version    # Mac
pdftotext -v
```

### Step 2 — Clone with Claude Code

Open Claude Code in the folder where you want the tool to live. Paste:

> *"Clone https://github.com/hamzaabduljabbar/autoConst-spec-drawings-crosscheck-claude into a folder called `spec-drawings-xcheck` and cd into it."*

### Step 3 — Set up your folders

Inside the cloned folder:
```
spec-drawings-xcheck/
  inputs/     ← put your spec PDF AND your drawings PDF here
  work/       ← extracted text goes here (auto-created)
  outputs/    ← the two databases and the report land here
  scripts/    ← the tool itself (already there)
```

Or ask Claude Code:
> *"Create `inputs/`, `work/`, and `outputs/` folders."*

Drop your spec book (e.g. `project-spec.pdf`) and your drawing set (e.g. `structural-drawings.pdf`) into `inputs/`.

### Step 4 — Extract both PDFs to text

```bash
pdftotext -layout inputs/project-spec.pdf work/spec_full.txt
pdftotext -layout inputs/structural-drawings.pdf work/plans_full.txt
```

Or ask Claude Code to do it for you.

### Step 5 — Build the two databases

**US spec (CSI MasterFormat — `SECTION 03 30 00`-style headers):**
```bash
py scripts/build_spec_index_us.py work/spec_full.txt outputs/spec_index.db
```

**Canadian municipal spec (`DIVISION N` + `Item NNN` headers):**
```bash
py scripts/build_spec_index.py work/spec_full.txt outputs/spec_index.db
```

Not sure which? Ask Claude Code:
> *"Look at my spec PDF, tell me which format it is, then build the right index."*

Then build the drawings index:
```bash
py scripts/build_plans_index.py work/plans_full.txt outputs/plans_index.db
```

### Step 6 — Run the cross-check

```bash
py scripts/cross_check.py outputs/spec_index.db outputs/plans_index.db
```

That's it. You get the coordination report printed to your terminal — every match, every mismatch, every gap, page-anchored on both sides.

---

## What it checks

| Category | What it compares |
|---|---|
| **Concrete strength (f'c)** | Spec minimum vs drawing schedule values — normalized to psi even if the source wrote MPa |
| **Masonry strength (f'm)** | Same, imperial + metric |
| **Rebar standard** | ASTM A615 / A706 / CSA G30.18 — spec vs drawing |
| **Rebar grade** | Grade 60 / Grade 400W — spec vs drawing |
| **External standards coverage** | Every ASTM/ACI/AWS/CSA/ANSI code the drawings cite — is it in the spec? Standards are canonicalized so `ACI 318-14` matches `ACI 318` |

Verdicts: **✅ MATCH** / **⚠ MISMATCH** / **⚠ VERIFY** / **📋 SPEC-ONLY** / **📋 DRAWING-ONLY**.

---

## For technical folks — what's in the databases

**`spec_index.db`** — divisions, items/sections, requirements (shall/must/submit clauses), external standard refs, FTS5.

**`plans_index.db`** — sheets, engineering notes, external standard refs, and normalized **facts** (concrete f'c, masonry f'm, rebar spec/grade — all stored in psi regardless of source unit).

The cross-check joins the two `refs` tables and the `facts`/spec-extracts, page-anchoring every finding.

---

## Folder layout after setup

```
spec-drawings-xcheck/
  README.md
  inputs/
    project-spec.pdf
    structural-drawings.pdf
  work/
    spec_full.txt          ← auto-generated
    plans_full.txt         ← auto-generated
  outputs/
    spec_index.db          ← auto-generated
    plans_index.db         ← auto-generated
  scripts/
    build_spec_index.py       ← Canadian municipal spec indexer
    build_spec_index_us.py    ← US CSI MasterFormat spec indexer
    build_plans_index.py      ← drawings indexer
    cross_check.py            ← the join / report
```

---

## Troubleshooting

**`pdftotext not found`** — Poppler isn't installed or not on PATH. Reopen your terminal after installing.

**`py not found`** — Python isn't installed or not on PATH. Windows: `winget install Python.Python.3.12`.

**`UnicodeEncodeError` on Windows** — prefix with `PYTHONIOENCODING=utf-8`, or ask Claude Code to run it for you.

**All facts show `— (not stated)`** — your drawings are a scanned image with no text layer. Run through OCR first (Acrobat "Recognize Text"), then re-extract.

**Spec headers not detected** — the two indexers cover US CSI and Canadian municipal formats. If yours is a different format (Australian, UK, etc.), ask Claude Code to look at the first 20 pages of `spec_full.txt` and tweak the section-header regex — it's usually a 2-minute change.

---

Built by [Hamza Jabbar](https://hamzajabbar.online) — [AutoConst](https://hamzajabbar.online).
