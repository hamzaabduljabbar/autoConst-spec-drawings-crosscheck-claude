"""Index the City of St. John's Specifications Book into SQLite."""
import re, sqlite3, os

SRC = os.path.join(os.path.dirname(__file__), "spec_full.txt")
DB  = os.path.join(os.path.dirname(__file__), "..", "outputs", "spec_index.db")

with open(SRC, "r", encoding="utf-8", errors="replace") as f:
    raw = f.read()

pages = raw.split("\f")
print(f"Pages loaded: {len(pages)}")

con = sqlite3.connect(DB)
cur = con.cursor()
cur.executescript("""
DROP TABLE IF EXISTS pages;
DROP TABLE IF EXISTS divisions;
DROP TABLE IF EXISTS items;
DROP TABLE IF EXISTS requirements;
DROP TABLE IF EXISTS refs;
DROP TABLE IF EXISTS items_fts;
DROP TABLE IF EXISTS pages_fts;
CREATE TABLE pages       (page INTEGER PRIMARY KEY, division INTEGER, text TEXT);
CREATE TABLE divisions   (num INTEGER PRIMARY KEY, title TEXT, start_page INTEGER, end_page INTEGER);
CREATE TABLE items       (id INTEGER PRIMARY KEY AUTOINCREMENT, item_no TEXT, title TEXT,
                          division INTEGER, start_page INTEGER, end_page INTEGER, text TEXT);
CREATE TABLE requirements(id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER, page INTEGER,
                          kind TEXT, sentence TEXT);
CREATE TABLE refs        (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER, page INTEGER,
                          ref_type TEXT, ref_code TEXT);
CREATE VIRTUAL TABLE items_fts   USING fts5(item_no, title, text, content='');
CREATE VIRTUAL TABLE pages_fts   USING fts5(text, content='');
""")

# ---------- pages ----------
for i, p in enumerate(pages, 1):
    cur.execute("INSERT INTO pages(page, text) VALUES (?, ?)", (i, p))
    cur.execute("INSERT INTO pages_fts(rowid, text) VALUES (?, ?)", (i, p))

# ---------- divisions ----------
DIV_RE = re.compile(r"^\s*Division\s+(\d+)\s+(.+?)\s*$", re.MULTILINE)
div_titles = {
    1: "General",
    2: "Water & Sewer Services",
    3: "Street Reconstruction",
    4: "Structures",
    5: "Reinstatement",
    6: "Miscellaneous Items",
    7: "Temporary Signs & Devices",
    8: "Traffic",
    9: "Environmental Requirements",
    10: "Standard Drawings",
}

# Anchor divisions on the ALL-CAPS "DIVISION N" header line that stands alone
# (ignores TOC entries like "Division 1 Specifications General" and inline mentions).
div_start = {}
for pg_no, txt in enumerate(pages, 1):
    for line in txt.splitlines():
        s = line.strip()
        m = re.match(r"^DIVISION\s+(\d+)\b", s)
        if m and len(s) < 60:
            d = int(m.group(1))
            if d not in div_start and 1 <= d <= 10:
                div_start[d] = pg_no
            break

div_sorted = sorted(div_start.items())
for i, (d, sp) in enumerate(div_sorted):
    ep = (div_sorted[i+1][1] - 1) if i+1 < len(div_sorted) else len(pages)
    cur.execute("INSERT INTO divisions VALUES (?,?,?,?)", (d, div_titles.get(d, "?"), sp, ep))
    for pg in range(sp, ep+1):
        cur.execute("UPDATE pages SET division=? WHERE page=?", (d, pg))

# ---------- items ----------
# Item headers appear as: "Item 112", "Item 250.09", "ITEM 130", sometimes with title on same line
ITEM_HDR = re.compile(r"(?:^|\n)\s*(?:Item|ITEM)\s+(\d{2,4}(?:\.\d{1,3})?)\s*[-–—:]?\s*([^\n]{0,120})")

# Walk the full text with a running page cursor
running_offsets = []
off = 0
for p in pages:
    running_offsets.append(off)
    off += len(p) + 1  # for \f
def offset_to_page(o):
    lo, hi = 0, len(running_offsets)-1
    while lo < hi:
        mid = (lo+hi+1)//2
        if running_offsets[mid] <= o: lo = mid
        else: hi = mid-1
    return lo + 1

full = "\f".join(pages)
items_found = []
for m in ITEM_HDR.finditer(full):
    no = m.group(1)
    title = re.sub(r"\s+", " ", m.group(2)).strip(" -:–—\"'")
    if re.match(r"^\d{4,}$", no):
        continue
    # If title is empty or just the item number repeated, look ahead for the real title line
    if not title or title == no:
        tail = full[m.end(): m.end()+400]
        for cand in tail.splitlines():
            cs = cand.strip(" -:–—\"'\t")
            if not cs or cs == no:
                continue
            # skip lines that are just page-header repeats
            if re.match(r"^(CITY OF|SPECIFICATIONS|DIVISION)\b", cs, re.I):
                continue
            title = re.sub(r"\s+", " ", cs)[:120]
            break
    items_found.append((no, title, m.start(), m.end()))

# collapse duplicates that appear on TOC/index pages: keep first occurrence per item_no
seen = {}
for no, title, s, e in items_found:
    if no not in seen:
        seen[no] = (title, s, e)

item_rows = sorted(seen.values(), key=lambda x: x[1])
sorted_items = sorted([(no, seen[no][0], seen[no][1], seen[no][2]) for no in seen], key=lambda x: x[2])

first_div_page = min((sp for sp in div_start.values()), default=1)
for i, (no, title, s, e) in enumerate(sorted_items):
    next_s = sorted_items[i+1][2] if i+1 < len(sorted_items) else len(full)
    body = full[s:next_s]
    sp = offset_to_page(s)
    ep = offset_to_page(next_s-1)
    # Skip TOC/index ghost hits that appear before any real division starts
    if sp < first_div_page:
        continue
    # division = division of start page
    div = None
    row = cur.execute("SELECT division FROM pages WHERE page=?", (sp,)).fetchone()
    if row: div = row[0]
    cur.execute("INSERT INTO items(item_no,title,division,start_page,end_page,text) VALUES (?,?,?,?,?,?)",
                (no, title, div, sp, ep, body))
    item_id = cur.lastrowid
    cur.execute("INSERT INTO items_fts(rowid, item_no, title, text) VALUES (?,?,?,?)",
                (item_id, no, title, body))

    # requirements: sentences containing shall/must/required/submit
    for sent in re.split(r"(?<=[.!?])\s+", body):
        s2 = sent.strip()
        if not s2 or len(s2) > 500: continue
        low = s2.lower()
        kind = None
        if re.search(r"\bshall submit|\bsubmit(?:ted|s|tal)?\b", low): kind = "submittal"
        elif re.search(r"\btest(?:ing|ed)?\b|\bcertif(?:y|ied|icate)", low): kind = "testing"
        elif re.search(r"\bshall\b|\bmust\b|\brequired\b", low): kind = "requirement"
        if kind:
            cur.execute("INSERT INTO requirements(item_id,page,kind,sentence) VALUES (?,?,?,?)",
                        (item_id, sp, kind, s2))

    # cross-refs: ASTM/CSA/ANSI/AASHTO codes and 'Item NNN'
    for rm in re.finditer(r"\b(ASTM|CSA|ANSI|AASHTO|CAN/CSA|ISO|OPSS|MTO)\s+[A-Z]?[- ]?\w[\w./\-]*", body):
        cur.execute("INSERT INTO refs(item_id,page,ref_type,ref_code) VALUES (?,?,?,?)",
                    (item_id, sp, "standard", rm.group(0)))
    for rm in re.finditer(r"\bItem\s+(\d{2,4}(?:\.\d{1,3})?)", body):
        cur.execute("INSERT INTO refs(item_id,page,ref_type,ref_code) VALUES (?,?,?,?)",
                    (item_id, sp, "item", rm.group(1)))

con.commit()

# ---------- summary ----------
def one(q, *a):
    return cur.execute(q, a).fetchone()[0]

print("\n=== INDEX SUMMARY ===")
print("Divisions :", one("SELECT COUNT(*) FROM divisions"))
print("Items     :", one("SELECT COUNT(*) FROM items"))
print("Requirements:", one("SELECT COUNT(*) FROM requirements"))
print("  submittal :", one("SELECT COUNT(*) FROM requirements WHERE kind='submittal'"))
print("  testing   :", one("SELECT COUNT(*) FROM requirements WHERE kind='testing'"))
print("  requirement:", one("SELECT COUNT(*) FROM requirements WHERE kind='requirement'"))
print("Standard refs:", one("SELECT COUNT(*) FROM refs WHERE ref_type='standard'"))
print("Item refs  :", one("SELECT COUNT(*) FROM refs WHERE ref_type='item'"))
print("DB size    :", os.path.getsize(DB), "bytes")

print("\nDivisions:")
for r in cur.execute("SELECT num,title,start_page,end_page FROM divisions"):
    print(f"  Div {r[0]:2d}  p{r[2]:>3}-{r[3]:>3}  {r[1]}")

print("\nSample items:")
for r in cur.execute("SELECT item_no,title,division,start_page FROM items LIMIT 15"):
    print(f"  Item {r[0]:>7}  Div{r[2]}  p{r[3]:>3}  {r[1][:70]}")

con.close()
