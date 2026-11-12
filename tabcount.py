"""Every tab on the spreadsheet, its row count and its newest timestamp.

WHY THIS EXISTS. "Is the collector writing?" has come up for four different
collectors tonight and there has been no way to answer it except by reading
a workflow log -- which GitHub refuses to serve while the job is still
running, which is exactly when the question matters. Twice I reported a
collector as running when it had already died, because the run list said
in_progress and I had nothing else to check.

A row count and a newest timestamp settle it in one read, for any tab,
without knowing anything about that tab's schema.

THE NEWEST TIMESTAMP IS THE POINT, not the count. A tab with 4,000 rows
whose newest is six hours old is a dead collector that looks busy, and a
count alone cannot tell the two apart. So the first column is read as a
timestamp where it looks like one -- every collector here writes 'Seen
UTC' or an equivalent first -- and the age of the newest row is reported
beside the count.

Read-only. Writes nothing, orders nothing.
"""
import json
import os
import time
from datetime import datetime, timezone

ONLY = [t.strip() for t in (os.environ.get("TABCOUNT_ONLY") or "").split(",")
        if t.strip()]


def client():
    import gspread
    from google.oauth2.service_account import Credentials
    raw = os.environ.get("GOOGLE_CREDENTIALS")
    if not raw:
        raise SystemExit("  no GOOGLE_CREDENTIALS")
    creds = Credentials.from_service_account_info(
        json.loads(raw),
        scopes=["https://www.googleapis.com/auth/spreadsheets"])
    sid = os.environ.get("MEME_SHEET_ID")
    if not sid:
        import predictor as P
        sid = P.SPREADSHEET_ID
    return gspread.authorize(creds).open_by_key(sid), sid


def as_time(s):
    """Parse the collectors' stamp formats, or None. No defaulting."""
    s = str(s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def main():
    print("=" * 96)
    print("TAB CENSUS -- row counts and how fresh the newest row is")
    print("=" * 96)
    sh, sid = client()
    print(f"\n  spreadsheet {sid}")
    now = datetime.now(timezone.utc)
    print(f"  now {now.strftime('%Y-%m-%d %H:%M:%S')} UTC\n")
    try:
        sheets = sh.worksheets()
    except Exception as e:
        raise SystemExit(f"  could not list tabs: {str(e)[:120]}")

    print(f"  {'tab':<24}{'rows':>8}{'newest row (UTC)':>22}{'age':>12}")
    print("  " + "-" * 64)
    stale = []
    for ws in sheets:
        if ONLY and ws.title not in ONLY:
            continue
        try:
            col = ws.col_values(1)
        except Exception as e:
            print(f"  {ws.title:<24}{'ERR':>8}  {str(e)[:40]}")
            continue
        n = max(0, len(col) - 1)          # minus the header
        newest, age = None, None
        for v in reversed(col[1:]):
            t = as_time(v)
            if t:
                newest = t
                age = (now - t).total_seconds()
                break
        if newest is None:
            print(f"  {ws.title:<24}{n:>8}{'-- no timestamp':>22}{'':>12}")
            continue
        if age < 120:
            agestr = f"{age:.0f}s"
        elif age < 7200:
            agestr = f"{age/60:.0f}m"
        else:
            agestr = f"{age/3600:.1f}h"
        print(f"  {ws.title:<24}{n:>8}"
              f"{newest.strftime('%Y-%m-%d %H:%M:%S'):>22}{agestr:>12}")
        if n and age > 3600:
            stale.append((ws.title, n, agestr))

    if stale:
        print(f"\n  NOT WRITTEN TO IN OVER AN HOUR:")
        for title, n, agestr in stale:
            print(f"    {title}  ({n} row(s), newest {agestr} old)")
        print("  A tab with rows whose newest is hours old is a collector")
        print("  that died looking busy. A count alone cannot show that.")
    else:
        print("\n  every tab with rows has been written to within the hour")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
