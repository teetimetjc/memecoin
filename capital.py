"""How much money has to be in the account to run these rules at $10 a bet?

This is NOT the same question as "is the edge real" and it does not depend on
the answer. Even a rule with a genuine edge needs enough cash to (a) hold
every position that is open at the same moment and (b) survive the worst
losing stretch it will hit before the edge shows up. Those are two different
numbers and the second one is much larger than people expect.

WHAT IS OPEN AT ONCE. These are 15-minute markets entered at T-9 and held to
settlement, so a bet lives nine minutes and dies. Bets sharing a close time
are open simultaneously; bets in different windows are not. Peak concurrent
exposure is therefore the largest number of qualifying bets in any single
close time, times the cost of a bet. Five coins times two sides caps it at
ten, but the 80-96c filter means the real maximum is lower -- and measured,
not assumed, because the cap and the observed peak are different numbers and
only one of them is a capital requirement.

WHAT THE BAD STRETCH COSTS. A rule winning 89.7% at 88c still loses one bet
in ten, and losses are much bigger than wins: a loser gives up the whole
~$10 cost, a winner makes about 25c. So eleven straight wins are undone by
one loss. The equity path is a long shallow climb with sudden cliffs, and the
deepest peak-to-trough fall on that path is the real account requirement.
Anyone funding only the concurrent exposure gets stopped out by variance
while the edge is still working, which is the single most common way a
positive-expectancy rule loses all its money.

TWO HONEST LIMITS ON WHAT FOLLOWS.

  THE DRAWDOWN IS MEASURED ON THE HISTORY THESE RULES WERE FOUND IN. That
  makes the profit figure meaningless, which is why none is quoted here. But
  the drawdown is a different statistic from the mean, and a search that
  selected for profitability selected AGAINST deep drawdowns -- so the
  historical worst case is, if anything, optimistic. It is a floor on the
  requirement, not a ceiling.

  THE WORST FALL SO FAR IS NOT THE WORST POSSIBLE. 11,546 bets is about six
  weeks. A rule run for a year will see something worse than its six-week
  worst, for no reason other than having more chances. The multiple applied
  at the bottom is a convention, not a measurement, and it is labelled as
  one.

Read-only. Places nothing, reads the same two tabs score.py reads.
"""

import collections
import random
import statistics as st
import sys

import score as S

# Conventional cushion on the observed worst drawdown. Not measured: a longer
# run sees a worse stretch than a short one purely from having more chances,
# and this is the usual allowance for that, not a result.
SAFETY = 3.0


def cost_of(px):
    """What one bet actually ties up: contracts, their price, and the fee."""
    c = int(S.STAKE / px)
    return c * px + S.fee(c, px)


def concurrency(bs):
    """Largest simultaneous exposure, in dollars and in bets.

    Keyed on close time because entry is T-9 and exit is settlement: every
    bet in one window overlaps every other bet in that window and nothing
    outside it.
    """
    per = collections.defaultdict(float)
    cnt = collections.Counter()
    for v, ct, px, won in bs:
        per[ct] += cost_of(px)
        cnt[ct] += 1
    if not per:
        return None
    vals = sorted(per.values())
    counts = sorted(cnt.values())
    return dict(
        peak=vals[-1], p99=vals[int(0.99 * (len(vals) - 1))],
        median=st.median(vals),
        peak_bets=counts[-1], median_bets=st.median(counts),
        windows=len(per),
        # Named to match what show() prints. drawdown() has its own
        # worst_at, which is a different moment -- the bottom of the equity
        # curve, not the window holding the most money -- and conflating
        # the two would report one as the other.
        worst_at=max(per, key=lambda k: per[k]),
    )


def drawdown(bs):
    """Deepest peak-to-trough fall on the equity path, in close-time order.

    Ordered by close time, not by the order rows happen to sit in the sheet.
    Within a window the order is arbitrary and irrelevant -- all those bets
    resolve together -- so each window is applied as one step.
    """
    per = collections.defaultdict(float)
    for v, ct, px, won in bs:
        per[ct] += v
    eq = 0.0
    peak = 0.0
    worst = 0.0
    worst_at = None
    run = 0
    worst_run = 0
    for ct in sorted(per):
        eq += per[ct]
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > worst:
            worst, worst_at = dd, ct
        if per[ct] < 0:
            run += 1
            worst_run = max(worst_run, run)
        else:
            run = 0
    return dict(worst=worst, worst_at=worst_at, end=eq,
                worst_losing_windows=worst_run,
                worst_window=min(per.values()) if per else 0.0)


def ruin(bs, stakes=(100.0, 250.0, 500.0, 1000.0, 1500.0, 2500.0), trials=2000):
    """Would a given starting balance have survived, and how often?

    THE QUESTION THIS ANSWERS is the one the per-window exposure cap does
    NOT. At most ~$50 is at stake in any single 15-minute window, and that
    is a real and correct ceiling -- but a lost window is money gone, and
    there are 96 windows a day. The cap bounds one bet; the balance has to
    absorb the running sum of all of them.

    Two numbers are reported.

      THE ACTUAL PATH. The historical windows in the order they happened,
      stepping the balance and stopping if it cannot fund the next window.
      One path, so it answers "did this particular six weeks kill it" and
      nothing more general.

      RESAMPLED PATHS. Whole windows drawn with replacement, which keeps
      each window's internal correlation (the five coins move together, so
      a window's bets are not independent of each other) while reshuffling
      the order in which good and bad windows arrive. The historical
      sequence is one draw from many possible orderings and there is no
      reason to think it was the unluckiest.

    A path is counted dead when the balance can no longer fund the window
    in front of it -- not when it reaches exactly zero, since a $3 balance
    cannot place a $20 window and is just as finished.
    """
    per = collections.defaultdict(float)
    cost = collections.defaultdict(float)
    for v, ct, px, won in bs:
        per[ct] += v
        cost[ct] += cost_of(px)
    order = sorted(per)
    seq = [(per[ct], cost[ct]) for ct in order]
    if not seq:
        return []
    rnd = random.Random(17)
    out = []
    for start in stakes:
        bal = start
        died_at = None
        for i, (pnl, need) in enumerate(seq):
            if bal < need:
                died_at = i
                break
            bal += pnl
        busts = 0
        for _ in range(trials):
            b = start
            for _ in range(len(seq)):
                pnl, need = seq[rnd.randrange(len(seq))]
                if b < need:
                    busts += 1
                    break
                b += pnl
        out.append(dict(start=start, died_at=died_at, ended=bal,
                        when=(order[died_at] if died_at is not None else None),
                        bust_pct=100.0 * busts / trials))
    return out


def show_ruin(label, bs):
    rows = ruin(bs)
    if not rows:
        return
    print(f"\n   SURVIVAL -- {label}")
    print(f"     {'start':>8}  {'the real sequence':<34}  "
          f"{'bust in a reshuffled run':>24}")
    for r in rows:
        if r["died_at"] is None:
            real = f"survived, ended ${r['ended']:,.0f}"
        else:
            real = f"BUST after {r['died_at']:,} windows ({r['when'][:10]})"
        print(f"     ${r['start']:>7,.0f}  {real:<34}  {r['bust_pct']:>23.0f}%")


def show(label, bs):
    n = len(bs)
    if n < 50:
        print(f"\n  {label}: only {n} bets, too few to size an account from")
        return
    c = concurrency(bs)
    d = drawdown(bs)
    losers = sum(1 for b in bs if not b[3])
    print("\n" + "-" * 92)
    print(f"  {label}   {n:,} bets across {c['windows']:,} close times")
    print("-" * 92)
    print("   OPEN AT ONCE")
    print(f"     typical window      {c['median_bets']:.0f} bets   "
          f"${c['median']:,.0f} at risk")
    print(f"     99th percentile                ${c['p99']:,.0f}")
    print(f"     worst observed      {c['peak_bets']} bets   "
          f"${c['peak']:,.0f}   ({c['worst_at']})")
    print("   THE BAD STRETCH")
    print(f"     deepest peak-to-trough fall    ${d['worst']:,.2f}"
          f"   (bottom at {d['worst_at']})")
    print(f"     worst single window            ${d['worst_window']:,.2f}")
    print(f"     longest run of losing windows  {d['worst_losing_windows']}")
    print(f"     bets that lost                 {losers:,} of {n:,} "
          f"({100.0*losers/n:.1f}%)")
    need = c["peak"] + SAFETY * d["worst"]
    print("   ACCOUNT NEEDED")
    print(f"     ${c['peak']:,.0f} concurrent  +  {SAFETY:.0f}x the "
          f"${d['worst']:,.0f} worst fall  =  ${need:,.0f}")
    show_ruin(label, bs)
    return need


def main():
    M = S.load()
    H = S.load_hist()
    print("\n" + "=" * 92)
    print("WHAT WOULD THIS COST TO RUN AT $10 A BET?")
    print("=" * 92)
    print("  Two requirements, not one: cash to hold what is open at the same")
    print("  moment, and cash to survive the worst losing stretch. No profit")
    print("  figures appear below -- this is a sizing question and the answer")
    print("  is the same whether the edge turns out to be real or not.")

    needs = {}
    for r in S.RULES:
        print("\n" + "=" * 92)
        print(f"{r['name'].upper()}   "
              f"({100*r['band'][0]:.0f}-{100*r['band'][1]:.0f}c, "
              f"{'/'.join(r['sides'])}, T-{S.ENTRY})")
        print("=" * 92)
        hb = S.bets(H, r["band"], r["sides"])
        fb = S.bets(M, r["band"], r["sides"])
        a = show("history (where the rule was found)", hb)
        b = show("forward, since the freeze", fb)
        needs[r["name"]] = (a, b)

        if hb:
            per_day = len({x[1] for x in hb})
            print(f"\n   TURNOVER: {len(hb)/max(1,len({x[1] for x in hb})):.1f} "
                  "bets per close time. At 96 close times a day that is")
            print(f"   roughly {96*len(hb)/max(1,per_day):.0f} bets and "
                  f"${10*96*len(hb)/max(1,per_day):,.0f} of turnover a day -- "
                  "turnover, NOT")
            print("   capital. The same dollars are reused every fifteen "
                  "minutes.")

    print("\n" + "=" * 92)
    print("READ THIS BEFORE FUNDING ANYTHING")
    print("=" * 92)
    print("  The drawdown above is the worst in about six weeks of data that")
    print("  these rules were SELECTED on. A search that picked a profitable")
    print("  rule also picked, by accident, one whose bad patch happened to")
    print("  end -- so the real worst case is deeper than what is printed,")
    print("  not shallower. The 3x is a convention for that, not a figure")
    print("  derived from anything.")
    print()
    print("  Kalshi also holds the full $1 per contract on a short, so a NO")
    print("  position's margin is not its price. If the live rule includes")
    print("  the NO side, confirm the exchange's own margin treatment before")
    print("  sizing -- this script prices the premium paid, which is the")
    print("  right number for YES and possibly the wrong one for NO.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
