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

# Columns to pull from DB (include all raw TT variants for coalescing)
KEEP_COLS = [
    "institution", "job_rank", "area", "tt_ntt",
    "tt-ntt-postdoc", "tenure track", "tenure-track", "type / position",
    "location", "region", "start_date", "post_date",
    "salary", "teaching_load", "notes", "_tab", "_sheet_id",
]

# ── TT/NTT normalisation ──────────────────────────────────────────────────────
# Header-row noise: values that mean "no data"
_TT_GARBAGE = {
    "nan", "none", "", "​", "type / position", "tt-ntt-postdoc",
    "tt/ntt", "tt-ntt", "tt-ntt-postdoc ",
    "do not sort", "unclear", "n/a", "?",
}

def norm_tt(v):
    if not v:
        return None
    s = str(v).strip()
    sl = s.lower()
    if sl in _TT_GARBAGE or len(sl) <= 1:
        return None
    # Visiting / Postdoc / Fixed Term first (before TT checks)
    if "visiting" in sl:                               return "Visiting"
    if "postdoc" in sl or "post-doc" in sl:            return "Postdoc"
    if "fixed" in sl or "rolling contract" in sl:      return "Fixed Term"
    # Mixed TT+NTT
    if "tt/ntt" in sl or "either" in sl:               return "TT or NTT"
    if re.search(r'\bntt\b', sl) and re.search(r'\btt\b', sl): return "TT or NTT"
    if "ntt or tt" in sl or "tt or ntt" in sl:         return "TT or NTT"
    if re.search(r'tt\s*\(\d+\)\s*\+\s*ntt', sl):     return "TT or NTT"
    if "tenure track/non tenure" in sl:                return "TT or NTT"
    # NTT signals
    if sl in ("ntt", "no", "no ", "nt", "non-tenure track", "ntt "):  return "NTT"
    if sl.startswith("ntt") or sl.startswith("non-tenure"):  return "NTT"
    if "non-tenure" in sl or "non tenure" in sl:       return "NTT"
    # TT signals — "tenure track" spelled out
    if "tenure track" in sl or "tenure-track" in sl:  return "TT"
    if "tenured" in sl:                                return "TT"
    if sl in ("tt", "yes", "tt ", "ft/tt", "yes?"):   return "TT"
    if sl in ("yes (both)", "yes (not for asst)"):     return "TT"
    if sl.startswith("tt x") or sl.startswith("tt,"):  return "TT"
    if sl.startswith("tt "):                            return "TT"
    # Longer strings — look for primary signal
    if re.search(r'\btt\b', sl) and not re.search(r'\bntt\b', sl):  return "TT"
    if re.search(r'\bntt\b', sl):                      return "NTT"
    return None   # discard ambiguous

# ── Area category ─────────────────────────────────────────────────────────────
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

# ── Broad region ──────────────────────────────────────────────────────────────
US_SIGNALS = ["us ", "u.s", "usa", "united states", "northeast", "midwest",
              "southeast", "southwest", "northwest", "mid-atlantic", "midatlantic",
              "pacific northwest", "pacific coast", "west coast", "east coast",
              "appalachia", "hawaii", "alaska", "texas", "boston", "baltimore",
              " ca", "new york", "chicago", "midsouth", "mid-south", "north central"]
def broad_region(raw):
    if not raw or str(raw).strip().lower() in ("nan", "none", ""):
        return None
    rl = str(raw).strip().lower()
    # Check Asia first to prevent "Asia Pacific" matching "pacific" in US_SIGNALS
    if any(x in rl for x in ["asia", "china", "korea", "japan", "singapore", "india",
                               "taiwan", "vietnam", "hong kong"]):
        return "Asia"
    if any(x in rl for x in US_SIGNALS) or rl in ("us", "usa", "us east", "us west", "west",
        "south", "north", "east", "midwest", "northeast", "southeast", "southwest",
        "northwest", "mid-atlantic"):
        return "United States"
    if "canada" in rl or "quebec" in rl:
        return "Canada"
    if any(x in rl for x in ["europe", " eu", "uk", "france", "netherlands", "spain",
                               "switzerland", "amsterdam", "nl", "paris"]):
        return "Europe"
    if any(x in rl for x in ["australia", "aus", "new zealand", "nz", "oceania", "melbourne"]):
        return "Australia / NZ"
    if any(x in rl for x in ["middle east", "mideast", "uae", "oil money", "kuwait", "saudi"]):
        return "Middle East"
    if any(x in rl for x in ["africa", "latin", "latam", "south america", "caribbean", "brazil"]):
        return "Latin America / Africa"
    if "remote" in rl or "worldwide" in rl or "international" in rl:
        return "International / Remote"
    return None

# ── Start date grouping (for filter dropdown) ─────────────────────────────────
def group_start_date(v):
    """Normalize raw start date to a sortable group like 'Fall 2026'."""
    if not v or str(v).strip().lower() in ('nan', 'none', ''):
        return None
    vl = str(v).strip().lower()
    m = re.search(r'20(\d{2})', vl)
    if not m:
        return None
    year = '20' + m.group(1)
    if any(x in vl for x in ('fall', 'aug', 'sep', 'oct', 'nov')):
        return f'Fall {year}'
    if any(x in vl for x in ('spring', 'spr', 'jan', 'feb', 'mar', 'apr')):
        return f'Spring {year}'
    if any(x in vl for x in ('summer', 'sum', 'may', 'jun', 'jul')):
        return f'Summer {year}'
    if any(x in vl for x in ('winter', 'win', 'dec')):
        return f'Winter {year}'
    return year  # year only if season unclear

def _sd_sort_key(g):
    """Sort 'Fall 2026' etc. chronologically descending."""
    season_order = {'spring': 1, 'summer': 2, 'fall': 3, 'winter': 4}
    parts = g.lower().split()
    try:
        year = int(parts[-1])
        season = season_order.get(parts[0], 0)
        return (-year, -season)
    except (ValueError, IndexError):
        return (0, 0)

# ── Start date cleanup ────────────────────────────────────────────────────────
_START_PREFIXES = ('fall', 'spring', 'spr ', 'sum', 'win', 'aut', 'jan', 'feb', 'mar',
                   'apr', 'may ', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec',
                   'asap', 'flex', 'imm', '20', 'first', 'second', 'acad', 'tbd',
                   'upon', 'negot', 'open', 'vary', 'late', 'early')
def clean_start_date(v):
    if not v or str(v).strip().lower() in ('nan', 'none', ''):
        return None
    v = str(v).strip()
    if len(v) <= 2:
        return None
    vl = v.lower()
    if any(vl.startswith(p) for p in _START_PREFIXES):
        return v
    if re.search(r'20\d{2}', v):   # contains a year
        return v
    return None

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
all_cols = [r[1] for r in con.execute('PRAGMA table_info(jobs)').fetchall()]
available = [c for c in KEEP_COLS if c in all_cols]
col_list = ", ".join('"' + c + '"' for c in available)
df = pd.read_sql(f'SELECT {col_list} FROM jobs', con)
con.close()

# ── Drop stray header rows ────────────────────────────────────────────────────
# Some sheets were manually sorted, leaving the header row mixed into the data.
# Those rows have cell VALUES that match column label names (e.g. "School", "Rank").
# We detect them by exact match against a set of known column-label strings.
_HEADER_CELL_VALS = frozenset([
    'school', 'university', 'institution', 'school or company',
    'rank', 'job rank', 'type / position', 'type',
    'area', 'job focus area', 'subfield', 'focus area',
    'location', 'region', 'notes', 'notes/comments',
    'deadline', 'due date', 'salary', 'link', 'url',
    'tt/ntt', 'tt-ntt', 'tenure track', 'tenure-track',
    'start date', 'date', 'post date', 'posted', 'date posted',
    'contact', 'status', 'position status',
    'do not sort', 'please do not sort', 'reminder',
])

def _is_stray_header(row):
    vals = {str(v).strip().lower() for v in row.values if pd.notna(v) and str(v).strip()}
    return len(vals & _HEADER_CELL_VALS) >= 3

# Also read all columns for wider detection
con2 = sqlite3.connect(os.path.join(script_dir, DB))
df_wide = pd.read_sql('SELECT * FROM jobs', con2)
con2.close()

# 3+ hits across all columns (original rule)
stray_all = df_wide.apply(_is_stray_header, axis=1)

# 2+ hits but only in the key semantic columns — avoids false positives from notes/urls
_KEY_COLS = ['institution', 'job_rank', 'area', 'location', 'region', 'tt_ntt']
_key_available = [c for c in _KEY_COLS if c in df_wide.columns]
def _is_header_in_key_cols(row):
    vals = {str(row[c]).strip().lower() for c in _key_available if pd.notna(row[c]) and str(row[c]).strip()}
    return len(vals & _HEADER_CELL_VALS) >= 2
stray_key = df_wide.apply(_is_header_in_key_cols, axis=1)

drop_mask = stray_all | stray_key
n_drop = drop_mask.sum()
if n_drop:
    print(f"  Removing {n_drop} stray header rows mixed into data")
    df = df[~drop_mask.values]

# ── Coalesce TT/NTT from all raw column variants ──────────────────────────────
TT_RAW_COLS = ["tt_ntt", "tt-ntt-postdoc", "tenure track", "tenure-track", "type / position"]
existing_tt = [c for c in TT_RAW_COLS if c in df.columns]
if existing_tt:
    combined_tt = df[existing_tt[0]].copy()
    for col in existing_tt[1:]:
        combined_tt = combined_tt.fillna(df[col])
    df["tt_ntt"] = combined_tt
# Drop the raw alternates
df = df.drop(columns=[c for c in TT_RAW_COLS[1:] if c in df.columns], errors='ignore')

# ── Apply transforms ──────────────────────────────────────────────────────────
df["area_category"] = df["area"].apply(categorize_area) if "area" in df.columns else "Other / Unspecified"
def broad_region_row(row):
    """Try region column first, fall back to location if region is empty."""
    result = broad_region(row.get("region"))
    if result is None:
        result = broad_region(row.get("location"))
    return result

df["region_broad"] = df.apply(broad_region_row, axis=1)
df["tt_ntt"]        = df["tt_ntt"].apply(norm_tt) if "tt_ntt" in df.columns else None
df["start_date"]    = df["start_date"].apply(clean_start_date) if "start_date" in df.columns else None
df["start_date_group"] = df["start_date"].apply(group_start_date)

# Year per sheet — hardcoded from full-text date analysis of each sheet.
# Each sheet = one academic hiring cycle; year = the starting calendar year,
# so the 2025-26 market cycle is labeled "2025".
SHEET_YEAR_MAP = {
    '14NRJYdqDCN3GgjkbKri2dVjBAghGa3QoiYIl2Y1pLDk': '2025',  # 2025-26
    '1x62jJptJB2IP-OiZTs68FUQUkVt9GK7G55nJNwy9F2Y': '2024',  # 2024-25
    '110R1iX4Jv2ufdqKvpgrMV5HHwoySNw37Vtk5DLF1ULc': '2023',  # 2023-24
    '1yfzDTxgndA-wkn8Tml5QNjgLAgfbutHLGjYM8bJOYo0': '2022',  # 2022-23
    '1_6SrJpgkK_gO2WJVTUo8E1nd3j-008Z_YrLkXe9xLp4': '2021',  # 2021-22
    '1TJA_SMhd7KBEC2wJsaGDvMEl2lMVFMN8H89ZvWWP_cc': '2020',  # 2020-21
    '1Bm5SzMeUuUaJ0FVVViq5II26YDXpLuKN4YA_nrtaqxw': '2019',  # 2019-20
    '16Q17xeFyEYoNQ1SWOqfqLuf0Nm-Tn9vR-3lYubqYVgM': '2018',  # 2018-19
    '1UwmJC5PmhRHi5QJmsGZGpyLmloPXQwxIsJqTPDTJHOE': '2017',  # 2017-18
    '1Zz6DhkzO2TFLZOnoFkT57TFVXwLhagGXE1A4AaxDwag': '2016',  # 2016-17
    '1gyB2QWkPIcUjMiBkkrwvkOg1uyo0VBrD9BDugzgEyhw': '2015',  # 2015-16
    '15bb_BdFhV3M4iDAdBX15h7HeMP3SajapOF7zJTOUXH8': None,    # year unclear
    '1-bSfridgSMqpD5ymikjfO_buGudBOt4X0TvfZ4z996g': None,    # year unclear
}
year_map = SHEET_YEAR_MAP
df["year"] = df["_sheet_id"].map(year_map)

# Drop rows where all identity fields are blank
key_fields = [c for c in ["institution", "area", "job_rank"] if c in df.columns]
df = df.dropna(subset=key_fields, how="all")

df = df.where(pd.notna(df), None)
df = df.rename(columns={"_tab": "tab", "_sheet_id": "sheet_id", "job_rank": "rank"})

records = df.to_dict(orient="records")

meta = {
    "total":           len(records),
    # Count only sheets with an identified market year (excludes 2 sheets with unknown dates)
    "sheets":          len({y for y in year_map.values() if y}),
    "years":           sorted({v for v in year_map.values() if v}, reverse=True),
    "generated":       pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "area_categories": sorted(df["area_category"].dropna().unique().tolist()),
    "regions_broad":   sorted([r for r in df["region_broad"].dropna().unique().tolist()]),
    "tt_ntt_vals":     sorted([v for v in df["tt_ntt"].dropna().unique().tolist()]),
    "tabs":            sorted(df["tab"].dropna().unique().tolist()),
    "start_date_groups": sorted(df["start_date_group"].dropna().unique().tolist(), key=_sd_sort_key),
}

out_path = os.path.join(script_dir, OUT)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({"meta": meta, "jobs": records}, f, ensure_ascii=False, separators=(",", ":"))

sz = os.path.getsize(out_path) / 1024
print(f"Written {len(records):,} records ({sz:.0f} KB) → {out_path}")
print("TT/NTT vals:", meta["tt_ntt_vals"])
# TT coverage
with_tt = sum(1 for r in records if r.get("tt_ntt"))
print(f"TT/NTT coverage: {with_tt:,} / {len(records):,} ({with_tt/len(records)*100:.0f}%)")
start_cov = sum(1 for r in records if r.get("start_date"))
print(f"Start date coverage: {start_cov:,} / {len(records):,} ({start_cov/len(records)*100:.0f}%)")
