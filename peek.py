"""Read the sheet on a runner and print a status page, so nothing is exported.

The sheet has grown past what a browser will hand over as a single file -- two
dead paper bots alone have written ninety thousand rows of raw JSON into it --
and exporting the whole workbook to inspect four numbers was always the long
way round. The workflows already carry GOOGLE_CREDENTIALS, so a runner can
read the tabs directly and print only what was asked for.

WHAT IT PRINTS. Per tab: rows, the column count, and the first and last
timestamp it can find, which is what answers "is this still collecting". Then
three specifics worth a line each:

  M15 -- close-times collected per day, and THE LONGEST DARK GAP in each,
  which is the number that actually diagnoses the schedule. A daily
  percentage hides the shape: 60% spread evenly is a collector with a slow
  poll, while 60% in two blocks is a collector that was switched off for
  nine hours. The failure here has always been the second kind, and the gap
  is what shows it. Also how many close-times fall after the frozen spec's
  freeze date, which is the retest's progress toward 400.

  SLOWBOOK and its rotations -- rows, book versus trade split, and prints per
  hour. The fill-rate study lives here and its first 3,247 rows were written
  by a version with a broken age filter, so the cutoff is printed too.

  H60 -- the same coverage question for the hourly markets.

`tabs` narrows it to named tabs when the full sweep is more than is wanted.
Reads only: it writes nothing, to any tab.
"""

import calendar
import collections
import os
import re
import sys
import time

import predictor as P

FREEZE = "2026-09-25T00:00:00Z"
# Rows written before the age filter worked are inflated by re-ingested
# history and must not be counted as activity.
SLOWBOOK_CUTOFF = "2026-09-28 19:20:00 UTC"
TS_COL = re.compile(r"(time|utc|stamp|logged|collected|built)", re.I)


def ts_columns(hdr):
    return [i for i, h in enumerate(hdr) if TS_COL.search(str(h or ""))]


def main():
    only = {a.strip().lower() for a in sys.argv[1:] if a.strip()}
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    print("=" * 92)
    print("SHEET STATUS (read on a runner; nothing exported, nothing written)")
    print("=" * 92)
    print(f"  {'tab':<20} {'rows':>8} {'cols':>5}   {'earliest':<22} "
          f"{'latest':<22}")
    print("  " + "-" * 86)
    keep = {}
    for ws in sh.worksheets():
        title = ws.title
        if only and title.lower() not in only:
            continue
        try:
            vals = ws.get_all_values()
        except Exception as e:
            print(f"  {title:<20} could not read ({str(e)[:40]})")
            continue
        if not vals:
            print(f"  {title:<20} {'0':>8}")
            continue
        hdr, body = vals[0], [r for r in vals[1:] if any(c for c in r)]
        cols = ts_columns(hdr)
        lo = hi = ""
        if cols and body:
            stamps = sorted(str(r[c]) for r in body for c in cols
                            if c < len(r) and str(r[c]).strip())
            if stamps:
                lo, hi = stamps[0][:22], stamps[-1][:22]
        print(f"  {title:<20} {len(body):>8} {len(hdr):>5}   {lo:<22} "
              f"{hi:<22}")
        keep[title] = (hdr, body)

    # ---- M15: coverage, and the retest's progress -----------------------
    if "M15" in keep:
        hdr, body = keep["M15"]
        h = {k: i for i, k in enumerate(hdr)}
        ct, res = h.get("Close Time"), h.get("Result")
        if ct is not None:
            byday = collections.defaultdict(set)
            for r in body:
                if ct < len(r) and r[ct]:
                    byday[str(r[ct])[:10]].add(str(r[ct]))
            print("\n" + "=" * 92)
            print("M15 COVERAGE  (96 fifteen-minute close-times exist per day)")
            print("=" * 92)
            print("  A percentage hides the shape. The GAP is the schedule:")
            print("  evenly spread means a slow collector, one long hole means")
            print("  a job that never started. Target: 85%+, no gap over 1h.\n")
            days = sorted(byday)[-7:]
            today = time.strftime("%Y-%m-%d", time.gmtime())
            now_q = (time.gmtime().tm_hour * 60 + time.gmtime().tm_min)
            for d in days:
                got = sorted(byday[d])
                n = len(got)
                # Only quarter-hours that have HAPPENED count. Scanning a
                # part-finished day to midnight reported "longest gap
                # 23h15m" at 00:41, which is not a collector failing, it is
                # tomorrow not having happened yet.
                end = now_q if d == today else 1440
                poss = max(1, len(range(0, end, 15)))
                mins = {int(t[11:13]) * 60 + int(t[14:16]) for t in got
                        if len(t) >= 16}
                worst = run = 0
                for q in range(0, end, 15):
                    if q in mins:
                        run = 0
                    else:
                        run += 15
                        worst = max(worst, run)
                pct = 100.0 * n / poss
                verdict = ("OK" if pct >= 85 and worst <= 60
                           else "DARK" if worst > 120 else "thin")
                part = "  (partial day)" if d == today else ""
                print(f"  {d}   {n:>3}/{poss:<3} {pct:>3.0f}%  "
                      f"longest gap {worst//60}h{worst%60:02d}m   "
                      f"{verdict:<4} {'#' * int(30 * n / poss)}{part}")
            recent = [d for d in days if d != today][-2:]
            if recent:
                ok = all(len(byday[d]) >= 82 for d in recent)
                print(f"\n  SCHEDULE VERDICT on the last {len(recent)} full "
                      f"days: {'PASS' if ok else 'FAIL'} "
                      f"(need 82+/96 on each)")

            # A schedule change cannot be judged by whole days until a whole
            # day has passed under it. SINCE measures only the windows that
            # closed after the change went live, which is available at once.
            since = os.environ.get("SINCE", "").strip()
            if since:
                try:
                    cut = calendar.timegm(time.strptime(since[:19],
                                                        "%Y-%m-%dT%H:%M:%S"))
                except Exception:
                    print(f"\n  SINCE='{since}' is not "
                          f"YYYY-MM-DDTHH:MM:SSZ -- ignored")
                    cut = None
                if cut:
                    nowe = time.time()
                    want = [q for q in range(int(cut // 900 * 900) + 900,
                                             int(nowe), 900)]
                    allct = {t for v in byday.values() for t in v}
                    seen = set()
                    for t in allct:
                        try:
                            e = calendar.timegm(time.strptime(t[:19],
                                                "%Y-%m-%dT%H:%M:%S"))
                        except Exception:
                            continue
                        if e > cut:
                            seen.add(e)
                    hit = sum(1 for q in want if q in seen)
                    print(f"\n  SINCE {since}: {hit}/{len(want)} windows "
                          f"({100.0*hit/max(1,len(want)):.0f}%)  "
                          f"-- {(nowe-cut)/3600:.1f}h elapsed")
                    print("  This is the only honest read on a change made "
                          "less than a day ago.")
            post = {str(r[ct]) for r in body
                    if ct < len(r) and str(r[ct]) > FREEZE
                    and (res is None or (res < len(r)
                         and str(r[res]).lower().strip() in ("yes", "no")))}
            print(f"\n  graded close-times after the freeze: {len(post)}/400 "
                  f"needed by specs/fallen_favourite.md")

    # ---- H60 ------------------------------------------------------------
    if "H60" in keep:
        hdr, body = keep["H60"]
        h = {k: i for i, k in enumerate(hdr)}
        ct = h.get("Close Time")
        if ct is not None:
            byday = collections.defaultdict(set)
            for r in body:
                if ct < len(r) and r[ct]:
                    byday[str(r[ct])[:10]].add(str(r[ct]))
            print("\n  H60 coverage (24 per day): " + "  ".join(
                f"{d[5:]} {len(v)}/24" for d, v in sorted(byday.items())[-6:]))

    # ---- SLOWBOOK and its rotations -------------------------------------
    sb = sorted(t for t in keep if t.startswith("SLOWBOOK"))
    if sb:
        print("\n" + "=" * 92)
        print("SLOWBOOK -- the fill-rate study")
        print("=" * 92)
        tot = good = books = trades = 0
        prints = 0.0
        for t in sb:
            hdr, body = keep[t]
            h = {k: i for i, k in enumerate(hdr)}
            ki, ui, pi = h.get("Kind"), h.get("UTC"), h.get("Prints")
            for r in body:
                tot += 1
                # Rows from before the age filter worked are re-ingested
                # history, not activity, and are excluded from every count.
                if ui is not None and ui < len(r) and str(r[ui]) \
                        < SLOWBOOK_CUTOFF:
                    continue
                good += 1
                kind = str(r[ki]) if ki is not None and ki < len(r) else ""
                if kind == "trade":
                    trades += 1
                    if pi is not None and pi < len(r):
                        try:
                            prints += float(r[pi] or 0)
                        except ValueError:
                            pass
                elif kind == "book":
                    books += 1
        print(f"  tabs: {', '.join(sb)}")
        print(f"  rows {tot}   usable {good}   "
              f"(dropped {tot-good} written before the age filter worked)")
        print(f"  book rows {books}   trade rows {trades}   "
              f"prints aggregated {prints:.0f}")
        if trades == 0:
            print("  NO TRADE ROWS YET -- either the study just started or"
                  " the trades path is broken; check a run's log.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
