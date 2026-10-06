"""Watch the threshold ladders for self-contradiction, with SIZE and DURATION.

ladder.py found 44 moments in the hourly history where two strikes on the same
market contradicted each other -- "gold above 4143.99" asking 28c while "gold
above 4145.99" bid 67c. The first pays whenever the second does, so buying the
low and selling the high cannot lose: minimum payoff $1 against a 61c cost.
That is arithmetic, not a forecast, which is why it cannot decay or fail out of
sample the way the other sixteen ideas did.

But it is also not yet money, and three specific things decide whether it is.
H60 cannot answer any of them. This collector can.

  HOW MANY CONTRACTS. H60 records no resting size, so a 36c crossing might be
  one lot. The market LIST call returns yes_bid_size_fp and yes_ask_size_fp
  beside the prices, so the takeable quantity is the minimum of the two legs
  and is recorded with every crossing.

  WERE BOTH LEGS TAKEABLE AT ONCE. H60 samples each market at its own offset,
  seconds apart, so a "crossing" there might be two quotes that never
  coexisted. One list call returns the WHOLE LADDER in a single response: every
  strike at one instant, from one read. That is the single most important
  design difference here, and it is why this is a test rather than another
  suggestive number.

  DID IT LAST LONG ENOUGH TO ACT. A crossing that exists for one second is
  not reachable by a human or by a script that has to place two orders. Each
  crossing is keyed and re-checked every poll, and its lifetime in seconds is
  recorded when it disappears.

WHAT WOULD STILL NOT BE PROVEN. Execution risk is real and is not measured
here: taking two legs means two orders, and if one fills and the other does
not you are left with a naked directional position, which is the opposite of
what this trade is for. A positive result here means the opportunity exists at
a size and duration worth attempting -- not that the attempt succeeds.

ONLY -T TICKERS, because bucket markets (-B) are not monotone in strike, and
comparing them as thresholds produced a 94-cent "arbitrage" on the first run
of ladder.py. 1,433 rows of that mistake are the reason this is explicit.

Places no orders. The workflow re-checks that.
"""

import collections
import sys
import time

import collect15 as C
import predictor as P

SHEET = "ARB"
# Where ladder.py found the contradictions: metals, energy, daily crypto and
# indices. Not 15-minute crypto, which is bot-saturated and showed nothing.
SERIES = ["KXGOLDH", "KXSILVERH", "KXWTIH", "KXBTCD", "KXETHD",
          "KXNASDAQ100U", "KXINXU", "KXCOPPERH", "KXNATGASH", "KXPLATINUMH"]
POLL_S = 20
PAUSE = 0.05
FLUSH_EVERY_S = 300
FEE_RATE = 0.07

HEADERS = ["UTC", "Event", "Series", "Low Ticker", "High Ticker",
           "Low Strike", "High Strike", "Ask Low", "Bid High",
           "Ask Low Size", "Bid High Size", "Takeable",
           "Gross c", "Net c per contract", "Locked $", "Seen s"]


def _f(v):
    return C._f(v)


def fee(px):
    return FEE_RATE * px * (1 - px)


def is_threshold(ticker):
    """-T<strike> is a threshold market; -B<low> is a bucket and is not."""
    tail = str(ticker).split("-")[-1]
    return tail[:1] == "T" and tail[1:].replace(".", "", 1).isdigit()


def strike_of(m):
    s = _f(m.get("floor_strike"))
    if s is not None:
        return s
    tail = str(m.get("ticker") or "").split("-")[-1]
    return _f(tail[1:]) if tail[:1] == "T" else None


def crossings(mk):
    """Every self-contradicting pair in ONE simultaneous snapshot."""
    rung = []
    for m in mk:
        tk = str(m.get("ticker") or "")
        if not is_threshold(tk):
            continue
        s = strike_of(m)
        b = _f(m.get("yes_bid_dollars"))
        a = _f(m.get("yes_ask_dollars"))
        bs = _f(m.get("yes_bid_size_fp"))
        as_ = _f(m.get("yes_ask_size_fp"))
        if s is None or b is None or a is None:
            continue
        if not (0 < b <= a < 1):
            continue
        rung.append(dict(tk=tk, s=s, bid=b, ask=a, bsz=bs, asz=as_,
                         ev=str(m.get("event_ticker") or "")))
    rung.sort(key=lambda x: x["s"])
    out = []
    for i in range(len(rung)):
        for j in range(i + 1, len(rung)):
            lo, hi = rung[i], rung[j]
            if lo["s"] >= hi["s"]:
                continue
            gross = hi["bid"] - lo["ask"]
            if gross <= 0:
                continue
            net = gross - fee(lo["ask"]) - fee(1.0 - hi["bid"])
            if net <= 0:
                continue
            take = min(x for x in (lo["asz"], hi["bsz"])
                       if x is not None) if (lo["asz"] is not None
                                             or hi["bsz"] is not None) else None
            out.append(dict(ev=lo["ev"], lo=lo, hi=hi, gross=gross, net=net,
                            take=take))
    return out


def sheet():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    try:
        ws = sh.worksheet(SHEET)
    except Exception:
        ws = sh.add_worksheet(title=SHEET, rows=20000, cols=len(HEADERS))
        ws.append_row(HEADERS, value_input_option="RAW")
    return ws


def main():
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 3540
    ws = sheet()
    deadline = time.time() + seconds
    live = {}            # key -> (first_seen, best row so far)
    out = []
    last_flush = time.time()
    polls = found = 0
    print(f"watching {len(SERIES)} threshold ladders for {seconds}s")

    while time.time() < deadline:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        now = time.time()
        seen_now = set()
        for s in SERIES:
            # ONE call per series: the whole ladder at one instant. Polling
            # each strike separately would compare quotes that never coexisted.
            d, err = C.get("/trade-api/v2/markets", series_ticker=s,
                           status="open", limit=200)
            time.sleep(PAUSE)
            if not d:
                continue
            for x in crossings(d.get("markets") or []):
                key = (x["lo"]["tk"], x["hi"]["tk"])
                seen_now.add(key)
                if key not in live:
                    live[key] = [now, x, s, stamp]
                    found += 1
                else:
                    # keep the biggest net seen while it persists
                    if x["net"] > live[key][1]["net"]:
                        live[key][1] = x
        # anything gone since the last poll has ended: write it with a lifetime
        for key in [k for k in live if k not in seen_now]:
            t0, x, ser, first = live.pop(key)
            took = x["take"]
            locked = (took * x["net"]) if took else ""
            out.append([first, x["ev"], ser, x["lo"]["tk"], x["hi"]["tk"],
                        x["lo"]["s"], x["hi"]["s"],
                        x["lo"]["ask"], x["hi"]["bid"],
                        x["lo"]["asz"], x["hi"]["bsz"], took,
                        round(100 * x["gross"], 2), round(100 * x["net"], 2),
                        locked, round(now - t0, 1)])
        polls += 1
        if polls % 10 == 0:
            print(f"[{time.strftime('%H:%M:%S')}] {polls} polls, {found} "
                  f"crossings seen, {len(live)} open now, {len(out)} written")
        if out and (time.time() - last_flush > FLUSH_EVERY_S):
            try:
                ws.append_rows([["" if v is None else v for v in r]
                                for r in out], value_input_option="RAW")
                print(f"  wrote {len(out)} crossings")
                out = []
            except Exception as e:
                print(f"  WRITE FAILED ({str(e)[:60]})")
            last_flush = time.time()
        time.sleep(POLL_S)

    # Anything still open at the end is written with its lifetime so far, so a
    # long-lived crossing is not silently dropped for outliving the session.
    for key, (t0, x, ser, first) in live.items():
        took = x["take"]
        out.append([first, x["ev"], ser, x["lo"]["tk"], x["hi"]["tk"],
                    x["lo"]["s"], x["hi"]["s"], x["lo"]["ask"], x["hi"]["bid"],
                    x["lo"]["asz"], x["hi"]["bsz"], took,
                    round(100 * x["gross"], 2), round(100 * x["net"], 2),
                    (took * x["net"]) if took else "",
                    round(time.time() - t0, 1)])
    if out:
        try:
            ws.append_rows([["" if v is None else v for v in r] for r in out],
                           value_input_option="RAW")
            print(f"wrote final {len(out)}")
        except Exception as e:
            print(f"FINAL WRITE FAILED ({str(e)[:60]}) -- {len(out)} lost")
    print(f"finished; {polls} polls, {found} crossings seen in total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
