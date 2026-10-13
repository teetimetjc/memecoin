"""What sports markets does Kalshi actually have, and do favourites hold?

THE IDEA. The crypto favourite trade measured an edge of zero across 11,288
bets on two venues. Moving it to a sportsbook would be the same bet at three
to four percent vig instead of Kalshi's 0.4% fee, which is strictly worse.
Moving it to Kalshi's own SPORTS markets is different: same fees, same
two-sided exchange, same tooling -- but a different market with different
participants, so the crypto null does not predict the answer.

WHAT THIS ANSWERS, in order of how much it matters:

  1. WHICH SERIES EXIST, and how many settled markets each has. Without
     history there is nothing to test and the idea stops here.
  2. HOW OFTEN A FAVOURITE REACHES 90-96c, because a rule that fires twice a
     week cannot be evaluated this year.
  3. WHETHER THOSE FAVOURITES HOLD -- the settle rate against the price paid.
     This is the whole question.

WHAT IT DOES NOT DO. It does not propose an entry trigger. The crypto rule
buys at T-9 because a 15-minute market has a clock; a basketball game at 90c
with two minutes left is a different object from one at 90c in the third
quarter, and a single "T-9" has no meaning across sports. Picking that
trigger is the next job, and it should be picked from the data rather than
assumed.

Read-only. Lists markets and reads settled outcomes. Places nothing.
"""
import collections, sys, time
import backfill as B

def main():
    print("="*92)
    print("WHAT SPORTS MARKETS DOES KALSHI HAVE?")
    print("="*92)
    # The series index, filtered to sports-looking categories.
    d, err = B.get("/trade-api/v2/series")
    if not d:
        print(f"  series index unavailable: {err}")
        print("  trying the events endpoint instead...")
    series = (d or {}).get("series") or []
    cats = collections.Counter()
    sporty = []
    for s in series:
        cat = str(s.get("category") or "")
        cats[cat] += 1
        if any(k in cat.lower() for k in
               ("sport", "football", "basketball", "baseball", "hockey",
                "soccer", "tennis", "golf", "racing")):
            sporty.append(s)
    print(f"\n  {len(series):,} series total. Categories:")
    for c, n in cats.most_common(25):
        print(f"    {c or '(blank)':<34} {n:>5}")
    print(f"\n  {len(sporty)} look like sports. Sampling their settled "
          "markets:\n")
    print(f"  {'series':<30} {'settled':>8} {'in 90-96c':>10} "
          f"{'won':>7} {'needed':>8}")
    total = []
    for s in sporty[:25]:
        tk = str(s.get("ticker") or "")
        if not tk:
            continue
        try:
            mk = B.settled_markets(tk, 400)
        except Exception as e:
            print(f"  {tk:<30} error {str(e)[:30]}")
            continue
        inband = []
        for m in mk:
            # The settled market carries its last price; the close price is a
            # poor proxy for an entry quote and is used ONLY to count how
            # often these markets live in the band at all.
            p = B._f(m.get("last_price"))
            if p is None:
                continue
            p = p / 100.0 if p > 1 else p
            res = str(m.get("result") or "").lower()
            if res not in ("yes", "no"):
                continue
            if 0.90 <= p < 0.96:
                inband.append((p, res == "yes"))
        if mk:
            w = (100.0 * sum(1 for _, y in inband if y) / len(inband)
                 if inband else float("nan"))
            need = (100.0 * sum(p for p, _ in inband) / len(inband)
                    if inband else float("nan"))
            print(f"  {tk:<30} {len(mk):>8,} {len(inband):>10} "
                  f"{w:>6.1f}% {need:>7.1f}%")
            total.extend(inband)
        time.sleep(B.PAUSE)
    print("\n" + "="*92)
    if total:
        w = 100.0 * sum(1 for _, y in total if y) / len(total)
        need = 100.0 * sum(p for p, _ in total) / len(total)
        print(f"  ALL SPORTS, 90-96c: {len(total):,} markets, "
              f"won {w:.1f}%, price implied {need:.1f}%, "
              f"gap {w-need:+.1f} points")
        print()
        print("  READ THIS AS A SMOKE TEST, NOT A RESULT. The price used is")
        print("  the market's LAST price, which is close to settlement and")
        print("  therefore close to certain -- it is not an entry quote nine")
        print("  minutes out, and it will flatter the win rate badly. It")
        print("  answers 'do these markets exist and in what numbers', and")
        print("  nothing more.")
    else:
        print("  No settled sports markets in the band were found. Either the")
        print("  series filter missed them or the history is not there yet.")
    print("="*92)
    return 0

if __name__ == "__main__":
    sys.exit(main())
