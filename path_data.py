"""Build the payload for the path-study page.

A DELIBERATELY SEPARATE PAGE, and a separate payload. The v6 dashboard is a
record of a strategy that turned out to be scored wrong; folding this into it
would invite reading one as evidence for the other. This is a clean slate for
a different question: not who wins at settlement, but whether a market that
runs away from its strike early comes back far enough to sell into.

WHAT CAN BE ANSWERED EARLY, AND WHAT CANNOT. Two things are measurable from
the paths alone, with no settled windows at all:

  how often the setup appears  -- a side priced under some threshold early
  how often it bounces         -- that side later touching a multiple of it

Those are worth showing from the first day. What they CANNOT give is P&L,
because a take-profit that never triggers leaves a position held to
settlement, and that is where this kind of strategy bleeds. So the touch rate
is reported as what it is -- a frequency, not a profit -- and the money
question stays "TOO EARLY" until enough windows have settled to answer it
honestly.

Every price here is the one you would actually get: entries pay the ASK,
exits sell into the BID.
"""

import json
import sys
from collections import defaultdict

import hedge

OUT_DEFAULT = "dashboard/path_data.js"

# Entry price BANDS, not thresholds. "Under 30c" contains every 20c setup,
# so comparing the two compares a set with a subset of itself -- which is
# exactly the comparison the page is asked to make.
BANDS = ((0, 20), (20, 30), (30, 40), (40, 45))
TAKE = (1.5, 2.0, 3.0)
ENTRY_S = 120.0                     # two minutes in


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build(path_rows, pred_rows, headers):
    if not path_rows or len(path_rows) < 2:
        return {"error": "no Path tab yet"}
    hi = {h: i for i, h in enumerate(path_rows[0])}

    def cell(r, k):
        i = hi.get(k, -1)
        return r[i] if 0 <= i < len(r) else ""

    windows, coverage, full = set(), 0, 0
    series = []
    for r in path_rows[1:]:
        ts = str(cell(r, "Timestamp")).replace(" UTC", "").strip()
        if ts in hedge.POISONED:
            continue
        asks = [_f(x) for x in str(cell(r, "Yes Asks")).split(",")]
        bids = [_f(x) for x in str(cell(r, "Yes Bids")).split(",")]
        if len(asks) < 6:
            continue
        first = _f(cell(r, "First Offset s")) or 30.0
        step = _f(cell(r, "Step s")) or 30.0
        windows.add(ts)
        coverage += 1
        if first <= 30.0:
            full += 1
        series.append({"ts": ts, "sym": str(cell(r, "Symbol")).replace("USDT", ""),
                       "first": first, "step": step, "asks": asks, "bids": bids})

    if not series:
        return {"error": "Path tab has no usable rows yet"}

    # Did each coin finish above its strike? The market's own YES/NO, so a
    # trade on either side resolves from it.
    ph = {h: i for i, h in enumerate(headers) if h}
    above = {}
    for r in pred_rows[1:]:
        tgt = _f(r[ph["K Target"]]) if ph.get("K Target", -1) < len(r) else None
        ev = _f(r[ph["Price at Eval"]]) if ph.get("Price at Eval", -1) < len(r) else None
        if tgt and ev:
            ts = str(r[ph["Timestamp"]]).replace(" UTC", "").strip()
            above[(ts, r[ph["Symbol"]])] = ev > tgt

    # ONE ROW PER TRADEABLE SETUP, and the filtering happens in the page.
    #
    # The page needs to slice by coin and by entry price, and cumulative
    # buckets cannot answer the question that prompted it: "under 30c"
    # contains every 20c bet, so comparing the two compares a set with a
    # subset of itself. Bands are what the reader means, and bands are only
    # honest if the underlying trades travel with the payload rather than
    # being pre-aggregated into whatever cuts were guessed here.
    #
    # So each setup ships with its entry price, the cheap side's own price
    # series from the entry onward, and -- once its window has settled -- the
    # outcome, which is what a take-profit that never triggers falls back on.
    trades = []
    for s_ in series:
        off = (ENTRY_S - s_["first"]) / s_["step"]
        if off < 0 or abs(off - round(off)) > 0.01:
            continue
        i = int(round(off))
        if i >= len(s_["asks"]):
            continue
        ya, yb = s_["asks"][i], s_["bids"][i]
        if ya is None or yb is None:
            continue
        no_ask = 100.0 - yb
        cheap_yes, cheap_no = ya <= 45.0, no_ask <= 45.0
        if cheap_yes == cheap_no:
            continue                    # neither cheap, or the book is wide
        entry = ya if cheap_yes else no_ask
        if entry <= 0:
            continue
        pts = []
        for k in range(i, len(s_["bids"])):
            b, a = s_["bids"][k], s_["asks"][k]
            pts.append(None if (b is None or a is None)
                       else round(b if cheap_yes else (100.0 - a), 1))
        # "w" is whether THIS TRADE won at settlement -- already resolved for
        # the side actually held, not the raw did-it-finish-above-the-strike
        # flag. The page shipped with the raw flag and scored every NO trade
        # by the YES question, which inflated its reported profit by about
        # 2.7x. Resolving it here means the page cannot repeat that.
        ab = above.get((s_["ts"], s_["sym"] + "USDT"))
        trades.append({
            "s": s_["sym"], "t": s_["ts"], "e": round(entry, 1),
            "y": 1 if cheap_yes else 0,
            "w": None if ab is None else (ab if cheap_yes else (not ab)),
            "p": pts,
        })

    out = {
        "built": None,
        "windows": len(windows),
        "rows": coverage,
        "full_start": full,
        "entry_s": ENTRY_S,
        "bands": [list(b) for b in BANDS],
        "take": list(TAKE),
        "trades": trades,
    }
    # The money question, with its holdout discipline intact.
    out["hedge"] = hedge.run(path_rows, pred_rows, headers)
    return out


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else OUT_DEFAULT
    import datetime
    import predictor as P
    client = P._get_client()
    sh = client.open_by_key(P.SPREADSHEET_ID)
    try:
        path_rows = sh.worksheet(hedge.SHEET).get_all_values()
    except Exception:
        path_rows = []
    pred_rows = P.open_pred_sheet(client).get_all_values()
    hedge.PRED_H = P.ALL_HEADERS
    data = build(path_rows, pred_rows, P.ALL_HEADERS)
    data["built"] = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC")

    import pathlib
    p = pathlib.Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("const PD=" + json.dumps(data, separators=(",", ":")) + ";")
    if "error" in data:
        print(f"  {out_path}: {data['error']}")
    else:
        h = data.get("hedge") or {}
        print(f"  {out_path}: {data['windows']} windows, {data['rows']} paths, "
              f"{data['full_start']} starting at +30s; "
              f"hedge -> {h.get('verdict', h.get('error', '?'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
