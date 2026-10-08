"""Inventory every tab in the sheet: size, shape, and who still reads it.

Written to decide what can be deleted. Deleting a tab is irreversible and the
frozen specs score from specific tabs, so this reports and classifies — it
removes nothing, and nothing here can.

WHO READS WHAT is taken from the repository rather than from memory: every
.py file is scanned for the tab's name as a string, so a tab that looks dead
but is still named somewhere shows up as live. That matters because the two
frozen specs read M15 and M15H, and losing either would end tests that cannot
be restarted -- their value comes from having been declared before the data
existed, which a re-run cannot recreate.

Read-only.
"""

import collections
import os
import re
import sys

import predictor as P

# Tabs the running system cannot lose. Named explicitly as well as detected,
# because a typo in the scan must not quietly downgrade one of these.
CRITICAL = {
    "M15": "live 15-min collection — both frozen specs score from this",
    "M15H": "backfilled history — every analysis baseline",
    "Live Bets": "the real-money order log",
    "Control": "the kill switch favlive reads every window",
}


def readers(name, root="."):
    """Every .py file that mentions this tab name as a string."""
    out = []
    pat = re.compile(re.escape(name))
    for fn in sorted(os.listdir(root)):
        if not fn.endswith(".py") or fn == "tabs.py":
            continue
        try:
            with open(os.path.join(root, fn), encoding="utf-8",
                      errors="replace") as f:
                src = f.read()
        except OSError:
            continue
        # Only count it when the name appears inside a quoted string, so a
        # coincidental substring in a comment does not mark a tab live.
        for m in pat.finditer(src):
            lo = src.rfind("\n", 0, m.start())
            line = src[lo + 1:src.find("\n", m.start())]
            if f'"{name}"' in line or f"'{name}'" in line:
                out.append(fn)
                break
    return out


def main():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    wss = sh.worksheets()
    print("=" * 104)
    print(f"SHEET INVENTORY — {len(wss)} tabs")
    print("=" * 104)

    rows_total = cells_total = 0
    info = []
    for ws in wss:
        try:
            vals = ws.get_all_values()
            n = max(0, len(vals) - 1)
            hdr = vals[0] if vals else []
        except Exception as e:
            n, hdr = -1, [f"<unreadable: {str(e)[:40]}>"]
        cells = ws.row_count * ws.col_count
        rows_total += max(0, n)
        cells_total += cells
        info.append(dict(title=ws.title, rows=n, cells=cells,
                         cols=len(hdr), hdr=hdr,
                         used_by=readers(ws.title)))

    print(f"  {'tab':<22} {'data rows':>10} {'grid cells':>12} "
          f"{'cols':>5}  read by")
    print("  " + "-" * 98)
    for d in sorted(info, key=lambda x: -x["rows"]):
        who = ", ".join(d["used_by"][:4]) or "— nothing"
        if len(d["used_by"]) > 4:
            who += f" +{len(d['used_by'])-4}"
        print(f"  {d['title']:<22} {d['rows']:>10,} {d['cells']:>12,} "
              f"{d['cols']:>5}  {who}")

    print(f"\n  totals: {rows_total:,} data rows, {cells_total:,} grid cells")
    print("  A spreadsheet caps at 10,000,000 cells; a tab's grid counts even")
    print("  where it is empty, so an oversized blank tab costs the same as a")
    print("  full one.")

    print("\n" + "=" * 104)
    print("CLASSIFICATION")
    print("=" * 104)
    keep, review, dead = [], [], []
    for d in info:
        if d["title"] in CRITICAL:
            keep.append(d)
        elif d["used_by"]:
            review.append(d)
        else:
            dead.append(d)

    print("\n  KEEP — the running system depends on these")
    for d in sorted(keep, key=lambda x: -x["rows"]):
        print(f"    {d['title']:<20} {d['rows']:>9,} rows   "
              f"{CRITICAL[d['title']]}")

    print("\n  STILL REFERENCED — code names them; check before removing")
    for d in sorted(review, key=lambda x: -x["rows"]):
        print(f"    {d['title']:<20} {d['rows']:>9,} rows   "
              f"{', '.join(d['used_by'][:3])}")

    print("\n  NO CODE REFERS TO THESE — the candidates for deletion")
    if not dead:
        print("    none")
    for d in sorted(dead, key=lambda x: -x["cells"]):
        print(f"    {d['title']:<20} {d['rows']:>9,} rows   "
              f"{d['cells']:>9,} cells   cols: "
              f"{', '.join(str(x) for x in d['hdr'][:5])[:54]}")

    freed = sum(d["cells"] for d in dead)
    print(f"\n  deleting all of those frees {freed:,} cells "
          f"({100.0*freed/max(1,cells_total):.0f}% of the grid in use)")
    print("\n  Nothing above has been deleted. A tab no code reads may still")
    print("  hold the only copy of something; the backfilled history took")
    print("  days to gather and cannot be re-fetched beyond Kalshi's window.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
