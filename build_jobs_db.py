"""
build_jobs_db.py
Fetches "Micro Jobs" and "Macro Jobs" tabs from a list of public Google Sheets,
combines them into a SQLite database, and exports a CSV.

Usage:  python3 build_jobs_db.py
Re-run any time to refresh the database.
"""

import re
import io
import time
import sqlite3
import warnings
import requests
import pandas as pd
from datetime import datetime

warnings.filterwarnings("ignore")  # suppress urllib3 SSL noise

# ── Config ───────────────────────────────────────────────────────────────────
SHEET_URLS = [
    "https://docs.google.com/spreadsheets/d/14NRJYdqDCN3GgjkbKri2dVjBAghGa3QoiYIl2Y1pLDk/edit?gid=1544517343#gid=1544517343",
    "https://docs.google.com/spreadsheets/d/1x62jJptJB2IP-OiZTs68FUQUkVt9GK7G55nJNwy9F2Y/edit#gid=0",
    "https://docs.google.com/spreadsheets/d/110R1iX4Jv2ufdqKvpgrMV5HHwoySNw37Vtk5DLF1ULc/edit#gid=0",
    "https://docs.google.com/spreadsheets/d/1yfzDTxgndA-wkn8Tml5QNjgLAgfbutHLGjYM8bJOYo0/edit#gid=0",
    "https://docs.google.com/spreadsheets/d/1_6SrJpgkK_gO2WJVTUo8E1nd3j-008Z_YrLkXe9xLp4",
    "https://docs.google.com/spreadsheets/d/1TJA_SMhd7KBEC2wJsaGDvMEl2lMVFMN8H89ZvWWP_cc/edit?usp=sharing",
    "https://docs.google.com/spreadsheets/d/1Bm5SzMeUuUaJ0FVVViq5II26YDXpLuKN4YA_nrtaqxw",
    "https://docs.google.com/spreadsheets/d/16Q17xeFyEYoNQ1SWOqfqLuf0Nm-Tn9vR-3lYubqYVgM/edit#gid=1255328557",
    "https://docs.google.com/spreadsheets/d/1UwmJC5PmhRHi5QJmsGZGpyLmloPXQwxIsJqTPDTJHOE/edit#gid=0",
    "https://docs.google.com/spreadsheets/d/1Zz6DhkzO2TFLZOnoFkT57TFVXwLhagGXE1A4AaxDwag/edit#gid=1815539170",
    "https://docs.google.com/spreadsheets/d/1gyB2QWkPIcUjMiBkkrwvkOg1uyo0VBrD9BDugzgEyhw/edit#gid=542375755",
    "https://docs.google.com/spreadsheets/d/15bb_BdFhV3M4iDAdBX15h7HeMP3SajapOF7zJTOUXH8/edit#gid=5",
    "https://docs.google.com/spreadsheets/d/1-bSfridgSMqpD5ymikjfO_buGudBOt4X0TvfZ4z996g/edit#gid=3",
    "https://docs.google.com/spreadsheets/d/1gA91jY_Tt2W5FB4bicnQcwWMoBf4bcxcjwvZ9gSENqE/edit#gid=5",
]

TARGET_TABS = ["Micro Jobs", "Macro Jobs"]
OUTPUT_DB   = "jobs_database.db"
OUTPUT_CSV  = "jobs_database.csv"
DELAY_SEC   = 1.0

HEADERS = {"User-Agent": "Mozilla/5.0"}

# ── Helpers ───────────────────────────────────────────────────────────────────
def extract_sheet_id(url):
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    return m.group(1) if m else None


# ── Column-name inference helpers ─────────────────────────────────────────────

# Exact keywords that appear when a real data row got used as the pandas header
_EXACT_HEADER_WORDS = {
    'school', 'rank', 'institution', 'start date', 'university',
    'location', 'job focus area', 'area', 'type / position',
    'tt/ntt', 'region', 'notes', 'deadline', 'salary', 'date',
    'posted', 'link', 'status', 'country', 'contact', 'post date',
    'focus area', 'tenure track', 'job rank', 'type', 'position',
}

# Known field-name prefixes used in older sheets (e.g. "job focus area management")
# — column name = field label + space + first cell value.
# Keep only DISTINCTIVE multi-word prefixes that won't false-positive on normal
# extended column names like "location (city/state)" or "deadline for application".
_FIELD_PREFIXES = {
    'job focus area':              'area',
    'job rank':                    'job_rank',
    'tenure track':                'tt_ntt',
    'posting url':                 'link',
    'position status':             'status',
    '# of positions':              'num_positions',
    'date of info':                'post_date',
    'country region':              'region',
    'other position information':  'other_info',
    'notes and questions/answers': 'notes',
    'aom interview':               'aom_interview',
    'orientation of school':       'orientation',
    'mandates graduation':         'aacsb',
}


def _infer_col_type(series):
    """Guess what field a column contains based on value patterns."""
    vals = series.dropna().astype(str).str.strip()
    if len(vals) < 3:
        return None
    vl = vals.str.lower()

    if vl.str.match(r'^\d{1,2}/\d{1,2}/\d{2,4}$').mean() > 0.25:
        return 'post_date'
    if vl.str.contains(r'\b(university|college|institute)\b', regex=True, na=False).mean() > 0.25:
        return 'institution'
    RANKS = {'assistant', 'associate', 'full', 'open', 'lecturer', 'instructor',
             'open rank', 'assistant professor', 'associate professor',
             'assistant/associate', 'visiting', 'clinical', 'adjunct'}
    if vl.isin(RANKS).mean() > 0.10 or \
       vl.str.contains(r'\b(assistant|associate professor|open rank|lecturer)\b', regex=True, na=False).mean() > 0.10:
        return 'job_rank'
    TT_VALS = {'tt', 'ntt', 'yes', 'no', 'tenure track', 'tenure-track',
               'non-tenure', 'visiting', 'tt/ntt (either)', 'postdoc'}
    if vl.isin(TT_VALS).mean() > 0.15:
        return 'tt_ntt'
    REGION_VALS = {'us', 'usa', 'europe', 'asia', 'canada', 'australia',
                   'us west', 'us east', 'us midwest', 'us northeast',
                   'us southeast', 'asia pacific', 'international', 'uk'}
    if vl.isin(REGION_VALS).mean() > 0.15:
        return 'region'
    AREA_VALS = {'ob', 'hr', 'strategy', 'entrepreneurship', 'management',
                 'ob/hr', 'mgmt', 'ent', 'str', 'marketing', 'finance',
                 'accounting', 'hrm', 'international business', 'obhr'}
    if vl.isin(AREA_VALS).mean() > 0.10:
        return 'area'
    return None


def _fix_data_as_header(text):
    """
    Handle sheets where the first data row was treated as the pandas header.
    Two sub-cases:
      A) Column names contain field-label prefixes, e.g. 'job focus area management'
      B) Column names are plain data values, e.g. '4/21/2020', 'university of phoenix'
    Returns corrected DataFrame, or None if no fix is needed.
    """
    try:
        df = pd.read_csv(io.StringIO(text), header=None)
    except Exception:
        return None
    df = df.dropna(how='all')
    if len(df) < 2:
        return None

    # The "header" row (what pandas used) is now row 0.
    col_names = [str(c).strip().lower() for c in df.iloc[0].values
                 if pd.notna(c) and str(c).strip()]

    # Sub-case A: column names contain field-label prefixes like "job focus area management".
    # These sheets encode both the column label AND the first data value in row 0.
    def _is_prefix_data_col(col):
        for p in _FIELD_PREFIXES:
            if col.startswith(p) and col != p:
                suffix = col[len(p):].strip()
                if suffix and suffix[0] not in '(/-:?':
                    return True
        return False
    prefix_hits = sum(1 for c in col_names if _is_prefix_data_col(c))
    if prefix_hits >= 3:
        # Build rename map: integer column index → canonical field name
        rename = {}
        for idx, raw_col in enumerate(df.iloc[0]):
            cl = str(raw_col).strip().lower()
            # Prefix match (e.g. "job focus area management" → area)
            for prefix, field in sorted(_FIELD_PREFIXES.items(), key=lambda x: -len(x[0])):
                if cl.startswith(prefix) and cl != prefix:
                    if field not in rename.values():
                        rename[idx] = field
                    break
            else:
                # Institution: row-0 value itself is an institution name
                if 'institution' not in rename.values() and \
                   re.search(r'\b(university|college|school of|institute)\b', cl) and \
                   len(cl.split()) > 2:
                    rename[idx] = 'institution'
        if len(rename) >= 3:
            # Strip the prefix portion from row 0 values so they become plain data
            row0 = df.iloc[0].copy()
            for idx2, field in rename.items():
                val = str(row0.iloc[idx2]).strip()
                val_l = val.lower()
                for prefix in _FIELD_PREFIXES:
                    if val_l.startswith(prefix) and val_l != prefix:
                        row0.iloc[idx2] = val[len(prefix):].strip()
                        break
            df.iloc[0] = row0
            # Keep ALL rows (row 0 IS real data), rename columns
            df = df.rename(columns=rename)
            return df

    # Sub-case B: column names are raw data values
    has_date_col_name = any(re.match(r'^\d{1,2}/\d{1,2}/\d{2,4}$', c) for c in col_names)
    has_inst_col_name = any(re.search(r'\b(university|college)\b', c) for c in col_names)
    if not (has_date_col_name or has_inst_col_name):
        return None  # no fix needed for this sheet

    # Infer column types from data values (rows 1+, skipping the original header row)
    data_rows = df.iloc[1:].reset_index(drop=True)
    inferred = {}
    for col_idx in data_rows.columns:
        ct = _infer_col_type(data_rows[col_idx])
        if ct and ct not in inferred.values():
            inferred[col_idx] = ct

    if len(inferred) < 3:
        return None  # not enough signal

    # Include row 0 (the original header-turned-data row) back in the data
    df_all = df.rename(columns=inferred)
    return df_all


def fetch_tab(sheet_id, tab_name):
    """Fetch a named tab as a DataFrame via the gviz CSV endpoint. Returns None on failure."""
    encoded = tab_name.replace(" ", "+")
    url = (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        f"/gviz/tq?tqx=out:csv&sheet={encoded}"
    )
    try:
        resp = requests.get(url, timeout=30, headers=HEADERS)
        if resp.status_code != 200 or len(resp.text) < 50:
            return None

        df = pd.read_csv(io.StringIO(resp.text))

        # Fix 1: first DATA row looks like column labels → use it as header instead
        if len(df) > 1:
            first_row_vals = [str(v).strip().lower()
                              for v in df.iloc[0].values if pd.notna(v) and str(v).strip()]
            hits = sum(1 for v in first_row_vals if v in _EXACT_HEADER_WORDS)
            if hits >= 3:
                df = pd.read_csv(io.StringIO(resp.text), header=1)
                return df

        # Fix 2: column NAMES look like data values → content-based column inference.
        # Use strict signals: a real date string, a multi-word institution name
        # (e.g. "university of phoenix" not just "university"), or 3+ field-label prefixes.
        col_names_lower = [str(c).strip().lower() for c in df.columns]
        has_date_col  = any(re.match(r'^\d{1,2}/\d{1,2}/\d{2,4}$', c) for c in col_names_lower)
        has_inst_col  = any(
            re.search(r'\b(university|college)\b', c) and len(c.split()) > 2
            for c in col_names_lower
        )
        def _pfx_hit(c):
            for p in _FIELD_PREFIXES:
                if c.startswith(p) and c != p:
                    sfx = c[len(p):].strip()
                    if sfx and sfx[0] not in '(/-:?':
                        return True
            return False
        has_prefix = sum(1 for c in col_names_lower if _pfx_hit(c)) >= 3
        if has_date_col or has_inst_col or has_prefix:
            fixed = _fix_data_as_header(resp.text)
            if fixed is not None:
                return fixed

        return df
    except Exception:
        return None


def clean_df(df, sheet_id, tab_name, source_url):
    """Normalize columns, drop blank rows, add provenance columns."""
    # Strip whitespace from column names
    df.columns = [str(c).strip() for c in df.columns]
    # Before dropping unnamed columns, rescue any that look like institution names.
    # Some sheets store institution in an unlabeled column (e.g. "Unnamed: 2").
    # Skip col 0 (often a row-number counter).
    has_inst = any(c.strip().lower() in (
        "institution", "university", "university ", "school", "school or company",
        "university *** please do not sort ***"
    ) for c in df.columns)
    if not has_inst:
        INST_SIGNALS = {"university", "college", "institute", "school of", "business school"}
        for col in list(df.columns):
            if col.startswith("Unnamed") and col != "Unnamed: 0":
                vals = df[col].dropna()
                if len(vals) > 5 and vals.dtype == object:
                    hits = sum(1 for v in vals.head(30).tolist()
                               if any(sig in str(v).lower() for sig in INST_SIGNALS))
                    if hits > len(vals.head(30)) * 0.25:
                        df = df.rename(columns={col: "institution"})
                        break

    # Drop columns with empty or purely whitespace names
    df = df.loc[:, df.columns.str.strip() != ""]
    # Drop remaining Unnamed columns (row counters, spare columns)
    df = df.loc[:, ~df.columns.str.match(r"^Unnamed")]
    # Drop fully-empty rows
    df = df.dropna(how="all")
    # Lowercase column names for uniformity
    df.columns = [c.lower() for c in df.columns]
    # Final safety: drop any columns whose lowercased name is empty
    df = df.loc[:, df.columns != ""]
    # Provenance
    df["_source_url"]    = source_url
    df["_sheet_id"]      = sheet_id
    df["_tab"]           = tab_name
    df["_fetched_at"]    = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    return df


# ── Canonical column mapping ─────────────────────────────────────────────────
# Many sheets use slightly different headers; map them to a common schema.
CANON = {
    # posting date variants
    "date":              "post_date",
    "post date please do not sort/filter the sheet directly, copy to your local drive!!!!": "post_date",
    "post date":         "post_date",
    "date posted":       "post_date",
    # university / institution
    "university":        "institution",
    "university ":       "institution",
    "institution":       "institution",
    "school":            "institution",
    "school or company": "institution",
    "university *** please do not sort ***": "institution",
    "university*** please do not sort ***":  "institution",
    # 2023 Micro Jobs: long column name with sort reminder embedded
    "do not sort. download a copy if you need to sort school": "institution",
    # alternate location column names
    "city":              "location",
    "city/state":        "location",
    # alternate date column names
    "posted":            "post_date",
    "date of info":      "post_date",
    # rank
    "rank":              "rank",
    "rank ":             "rank",
    # tt/ntt
    "tt-ntt":            "tt_ntt",
    "tt-ntt-postdoc ":   "tt_ntt",
    "tt/ntt":            "tt_ntt",
    # start date
    "start date":        "start_date",
    "start date ":       "start_date",
    # location
    "location":          "location",
    "location ":         "location",
    # area / subfield
    "job focus area":    "area",
    "area":              "area",
    "area ":             "area",
    "subfield":          "area",
    # salary
    "salary":            "salary",
    "salary ":           "salary",
    # due date / deadline
    "due date":          "deadline",
    "deadline":          "deadline",
    # link
    "link":              "link",
    # notes
    "notes":             "notes",
    # expired
    "expired?":          "expired",
    "expired":           "expired",
    # region
    "region":            "region",
    "region ":           "region",
    # teaching load
    "teaching load details": "teaching_load",
    "teaching load":     "teaching_load",
    "teaching l":        "teaching_load",
}

def apply_canon(df):
    df = df.rename(columns={c: CANON[c] for c in df.columns if c in CANON})
    return df


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    all_frames = []
    log = []

    for url in SHEET_URLS:
        sheet_id = extract_sheet_id(url)
        if not sheet_id:
            print(f"[SKIP] cannot parse ID from: {url[:60]}")
            continue

        print(f"\n── {sheet_id[:30]}… ──")
        for tab in TARGET_TABS:
            time.sleep(DELAY_SEC)
            df = fetch_tab(sheet_id, tab)
            if df is None or df.empty:
                print(f"  [{tab}] not found or empty")
                log.append({"sheet_id": sheet_id, "tab": tab, "rows": 0, "status": "missing"})
                continue
            df = clean_df(df, sheet_id, tab, url)
            df = apply_canon(df)
            rows = len(df)
            all_frames.append(df)
            print(f"  [{tab}] {rows} rows, {len(df.columns)} cols")
            log.append({"sheet_id": sheet_id, "tab": tab, "rows": rows, "status": "ok"})

    if not all_frames:
        print("\n[ERROR] No data collected.")
        return

    combined = pd.concat(all_frames, ignore_index=True, sort=False)
    print(f"\n── Total: {len(combined):,} rows across {len(combined.columns)} columns ──")

    # Rename 'rank' → 'job_rank' to avoid FTS5 reserved-word collision
    if "rank" in combined.columns:
        combined = combined.rename(columns={"rank": "job_rank"})

    # Merge any duplicate column names (can happen when multiple inference paths
    # produce the same canonical name from different source columns).
    # For string columns, coalesce (first non-null wins); for the rest, keep first.
    dupes = combined.columns[combined.columns.duplicated()].unique().tolist()
    for col in dupes:
        cols = combined.loc[:, combined.columns == col]
        if cols.dtypes.iloc[0] == object:
            combined[col] = cols.bfill(axis=1).iloc[:, 0]
        else:
            combined[col] = cols.iloc[:, 0]
    combined = combined.loc[:, ~combined.columns.duplicated()]

    # ── SQLite ──
    con = sqlite3.connect(OUTPUT_DB)
    combined.to_sql("jobs", con, if_exists="replace", index=False)

    # Full-text search on key text columns
    WANT_FTS = {"institution", "job_rank", "area", "location", "region",
                "notes", "tt_ntt", "link", "post_date", "deadline",
                "_tab", "_sheet_id"}
    fts_cols = [c for c in combined.columns if c in WANT_FTS]
    fts_cols_sql = ", ".join(fts_cols) if fts_cols else "institution"
    con.execute("DROP TABLE IF EXISTS jobs_fts")
    con.execute(
        f"CREATE VIRTUAL TABLE jobs_fts USING fts5({fts_cols_sql}, "
        f"content=jobs, content_rowid=rowid)"
    )
    con.execute("INSERT INTO jobs_fts(jobs_fts) VALUES('rebuild')")
    con.commit()
    con.close()
    print(f"  Written → {OUTPUT_DB}")

    # ── CSV ──
    combined.to_csv(OUTPUT_CSV, index=False)
    print(f"  Written → {OUTPUT_CSV}")

    # ── Summary ──
    print("\n── Fetch summary ──")
    ok  = sum(1 for e in log if e["status"] == "ok")
    mis = sum(1 for e in log if e["status"] == "missing")
    print(f"  Tabs found: {ok}  |  Missing/unavailable: {mis}")
    print(f"  Sheets with data: {combined['_sheet_id'].nunique()}")
    if "institution" in combined.columns:
        print(f"  Distinct institutions: {combined['institution'].nunique()}")
    if "_tab" in combined.columns:
        print(f"  Rows by tab:\n  {combined['_tab'].value_counts().to_string()}")
    print("\nDone.")


if __name__ == "__main__":
    main()
