"""Which bets did the bot take, and how did the ones it skipped do?

THE GAP. Price the live bot's own bets at their QUOTED entry and they come
to -1.48% of stake. The scoring harness, running the same rule and band
over its window, got +2.84% on quotes. Execution is not the difference --
fills measured +0.80 points in our favour. So the difference is WHICH BETS
GOT TAKEN, and that is what this measures.

EVERYTHING HERE IS PRICED AT THE T-9 QUOTE, taken and skipped alike. That
is the point: holding the price source fixed removes fills from the
comparison entirely, so whatever remains is selection and nothing else.

TWO SEPARATE QUESTIONS, because they have different causes and different
fixes:

  WITHIN a window the bot played -- of the markets that qualified at T-9,
  it bought some and not others. FAVLIVE_MAX_PER_WINDOW caps how many it
  takes, and if the ones it leaves behind do BETTER, the cap is not
  rationing risk, it is picking badly: taking whatever the scan reached
  first rather than whatever was best.

  ACROSS windows -- the bot was absent from many windows entirely, through
  funding halts, outages, quiet hours and sessions cancelling each other.
  If the windows it missed were better than the ones it played, the record
  is a biased sample of the rule and the headline number says more about
  the uptime than about the strategy.

WHY BOTH MATTER AND NEITHER IS OBVIOUS. A cap that picks badly is a bug
with a one-line fix. Missing good windows is an infrastructure problem.
Either could produce the four-point gap, and the fixes have nothing in
common, so guessing between them is worse than measuring.

THE COMPARISON IS WITHIN-WINDOW WHERE IT CAN BE. Legs sharing a close time
resolve together about 96% of the time, so comparing a taken leg against a
skipped leg in the SAME window controls for the thing that dominates the
outcome. Comparing across windows does not, and is reported separately and
labelled as the weaker of the two.

WHAT THIS CANNOT DO. It cannot say what the bot WOULD have filled at on the
skipped bets -- there is no fill for an order never sent, and a market the
scan did not reach may have been unreachable for a reason. So a skipped set
that looks better is a lead, not a verdict, and the fix it points to still
has to be paper-tested before it touches money.

Read-only. Places no order, writes no row, changes no setting.
"""
import collections
import math
import statistics as st
import sys
import time

import backfill as B
import tally as T
import coincut as C

SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")
ENTRY_OFFSET = 9
BAND_LO, BAND_HI = 0.90, 0.96
PER_SERIES = 4000
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
FEE = 0.07


def ask_at(cs, close_ts, off):
    want = close_ts - off * 60
    best = None
    for cd in cs or []:
        t = B._f(cd.get("end_period_ts"))
        if t is None or t > want:
            continue

        def g(k):
            v = cd.get(k)
            return (B._f(v.get("close_dollars")) if isinstance(v, dict)
                    else None)
        a, b = g("yes_ask"), g("yes_bid")
        if a is None or b is None or not (0 < b <= a < 1):
            continue
        if best is None or t > best[0]:
            best = (t, a)
    return best[1] if best else None


def value(px, won, stake=5.0):
    """Net dollars for one quoted bet, at the live stake and fee."""
    c = int(stake / px)
    if c < 1:
        return None, 0.0
    fee = math.ceil(FEE * c * px * (1 - px) * 100.0 - 1e-9) / 100.0
    return (c * (1.0 if won else 0.0)) - (c * px + fee), (c * px + fee)


def summarise(label, rows):
    """rows: list of (price, won). Everything quoted, nothing filled."""
    if not rows:
        print(f"  {label:<34} none")
        return None
    net = staked = 0.0
    wins = 0
    for px, won in rows:
        v, cost = value(px, won)
        if v is None:
            continue
        net += v
        staked += cost
        wins += 1 if won else 0
    n = len(rows)
    roi = 100.0 * net / staked if staked else 0.0
    w = wins / n
    se = 100.0 * math.sqrt(max(w * (1 - w), 0.0) / n)
    print(f"  {label:<34} n {n:>5}   won {100*w:6.2f}% +/-{1.96*se:5.2f}   "
          f"mean px {100*st.mean([p for p, _ in rows]):5.2f}c   "
          f"net ${net:+8.2f}   ROI {roi:+6.2f}%")
    return dict(n=n, won=100 * w, net=net, roi=roi, se=se, staked=staked)


def main():
    print("=" * 112)
    print("THE SELECTION GAP: what the bot took, against what it skipped")
    print("  Everything priced at the T-9 QUOTE -- taken and skipped alike --")
    print("  so fills cannot enter the comparison.")
    print("=" * 112)

    # 1. The live record: which tickers were actually bought.
    bets = T.rows_since(T.START)
    bets, _why = C.keep(bets)
    taken = {b["ticker"] for b in bets}
    print(f"\n  {len(taken)} distinct markets bought by the live T-9 bot")
    if not taken:
        print("  nothing to compare")
        return 1

    # 2. The universe: every settled market, with its T-9 quote and result.
    #
    # ROUND-ROBIN ACROSS THE SERIES, NOT ONE AT A TIME. The first run walked
    # BTC, then ETH, then SOL, then XRP, and hit its budget partway through
    # the fourth -- so DOGE, 152 of the bot's bets, was absent from the
    # universe entirely and the comparison silently ran on four coins. The
    # run did not fail; it reported a confident table built on a population
    # that was missing a fifth of itself.
    #
    # Taking one market from each series in turn means the budget degrades
    # every coin together instead of deleting the last one. A partial run is
    # then a thinner sample of the same thing, rather than a different thing.
    end = time.time() + BUDGET
    uni = []
    pending = {}
    for s in SERIES:
        try:
            mk = B.settled_markets(s, PER_SERIES)
        except Exception as e:
            print(f"  {s}: could not list ({str(e)[:60]})")
            continue
        graded = [m for m in mk
                  if str(m.get("result") or "").lower() in ("yes", "no")]
        pending[s] = graded
        print(f"  {s}: {len(graded)} graded of {len(mk)} settled", flush=True)

    seen = {s: 0 for s in pending}
    idx = 0
    stopped = False
    while pending and not stopped:
        for s in list(pending):
            if time.time() > end:
                stopped = True
                break
            if idx >= len(pending[s]):
                continue
            m = pending[s][idx]
            seen[s] += 1
            close = B.ts(m.get("close_time"))
            if close is None:
                continue
            cs = B.candles(s, m.get("ticker"), m.get("close_time"))
            time.sleep(B.PAUSE)
            a = ask_at(cs, close, ENTRY_OFFSET)
            if a is None or not (BAND_LO <= a < BAND_HI):
                continue
            uni.append(dict(tk=m.get("ticker"), close=close, px=a,
                            won=str(m.get("result")).lower() == "yes"))
        idx += 1
        if all(idx >= len(pending[s]) for s in pending):
            break

    print()
    for s in sorted(seen):
        tot = len(pending.get(s, []))
        print(f"  {s}: read {seen[s]} of {tot} graded "
              f"({100.0*seen[s]/max(tot,1):.0f}%)")
    if stopped:
        print("  BUDGET REACHED -- every series was read to the same depth,")
        print("  so what is missing is depth of history, not a whole coin.")

    print(f"\n  {len(uni)} markets qualified at T-9 in 90-96c across the "
          "whole history")
    if not uni:
        print("  nothing qualified; cannot answer")
        return 1

    hit = [u for u in uni if u["tk"] in taken]
    print(f"  {len(hit)} of them are in the live record "
          f"({100.0*len(hit)/max(len(uni),1):.1f}%)")
    if not hit:
        print("  NONE of the bought tickers appear in the qualifying set.")
        print("  That is not a selection finding -- it means the universe")
        print("  and the record are not being matched correctly, and the")
        print("  rest of this run would be meaningless. Stopping.")
        return 1

    # 3. WITHIN the windows the bot played.
    played = {u["close"] for u in hit}
    inside = [u for u in uni if u["close"] in played]
    took = [(u["px"], u["won"]) for u in inside if u["tk"] in taken]
    left = [(u["px"], u["won"]) for u in inside if u["tk"] not in taken]

    print(f"\n{'-' * 112}")
    print(f"WITHIN THE {len(played)} WINDOWS THE BOT PLAYED  "
          "(the strong comparison: same windows, same clock)")
    print("-" * 112)
    a = summarise("TAKEN", took)
    b = summarise("SKIPPED (qualified, not bought)", left)
    if a and b:
        gap = b["won"] - a["won"]
        band = 1.96 * math.sqrt(a["se"] ** 2 + b["se"] ** 2)
        print(f"\n  skipped minus taken, on the win rate: {gap:+.2f} points "
              f"(interval +/-{band:.2f})")
        if abs(gap) < band:
            print("  -> INSIDE the noise. Within a window the bot's choice")
            print("     of which legs to buy is not measurably worse, so the")
            print("     per-window cap is not where the gap comes from.")
        elif gap > 0:
            print("  -> The legs it SKIPPED did better, beyond the interval.")
            print("     The cap is not rationing risk, it is choosing badly:")
            print("     look at the order the scan yields candidates in.")
        else:
            print("  -> The legs it took did BETTER. Selection within a")
            print("     window is working; the gap is elsewhere.")

    # 4. ACROSS windows -- the weaker comparison, labelled as such.
    outside = [(u["px"], u["won"]) for u in uni if u["close"] not in played]
    print(f"\n{'-' * 112}")
    print("ACROSS WINDOWS  (weaker: different clock, different conditions)")
    print("-" * 112)
    c = summarise("IN WINDOWS THE BOT PLAYED", [(u["px"], u["won"])
                                                for u in inside])
    d = summarise("IN WINDOWS IT MISSED ENTIRELY", outside)
    if c and d:
        gap = d["won"] - c["won"]
        band = 1.96 * math.sqrt(c["se"] ** 2 + d["se"] ** 2)
        print(f"\n  missed minus played, on the win rate: {gap:+.2f} points "
              f"(interval +/-{band:.2f})")
        if abs(gap) < band:
            print("  -> Inside the noise: the windows it was absent from")
            print("     were not better, so uptime is not biasing the record.")
        elif gap > 0:
            print("  -> The windows it MISSED were better. The live record is")
            print("     a biased sample of the rule, and the headline says as")
            print("     much about uptime as about the strategy.")
        else:
            print("  -> The windows it missed were worse. Being down helped.")

    # 5. How much of the universe does the bot even see?
    print(f"\n{'-' * 112}")
    print("COVERAGE")
    print("-" * 112)
    allw = {u["close"] for u in uni}
    print(f"  windows with a qualifying market : {len(allw)}")
    print(f"  windows the bot bought in        : {len(played)}  "
          f"({100.0*len(played)/max(len(allw),1):.1f}%)")
    per = collections.Counter()
    for u in inside:
        per[u["close"]] += 1
    tk_per = collections.Counter()
    for u in inside:
        if u["tk"] in taken:
            tk_per[u["close"]] += 1
    if per:
        print(f"  qualifying legs per played window: "
              f"mean {st.mean(list(per.values())):.2f}")
        print(f"  legs actually bought per window  : "
              f"mean {st.mean([tk_per[w] for w in played]):.2f}")

    print("\n" + "=" * 112)
    print("  READ IT AS A LEAD, NOT A VERDICT. There is no fill for an order")
    print("  never sent, and a market the scan did not reach may have been")
    print("  unreachable rather than merely unchosen. Whatever this points")
    print("  at gets paper-tested before it touches money.")
    print("=" * 112)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
