"""The newest order attempts with the execution columns, raw.

Exists to answer one question the old record could not: when a fill lands far
below the quote, had the ask ALREADY fallen when we looked at it (the guard
should have fired and something is wrong), or did the book gap after the
order left (the guard never could have caught it)?

Presubmit c is the executable ask read immediately before sending. Read-only.
"""
import sys
import tally as T
import live


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    sh = T.P._get_client().open_by_key(T.P.SPREADSHEET_ID)
    rows = sh.worksheet(live.LIVE_SHEET).get_all_values()
    head = rows[0]
    h = {k: i for i, k in enumerate(head)}
    print("  columns present:", ", ".join(head))
    print()

    def c(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    body = [r for r in rows[1:] if r and r[0]]
    body.sort(key=lambda r: r[0])
    print(f"{'timestamp':<20} {'sym':<10} {'entry':>6} {'presub':>7} "
          f"{'drop':>6} {'limit':>6} {'age s':>6} {'ack ms':>7} "
          f"{'status':<9} detail")
    for r in body[-n:]:
        e = T._f(c(r, "Entry ¢"))
        p = T._f(c(r, "Presubmit ¢"))
        drop = (e - p) if (e is not None and p is not None) else None
        print(f"{r[0]:<20} {str(c(r,'Symbol')):<10} "
              f"{(f'{e:.1f}' if e is not None else '-'):>6} "
              f"{(f'{p:.1f}' if p is not None else '-'):>7} "
              f"{(f'{drop:+.1f}' if drop is not None else '-'):>6} "
              f"{str(c(r,'Limit ¢')):>6} {str(c(r,'Quote age s')):>6} "
              f"{str(c(r,'Ack ms')):>7} {str(c(r,'Status')):<9} "
              f"{str(c(r,'Detail'))[:40]}")
    print()
    print("  drop = entry - presubmit. The guard declines when drop > 5.")
    print("  A blank presubmit means the pre-send read failed, and the code")
    print("  treats an unreadable quote as no reason to decline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
