"""What would ADDING the NO side and the 80-90c band actually buy?

The narrow slice is a strict subset of the expensive favourite: 90-96c YES
sits inside 80-96c YES/NO. So switching from one to the other is not swapping
rules, it is ADDING bets -- the 80-90c YES band, and every NO bet. The
decision is about those incremental bets alone, and comparing the two rules'
averages hides them, because the wider rule's average is diluted by the subset
already being run.

So this scores three groups separately on the SAME data:

    the slice            90-96c YES          (live now)
    the increment        everything else in 80-96c YES/NO   (what gets added)
    the whole rule       80-96c YES/NO       (the two combined)

and does it on the GAP WINDOW -- the eight days of live rows that no search
ever touched, which is the only clean out-of-sample evidence either rule has
in quantity. The forward window since the freeze is reported beside it but is
much smaller.

WHY THE INCREMENT IS THE RIGHT UNIT. If the increment loses money, adding it
is a bad trade even when the combined rule still shows a profit, because the
combination is being carried by the part already running. An average over both
cannot distinguish "this is good" from "this is bad but outweighed".

WHAT THIS IS NOT. Not a test of either frozen spec -- those score on
post-freeze data around Oct 16 and nothing here moves them. The gap window was
examined after the fact, which is why it informs a sizing decision rather than
settling a hypothesis.

Read-only.
"""

import collections
import math
import sys

import score as S

SLICE = dict(band=(0.90, 0.96), sides=("YES",))
WIDE = dict(band=(0.80, 0.96), sides=("YES", "NO"))


def keyed(bs):
    """Bets keyed so the subset can be removed from the superset exactly.

    (close time, price, won) identifies a bet within a window without needing
    the side, which S.bets does not return. Two bets sharing all three are
    genuinely interchangeable for scoring, so collapsing them is harmless.
    """
    d = collections.Counter()
    for v, ct, px, won in bs:
        d[(ct, round(px, 4), won)] += 1
    return d


def subtract(wide, slim):
    """The wide rule's bets that are NOT in the narrow one."""
    w, s = keyed(wide), keyed(slim)
    out = []
    lookup = collections.defaultdict(list)
    for b in wide:
        lookup[(b[1], round(b[2], 4), b[3])].append(b)
    for k, n in w.items():
        take = max(0, n - s.get(k, 0))
        out.extend(lookup[k][:take])
    return out


def tally(label, bs):
    n = len(bs)
    if not n:
        print(f"  {label:<34} no bets")
        return None
    profit = sum(b[0] for b in bs)
    win = sum(1 for b in bs if b[3]) / n
    px = sum(b[2] for b in bs) / n
    c = int(S.STAKE / px)
    need = (c * px + S.fee(c, px)) / c
    g = collections.defaultdict(list)
    for b in bs:
        g[b[1]].append(b[0])
    se = S.cse(g, n)
    t = (profit / n / se) if se else 0.0
    print(f"  {label:<34} {n:>5} bets  ${profit:>+9.2f}  "
          f"${profit/n:>+7.3f}/bet  won {100*win:>5.1f}% vs "
          f"{100*need:>5.1f}%  t={t:>+5.1f}")
    return profit / n


def main():
    H = S.load_hist()
    M = S.load()
    if not H:
        print("no history")
        return 1
    hmax = max(m["t"] for m in H)
    G = S.load_gap(hmax)

    print("\n" + "=" * 104)
    print("ADDING THE NO SIDE: what do the EXTRA bets do on their own?")
    print("=" * 104)
    print("  The slice is a subset of the wide rule, so switching ADDS bets")
    print("  rather than replacing them. Only the increment is the decision.")

    for name, data in (("GAP WINDOW -- 8 days of live rows no search touched", G),
                       ("FORWARD -- since the freeze, much smaller", M)):
        if not data:
            continue
        print("\n" + "-" * 104)
        print(f"  {name}")
        print("-" * 104)
        slim = S.bets(data, SLICE["band"], SLICE["sides"])
        wide = S.bets(data, WIDE["band"], WIDE["sides"])
        incr = subtract(wide, slim)
        a = tally("the slice (live now)", slim)
        b = tally("THE INCREMENT (what gets added)", incr)
        c = tally("the whole wide rule", wide)
        if b is not None:
            print()
            if b < 0:
                print(f"    The increment loses ${-b:.3f} a bet here. Adding it")
                print("    is a bad trade on this evidence whatever the")
                print("    combined average says, because the combination is")
                print("    being carried by the part already running.")
            else:
                print(f"    The increment makes ${b:.3f} a bet here.")

    print("\n" + "=" * 104)
    print("  History, for contrast -- the data both rules were SELECTED in, so")
    print("  it cannot argue for either, only show what the search liked.")
    print("=" * 104)
    slim = S.bets(H, SLICE["band"], SLICE["sides"])
    wide = S.bets(H, WIDE["band"], WIDE["sides"])
    tally("the slice", slim)
    tally("THE INCREMENT", subtract(wide, slim))
    tally("the whole wide rule", wide)
    return 0


if __name__ == "__main__":
    sys.exit(main())
