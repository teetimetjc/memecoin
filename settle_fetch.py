"""CG0 -- get the real settlement result from Kalshi, per market.

WHY THIS EXISTS. Every calibration number for the cheap-contract work was
computed from a flag we derived ourselves:

    Price at Eval > K Target

Checked against the order book's own verdict 90 seconds from settlement,
that flag is WRONG about 19% of the time. Markets the book priced at 98c
were labelled losers 20.5% of the time, and markets priced at 2c were
labelled winners 17.6% of the time. Neither happens in a real market.

The disagreements concentrate where spot sits close to the strike -- median
0.12% away when they disagree against 0.32% when they agree -- which is the
signature of comparing against the WRONG PRICE. Kalshi settles on its own
reference index at its own moment; we were comparing our spot feed sampled
at our moment. Every 15-minute window is near the money, so the two
disagree constantly.

And that mislabelling fully explains the "anomaly" it was used to find.
With label noise e, a true 8% win rate measures as 0.08 + 0.84e; at e=0.136
that is 19.4%, which is exactly the rate the sub-10c study reported. There
was never a mispricing -- only a noisy label pushing every measurement
toward a coin flip.

WHAT THIS DOES INSTEAD. Reads the tickers the Path sampler already logged
and asks Kalshi what each market actually settled to. Two changes, both
load-bearing:

  THE SOURCE is Kalshi's own `result` field, not arithmetic on a price
  feed. It cannot disagree with settlement because it IS settlement.

  THE KEY is the ticker, not (timestamp, symbol). A ticker names one market
  and nothing else; the old key relied on our timestamp and Kalshi's window
  lining up, which is a second thing to get wrong.

Read-only against the exchange. Places no orders. Writes one tab.
"""

import sys
import time

import requests

import predictor as P

SHEET = "Settled"
HEADERS = ["Ticker", "Result", "Close Time", "Fetched"]
HOST = "https://api.elections.kalshi.com"
PAUSE = 0.12                     # gentle on the rate limiter


def fetch(ticker):
    """Kalshi's own verdict for one market: 'yes', 'no', '' or None.

    An empty string means the market exists but has not settled; None means
    the lookup itself failed. The two must not be conflated -- treating a
    failed fetch as "not settled yet" would silently shrink the sample and
    look like patience.
    """
    path = f"/trade-api/v2/markets/{ticker}"
    try:
        hdrs = P._kalshi_headers("GET", path) or {}
        r = requests.get(HOST + path, headers=hdrs, timeout=15)
        if not r.ok:
            return None, None, f"HTTP {r.status_code}"
        m = (r.json() or {}).get("market") or {}
        return (str(m.get("result") or "").lower().strip(),
                str(m.get("close_time") or ""), None)
    except Exception as e:
        return None, None, str(e)[:60]


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    client = P._get_client()
    sh = client.open_by_key(P.SPREADSHEET_ID)

    rows = sh.worksheet("Path").get_all_values()
    if len(rows) < 2:
        print("no Path rows")
        return 1
    hi = {h: i for i, h in enumerate(rows[0])}
    ti = hi.get("Ticker", -1)
    if ti < 0:
        print("Path has no Ticker column")
        return 1
    tickers = []
    seen = set()
    for r in rows[1:]:
        t = (r[ti] if ti < len(r) else "").strip()
        if t and t not in seen:
            seen.add(t)
            tickers.append(t)

    # Already-fetched tickers are skipped, so this can be re-run to top up
    # without paying for the whole history again.
    try:
        ws = sh.worksheet(SHEET)
        have = {r[0] for r in ws.get_all_values()[1:] if r and r[0]}
    except Exception:
        ws = sh.add_worksheet(title=SHEET, rows=5000, cols=len(HEADERS))
        ws.update("A1", [HEADERS])
        have = set()

    todo = [t for t in tickers if t not in have]
    if limit:
        todo = todo[:limit]
    print(f"{len(tickers)} tickers in Path, {len(have)} already fetched, "
          f"{len(todo)} to go")
    if not todo:
        print("nothing to fetch")
        return 0

    out, bad, unsettled = [], 0, 0
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    for i, t in enumerate(todo, 1):
        res, close, err = fetch(t)
        if err:
            bad += 1
            if bad <= 3:
                print(f"  {t}: {err}")
        elif not res:
            unsettled += 1
        else:
            out.append([t, res, close, stamp])
        if i % 100 == 0:
            print(f"  {i}/{len(todo)} ... {len(out)} settled, "
                  f"{unsettled} open, {bad} failed")
        time.sleep(PAUSE)

    if out:
        ws.append_rows(out, value_input_option="USER_ENTERED",
                       table_range="A1")
    print(f"\nwrote {len(out)} settled results; "
          f"{unsettled} not settled yet, {bad} lookups failed")
    if out:
        yes = sum(1 for r in out if r[1] == "yes")
        print(f"  of the new rows: {yes} yes, {len(out)-yes} no "
              f"({yes/len(out)*100:.1f}% yes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
