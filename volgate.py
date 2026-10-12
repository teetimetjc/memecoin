"""Was the market already moving when we decided to bet, and does it matter?

THE EVENT THIS COMES FROM. On 2026-10-12 at 08:35 UTC three orders went out
in the same close window -- DOGE quoted 92.4c, SOL 90.0c, XRP 94.3c -- and
came back filled at 61.0c, 67.0c and 61.0c. All three lost, -$19.39 in a
minute, 61% of that day's loss. The quotes said 90-94c favourites. The
prices actually paid said something nearer 61c, and the results agreed with
the prices rather than with the quotes.

So those were not favourites that crashed. They were markets already
crashing when the order landed, selected off a quote that had expired.

WHAT THIS ASKS. The narrow slice selects on one number: the yes ask at T-9,
between 90c and 96c. That number says where the price IS and nothing about
where it was going. A market that fell 99c -> 92c over the preceding minutes
lands in the band BECAUSE it is deteriorating; one that rose 85c -> 92c
lands in the same band while strengthening. The rule cannot tell them apart.
This measures whether it should.

THE MEASURE IS COMPUTABLE AT DECISION TIME, which is the only kind worth
testing. The sheet stores the ask at offsets 14, 12 and 9, so at the moment
of the T-9 decision the three most recent readings are all in hand and
nothing after T-9 is touched. Two summaries of them:

    DROP    ask14 - ask9, in cents. Positive means the favourite was
            weakening into the entry. This is the 08:35 signature.
    RANGE   max - min across the three, in cents. Direction-blind
            choppiness: a market moving fast either way.

WHAT THIS CAN AND CANNOT PROVE, stated up front because the honest answer is
narrower than the question that prompted it.

  IT CAN say whether a falling or choppy entry wins less often at the quoted
  price. That is a property of the market and it is what history holds.

  IT CANNOT say whether a falling entry gets a worse FILL, because the
  history has no fills in it -- every backtest here prices the bet at the
  quote, which is the assumption the 08:35 bets broke. Testing that needs
  the live record's quote-versus-fill pairs, which is a separate job and a
  far smaller sample.

  So a clean result here means "avoid falling entries because they lose",
  not "avoid them because they fill badly". Those are two different reasons
  to build the same gate, and only the first is settled by this script.

MULTIPLE COMPARISONS, declared rather than discovered: two measures at four
thresholds each is eight tests, and something will look good by luck. The
defence is that the whole curve gets printed instead of the best cell. A
real effect degrades smoothly as the threshold loosens; a lucky one spikes
in a single bucket and vanishes either side of it. Read the shape, not the
maximum.

Read-only: opens the sheet, writes nothing, places no orders, makes no API
calls to the exchange at all.
"""

import collections
import math
import statistics as st
import sys

import predictor as P
import score as S


TAB = S.TAB
MAJORS = set(S.MAJORS)
STAKE = S.STAKE
# THREE NESTED BANDS, AND WHY NOT JUST THE LIVE ONE. The narrow slice is
# 90-96c, which is roughly one market in twenty: the whole post-freeze
# history holds about 120 such bets, and 120 cannot answer a question about
# a tail. The hypothesis here is not about the slice, it is about market
# dynamics -- whether a price falling into a band predicts that band failing
# -- so it is tested where there is enough data to see it, then checked for
# the same DIRECTION inside the live band.
#
# Only the price band changes between the three. All of them stay YES-only,
# at the same T-9 entry, held to settlement, priced the same way. Widening
# the band is not switching to the expensive-favourite rule, which bets
# either side and whose NO leg measured no edge at all over 2,052 bets.
#
# The slice line is the one to read for a verdict about the live bot. The
# wider lines say whether the effect exists at all, because an effect that is
# absent at 1,500 bets is not hiding in 120.
BANDS = (("live slice  90-96c", (0.90, 0.96), 100),
         ("wider       80-96c", (0.80, 0.96), 500),
         ("widest      70-96c", (0.70, 0.96), 500))
# The three offsets available at the moment of a T-9 decision. Order matters:
# oldest first, so DROP reads as "fell by" when positive.
PATH = (14, 12, 9)
# Thresholds in cents. Each is "gate out anything at or above this".
CUTS = (2, 4, 6, 10)


def load_paths():
    """Settled majors after the freeze, each with its pre-decision ask path.

    Deliberately a near-copy of score.load() rather than an import of it:
    score.load returns only the entry bid and ask, having thrown the rest
    away, and widening it would change the function the frozen specs are
    scored by. A read-only script is not worth that risk.
    """
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(TAB).get_all_values()
    h = {k: i for i, k in enumerate(rows[0])}
    cut = S.ts(S.FREEZE)
    out = 0
    kept = []
    thin = 0
    for r in rows[1:]:
        if not r or not r[0]:
            continue

        def c(k):
            i = h.get(k)
            return r[i] if i is not None and i < len(r) else ""

        if str(c("Series")) not in MAJORS:
            continue
        res = str(c("Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        t = S.ts(c("Close Time"))
        if t is None or t <= cut:
            continue
        out += 1
        a = [S._f(c(f"ask{m}")) for m in PATH]
        b9 = S._f(c(f"bid{S.ENTRY}"))
        a9 = a[-1]
        # The entry quote must be a real two-sided price, the same guard
        # score.py applies. Without it a crossed or empty book enters the
        # sample as a bargain.
        if a9 is None or b9 is None or not (0 < b9 <= a9 < 1):
            continue
        # The PATH must be complete. A market missing ask14 cannot be judged
        # on how it moved, and guessing a value would manufacture the calm
        # readings this is trying to count.
        if any(x is None or not (0 < x < 1) for x in a):
            thin += 1
            continue
        kept.append(dict(ct=str(c("Close Time")), series=str(c("Series")),
                         ask=a9, path=[100.0 * x for x in a],
                         yes=(res == "yes")))
    print(f"  {TAB}: {out:,} graded majors after the freeze")
    print(f"  {len(kept):,} with a complete T-14/T-12/T-9 ask path "
          f"({thin:,} dropped for a gap in it)")
    return kept


def slice_bets(M, band):
    """YES bets in one price band, priced exactly as score.py prices it."""
    lo, hi = band
    out = []
    for m in M:
        px = m["ask"]
        if not (lo <= px < hi):
            continue
        c = int(STAKE / px)
        if c < 1:
            continue
        won = m["yes"]
        v = (c if won else 0) - (c * px + S.fee(c, px))
        drop = m["path"][0] - m["path"][-1]
        rng = max(m["path"]) - min(m["path"])
        out.append(dict(v=v, ct=m["ct"], px=px, won=won,
                        drop=drop, rng=rng))
    return out


def line(label, bs, indent="    "):
    n = len(bs)
    if not n:
        print(f"{indent}{label:<22} no bets")
        return None
    profit = sum(b["v"] for b in bs)
    per = profit / n
    win = 100.0 * sum(1 for b in bs if b["won"]) / n
    px = st.mean([b["px"] for b in bs])
    c = int(STAKE / px)
    need = 100.0 * (c * px + S.fee(c, px)) / c
    g = collections.defaultdict(list)
    for b in bs:
        g[b["ct"]].append(b["v"])
    se = S.cse(g, n)
    t = (per / se) if se else 0.0
    print(f"{indent}{label:<22} {n:>6,} bets  won {win:>5.1f}% vs "
          f"{need:>5.1f}% needed  ${per:>+7.3f}/bet  t={t:>+6.2f}")
    return dict(n=n, per=per, t=t, win=win, need=need)


def sweep(bs, key, name):
    print(f"\n  BY {name}, the full curve:")
    print(f"    (gate = refuse the bet when {name.lower()} is at or above "
          f"the cut)")
    print()
    print(f"    {'cut':>5}  {'KEPT':>46}")
    base = line("no gate", bs)
    print()
    for k in CUTS:
        kept = [b for b in bs if b[key] < k]
        gone = [b for b in bs if b[key] >= k]
        r = line(f"< {k}c  kept", kept)
        line(f">= {k}c  refused", gone, indent="      ")
        if r and base and gone:
            d = r["per"] - base["per"]
            print(f"        gate moves profit per bet by ${d:+.3f} and "
                  f"declines {100.0*len(gone)/len(bs):.0f}% of the volume")
        print()
    return base


def main():
    print("=" * 96)
    print("DOES A FALLING OR CHOPPY ENTRY LOSE MORE OFTEN?")
    print("  narrow slice, 90-96c yes ask at T-9, judged on the ask path at "
          "T-14 / T-12 / T-9")
    print("=" * 96)

    M = load_paths()

    def pct(v, q):
        return v[min(len(v) - 1, int(q * len(v)))]

    base = None
    for name, band, floor in BANDS:
        bs = slice_bets(M, band)
        print("\n" + "-" * 96)
        print(f"BAND: {name}      {len(bs):,} YES bets")
        print("-" * 96)
        if len(bs) < floor:
            print(f"  only {len(bs):,} bets, under the {floor:,} this band "
                  "needs to be read. NOT ENOUGH DATA YET.")
            continue

        d = sorted(b["drop"] for b in bs)
        r = sorted(b["rng"] for b in bs)
        print("  WHAT GOES IN:")
        print(f"    drop  (ask14 - ask9)  median {pct(d,0.5):+.1f}c   "
              f"75th {pct(d,0.75):+.1f}c   90th {pct(d,0.90):+.1f}c   "
              f"99th {pct(d,0.99):+.1f}c")
        print(f"    range (max - min)     median {pct(r,0.5):.1f}c   "
              f"75th {pct(r,0.75):.1f}c   90th {pct(r,0.90):.1f}c   "
              f"99th {pct(r,0.99):.1f}c")
        print("\n  A median drop near zero is the expected shape: most "
              "entries are quiet. The question is entirely about the tail.")

        b1 = sweep(bs, "drop", "DROP INTO THE ENTRY")
        sweep(bs, "rng", "RANGE ACROSS THE PATH")
        if base is None:
            base = b1

    print("=" * 96)
    print("  HOW TO READ THIS")
    print()
    print("  THE WIDER BANDS ARE FOR POWER, NOT FOR A VERDICT. They say")
    print("  whether a falling entry predicts a loss at all. Only the live")
    print("  slice line speaks about the live bot, and if the wider bands")
    print("  show nothing then the slice has nothing to find either.")
    print()
    print("  A gate worth building shows BOTH of these:")
    print("    - the refused bets lose, or win clearly less, at every cut")
    print("    - the effect fades smoothly as the cut loosens, rather than")
    print("      living in one bucket")
    print()
    print("  A gate NOT worth building looks like a single good cell with")
    print("  noise either side of it, or refuses a large share of volume to")
    print("  move profit per bet by a fraction of a cent.")
    print()
    print("  AND WHATEVER THIS SHOWS, it is about the market, not the fill.")
    print("  The 08:35 losses were bets whose PRICE PAID was 30c below the")
    print("  quote, and no backtest here can see a fill. If the gate shows")
    print("  nothing, that does not clear falling entries -- it only means")
    print("  the damage is in the execution rather than in the market, and")
    print("  the live quote-versus-fill record is where to look next.")
    if base:
        print()
        print(f"  Ungated baseline for reference: ${base['per']:+.3f}/bet "
              f"at t={base['t']:+.2f} over {base['n']:,} bets.")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    sys.exit(main())
