"""Index a CSI MasterFormat (US) construction specifications book into SQLite.

Matches DOCUMENT/SECTION XX XX XX headers (6-digit MasterFormat codes) and
uses the first two digits as the CSI Division number (00-49).
"""
import re, sqlite3, os, sys

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(__file__), "..", "work", "us_spec.txt")
DB  = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    os.path.dirname(__file__), "..", "outputs", "spec_index_us.db")

os.makedirs(os.path.dirname(DB), exist_ok=True)
with open(SRC, "r", encoding="utf-8", errors="replace") as f:
    raw = f.read()

pages = raw.split("\f")
print(f"Pages loaded: {len(pages)}")

con = sqlite3.connect(DB); cur = con.cursor()
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
CREATE VIRTUAL TABLE items_fts USING fts5(item_no, title, text, content='');
CREATE VIRTUAL TABLE pages_fts USING fts5(text, content='');
""")

for i, p in enumerate(pages, 1):
    cur.execute("INSERT INTO pages(page,text) VALUES (?,?)", (i, p))
    cur.execute("INSERT INTO pages_fts(rowid,text) VALUES (?,?)", (i, p))

CSI_DIV_TITLES = {
    0: "Procurement and Contracting Requirements",
    1: "General Requirements",
    2: "Existing Conditions", 3: "Concrete", 4: "Masonry", 5: "Metals",
    6: "Wood, Plastics, Composites", 7: "Thermal and Moisture Protection",
    8: "Openings", 9: "Finishes", 10: "Specialties", 11: "Equipment",
    12: "Furnishings", 13: "Special Construction", 14: "Conveying Equipment",
    21: "Fire Suppression", 22: "Plumbing", 23: "HVAC",
    26: "Electrical", 27: "Communications", 28: "Electronic Safety and Security",
    31: "Earthwork", 32: "Exterior Improvements", 33: "Utilities",
    34: "Transportation",
}

SECTION_HDR = re.compile(
    r"\b(?:DOCUMENT|SECTION)\s+"
    r"(\d{2}\s\d{2}\s\d{2}(?:\.\d{1,3})?)"
    r"[ \t]*[-–—]?[ \t]*([^\n]{0,120})")

running_offsets = []; off = 0
for p in pages:
    running_offsets.append(off); off += len(p) + 1
def offset_to_page(o):
    lo, hi = 0, len(running_offsets)-1
    while lo < hi:
        mid = (lo+hi+1)//2
        if running_offsets[mid] <= o: lo = mid
        else: hi = mid - 1
    return lo + 1

full = "\f".join(pages)
seen = {}
for m in SECTION_HDR.finditer(full):
    no = m.group(1)
    title = re.sub(r"\s+", " ", m.group(2)).strip(" -:–—\"'")
    if not title or title == no:
        tail = full[m.end(): m.end()+400]
        for cand in tail.splitlines():
            cs = cand.strip(" -:–—\"'\t")
            if not cs or cs == no: continue
            if re.match(r"^(STATE OF|CALIFORNIA|SECTION|DOCUMENT|PART)\b", cs, re.I): continue
            title = re.sub(r"\s+", " ", cs)[:120]; break
    if no not in seen:
        seen[no] = (title, m.start(), m.end())

sorted_items = sorted([(no, t, s, e) for no, (t, s, e) in seen.items()], key=lambda x: x[2])
print(f"Sections found: {len(sorted_items)}")

div_pages = {}
for i, (no, title, s, e) in enumerate(sorted_items):
    next_s = sorted_items[i+1][2] if i+1 < len(sorted_items) else len(full)
    body = full[s:next_s]
    sp = offset_to_page(s); ep = offset_to_page(next_s-1)
    div = int(no[:2])
    cur.execute("INSERT INTO items(item_no,title,division,start_page,end_page,text) VALUES (?,?,?,?,?,?)",
                (no, title, div, sp, ep, body))
    iid = cur.lastrowid
    cur.execute("INSERT INTO items_fts(rowid,item_no,title,text) VALUES (?,?,?,?)",
                (iid, no, title, body))
    if div not in div_pages: div_pages[div] = [sp, ep]
    else:
        div_pages[div][0] = min(div_pages[div][0], sp)
        div_pages[div][1] = max(div_pages[div][1], ep)

    for sent in re.split(r"(?<=[.!?])\s+", body):
        s2 = sent.strip()
        if not s2 or len(s2) > 500: continue
        low = s2.lower(); kind = None
        if re.search(r"\bshall submit|\bsubmit(?:ted|s|tal)?\b", low): kind = "submittal"
        elif re.search(r"\btest(?:ing|ed)?\b|\bcertif(?:y|ied|icate)", low): kind = "testing"
        elif re.search(r"\bshall\b|\bmust\b|\brequired\b", low): kind = "requirement"
        if kind:
            cur.execute("INSERT INTO requirements(item_id,page,kind,sentence) VALUES (?,?,?,?)",
                        (iid, sp, kind, s2))
    for rm in re.finditer(r"\b(ASTM|ANSI|AASHTO|ACI|AISC|AWS|NFPA|UL|CSA|ISO|CBC|IBC|SSPC|ICC)\s+[A-Z]?[- ]?\w[\w./\-]*", body):
        cur.execute("INSERT INTO refs(item_id,page,ref_type,ref_code) VALUES (?,?,?,?)",
                    (iid, sp, "standard", rm.group(0)))
    for rm in re.finditer(r"\bSection\s+(\d{2}\s\d{2}\s\d{2}(?:\.\d{1,3})?)", body):
        cur.execute("INSERT INTO refs(item_id,page,ref_type,ref_code) VALUES (?,?,?,?)",
                    (iid, sp, "section", rm.group(1)))

for d, (sp, ep) in sorted(div_pages.items()):
    cur.execute("INSERT INTO divisions(num,title,start_page,end_page) VALUES (?,?,?,?)",
                (d, CSI_DIV_TITLES.get(d, "?"), sp, ep))
    for pg in range(sp, ep+1):
        cur.execute("UPDATE pages SET division=? WHERE page=? AND division IS NULL", (d, pg))

con.commit()

def one(q, *a): return cur.execute(q, a).fetchone()[0]
print("\n=== INDEX SUMMARY (CSI MasterFormat) ===")
print("Divisions   :", one("SELECT COUNT(*) FROM divisions"))
print("Sections    :", one("SELECT COUNT(*) FROM items"))
print("Requirements:", one("SELECT COUNT(*) FROM requirements"))
print("  submittal :", one("SELECT COUNT(*) FROM requirements WHERE kind='submittal'"))
print("  testing   :", one("SELECT COUNT(*) FROM requirements WHERE kind='testing'"))
print("Standard refs:", one("SELECT COUNT(*) FROM refs WHERE ref_type='standard'"))
print("Section refs:", one("SELECT COUNT(*) FROM refs WHERE ref_type='section'"))
print("DB size     :", os.path.getsize(DB), "bytes")

print("\nDivisions found:")
for r in cur.execute("SELECT num,title,start_page,end_page FROM divisions"):
    print(f"  Div {r[0]:02d}  p{r[2]:>3}-{r[3]:>3}  {r[1]}")

print("\nFirst 15 sections:")
for r in cur.execute("SELECT item_no,title,division,start_page FROM items LIMIT 15"):
    print(f"  {r[0]}  Div{r[2]:02d}  p{r[3]:>3}  {r[1][:60]}")
con.close()
