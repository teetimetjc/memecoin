"""How many markets SHOULD the bot have bet in the last few hours?

WHY THIS EXISTS. On 2026-10-12 the bot's last bet was at 12:51 UTC and it
then went quiet for hours. The Control tab reads RUNNING, the balance is
$127.11, the funding check passes at every window size, and the session is
alive -- so the switch is on and the money is there. The remaining
explanation is that nothing qualified, and "nothing qualified" is exactly
what a broken signal path looks like from outside.

So this reconstructs the decision the bot faced, from the exchange's own
candles, for every window that has closed in the last few hours:

    the yes ask at T-9, the one number the narrow slice selects on,
    against the 90-96c band the live bot is configured for.

WHAT EACH OUTCOME MEANS.

  IF ZERO QUALIFIED, the bot is behaving correctly and the market simply has
  not offered the band. That is a real condition, not a fault: in a calm
  hour the favourite sits at 97-99c, which is ABOVE the band and skipped by
  MAX_ENTRY_CENTS, and in a violent hour it is below 90c. The slice lives in
  between and it is roughly one market in twenty.

  IF SEVERAL QUALIFIED and no bet was placed, the signal path is broken and
  the job log at the end of the session is the next place to look.

This counts what the bot could have seen, not what it did. It cannot read
the bot's own skip reasons -- those are in the sealed log. A market that
qualified here might still have been declined for a reason this does not
model (the per-window cap, the last-look guard, an unreadable exchange
index). So treat a small disagreement as expected and a large one as the
finding.

Read-only: candles and market metadata, no orders, no writes.
"""

import sys
import time

import backfill as B
import score as S


MAJORS = list(S.MAJORS)
ENTRY = S.ENTRY            # 9
# The live bot's band, from favlive.yml: MIN_ENTRY_CENTS / MAX_ENTRY_CENTS.
LO, HI = 90.0, 96.0
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
# Enough to cover the window at four markets an hour per series, with slack.
PER_SERIES = 60


def ask_at(series, ticker, close, offset):
    """The yes ask `offset` minutes before close, in cents, or None."""
    end = B.ts(close)
    if end is None:
        return None
    d, err = B.get(
        f"/trade-api/v2/series/{series}/markets/{ticker}/candlesticks",
        start_ts=int(end) - (offset + 3) * 60, end_ts=int(end),
        period_interval=1)
    if not d:
        return None
    for c in (d or {}).get("candlesticks") or []:
        t = B._f(c.get("end_period_ts"))
        if t is None:
            continue
        if int(round((end - t) / 60.0)) != offset:
            continue
        v = c.get("yes_ask")
        if not isinstance(v, dict):
            continue
        a = B._f(v.get("close_dollars"))
        if a is None or not (0 < a < 1):
            return None
        return 100.0 * a
    return None


def main():
    now = time.time()
    cutoff = now - HOURS * 3600.0
    print("=" * 90)
    print(f"WHAT THE BOT COULD HAVE BET IN THE LAST {HOURS:.1f} HOURS")
    print(f"  band {LO:.0f}-{HI:.0f}c on the yes ask at T-{ENTRY}")
    print("=" * 90)

    rows = []
    for s in MAJORS:
        got = B.settled_markets(s, PER_SERIES)
        n = 0
        for m in got:
            ct = m.get("close_time")
            t = B.ts(ct)
            if t is None or t < cutoff or t > now:
                continue
            n += 1
            a = ask_at(s, str(m.get("ticker")), ct, ENTRY)
            rows.append((t, s, str(m.get("ticker")), a,
                         str(m.get("result") or "")))
            time.sleep(B.PAUSE)
        print(f"  {s:<12} {n} window(s) closed in range")

    rows.sort()
    if not rows:
        print("\n  no settled windows in range -- raise the hours argument.")
        return 0

    inband = [r for r in rows if r[3] is not None and LO <= r[3] < HI]
    noquote = [r for r in rows if r[3] is None]

    print(f"\n  {len(rows)} windows closed, {len(noquote)} without a readable "
          f"T-{ENTRY} quote")
    print(f"  {len(inband)} fell in the {LO:.0f}-{HI:.0f}c band\n")

    print(f"  {'closed (UTC)':<18} {'series':<8} {'ask T-9':>9}  in band?  "
          f"result")
    for t, s, tk, a, res in rows:
        when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(t))
        aa = "  --  " if a is None else f"{a:>6.1f}c"
        mark = "  YES  " if (a is not None and LO <= a < HI) else "   .   "
        print(f"  {when:<18} {s:<8} {aa:>9}  {mark}  {res}")

    print("\n" + "=" * 90)
    rate = len(inband) / HOURS if HOURS else 0
    print(f"  {len(inband)} qualifying window(s) in {HOURS:.1f} hours "
          f"= {rate:.2f}/hour")
    print(f"  the live record's long-run rate is about 1.08 bets/hour")
    print()
    if not inband:
        print("  NOTHING QUALIFIED. The bot had nothing to bet, and the quiet")
        print("  is the market rather than a fault. Worth knowing WHERE the")
        print("  prices sat instead -- above 96c is a calm market, below 90c")
        print("  is a violent one, and they mean opposite things.")
    else:
        print("  SOMETHING QUALIFIED. If no order was placed against these,")
        print("  the signal path is the next thing to read -- the per-window")
        print("  cap and the last-look guard are legitimate reasons to skip,")
        print("  and the session's job log names which one fired.")
    print("=" * 90)

    # Where the prices actually sat, since that is the whole diagnosis when
    # nothing qualified.
    got = [r[3] for r in rows if r[3] is not None]
    if got:
        buckets = [("below 80c", lambda x: x < 80),
                   ("80-90c", lambda x: 80 <= x < 90),
                   ("90-96c  <- the band", lambda x: 90 <= x < 96),
                   ("96-99c", lambda x: 96 <= x < 99),
                   ("99c and up", lambda x: x >= 99)]
        print("\n  WHERE THE T-9 ASK SAT:")
        for name, f in buckets:
            k = sum(1 for x in got if f(x))
            bar = "#" * k
            print(f"    {name:<22} {k:>3}  {bar}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
