"""How often does the retry path -- and therefore item 4's fallback -- run?

WHAT CANNOT BE ANSWERED FROM THIS DATA, stated first. Nothing records which
bets were re-quoted, and a retried bet that succeeds writes an empty Detail
exactly like a first-attempt one. So "how many bets used the last_price
fallback" is NOT recoverable from the sheet. What IS recoverable is how often
the retry path ran at all, which is an upper bound on how often the fallback
could have fired.

The new `Quote age s` column will answer the real question going forward: a
re-quoted bet waits 15s per attempt, so its quote age will be far larger than
a first-attempt bet's.

Read-only.
"""
import collections
import tally as T
import live


def main():
    sh = T.P._get_client().open_by_key(T.P.SPREADSHEET_ID)
    rows = sh.worksheet(live.LIVE_SHEET).get_all_values()
    if len(rows) < 2:
        print("  no rows")
        return 0
    h = {k: i for i, k in enumerate(rows[0])}

    def c(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    body = [r for r in rows[1:] if r and r[0] >= T.START.replace(" ", " ")]
    print("=" * 84)
    print("RETRY-PATH AUDIT -- upper bound on item 4's exposure")
    print("=" * 84)
    print(f"  {len(body)} rows since {T.START}\n")
    det = collections.Counter()
    for r in body:
        d = str(c(r, "Detail")).strip()
        st = str(c(r, "Status")).strip()
        if not d:
            det[f"{st} (no detail)"] += 1
        else:
            det[f"{st}: {d[:60]}"] += 1
    for k, v in det.most_common(20):
        print(f"   {v:>4}  {k}")
    never = sum(v for k, v in det.items() if "never listed" in k)
    requoted = sum(v for k, v in det.items() if "re-quoted" in k)
    print()
    print(f"  reached the retry loop and never listed : {never}")
    print(f"  re-quoted then skipped (band cut)       : {requoted}")
    print(f"  re-quoted then BET                      : NOT RECORDED")
    print()
    print("  The first two are the visible tail of the retry path. A bet that")
    print("  retried successfully is indistinguishable from one that did not,")
    print("  so the exact count is unavailable and is marked so rather than")
    print("  estimated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
