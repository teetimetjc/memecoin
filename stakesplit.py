"""Did the small-stake YES version actually work, or was it just smaller?

THE QUESTION. The daily table shows five green days and two red ones, and
the red ones are the $10 days with DOWN and NO in the mix. That invites an
obvious read: go back to $4 and $7 on YES only. But stake size cannot create
edge. Betting $4 instead of $10 scales every number by 0.4 -- the wins, the
losses and the net alike. If the per-bet math was break-even, a smaller
stake loses more slowly and nothing else. So the thing to look at is ROI and
the win rate against its own break-even line, never the dollar total.

BREAK-EVEN IS COMPUTED FROM WHAT WAS PAID, NOT ASSUMED. A winning contract
pays exactly $1. So the win rate a group needs is its own mean cost per
contract -- fees included, because the fee is in the cost. A group paying
93.4c a contract needs to win 93.4% to stand still. That is the line every
group is measured against here.

IT ALSO SPLITS BY SIDE AND BY DAY, because the two bad days are not just
bigger stakes -- they are when DOWN and NO were running. Separating stake
from side is the whole point: if YES at $10 is fine and NO is the damage,
the stake was never the problem.

AND IT PRINTS THE INTERVAL. Sixty-odd bets cannot resolve a three-point
margin. A group that reads +2 points with an interval spanning -6 to +9 has
not shown anything, and saying so is the difference between a finding and a
story. Read-only.
"""
import collections
import tally as T


def be_line(group):
    """Win rate this group needs: its own mean cost per contract, in cents."""
    c = sum(b["cost"] or 0.0 for b in group)
    n = sum(int(b["contracts"] or 0) for b in group)
    return (100.0 * c / n) if n else float("nan")


def report(label, group):
    if not group:
        return
    n = len(group)
    wins = sum(1 for b in group if (b.get("rev_share") or 0.0) > 0)
    staked = sum(b["cost"] or 0.0 for b in group)
    ret = sum(b.get("rev_share") or 0.0 for b in group)
    net = ret - staked
    wr = 100.0 * wins / n
    be = be_line(group)
    lo, hi = T.wilson(wins, n)
    print(f"  {label:<22} {n:>4} bets  win {wr:>6.2f}%  need {be:>6.2f}%  "
          f"gap {wr-be:>+6.2f}  net ${net:>+8.2f}  ROI {100*net/staked:>+7.2f}%"
          if staked else f"  {label:<22} {n:>4} bets")
    print(f"  {'':<22} 95% interval on the win rate "
          f"{100*lo:.1f}% to {100*hi:.1f}%  "
          f"({'contains' if 100*lo <= be <= 100*hi else 'EXCLUDES'} break-even)")


def main():
    bets = T.rows_since(T.START)
    print("=" * 104)
    print("STAKE AND SIDE SPLIT -- is the small-stake YES version profitable, "
          "or only less expensive?")
    print("=" * 104)
    if not bets:
        print("  No filled bets recorded yet.")
        return 0

    # PAGED, for the same reason tally.py is: one page of 200 is a sliding
    # window, not a history, and it silently drops the oldest bets as new
    # ones settle.
    setts = {}
    cur, pages = None, 0
    while pages < 60:
        q = dict(limit=200)
        if cur:
            q["cursor"] = cur
        d = T.get("/portfolio/settlements", **q)
        rows = (d or {}).get("settlements") or []
        T._ingest_settlements(rows, setts)
        cur = (d or {}).get("cursor")
        pages += 1
        if not cur or not rows:
            break

    # One settlement shared across every bet on that ticker, by contract
    # count -- the same split tally.py makes. Claiming the whole payout on
    # each of two bets is what showed a profit that did not exist.
    by_ticker = {}
    for b in bets:
        by_ticker.setdefault(b["ticker"], []).append(b)
    for tk, g in by_ticker.items():
        tot = sum(int(x["contracts"] or 0) for x in g) or 1
        for x in g:
            x["_share"] = (int(x["contracts"] or 0) / tot) if len(g) > 1 else 1.0

    fills = T.actual_costs()
    est = 0
    for b in bets:
        f = fills.get(b["oid"])
        if f and f["cost"]:
            b["cost"] = f["cost"]
        else:
            b["cost"] = b["limit_cost"] or 0.0
            est += 1
        s = setts.get(b["ticker"])
        b["rev_share"] = (s["rev"] * b.get("_share", 1.0)) if s else None

    settled = [b for b in bets if b["rev_share"] is not None]
    print(f"  {len(bets)} bets, {len(settled)} settled"
          + (f", {est} costs estimated from the limit" if est else ""))
    print("  A winning contract pays $1, so 'need' is the group's own mean "
          "cost per contract.\n")

    # The sheet has carried both namings; treat either as the YES side
    # rather than silently reporting zero bets for a group that exists.
    YESISH = ("UP", "YES")

    def side_of(b):
        return str(b.get("side") or "").upper() or "?"

    print("-- BY STAKE " + "-" * 92)
    bys = collections.defaultdict(list)
    for b in settled:
        bys[T.stake_of(b) or "?"].append(b)
    for k in sorted(bys, key=lambda x: (str(x))):
        report(f"${k} a bet", bys[k])

    print("\n-- BY SIDE " + "-" * 93)
    byd = collections.defaultdict(list)
    for b in settled:
        byd[side_of(b)].append(b)
    for k in sorted(byd):
        report(k, byd[k])

    print("\n-- BY STAKE AND SIDE " + "-" * 83)
    bysd = collections.defaultdict(list)
    for b in settled:
        bysd[(T.stake_of(b) or "?", side_of(b))].append(b)
    for k in sorted(bysd, key=lambda x: (str(x[0]), x[1])):
        report(f"${k[0]} {k[1]}", bysd[k])

    print("\n-- THE QUESTION ASKED DIRECTLY " + "-" * 73)
    small = [b for b in settled
             if str(T.stake_of(b)) in ("4", "7") and side_of(b) in YESISH]
    big = [b for b in settled
           if str(T.stake_of(b)) == "10" and side_of(b) in YESISH]
    report("small YES ($4/$7)", small)
    report("big YES ($10)", big)
    print()
    print("  READ THE GAP AND THE INTERVAL, NOT THE NET. A positive gap whose")
    print("  interval still contains break-even has not shown an edge; it has")
    print("  shown a sample too small to tell. And if small YES and big YES")
    print("  have the same gap, the stake was never what went wrong -- the")
    print("  dollars differ only because the bets were smaller.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
