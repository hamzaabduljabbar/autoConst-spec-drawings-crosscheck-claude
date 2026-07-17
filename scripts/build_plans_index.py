"""Index a construction drawing set's vector text layer into SQLite.

Mirrors the spec indexer's shape so the two databases can be cross-checked.
Extracts: sheets, engineering notes, external standard refs, and normalized
'facts' (concrete strength, masonry strength, rebar grade, etc.).
"""
import re, sqlite3, os, sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "work/plans_full.txt"
DB  = sys.argv[2] if len(sys.argv) > 2 else "outputs/plans_index.db"
os.makedirs(os.path.dirname(DB), exist_ok=True)

raw = open(SRC, "r", encoding="utf-8", errors="replace").read()
pages = raw.split("\f")
print(f"Sheets loaded: {len(pages)}")

con = sqlite3.connect(DB); cur = con.cursor()
cur.executescript("""
DROP TABLE IF EXISTS sheets;
DROP TABLE IF EXISTS notes;
DROP TABLE IF EXISTS refs;
DROP TABLE IF EXISTS facts;
CREATE TABLE sheets (page INTEGER PRIMARY KEY, sheet_no TEXT, title TEXT,
                     discipline TEXT, has_text INTEGER, char_count INTEGER);
CREATE TABLE notes  (id INTEGER PRIMARY KEY AUTOINCREMENT, page INTEGER, sentence TEXT);
CREATE TABLE refs   (id INTEGER PRIMARY KEY AUTOINCREMENT, page INTEGER, ref_code TEXT);
CREATE TABLE facts  (id INTEGER PRIMARY KEY AUTOINCREMENT, page INTEGER,
                     category TEXT, value TEXT, raw TEXT, confidence TEXT);
""")

DISC = {"S": "Structural", "A": "Architectural", "C": "Civil",
        "M": "Mechanical", "E": "Electrical", "P": "Plumbing",
        "L": "Landscape", "G": "General"}

def norm(s): return re.sub(r"\s+", " ", s).strip()

def add_fact(pg, cat, val, raw, conf):
    cur.execute("INSERT INTO facts(page,category,value,raw,confidence) VALUES (?,?,?,?,?)",
                (pg, cat, val, norm(raw)[:160], conf))

for i, p in enumerate(pages, 1):
    cc = len(p)
    # A real sheet's title-block alone is ~700 chars — anything above that has
    # real content. Threshold set well below the observed image-only baseline.
    has_text = 1 if cc > 900 else 0
    # sheet number: S001, S-311, A101 style tokens — but NOT ASTM/ACI standard codes
    sheet_no = None
    for sm in re.finditer(r"\b([SACMEPLG])[- ]?(\d{3}[A-Z]?)\b", p):
        pre = p[max(0, sm.start()-6):sm.start()]
        if re.search(r"ASTM|ACI|AISC|ANSI|A\s?$", pre):  # skip 'ASTM A615' etc.
            continue
        sheet_no = f"{sm.group(1)}{sm.group(2)}"
        break
    disc = DISC.get(sheet_no[0], "?") if sheet_no else ("(no text layer)" if not has_text else "?")
    cur.execute("INSERT INTO sheets(page,sheet_no,title,discipline,has_text,char_count) VALUES (?,?,?,?,?,?)",
                (i, sheet_no, None, disc, has_text, cc))
    if not has_text:
        continue

    # ---- notes: sentences with shall / ASTM / spec-like content ----
    for sent in re.split(r"(?<=[.!?])\s+", p):
        s = norm(sent)
        if 15 < len(s) < 400 and re.search(r"\bshall\b|\bASTM\b|PSI\b|Grade \d", s):
            cur.execute("INSERT INTO notes(page,sentence) VALUES (?,?)", (i, s))

    # ---- external standard refs ----
    for rm in re.finditer(r"\b(ASTM|ANSI|AASHTO|ACI|AISC|AWS|NFPA|UL|CBC|IBC|ICC|IAPMO)\s*[A-Z]?\s?\d[\w./\-]*", p):
        cur.execute("INSERT INTO refs(page,ref_code) VALUES (?,?)", (i, norm(rm.group(0))))

    # ---- normalized facts ----
    # Concrete + masonry strength: capture BOTH psi and MPa, store the
    # value normalized to psi (1 MPa = 145.038 psi) so the cross-check
    # can compare imperial vs metric drawings against imperial vs metric
    # specs. The `raw` column preserves the original unit the source used.
    def psi_from(val_str, unit):
        n = float(val_str.replace(",", ""))
        return str(int(round(n * 145.038))) if unit.lower() == "mpa" else str(int(n))

    # f'c (concrete): F'c = 4000 psi  OR  f'c = 30 MPa
    for m in re.finditer(r"F'?c\s*=?\s*([\d,]{1,6}(?:\.\d+)?)\s*(PSI|MPa)", p, re.I):
        add_fact(i, "concrete_strength_psi", psi_from(m.group(1), m.group(2)), m.group(0), "high")
    # f'm (masonry): F'm = 2000 psi OR F'm = 15 MPa
    for m in re.finditer(r"F'?m\s*=?\s*([\d,]{1,6}(?:\.\d+)?)\s*(PSI|MPa)", p, re.I):
        add_fact(i, "masonry_strength_psi", psi_from(m.group(1), m.group(2)), m.group(0), "high")
    # Any other strength value in psi or MPa (element unknown - medium confidence)
    for m in re.finditer(r"([\d,]{1,6}(?:\.\d+)?)\s*(PSI|MPa)\b", p, re.I):
        if not re.search(r"F'?[cm]", p[max(0, m.start()-10):m.start()], re.I):
            add_fact(i, "strength_psi_generic", psi_from(m.group(1), m.group(2)), m.group(0), "medium")

    # Rebar — imperial (ASTM) and metric (CSA + Canadian bar sizes)
    for m in re.finditer(r"Reinforcing Bars?:\s*ASTM\s*A\s?615[^.]{0,30}", p, re.I):
        add_fact(i, "rebar_main", "ASTM A615", m.group(0), "high")
    for m in re.finditer(r"ASTM\s*A\s?615[^.]{0,25}Grade\s*60", p, re.I):
        add_fact(i, "rebar_grade", "Grade 60", m.group(0), "high")
    for m in re.finditer(r"ASTM\s*A\s?706", p, re.I):
        add_fact(i, "rebar_lowalloy", "ASTM A706", m.group(0), "high")
    # Canadian rebar spec: CSA G30.18, Grade 400 (metric equivalent of Grade 60)
    for m in re.finditer(r"CSA\s*G\s?30\.18|G30\.?18", p, re.I):
        add_fact(i, "rebar_main", "CSA G30.18", m.group(0), "high")
    for m in re.finditer(r"Grade\s*400W?\b", p, re.I):
        add_fact(i, "rebar_grade", "Grade 400", m.group(0), "high")
    # Canadian metric bar sizes: 10M, 15M, 20M, 25M, 30M, 35M
    for m in set(re.findall(r"\b(10M|15M|20M|25M|30M|35M)\b", p)):
        add_fact(i, "rebar_sizes_metric", m, m, "high")

con.commit()
def one(q): return cur.execute(q).fetchone()[0]
print("\n=== PLANS INDEX SUMMARY ===")
print("Sheets total :", one("SELECT COUNT(*) FROM sheets"))
print("  with text  :", one("SELECT COUNT(*) FROM sheets WHERE has_text=1"))
print("  image-only :", one("SELECT COUNT(*) FROM sheets WHERE has_text=0"))
print("Notes        :", one("SELECT COUNT(*) FROM notes"))
print("Standard refs:", one("SELECT COUNT(*) FROM refs"))
print("Facts        :", one("SELECT COUNT(*) FROM facts"))
print("\nFacts by category:")
for r in cur.execute("SELECT category, COUNT(*), GROUP_CONCAT(DISTINCT value) FROM facts GROUP BY category"):
    print(f"  {r[0]:26s} n={r[1]:<3} values={r[2]}")
print("\nSheets:")
for r in cur.execute("SELECT page,sheet_no,discipline,char_count FROM sheets"):
    print(f"  p{r[0]:2d}  {str(r[1]):6s}  {r[2]:20s}  {r[3]} chars")
con.close()
