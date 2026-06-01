"""
search_jobs.py  —  Search the academic jobs database.

Usage examples:
  python3 search_jobs.py "organizational behavior"
  python3 search_jobs.py "IO psychology" --tab micro
  python3 search_jobs.py "California" --tab macro --rank assistant
  python3 search_jobs.py --stats
"""

import argparse
import sqlite3
import warnings
import sys

warnings.filterwarnings("ignore")

DB = "jobs_database.db"


def search(query, tab_filter=None, rank_filter=None, limit=30):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    if query:
        sql = """
            SELECT j.institution, j.job_rank, j.area, j.location, j.tt_ntt,
                   j.post_date, j.deadline, j._tab, j._sheet_id, j.link
            FROM jobs j
            WHERE j.rowid IN (SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH ?)
        """
        params = [query]
    else:
        sql = """
            SELECT institution, job_rank, area, location, tt_ntt,
                   post_date, deadline, _tab, _sheet_id, link
            FROM jobs WHERE 1=1
        """
        params = []

    if tab_filter:
        tab_map = {"micro": "Micro Jobs", "macro": "Macro Jobs"}
        tab_val = tab_map.get(tab_filter.lower(), tab_filter)
        sql += " AND _tab = ?"
        params.append(tab_val)

    if rank_filter:
        sql += " AND LOWER(job_rank) LIKE ?"
        params.append(f"%{rank_filter.lower()}%")

    sql += f" LIMIT {limit}"

    cur = con.execute(sql, params)
    rows = cur.fetchall()
    con.close()
    return rows


def stats():
    con = sqlite3.connect(DB)
    total = con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    by_tab = con.execute(
        "SELECT _tab, COUNT(*) FROM jobs GROUP BY _tab"
    ).fetchall()
    by_sheet = con.execute(
        "SELECT _sheet_id, COUNT(*) FROM jobs GROUP BY _sheet_id ORDER BY COUNT(*) DESC"
    ).fetchall()
    top_areas = con.execute(
        "SELECT area, COUNT(*) n FROM jobs WHERE area IS NOT NULL GROUP BY area ORDER BY n DESC LIMIT 15"
    ).fetchall()
    top_ranks = con.execute(
        "SELECT job_rank, COUNT(*) n FROM jobs WHERE job_rank IS NOT NULL GROUP BY job_rank ORDER BY n DESC LIMIT 10"
    ).fetchall()
    con.close()
    print(f"\n{'='*60}")
    print(f"  Total rows: {total:,}")
    print(f"\n  By tab:")
    for tab, n in by_tab:
        print(f"    {tab:<20} {n:>5}")
    print(f"\n  Rows per source sheet:")
    for sid, n in by_sheet:
        print(f"    {sid[:45]:<45} {n:>5}")
    print(f"\n  Top areas/subfields:")
    for area, n in top_areas:
        print(f"    {str(area):<55} {n:>4}")
    print(f"\n  Top ranks:")
    for rank, n in top_ranks:
        print(f"    {str(rank):<45} {n:>4}")
    print(f"{'='*60}\n")


def print_results(rows, query):
    if not rows:
        print(f"\n  No results for: {query!r}\n")
        return
    print(f"\n  {len(rows)} result(s):\n")
    print(f"  {'Institution':<35} {'Rank':<22} {'Area':<25} {'Tab':<12} {'Location':<20} {'Deadline'}")
    print(f"  {'-'*35} {'-'*22} {'-'*25} {'-'*12} {'-'*20} {'-'*12}")
    for r in rows:
        inst = (str(r["institution"] or ""))[:34]
        rank = (str(r["job_rank"] or ""))[:21]
        area = (str(r["area"] or ""))[:24]
        tab  = (str(r["_tab"] or ""))[:11]
        loc  = (str(r["location"] or ""))[:19]
        dl   = str(r["deadline"] or "")[:12]
        print(f"  {inst:<35} {rank:<22} {area:<25} {tab:<12} {loc:<20} {dl}")
    print()


def main():
    p = argparse.ArgumentParser(description="Search the academic jobs database.")
    p.add_argument("query", nargs="?", default=None, help="Search query (FTS5)")
    p.add_argument("--tab", choices=["micro", "macro"], help="Filter to Micro or Macro Jobs tab")
    p.add_argument("--rank", help="Filter by rank (partial match, case-insensitive)")
    p.add_argument("--limit", type=int, default=30, help="Max results (default 30)")
    p.add_argument("--stats", action="store_true", help="Show database summary statistics")
    args = p.parse_args()

    if args.stats:
        stats()
        return

    if not args.query and not args.tab and not args.rank:
        p.print_help()
        sys.exit(0)

    rows = search(args.query, tab_filter=args.tab, rank_filter=args.rank, limit=args.limit)
    print_results(rows, args.query)


if __name__ == "__main__":
    main()
