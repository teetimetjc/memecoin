"""Why does the bot reach only 42% of qualifying windows? Count the gates.

THE NUMBER THIS EXPLAINS. The selection-gap run found 720 windows across the
history where a qualifying contract existed at T-9, and the bot bought in
303 of them -- 42.1%. Within the windows it did play, 2.18 legs qualified
and it bought 1.24. Three of every four opportunities pass untouched.

Raising that is the one change that shortens the wait for a verdict without
touching the signal, the band or the threshold. But a change aimed at the
wrong gate does nothing, and changing several at once means the result
cannot be attributed. So this counts before anything is changed.

THERE ARE TWO KINDS OF MISS AND THEY HAVE DIFFERENT FIXES.

  SEEN AND REFUSED. The window was scanned, a signal existed, and a gate
  turned it down -- the per-window cap, the band, funding, quiet hours.
  live.py writes these to the sheet as SKIPPED rows carrying their reason,
  so they can be counted exactly rather than inferred.

  NEVER SEEN. No row of any kind exists for that window. The session was
  between restarts, or the scan did not reach the market. Nothing in the
  sheet records a window that was never looked at, so this is measured as a
  RESIDUAL: qualifying windows in the collector's own history, minus the
  windows the sheet has any row for.

The split decides the work. Mostly refusals means editing a gate in
live.py. Mostly never-seen means session continuity, which is a workflow
problem and a different job entirely.

THE RESIDUAL IS AN UPPER BOUND, NOT A COUNT. A window with no row might
have been missed, or might have held nothing worth a row at the instant the
scan passed. The two cannot be separated from the sheet alone, so the
number is reported as "no row exists" and not as "the bot was asleep".
Claiming the stronger version is how this project has talked itself into
three wrong diagnoses already.

Read-only. Places no order, writes no row, changes no setting.
"""
import collections
import os
import re

import live
import predictor as P
import tally as T

START = os.environ.get("COVERCUT_START") or T.START
BAND = (0.90, 0.96)


def norm_reason(status, detail, note):
    """Group a skip by its CAUSE, not its wording.

    The reason strings carry run-specific detail -- a cap value, a balance,
    a ticker -- so counting them raw produces a long tail of near-identical
    lines and hides which gate dominates.
    """
    blob = " ".join(str(x or "") for x in (status, detail, note)).lower()
    if "live_max_bets" in blob or "held back" in blob:
        return "per-window cap (LIVE_MAX_BETS)"
    if "fund" in blob or "balance" in blob or "shard" in blob:
        return "funding refused"
    if "quiet" in blob:
        return "quiet hours"
    if "band" in blob or "out of band" in blob:
        return "outside the band"
    if "depth" in blob or "size" in blob or "thin" in blob:
        return "not enough size at the ask"
    if "halt" in blob:
        return "halted"
    if "reject" in blob:
        return "rejected by Kalshi"
    if not blob.strip():
        return "(no reason recorded)"
    return "other: " + re.sub(r"\d+", "N", blob)[:60]


def main():
    print("=" * 100)
    print(f"COVERAGE: which gate turns the bot away? since {START} UTC")
    print("=" * 100)

    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(live.LIVE_SHEET).get_all_values()
    if len(rows) < 2:
        print("\n  the live sheet is empty.")
        return 1
    head = {k: i for i, k in enumerate(rows[0])}

    def c(r, k):
        i = head.get(k)
        return r[i] if i is not None and i < len(r) else ""

    seen_windows = set()
    by_status = collections.Counter()
    skip_reason = collections.Counter()
    placed_windows = set()

    for r in rows[1:]:
        if not r or not r[0] or T.norm_ts(r[0]) < T.norm_ts(START):
            continue
        status = str(c(r, "Status")).strip().upper()
        by_status[status or "(blank)"] += 1
        tk = str(c(r, "Ticker")).strip()
        # The event ticker IS the window: every leg of one window shares it.
        ev = tk.rsplit("-", 1)[0] if "-" in tk else tk
        if ev:
            seen_windows.add(ev)
            if status == "PLACED":
                placed_windows.add(ev)
        if status and status != "PLACED":
            skip_reason[norm_reason(status, c(r, "Detail"),
                                    c(r, "Note") or c(r, "Reason"))] += 1

    print(f"\n  {sum(by_status.values())} sheet row(s) since {START}")
    for k, v in by_status.most_common():
        print(f"    {v:>5}  {k}")

    print("\n" + "-" * 100)
    print("SEEN AND REFUSED -- a signal existed and a gate turned it down")
    print("-" * 100)
    tot = sum(skip_reason.values())
    if not tot:
        print("  no skip rows at all. Either every seen signal was placed, or")
        print("  the skips are not being written, and those are very")
        print("  different facts. Check live.py before reading anything into")
        print("  the residual below.")
    for k, v in skip_reason.most_common():
        print(f"  {v:>5}  ({100.0 * v / tot:5.1f}%)  {k}")

    print("\n" + "-" * 100)
    print("WINDOWS")
    print("-" * 100)
    print(f"  windows with any row   : {len(seen_windows)}")
    print(f"  windows with a PLACED  : {len(placed_windows)}")
    if seen_windows:
        pct = 100.0 * len(placed_windows) / len(seen_windows)
        print(f"  of the windows it SAW, it bet in {pct:.1f}%")
        print()
        print("  Compare that against the 42.1% of ALL qualifying windows the")
        print("  selection-gap run measured. The difference between the two")
        print("  is the share lost to windows that produced no row at all.")
        print()
        if pct >= 85:
            print("  THE GATES ARE NOT THE PROBLEM. The bot bets in nearly")
            print("  every window it looks at, so the missing coverage is")
            print("  windows it never looked at -- session continuity, not a")
            print("  threshold in live.py. Editing a gate would change almost")
            print("  nothing.")
        elif pct <= 60:
            print("  THE GATES ARE THE PROBLEM. A large share of windows the")
            print("  bot actually saw were refused, and the table above says")
            print("  by what. That is an edit in live.py, and the dominant")
            print("  reason is the only one worth touching first.")
        else:
            print("  BOTH MATTER. Neither explanation dominates, so fixing")
            print("  either alone recovers part of the gap. Take the larger")
            print("  one first and re-measure before touching the second.")

    print("\n" + "=" * 100)
    print("  WHAT THIS DOES NOT SAY. A window with no row might have been")
    print("  missed, or might have held nothing worth a row when the scan")
    print("  passed it. The sheet cannot tell those apart, so the residual")
    print("  is an upper bound on what session continuity could recover,")
    print("  not a count of windows the bot slept through.")
    print()
    print("  AND RAISING COVERAGE IS NOT FREE. It does not change the signal,")
    print("  the band or the threshold, but it does roughly double the money")
    print("  at risk per day. On an edge indistinguishable from zero that")
    print("  buys a faster answer, not a better one.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
