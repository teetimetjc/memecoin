"""Is XRP's $30 hole live YES-side damage, or old DOWN-side damage?

WHY THIS IS THE RIGHT NEXT QUESTION. patterns.py found XRP negative in both
chronological halves -- the only bucket in weeks to clear that bar. But
"negative in both halves" is a time cut and nothing else, and there is a
specific confound sitting underneath it: the DOWN and NO bets were the $10
era, and DOWN lost $20.59 across 10 bets. If a few of those were XRP, then
"XRP is bad" is really "NO on XRP at $10 was bad", which is a thing the bot
already stopped doing. That would make the finding stale rather than
actionable, and switching a coin off for it would be acting on history that
cannot repeat.

So this splits XRP three ways -- side, stake, hour -- and prints the other
four coins beside it on the same cuts, because a number with nothing to
compare it to is not evidence. The comparison is the point: if XRP's YES
bets at the current stake look like everyone else's, the hole is in the
columns that are already closed.

HOURS ARE PRINTED IN CDT because that is the clock being read. The sheet
stores UTC; the shift is applied in one place and labelled.

EXPECT THE HOUR CUT TO SAY NOTHING. 52 bets across 24 hours is two bets an
hour, and hourtest already failed on exactly that. It is included because it
was asked for, and it is reported with its n so it cannot be mistaken for a
finding. A bucket with six bets is not a weak signal; it is no signal.

Read-only.
"""
import collections

import patterns as PT
import tally as T

CDT_SHIFT = -5          # the sheet is UTC; this is the only place that moves


def hour_cdt(ts):
    """Hour of day in CDT from a padded 'YYYY-MM-DD HH:MM' UTC string."""
    try:
        return (int(str(ts)[11:13]) + CDT_SHIFT) % 24
    except (ValueError, IndexError):
        return None


def cut(title, group, keyfn, order=None, minimum=10):
    print(f"  {title}")
    if not group:
        print("    (no bets)")
        return
    g = collections.defaultdict(list)
    for b in group:
        g[keyfn(b)].append(b)
    PT.header()
    keys = order if order is not None else sorted(g, key=lambda k: str(k))
    for k in keys:
        if k in g:
            PT.line(str(k), g[k], minimum=minimum)
    print()


def main():
    _bets, settled = PT.load()
    print("=" * 100)
    print("XRP, SPLIT THREE WAYS -- is the hole live, or already closed?")
    print("=" * 100)
    if not settled:
        print("  nothing settled yet")
        return 0

    xrp = [b for b in settled if b["coin"] == "XRP"]
    rest = [b for b in settled if b["coin"] != "XRP"]
    print(f"  {len(xrp)} settled XRP bets, {len(rest)} on the other four coins")
    print("  'need' is each group's own mean cost per contract: a winning")
    print("  contract pays exactly $1, so the break-even is what was paid.\n")

    def side(b):
        s = str(b.get("side") or "").upper()
        return "YES/UP" if s in ("UP", "YES") else (s or "?")

    print("-" * 100)
    print("BY SIDE -- the confound. DOWN/NO was the $10 era and is switched off.")
    print("-" * 100)
    cut("XRP", xrp, side)
    cut("the other four coins", rest, side)

    print("-" * 100)
    print("BY STAKE -- was it the $10 era, which is also over?")
    print("-" * 100)
    cut("XRP", xrp, lambda b: f"${T.stake_of(b) or '?'}")
    cut("the other four coins", rest, lambda b: f"${T.stake_of(b) or '?'}")

    print("-" * 100)
    print("XRP, YES ONLY, AT THE STAKES STILL IN USE -- the only cell that can")
    print("still happen. If this one is fine, the hole is already closed.")
    print("-" * 100)
    live_now = [b for b in xrp if side(b) == "YES/UP"
                and str(T.stake_of(b) or "") in ("4", "5", "6")]
    PT.header()
    PT.line("XRP YES $4-6", live_now, minimum=10)
    peers = [b for b in rest if side(b) == "YES/UP"
             and str(T.stake_of(b) or "") in ("4", "5", "6")]
    PT.line("others YES $4-6", peers, minimum=10)
    print()
    if live_now:
        older, newer = PT.halves(live_now)
        print("  and split in half, because that is the bar everything else "
              "has to clear:")
        PT.header()
        PT.line("older half", older, minimum=10)
        PT.line("newer half", newer, minimum=10)
        print()

    print("-" * 100)
    print("BY HOUR, CDT -- asked for, and expected to say nothing. 52 bets")
    print("across 24 hours is two an hour; read the n before the gap.")
    print("-" * 100)
    cut("XRP by CDT hour", xrp, hour_cdt, order=list(range(24)), minimum=10)

    print("  Grouped into four six-hour blocks, which is the finest cut this")
    print("  sample can carry at all:")
    cut("XRP by CDT block", xrp,
        lambda b: {0: "00-06", 1: "06-12", 2: "12-18", 3: "18-24"}[
            (hour_cdt(b["ts"]) or 0) // 6],
        order=["00-06", "06-12", "12-18", "18-24"])

    print("=" * 100)
    print("  HOW TO READ THIS. If XRP's loss sits in DOWN/NO or in $10, it is")
    print("  history that cannot repeat and the coin needs no action. If it")
    print("  survives into YES at $4-6 and is negative in both halves of that")
    print("  cell, it is live damage and switching XRP off is a one-line")
    print("  change. An hour bucket with ten bets decides nothing either way.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
