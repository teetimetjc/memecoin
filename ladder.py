"""Is the strike ladder ever self-contradictory? A test that needs no forecast.

Sixteen strategies have died here and every one of them asked the same
question: can we predict better than the price? The answer is no, measured
many ways. This asks something different, and it is the only untried CATEGORY
left in the data: is the market internally inconsistent?

THE ARITHMETIC. The hourly crypto markets are ladders -- "SOL above 75",
"SOL above 75.25", "SOL above 75.5" -- all on the same coin, all settling at
the same instant. Above 80 cannot happen without above 75, so

    payoff(YES at the lower strike)  >=  payoff(YES at the higher strike)

ALWAYS, whatever the coin does. So buy YES at the low strike and sell YES at
the high strike -- on Kalshi, selling YES means buying NO, which costs
1 - yes_bid:

    cost   = ask(low) + 1 - bid(high)
    payoff = 1 if the coin ends outside the pair, 2 if it ends between them
    locked = bid(high) - ask(low)      when that is positive

The minimum payoff is 1, so whenever bid(high) exceeds ask(low) the trade
cannot lose. That is not a forecast, an indicator or a pattern. It is the
ladder contradicting itself, and the only questions are how often it happens,
by how much, and whether the fee eats it.

WHY THIS MIGHT EXIST WHERE AN EDGE DID NOT. Nothing has to be mispriced for
this to pay -- only unsynchronised. A thin ladder where one rung's quote goes
stale while its neighbour moves produces exactly this, and the calibration
survey already found the hourly books nearly abandoned: of 400 settled hourly
ETH markets, seven had as much as five contracts of volume. Abandoned books
are where stale rungs live.

AND WHY IT MIGHT NOT. Three reasons, all checked rather than assumed:

  THE FEE IS CHARGED TWICE, once per leg, and on a one-cent crossing that is
  decisive. Every number here is net.

  A QUOTE IS NOT A FILL. Both legs must be takeable at the same moment, and
  H60 does not record resting size, so a violation found here is an
  OPPORTUNITY SEEN, not money earned. The output says so in those words.

  THE TWO LEGS MUST BE THE SAME SNAPSHOT. Comparing a bid read at T-9 with
  an ask read at T-6 would invent crossings out of the clock, which is the
  error that once manufactured a 97.2% hit rate here. Only same-offset pairs
  are compared.

Read-only.
"""

import collections
import statistics as st
import sys

import predictor as P

TABS = ("H60", "M15")
OFFSETS = (14, 12, 9, 6, 3, 1)
FEE_RATE = 0.07


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def fee(px):
    """Taker fee per single contract at this price."""
    return FEE_RATE * px * (1 - px)


def load(tab):
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    try:
        rows = sh.worksheet(tab).get_all_values()
    except Exception as e:
        print(f"  {tab}: cannot read ({str(e)[:50]})")
        return {}
    h = {k: i for i, k in enumerate(rows[0])}
    ev = collections.defaultdict(list)
    for r in rows[1:]:
        if not r or not r[0]:
            continue

        def c(k):
            i = h.get(k)
            return r[i] if i is not None and i < len(r) else ""

        strike = _f(c("Strike"))
        if strike is None:
            continue
        q = {}
        for o in OFFSETS:
            b, a = _f(c(f"bid{o}")), _f(c(f"ask{o}"))
            if b is not None and a is not None and 0 < b <= a < 1:
                q[o] = (b, a)
        if not q:
            continue
        ev[str(c("Event"))].append(dict(
            tk=str(r[0]), ser=str(c("Series")), strike=strike, q=q,
            res=str(c("Result")).lower().strip(), ct=str(c("Close Time"))))
    multi = {k: v for k, v in ev.items() if len(v) >= 2}
    print(f"  {tab}: {len(ev)} events, {len(multi)} with 2+ strikes quoted")
    return multi


def scan(events, label):
    """Every adjacent-or-wider strike pair, at every shared offset."""
    pairs = cross = 0
    gains, byser, byoff = [], collections.Counter(), collections.Counter()
    best = []
    for evname, mk in events.items():
        mk = sorted(mk, key=lambda m: m["strike"])
        for i in range(len(mk)):
            for j in range(i + 1, len(mk)):
                lo, hi = mk[i], mk[j]
                if lo["strike"] >= hi["strike"]:
                    continue
                for o in OFFSETS:
                    if o not in lo["q"] or o not in hi["q"]:
                        continue        # must be the SAME snapshot
                    pairs += 1
                    ask_low = lo["q"][o][1]
                    bid_high = hi["q"][o][0]
                    raw = bid_high - ask_low
                    if raw <= 0:
                        continue
                    net = raw - fee(ask_low) - fee(1.0 - bid_high)
                    cross += 1
                    gains.append(net)
                    byser[lo["ser"]] += 1
                    byoff[o] += 1
                    best.append((net, raw, evname, lo["strike"], hi["strike"],
                                 o, ask_low, bid_high, lo["ser"]))
    print("\n" + "=" * 104)
    print(f"{label}")
    print("=" * 104)
    print(f"  strike pairs examined at a shared offset   {pairs:,}")
    if not pairs:
        print("  nothing to compare -- no event had two strikes quoted at the "
              "same offset")
        return
    print(f"  pairs where bid(high) > ask(low)           {cross:,} "
          f"({100.0*cross/pairs:.3f}%)")
    if not cross:
        print("\n  THE LADDER NEVER CONTRADICTS ITSELF in this data. That is a")
        print("  clean negative: whatever else is wrong with these markets,")
        print("  they are internally consistent, and there is no free money in")
        print("  the relationship between strikes.")
        return
    pos = [g for g in gains if g > 0]
    print(f"  of those, still positive after both fees   {len(pos):,} "
          f"({100.0*len(pos)/cross:.1f}% of crossings)")
    print(f"  gross crossing, median                     "
          f"{100*st.median([b[1] for b in best]):.2f}c per contract")
    print(f"  net of two fees, median                    "
          f"{100*st.median(gains):+.2f}c per contract")
    if pos:
        print(f"  net of fees, total if each were filled once "
              f"${sum(pos):.2f} per contract-pair")
        print(f"  by series: "
              + ", ".join(f"{k} {v}" for k, v in byser.most_common(6)))
        print(f"  by offset: "
              + ", ".join(f"T-{k} {v}" for k, v in byoff.most_common(6)))
        print("\n  LARGEST, as examples:")
        for net, raw, evn, sl, sh_, o, al, bh, ser in sorted(
                best, reverse=True)[:8]:
            print(f"    {evn[:28]:<30} T-{o:<3} "
                  f"above {sl:g} ask {100*al:.0f}c / above {sh_:g} bid "
                  f"{100*bh:.0f}c  -> {100*net:+.2f}c net")
    print("\n  AN OPPORTUNITY SEEN IS NOT MONEY EARNED. Both legs have to be")
    print("  takeable at that instant, and this tab does not record resting")
    print("  size, so none of the above is a fill. It is a count of moments")
    print("  when the ladder disagreed with itself by more than the fee.")


def main():
    for tab in TABS:
        ev = load(tab)
        if ev:
            scan(ev, f"{tab}: STRIKE-LADDER CONSISTENCY")
    print("\n" + "=" * 104)
    print("This is the one test here that needs no forecast. If the count is")
    print("zero, the ladder is consistent and that avenue is closed for good.")
    print("If it is not zero, the next question is depth -- whether the two")
    print("legs can actually be taken -- and SLOWBOOK-style size collection")
    print("is what would answer it.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    sys.exit(main())
