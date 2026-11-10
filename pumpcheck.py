"""What did the Solana meme-coin scanner actually earn? Read-only.

WHY THIS BEFORE ANY OPINION ABOUT PUMP.FUN. This project began as that
scanner: DexScreener's boosted and trending Solana pairs, filtered on
liquidity, age and volume, alerted when two of three signals fired -- new
listing, 5-minute momentum, volume spike -- and then tracked per-minute for
fifteen minutes. That record exists. Arguing about whether memecoins can be
traded profitably while a logged answer sits in a spreadsheet would be the
same mistake as every other one avoided here.

THE SHEET'S OWN VERDICT COLUMN IS NOT USABLE AND THAT IS THE FIRST THING TO
SAY. memecoinscanner.py computes:

    peak_pct   = max(% change over the fifteen minutes)
    profitable = "Yes" if peak_pct > 0

That marks a coin profitable if it ticked up at ANY point in fifteen
minutes, which is nearly all of them, and it prices the exit at the high
with timing nobody has. A "win rate" built on it measures whether a price
ever moved up, not whether money was made. So it is reported, labelled as
what it is, and then set aside.

WHAT IS COMPUTED INSTEAD: the return from buying at the alert price and
selling at a FIXED minute, for every minute from 1 to 15. A fixed rule is
something a person could actually follow. Every alert that reached that
minute is counted, winners and losers alike.

Reported for each exit minute:
    mean and median return, because memecoin returns are skewed and the
    mean is carried by a handful of spikes the median will not show;
    the share of alerts that were up at all;
    and the total return of putting the same stake into every alert.

NO FEES OR SLIPPAGE ARE CHARGED, and that matters more here than on Kalshi.
A Solana swap pays a priority fee, a DEX fee and spread, and a thin new
pair's spread can be several percent each way. So every number below is
BETTER than the trade would have been, and a rule that merely breaks even
here loses in practice.

Read-only. Places no order, writes no row, changes no setting.
"""
import os
import statistics as st

import gspread
from google.oauth2.service_account import Credentials
import json

SHEET_ID = os.environ.get("MEME_SHEET_ID",
                          "1PjtaTxSW1AKZ4rAUeIoHSfrV8Imh6WV_XM9uErXunQc")
TRACK = int(os.environ.get("MEME_TRACK_MINUTES") or 15)
STAKE = float(os.environ.get("MEME_STAKE") or 100.0)


def client():
    raw = os.environ.get("GOOGLE_CREDENTIALS")
    if not raw:
        raise SystemExit("  no GOOGLE_CREDENTIALS in the environment")
    creds = Credentials.from_service_account_info(
        json.loads(raw), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return gspread.authorize(creds)


def f(v):
    try:
        x = float(str(v).replace("%", "").replace("$", "").strip())
        return x if x == x else None
    except (TypeError, ValueError, AttributeError):
        return None


def main():
    print("=" * 100)
    print("THE SOLANA MEME-COIN SCANNER'S OWN RECORD")
    print("  Buy at the alert, sell at a fixed minute. No fees charged, so "
          "every figure is optimistic.")
    print("=" * 100)

    sh = client().open_by_key(SHEET_ID)
    try:
        rows = sh.worksheet("Sheet1").get_all_values()
    except Exception as e:
        print(f"\n  could not open Sheet1: {str(e)[:120]}")
        return 1
    if len(rows) < 2:
        print("\n  THE SHEET IS EMPTY. The scanner logged nothing, or the "
              "rows were cleared.")
        return 0

    h = {k: i for i, k in enumerate(rows[0])}

    def g(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    alerts = [r for r in rows[1:] if r and g(r, "Address")]
    print(f"\n  {len(alerts)} alert(s) logged")
    if not alerts:
        return 0

    dates = sorted(str(g(r, "Alert Timestamp"))[:10] for r in alerts
                   if g(r, "Alert Timestamp"))
    if dates:
        print(f"  from {dates[0]} to {dates[-1]}")

    # The sheet's own column, reported and then set aside.
    prof = [str(g(r, "Profitable?")).strip() for r in alerts]
    yes = sum(1 for p in prof if p.lower() == "yes")
    done = sum(1 for p in prof if p)
    if done:
        print(f"\n  the sheet's own 'Profitable?' column: {yes}/{done} = "
              f"{100.0*yes/done:.1f}%")
        print("  IGNORE IT. It marks a coin profitable if the price ticked up")
        print("  at any point in fifteen minutes, priced at the peak. It")
        print("  measures movement, not money.")

    # -------------------------------------------------- the honest version
    print(f"\n{'-' * 100}")
    print(f"BUY AT THE ALERT, SELL AT MINUTE N  (${STAKE:.0f} into every alert)")
    print("-" * 100)
    print(f"  {'exit':>5}{'n':>7}{'mean %':>10}{'median %':>11}"
          f"{'up %':>8}{'total $':>12}")
    best = None
    for m in range(1, TRACK + 1):
        vals = [f(g(r, f"% +{m}m")) for r in alerts]
        vals = [v for v in vals if v is not None]
        if len(vals) < 10:
            continue
        mean = st.mean(vals)
        med = st.median(vals)
        up = 100.0 * sum(1 for v in vals if v > 0) / len(vals)
        total = sum(STAKE * v / 100.0 for v in vals)
        print(f"  {m:>5}{len(vals):>7}{mean:>+10.2f}{med:>+11.2f}"
              f"{up:>8.1f}{total:>+12.2f}")
        if best is None or mean > best[1]:
            best = (m, mean, len(vals), total)

    if best:
        m, mean, n, total = best
        print(f"\n  best fixed exit: minute {m}, mean {mean:+.2f}% over {n} "
              f"alert(s), ${total:+,.2f} total")
        print("  CHOSEN AFTER SEEING ALL FIFTEEN, so it is the best of fifteen")
        print("  tries and overstates what picking it in advance would give.")

    # ------------------------------------------------------ the shape of it
    last = [f(g(r, f"% +{TRACK}m")) for r in alerts]
    last = [v for v in last if v is not None]
    if len(last) >= 10:
        last.sort()
        print(f"\n{'-' * 100}")
        print(f"THE DISTRIBUTION AT MINUTE {TRACK} -- where the money is")
        print("-" * 100)
        n = len(last)
        for label, idx in (("worst", 0), ("10th pct", n // 10),
                           ("median", n // 2),
                           ("90th pct", min(n - 1, 9 * n // 10)),
                           ("best", n - 1)):
            print(f"  {label:<12}{last[idx]:+10.2f}%")
        top = last[max(0, n - max(1, n // 20)):]
        rest = last[:max(0, n - max(1, n // 20))]
        if rest:
            print(f"\n  the top 5% of alerts average {st.mean(top):+.2f}%")
            print(f"  the other 95% average          {st.mean(rest):+.2f}%")
            print("  If the whole return sits in a handful of spikes, the")
            print("  strategy is a lottery and the sample is far too small to")
            print("  say whether the tickets are priced right.")

    print("\n" + "=" * 100)
    print("  AND NOTHING ABOVE PAYS A FEE. A Solana swap costs a priority fee,")
    print("  a DEX fee and the spread, and a thin new pair's spread runs to")
    print("  several percent EACH WAY. Subtract roughly 2-6% round trip from")
    print("  every row before believing any of it.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
