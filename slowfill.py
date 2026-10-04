"""Did resting orders in slow markets get filled before the price moved, or because it did?

slowbook bracketed resting at optimistic +$0.117 a bet and adverse -$0.130
and could not narrow it, because the two are assumptions about queue
position and candlestick history does not record queue position. slowcollect
has since recorded it forward: the book with sizes, every print with the
taker's side, and the book afterwards. This turns that into one number.

THE ESTIMATOR. A maker's profit on a filled order is the price obtained
minus what the contract was worth immediately afterwards:

    resting an ASK at a, filled  ->  a - mid(t+delta)
    resting a BID at b, filled   ->  mid(t+delta) - b

That single quantity contains both halves of the question. The spread
captured is in it, and so is the adverse move, so there is no need to
assume either. It also needs no settlement outcome, which SLOWBOOK does not
carry -- a maker who is run over loses before expiry, not at it.

WE JOIN THE BACK OF THE QUEUE. A fill counts only once the contracts traded
on our side EXCEED the size that was already resting at that price when we
arrived. Assuming the front of the queue would flatter every number here,
and queue position is the entire thing being measured.

TAKER SIDE DECIDES WHICH ORDER FILLS. taker_side=yes lifted the ask, so a
resting ASK is hit; taker_side=no sold YES into the bid, so a resting BID is
hit. Getting this backwards would invert the result, so it is asserted
against the data rather than assumed: a print's price is checked against the
book it landed on, and mismatches are counted and reported.

FOUR THINGS THAT WOULD FAKE A RESULT, ALL HANDLED.

  AN UNOBSERVABLE HORIZON IS NOT A ZERO DRIFT. If the data ends before
  t+delta, that attempt is dropped, not scored as break-even. Treating
  missing as zero is how a collector's own gaps become an edge.

  ROWS ARE CHANGE-ONLY, so between rows the book is unchanged by
  construction -- that is what the collector's filter means. The last known
  book at or before a moment is therefore the book at that moment, and no
  interpolation is invented.

  STALE QUOTES ARE PRICED BOTH WAYS. An order left resting while the market
  walks away is a different strategy from one cancelled when the mid moves.
  Both are reported, because the first flatters fill rates and the second
  flatters profit.

  ERRORS ARE CLUSTERED BY MARKET. Forty attempts in one ticker on one
  afternoon are not forty independent observations.

Read-only.
"""

import collections
import math
import statistics as st
import sys

import predictor as P

TABS_PREFIX = "SLOWBOOK"
# Rows before the trade age filter worked are re-ingested history at roughly
# five stale prints per real one. Counting them would inflate every fill.
CUTOFF = "2026-09-28 19:20:00 UTC"
HORIZONS = (5, 15, 60)      # minutes after a fill at which to mark it
CANCEL_TICKS = 0.03         # "cancelled" variant: pull if mid moves this far
MIN_N = 60


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def ts(s):
    """Epoch from either stamp shape the tab carries."""
    import calendar
    import time
    s = str(s or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%dT%H:%M:%S"):
        try:
            return calendar.timegm(time.strptime(s[:len(fmt) + 2].strip()
                                                 .rstrip("Z"), fmt))
        except Exception:
            continue
    try:
        return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def cse(groups, n):
    G = len(groups)
    if G < 2 or not n:
        return None
    m = sum(sum(v) for v in groups.values()) / n
    ss = sum((sum(v) - len(v) * m) ** 2 for v in groups.values())
    return math.sqrt((G / (G - 1.0)) * ss / (n * n))


def load():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    ev = collections.defaultdict(list)
    tabs = [w for w in sh.worksheets() if w.title.startswith(TABS_PREFIX)]
    for ws in tabs:
        rows = ws.get_all_values()
        if not rows:
            continue
        h = {k: i for i, k in enumerate(rows[0])}

        def idx(*names):
            for nm in names:
                if nm in h:
                    return h[nm]
            return None

        i_u, i_k, i_t = idx("UTC"), idx("Kind"), idx("Ticker")
        i_s = idx("Series")
        i_b, i_a = idx("Yes Bid"), idx("Yes Ask")
        i_bs, i_as = idx("Bid Size"), idx("Ask Size")
        # The tab's header predates a rename, so both spellings are accepted.
        i_px = idx("VWAP", "Trade Price")
        i_c = idx("Contracts", "Trade Count")
        i_side = idx("Taker Side")
        for r in rows[1:]:
            if i_u is None or i_u >= len(r) or not r[i_u]:
                continue
            if str(r[i_u]) < CUTOFF:
                continue
            t = ts(r[i_u])
            if t is None:
                continue
            tk = str(r[i_t]) if i_t is not None and i_t < len(r) else ""
            if not tk:
                continue
            ev[tk].append(dict(
                t=t,
                kind=str(r[i_k]) if i_k is not None and i_k < len(r) else "",
                ser=str(r[i_s]) if i_s is not None and i_s < len(r) else "",
                bid=_f(r[i_b]) if i_b is not None and i_b < len(r) else None,
                ask=_f(r[i_a]) if i_a is not None and i_a < len(r) else None,
                bsz=_f(r[i_bs]) if i_bs is not None and i_bs < len(r) else None,
                asz=_f(r[i_as]) if i_as is not None and i_as < len(r) else None,
                px=_f(r[i_px]) if i_px is not None and i_px < len(r) else None,
                cts=_f(r[i_c]) if i_c is not None and i_c < len(r) else None,
                side=(str(r[i_side]).lower()
                      if i_side is not None and i_side < len(r) else ""),
            ))
    for tk in ev:
        ev[tk].sort(key=lambda e: e["t"])
    print(f"  {len(tabs)} tab(s), {len(ev)} markets, "
          f"{sum(len(v) for v in ev.values())} usable rows")
    return ev


def mid_at(seq, want):
    """The mid from the last book state at or before `want`, or None.

    None means the data does not reach that far, and the caller must DROP
    the attempt rather than score it as zero drift.
    """
    best = None
    for e in seq:
        if e["t"] > want:
            break
        if e["bid"] is not None and e["ask"] is not None:
            best = (e["bid"] + e["ask"]) / 2.0
    if best is None:
        return None
    last = seq[-1]["t"] if seq else 0
    return best if last >= want else None


def attempts(seq, side, delta_s, cancel):
    """Rest at the touch on `side`, walk forward, score the fills.

    Returns (results, filled, unfilled, dropped) where results are per-contract
    dollar outcomes.
    """
    out = []
    filled = unfilled = dropped = 0
    n = len(seq)
    for i, e0 in enumerate(seq):
        if e0["bid"] is None or e0["ask"] is None:
            continue
        price = e0["ask"] if side == "ask" else e0["bid"]
        queue = (e0["asz"] if side == "ask" else e0["bsz"])
        if price is None or queue is None:
            continue
        mid0 = (e0["bid"] + e0["ask"]) / 2.0
        ahead = queue           # we join BEHIND what is already there
        hit = None
        for j in range(i + 1, n):
            e = seq[j]
            if cancel and e["bid"] is not None and e["ask"] is not None:
                if abs((e["bid"] + e["ask"]) / 2.0 - mid0) > CANCEL_TICKS:
                    break       # pulled the quote; not a fill, not a loss
            if e["kind"] != "trade" or not e["cts"]:
                continue
            # taker_side=yes lifted the ask; taker_side=no sold into the bid.
            want = "yes" if side == "ask" else "no"
            if not e["side"].startswith(want):
                continue
            ahead -= e["cts"]
            if ahead < 0:
                hit = e
                break
        if hit is None:
            unfilled += 1
            continue
        m = mid_at(seq, hit["t"] + delta_s)
        if m is None:
            dropped += 1        # horizon not observable; NOT zero drift
            continue
        filled += 1
        out.append(price - m if side == "ask" else m - price)
    return out, filled, unfilled, dropped


def rep(vals, keys, label):
    n = len(vals)
    if n < MIN_N:
        print(f"    {label:<40} n={n:<5} too few")
        return
    g = collections.defaultdict(list)
    for v, k in zip(vals, keys):
        g[k].append(v)
    m = sum(vals) / n
    se = cse(g, n)
    t = (m / se) if se else 0.0
    flag = ""
    if m > 0:
        flag = "  <== POSITIVE" if t > 2.0 else "  (positive, t<2)"
    print(f"    {label:<40} n={n:<5} {100*m:>+7.3f}c/contract "
          f"+/-{100*(se or 0):.3f}  t={t:>+5.1f}{flag}")


def main():
    ev = load()
    if not ev:
        print("no SLOWBOOK rows -- has slowcollect run since the age fix?")
        return 0

    # Sanity: does taker_side agree with the book the print landed on? If
    # these were inverted the whole result would flip sign, so it is checked
    # rather than trusted.
    ok = bad = 0
    for tk, seq in ev.items():
        for e in seq:
            if e["kind"] != "trade" or e["px"] is None:
                continue
            if e["bid"] is None or e["ask"] is None:
                continue
            mid = (e["bid"] + e["ask"]) / 2.0
            if e["side"].startswith("yes"):
                ok += 1 if e["px"] >= mid - 0.02 else 0
                bad += 0 if e["px"] >= mid - 0.02 else 1
            elif e["side"].startswith("no"):
                ok += 1 if e["px"] <= mid + 0.02 else 0
                bad += 0 if e["px"] <= mid + 0.02 else 1
    tot = ok + bad
    print(f"\n  taker-side check: {ok}/{tot} prints sit on the side the flag "
          f"says ({100.0*ok/max(1,tot):.0f}%)")
    if tot and ok / tot < 0.6:
        print("  THE FLAG DOES NOT MATCH THE BOOK. Every number below would")
        print("  be inverted; stopping rather than reporting it.")
        return 1

    print("\n" + "=" * 104)
    print("WHAT A RESTING ORDER ACTUALLY COLLECTED  (cents per contract, "
          "maker fee measured at zero)")
    print("=" * 104)
    print("  price obtained minus the mid shortly after the fill. Positive "
          "means the spread")
    print("  captured outweighed the adverse move. Joining the BACK of the "
          "queue throughout.\n")

    for cancel in (False, True):
        print(f"  {'LEAVE the quote resting' if not cancel else f'CANCEL if the mid moves {100*CANCEL_TICKS:.0f}c'}")
        for mins in HORIZONS:
            for side in ("ask", "bid"):
                vals, keys = [], []
                F = U = D = 0
                for tk, seq in ev.items():
                    o, f, u, d = attempts(seq, side, mins * 60, cancel)
                    vals += o
                    keys += [tk] * len(o)
                    F, U, D = F + f, U + u, D + d
                tot_at = F + U + D
                fr = (100.0 * F / tot_at) if tot_at else 0.0
                rep(vals, keys, f"rest {side}, marked +{mins}min "
                                f"(fill {fr:.0f}%)")
                if D:
                    print(f"      {D} fills dropped: the data does not reach "
                          f"+{mins}min, so they are not scored as zero")
        print()

    print("=" * 104)
    print("A positive number here is the first evidence in this project that")
    print("resting pays. It is still ONE venue type and needs a frozen spec")
    print("and held-out data before it means anything -- the fallen favourite")
    print("looked like +3.71pp on discovery and came back -2.7pp on 2,355 bets.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    sys.exit(main())
