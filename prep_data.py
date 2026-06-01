"""
prep_data.py
Generates a clean, web-ready data.json from jobs_database.db.
Run from the jobs-dashboard/ directory:  python3 prep_data.py
"""
import re, json, os, sqlite3, warnings
import pandas as pd
warnings.filterwarnings("ignore")

DB  = "../jobs_database.db"
OUT = "data.json"

KEEP_COLS = [
    "institution", "job_rank", "area", "tt_ntt", "location", "region",
    "start_date", "post_date", "deadline", "expired", "salary",
    "teaching_load", "link", "notes", "_tab", "_sheet_id",
]

# ── Area category ──────────────────────────────────────────────────────────────
def _tok(text, *words):
    for w in words:
        if re.search(r'(?<![a-z])' + re.escape(w) + r'(?![a-z])', text):
            return True
    return False

def categorize_area(raw):
    if not raw or str(raw).strip().lower() in ("nan", "none", ""):
        return "Other / Unspecified"
    t = str(raw).lower().strip()
    io   = _tok(t, "io", "i/o") or "industrial" in t or "io psychology" in t
    ob   = _tok(t, "ob") or "organizational behavior" in t or "organisational behavior" in t or "obhr" in t
    hr   = _tok(t, "hr", "hrm") or "human resource" in t
    st   = _tok(t, "str") or "strategy" in t or "strategic" in t
    ent  = _tok(t, "ent") or "entrepreneur" in t
    mgmt = _tok(t, "mgmt") or "management" in t
    lead = "leadership" in t
    ib   = _tok(t, "ib") or "international business" in t
    fin  = "finance" in t or "accounting" in t
    mkt  = "marketing" in t
    ops  = "operations" in t or "supply chain" in t or _tok(t, "scm", "om")
    if io:          return "IO Psychology"
    if ob and hr:   return "OB / HR"
    if ob:          return "Organizational Behavior"
    if hr:          return "HR / HRM"
    if lead:        return "Leadership"
    if st and ent:  return "Strategy / Entrepreneurship"
    if st:          return "Strategy"
    if ent:         return "Entrepreneurship"
    if ib:          return "International Business"
    if fin:         return "Finance / Accounting"
    if mkt:         return "Marketing"
    if ops:         return "Operations / SCM"
    if mgmt:        return "Management (General)"
    return "Other / Unspecified"

# ── Broad region ───────────────────────────────────────────────────────────────
US_SIGNALS = ["us ", "u.s", "usa", "united states", "northeast", "midwest",
              "southeast", "southwest", "northwest", "mid-atlantic", "midatlantic",
              "pacific", "west coast", "east coast", "appalachia", "hawaii",
              "alaska", "texas", "boston", "baltimore", " ca", "new york",
              "chicago", "midsouth", "mid-south", "north central"]
def broad_region(raw):
    if not raw or str(raw).strip().lower() in ("nan", "none", ""):
        return None
    rl = str(raw).strip().lower()
    if any(x in rl for x in US_SIGNALS) or rl in ("us", "usa", "us east", "us west", "west", "south", "north", "east", "midwest", "northeast", "southeast", "southwest", "northwest", "mid-atlantic"):
        return "United States"
    if "canada" in rl or "quebec" in rl:
        return "Canada"
    if any(x in rl for x in ["europe", " eu", "uk", "france", "netherlands", "spain", "switzerland", "amsterdam", "nl", "paris"]):
        return "Europe"
    if any(x in rl for x in ["asia", "china", "korea", "japan", "singapore", "india", "taiwan", "vietnam", "hong kong"]):
        return "Asia"
    if any(x in rl for x in ["australia", "aus", "new zealand", "nz", "oceania", "melbourne"]):
        return "Australia / NZ"
    if any(x in rl for x in ["middle east", "mideast", "uae", "oil money", "kuwait", "saudi"]):
        return "Middle East"
    if any(x in rl for x in ["africa", "latin", "latam", "south america", "caribbean", "brazil"]):
        return "Latin America / Africa"
    if "remote" in rl or "worldwide" in rl or "international" in rl:
        return "International / Remote"
    return None

# ── TT/NTT normalisation ───────────────────────────────────────────────────────
def norm_tt(v):
    if not v or str(v).strip().lower() in ("nan", "none", ""):
        return None
    lv = str(v).strip().lower()
    if lv in ("tt", "tenure track", "tenure-track", "yes"):  return "TT"
    if lv in ("ntt", "non-tenure", "non-tenure track", "no"): return "NTT"
    if "visiting" in lv:  return "Visiting"
    if "postdoc" in lv:   return "Postdoc"
    if "fixed" in lv:     return "Fixed Term"
    return None   # discard ambiguous

# ── Year inference ────────────────────────────────────────────────────────────
def infer_year(dates):
    years = []
    for d in dates.dropna():
        years.extend(re.findall(r'\b(20\d{2})\b', str(d)))
    if not years:
        return None
    from collections import Counter
    return Counter(years).most_common(1)[0][0]

# ── Main ──────────────────────────────────────────────────────────────────────
script_dir = os.path.dirname(os.path.abspath(__file__))
con = sqlite3.connect(os.path.join(script_dir, DB))
df  = pd.read_sql("SELECT * FROM jobs", con)
con.close()

available = [c for c in KEEP_COLS if c in df.columns]
df = df[available].copy()

# Infer year per sheet
year_map = {}
for sid, grp in df.groupby("_sheet_id"):
    year_map[sid] = infer_year(grp.get("post_date", pd.Series([]))) or "?"

df["year"]         = df["_sheet_id"].map(year_map)
df["area_category"] = df["area"].apply(categorize_area)
df["region_broad"]  = df["region"].apply(broad_region)
df["tt_ntt"]        = df["tt_ntt"].apply(norm_tt) if "tt_ntt" in df.columns else None

# Drop rows where all key identity fields are blank
key_fields = [c for c in ["institution", "area", "job_rank"] if c in df.columns]
df = df.dropna(subset=key_fields, how="all")

# Clean expired
if "expired" in df.columns:
    df["expired"] = df["expired"].apply(lambda v: str(v).strip().title() if pd.notna(v) else None)

df = df.where(pd.notna(df), None)
df = df.rename(columns={"_tab": "tab", "_sheet_id": "sheet_id", "job_rank": "rank"})

records = df.to_dict(orient="records")

meta = {
    "total":           len(records),
    "sheets":          df["sheet_id"].nunique(),
    "years":           sorted({v for v in year_map.values() if v != "?"}, reverse=True),
    "generated":       pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "area_categories": sorted(df["area_category"].dropna().unique().tolist()),
    "regions_broad":   sorted([r for r in df["region_broad"].dropna().unique().tolist()]),
    "tt_ntt_vals":     sorted([v for v in df["tt_ntt"].dropna().unique().tolist()]),
    "tabs":            sorted(df["tab"].dropna().unique().tolist()),
}

out_path = os.path.join(script_dir, OUT)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({"meta": meta, "jobs": records}, f, ensure_ascii=False, separators=(",", ":"))

sz = os.path.getsize(out_path) / 1024
print(f"Written {len(records):,} records ({sz:.0f} KB) → {out_path}")
print("Years found:", meta["years"])
print("Area categories:", meta["area_categories"])
print("Regions:", meta["regions_broad"])
print("TT/NTT:", meta["tt_ntt_vals"])
