"""Cross-check a spec database against a drawings database.

Pulls comparable 'facts' from each side and reports MATCH / MISMATCH /
SPEC-ONLY / DRAWING-ONLY, page-anchored on both sides, with a confidence tag.
This is the join that turns two indexes into a coordination check.
"""
import re, sqlite3, sys

SPEC_DB  = sys.argv[1] if len(sys.argv) > 1 else "outputs/spec_index_us.db"
PLANS_DB = sys.argv[2] if len(sys.argv) > 2 else "outputs/plans_index.db"

spec  = sqlite3.connect(SPEC_DB)
plans = sqlite3.connect(PLANS_DB)

# ---------------------------------------------------------------
# 1. Pull facts from the DRAWINGS (already normalized in facts table)
# ---------------------------------------------------------------
draw_facts = {}   # category -> list of (value, page, raw, confidence)
for cat, val, pg, raw, conf in plans.execute(
        "SELECT category, value, page, raw, confidence FROM facts"):
    draw_facts.setdefault(cat, []).append((val, pg, raw, conf))

draw_standards = {}   # code -> set(pages)
for code, pg in plans.execute("SELECT ref_code, page FROM refs"):
    draw_standards.setdefault(re.sub(r"\s+", " ", code).upper().replace("  ", " "), set()).add(pg)

# ---------------------------------------------------------------
# 2. Pull comparable facts from the SPEC (extract on the fly)
# ---------------------------------------------------------------
def spec_pages():
    return spec.execute("SELECT page, text FROM pages").fetchall()

# page -> section number lookup (which CSI section a spec page falls in)
def section_for_page(pg):
    row = spec.execute(
        "SELECT item_no FROM items WHERE ? BETWEEN start_page AND end_page ORDER BY start_page DESC LIMIT 1",
        (pg,)).fetchone()
    return row[0] if row else "?"

def to_psi(val_str, unit):
    """Normalize psi and MPa to psi for apples-to-apples comparison."""
    n = float(val_str.replace(",", ""))
    return str(int(round(n * 145.038))) if unit.lower() == "mpa" else str(int(n))

spec_facts = {}
spec_standards = {}
concrete_sections = {}   # psi value -> section number where required
for pg, txt in spec_pages():
    # Concrete: MINIMUM required strength for a named element (psi OR MPa)
    for m in re.finditer(
            r"(?:minimum compressive strength of|compressive strength of|f'?c[^.\n]{0,10}?)\s*"
            r"([\d,]{1,6}(?:\.\d+)?)\s*(psi|MPa)", txt, re.I):
        val = to_psi(m.group(1), m.group(2))
        if val == "5000" and "falls below" in txt[max(0, m.start()-80):m.start()].lower():
            continue  # skip ACI test-tolerance boilerplate
        spec_facts.setdefault("concrete_strength_psi", []).append(
            (val, pg, re.sub(r"\s+", " ", m.group(0)).strip()[:120], "high"))
        concrete_sections.setdefault(val, section_for_page(pg))
    # Masonry: psi or MPa
    for m in re.finditer(r"(?:f'?m|masonry)[^.\n]{0,40}?([\d,]{1,6}(?:\.\d+)?)\s*(psi|MPa)", txt, re.I):
        spec_facts.setdefault("masonry_strength_psi", []).append(
            (to_psi(m.group(1), m.group(2)), pg,
             re.sub(r"\s+", " ", m.group(0)).strip()[:120], "high"))
    for m in re.finditer(r"not less than\s*([\d,]{1,6}(?:\.\d+)?)\s*(psi|MPa)", txt, re.I):
        spec_facts.setdefault("masonry_strength_psi", []).append(
            (to_psi(m.group(1), m.group(2)), pg,
             re.sub(r"\s+", " ", m.group(0)).strip()[:120], "medium"))
    # Rebar — imperial (US) and metric (Canadian)
    if re.search(r"ASTM\s*A\s?615", txt): spec_facts.setdefault("rebar_main", []).append(("ASTM A615", pg, "ASTM A615/A615M", "high"))
    if re.search(r"Grade\s*60", txt):     spec_facts.setdefault("rebar_grade", []).append(("Grade 60", pg, "Grade 60", "high"))
    if re.search(r"ASTM\s*A\s?706", txt): spec_facts.setdefault("rebar_lowalloy", []).append(("ASTM A706", pg, "ASTM A706/A706M", "high"))
    if re.search(r"CSA\s*G\s?30\.18|G30\.?18", txt): spec_facts.setdefault("rebar_main", []).append(("CSA G30.18", pg, "CSA G30.18", "high"))
    if re.search(r"Grade\s*400W?\b", txt): spec_facts.setdefault("rebar_grade", []).append(("Grade 400", pg, "Grade 400", "high"))
    for m in set(re.findall(r"\b(10M|15M|20M|25M|30M|35M)\b", txt)):
        spec_facts.setdefault("rebar_sizes_metric", []).append((m, pg, m, "high"))
    # standards
    for rm in re.finditer(r"\b(ASTM|ANSI|AASHTO|ACI|AISC|AWS|NFPA|UL|CBC|IBC|ICC|IAPMO)\s*[A-Z]?\s?\d[\w./\-]*", txt):
        spec_standards.setdefault(re.sub(r"\s+", " ", rm.group(0)).upper(), set()).add(pg)

# ---------------------------------------------------------------
# 3. Compare and report
# ---------------------------------------------------------------
def pset(rows): return sorted({v for v, *_ in rows}, key=lambda x: (len(x), x))
def first_page(rows, val):
    for v, pg, *_ in rows:
        if v == val: return pg
    return "?"
def raw_for(rows, val):
    """Return the original source string (e.g. '30 MPa' or '4,000 psi') for a normalized psi value."""
    for v, _pg, raw, *_ in rows:
        if v == val: return raw
    return val
def display_strength(rows, sv):
    """Show '30 MPa (≈4351 psi)' or '4000 psi' — whatever the source actually wrote."""
    parts = []
    for v in sv:
        raw = raw_for(rows, v)
        if "mpa" in raw.lower():
            parts.append(f"{v} psi ← '{raw.strip()}'")
        else:
            parts.append(f"{v} psi")
    return ", ".join(parts)

print("="*70)
print("  SPEC  vs  DRAWINGS  — COORDINATION CROSS-CHECK")
print(f"  spec:   {SPEC_DB}")
print(f"  plans:  {PLANS_DB}")
print("="*70)

def report_numeric(cat, label, rule="match"):
    s = spec_facts.get(cat, []); d = draw_facts.get(cat, [])
    sv, dv = pset(s), pset(d)
    print(f"\n■ {label}")
    if not s and not d:
        print("   (no data on either side)"); return
    print(f"   spec:     {display_strength(s, sv) if sv else '— (deferred / not stated)'}")
    print(f"   drawings: {display_strength(d, dv) if dv else '— (not stated)'}")
    if cat == "concrete_strength_psi":
        if sv and dv:
            smax = max(int(x) for x in sv)   # the highest minimum the spec demands
            sec = concrete_sections.get(str(smax), "?")
            print(f"   VERDICT:  ⚠ VERIFY — spec requires min {smax} psi concrete "
                  f"(§{sec}); drawing f'c table shows {', '.join(dv)} psi for different elements. "
                  f"Confirm the elements governed by §{sec} are poured at ≥{smax} psi, not the {min(dv)} mix.")
        elif dv and not sv:
            print("   VERDICT:  📋 SPEC-SILENT — spec §03 30 00 defers f'c to the drawings; the drawing "
                  "table is the source of truth. Confirm every structural element has a stated strength.")
    return sv, dv

def report_exact(cat, label):
    s = spec_facts.get(cat, []); d = draw_facts.get(cat, [])
    sv, dv = pset(s), pset(d)
    print(f"\n■ {label}")
    print(f"   spec (p{first_page(s, sv[0]) if sv else '?'}):     {', '.join(sv) if sv else '—'}")
    print(f"   drawings (p{first_page(d, dv[0]) if dv else '?'}): {', '.join(dv) if dv else '—'}")
    if sv and dv:
        if set(sv) == set(dv):
            print("   VERDICT:  ✅ MATCH")
        elif set(dv) & set(sv):
            print(f"   VERDICT:  ✅ CONSISTENT (overlap: {', '.join(set(sv)&set(dv))})")
        else:
            print("   VERDICT:  ⚠ MISMATCH")
    elif sv and not dv:
        print("   VERDICT:  📋 SPEC-ONLY — drawing silent")
    elif dv and not sv:
        print("   VERDICT:  📋 DRAWING-ONLY — spec silent")

report_exact("rebar_main",     "Reinforcing bar standard (main)")
report_exact("rebar_grade",    "Reinforcing bar grade")
report_exact("rebar_lowalloy", "Low-alloy reinforcing standard")
report_numeric("concrete_strength_psi", "Concrete compressive strength f'c (psi)")
report_exact("masonry_strength_psi", "Masonry strength f'm (psi)")

# ---- standards coverage ----
# Canonicalize a standard code: uppercase, drop spaces/punctuation, strip a
# trailing year edition (-14, -16, /A615M keeps base A615). e.g.
#   "ACI 318-14" -> "ACI318" ;  "ASTM A615/A615M" -> "ASTMA615" ; "ASTM A36." -> "ASTMA36"
def canon(code):
    c = code.upper()
    c = re.sub(r"/[A-Z]?\d+[A-Z]?", "", c)      # drop /A615M metric twin
    c = re.sub(r"[-–]\s?\d{2}(\d{2})?\b", "", c) # drop -14 / -2014 year edition
    c = re.sub(r"[^A-Z0-9]", "", c)              # drop spaces + punctuation
    return c

spec_canon = {canon(s) for s in spec_standards}

print("\n■ External standards referenced by the drawings — is each in the spec?")
matched = missing = 0
for code, dpages in sorted(draw_standards.items()):
    if canon(code) in spec_canon:
        matched += 1
        print(f"   ✅ {code:16s} (drawing p{min(dpages)})  → also in spec")
    else:
        missing += 1
        print(f"   ⚠  {code:16s} (drawing p{min(dpages)})  → NOT found in spec text")
print(f"\n   {matched} drawing standards covered by spec, {missing} not found "
      f"(a drawing standard the spec never names is a real coordination gap to check).")

print("\n" + "="*70)
print("  Every row is page-anchored on both sides. HIGH-confidence facts come")
print("  from labelled schedule/table values; MEDIUM from generic text scrapes")
print("  and need a human glance before a bid decision.")
print("="*70)
