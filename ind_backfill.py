"""Rebuild v6's indicators over the 70-day history, from Coinbase 1m bars.

M15H carries the book and nothing else. The indicators -- RSI, EMA, MACD,
Bollinger, VWAP deviation, volume spike -- exist only in the v6 tab, five
coins over two weeks, already searched 562 ways. All of them are functions
of 1-minute closes, so they can be recomputed for any past minute given the
bars.

WHY COINBASE. predictor uses Kraken, whose OHLC endpoint returns only the
most recent ~720 candles no matter what `since` says -- twelve hours, not
seventy days. Binance has the history and answers HTTP 451 from a runner,
geo-blocked. Coinbase served a 1-minute candle from July 29 when asked, so
it is the only one of the three that can do this job. All three were asked
rather than assumed.

THE FIELD ORDER IS A TRAP. Coinbase returns [time, low, high, open, close,
volume] -- low BEFORE high, which is the reverse of most APIs and of the
shape predictor's calc_* functions expect ([ts, open, high, low, close,
vol]). Mapped carelessly, every high/low pair inverts and Bollinger and
VWAP come out plausible but wrong. The mapping is asserted on every bar.

THE INDICATORS ARE COMPUTED WITH PREDICTOR'S OWN FUNCTIONS, not
reimplementations, so a value here means the same thing it meant in v6. The
prices underneath differ -- Coinbase spot against Kraken spot -- so the
overlapping fortnight will not match exactly; that gap gets measured, not
assumed, the way the M15H-against-M15 check was.

Read-only against the exchanges. Writes one tab.
"""

import calendar
import collections
import sys
import time

import requests

import predictor as P

SHEET = "IND"
ENTRY = 3
BARS = 60                       # 1m closes feeding each indicator
CB = "https://api.exchange.coinbase.com"
PAUSE = 0.18
PRODUCT = {"KXBTC15M": "BTC-USD", "KXETH15M": "ETH-USD",
           "KXSOL15M": "SOL-USD", "KXXRP15M": "XRP-USD",
           "KXDOGE15M": "DOGE-USD"}
HEADERS = ["Ticker", "Series", "Symbol", "Close Time", "Entry Offset",
           "RSI7", "EMA Signal", "MACD Hist", "BB Position", "VWAP Dev %",
           "Vol Spike", "Bars Used", "Built UTC"]


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def ts(s):
    try:
        return calendar.timegm(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def fetch_bars(product, start, end):
    """1m bars in predictor's normalised shape, keyed by minute.

    Coinbase gives [time, low, high, open, close, volume]; predictor's
    calc_* functions read [ts, open, high, low, close, vol]. The remap is
    asserted, because inverting high and low produces numbers that look
    entirely reasonable and are entirely wrong."""
    out = {}
    cur = start
    while cur < end:
        stop = min(cur + 300 * 60, end)
        try:
            r = requests.get(f"{CB}/products/{product}/candles",
                             params={"granularity": 60, "start": iso(cur),
                                     "end": iso(stop)},
                             headers={"User-Agent": "research"}, timeout=25)
            if r.ok:
                for c in r.json() or []:
                    t = int(c[0])
                    lo, hi, op, cl, vol = (_f(c[1]), _f(c[2]), _f(c[3]),
                                           _f(c[4]), _f(c[5]))
                    if None in (lo, hi, op, cl, vol):
                        continue
                    if hi < lo:
                        raise SystemExit(
                            f"FATAL: Coinbase high {hi} < low {lo} for "
                            f"{product} at {iso(t)} -- the field order "
                            f"assumption is wrong, stopping rather than "
                            f"writing inverted bars")
                    out[t // 60] = [t * 1000, op, hi, lo, cl, vol]
            elif r.status_code == 429:
                time.sleep(1.5)
                continue
        except SystemExit:
            raise
        except Exception:
            pass
        cur = stop
        time.sleep(PAUSE)
    return out


def indicators(bars, minute):
    """v6's indicators at one minute, or None if the window is short."""
    seq = []
    for m in range(minute - BARS + 1, minute + 1):
        k = bars.get(m)
        if k:
            seq.append(k)
    if len(seq) < 40:
        return None
    closes = [float(k[4]) for k in seq]
    vols = [float(k[5]) for k in seq]
    # A window where the price never ticked DOWN divides by a zero average
    # loss inside predictor.calc_rsi. v6 never hit it because Kraken's feed
    # always moved; Coinbase's 1-minute DOGE bars can be perfectly flat.
    # Skipped rather than patched with an invented RSI, and skipped rather
    # than patching predictor, which the live v6 collector still runs on.
    try:
        rsi = P.calc_rsi(closes, 7)
    except ZeroDivisionError:
        return "flat"
    e9, e21 = P.calc_ema(closes, 9), P.calc_ema(closes, 21)
    ema_sig = ("BULL" if e9 > e21 else "BEAR") if (e9 and e21) else "FLAT"
    try:
        macd = P.calc_macd(closes)
    except ZeroDivisionError:
        macd = None
    hist = macd[2] if macd else None
    try:
        bb = P.calc_bollinger(closes, 20, 2.0)
    except ZeroDivisionError:
        bb = None
    bbpos = None
    if bb:
        up, mid, lo = bb
        if up != lo:
            bbpos = (closes[-1] - mid) / ((up - lo) / 2.0)
    vwap = P.calc_vwap(seq)
    vdev = ((closes[-1] - vwap) / vwap * 100.0) if vwap else None
    spike = P.calc_vol_spike(vols)
    return dict(rsi=rsi, ema=ema_sig, hist=hist, bb=bbpos, vdev=vdev,
                spike=spike, bars=len(seq))


def main():
    budget = int(sys.argv[1]) if len(sys.argv) > 1 else 1800
    stop_at = time.time() + budget
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    try:
        ws = sh.worksheet(SHEET)
        have = {r[0] for r in ws.get_all_values()[1:] if r and r[0]}
    except Exception:
        ws = sh.add_worksheet(title=SHEET, rows=40000, cols=len(HEADERS))
        ws.update(values=[HEADERS], range_name="A1")
        have = set()
    print(f"{SHEET}: {len(have)} markets already built")

    rows = sh.worksheet("M15H").get_all_values()
    hi = {h: i for i, h in enumerate(rows[0])}
    want = collections.defaultdict(list)
    for r in rows[1:]:
        if not r or not r[0] or r[0] in have:
            continue
        ser = r[hi["Series"]] if hi.get("Series") is not None else ""
        if ser not in PRODUCT:
            continue
        ct = ts(r[hi["Close Time"]])
        if ct is None:
            continue
        want[ser].append((r[0], ct))
    print("to build: " + ", ".join(f"{k}={len(v)}" for k, v in want.items()))

    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    total = flat = short = 0
    for ser, items in want.items():
        if time.time() > stop_at:
            print("  budget spent; run again to continue")
            break
        lo = min(c for _, c in items) - (ENTRY + BARS + 5) * 60
        hi_t = max(c for _, c in items)
        print(f"  {ser}: fetching bars {iso(lo)} .. {iso(hi_t)}")
        bars = fetch_bars(PRODUCT[ser], lo, hi_t)
        print(f"    {len(bars)} one-minute bars")
        if not bars:
            continue
        out = []
        for tk, ct in items:
            if time.time() > stop_at:
                break
            ind = indicators(bars, (ct - ENTRY * 60) // 60)
            if ind == "flat":
                flat += 1
                continue
            if not ind:
                short += 1
                continue
            out.append([tk, ser, PRODUCT[ser], iso(ct), ENTRY,
                        ind["rsi"], ind["ema"], ind["hist"], ind["bb"],
                        ind["vdev"], ind["spike"], ind["bars"], stamp])
            have.add(tk)
            if len(out) >= 400:
                ws.append_rows([["" if v is None else v for v in r]
                                for r in out], value_input_option="RAW")
                total += len(out)
                print(f"    wrote {len(out)} ({total} this run)")
                out = []
        if out:
            ws.append_rows([["" if v is None else v for v in r] for r in out],
                           value_input_option="RAW")
            total += len(out)
            print(f"    wrote {len(out)} ({total} this run)")
    print(f"finished; {total} added, {len(have)} in {SHEET}")
    print(f"  skipped: {flat} flat windows (RSI undefined), "
          f"{short} with too few bars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
