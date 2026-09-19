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

# Thresholds shown on the page. Kept small and fixed: this panel is
# descriptive, and a wide sweep here would be the first step toward picking
# the flattering cell and calling it a finding.
CHEAP = (20.0, 30.0, 40.0)
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

    # THE SETUP, and whether it bounces. Both computed from prices only, so
    # they are available before a single window has settled.
    setup = {}
    for cheap in CHEAP:
        hits = {t: 0 for t in TAKE}
        n = 0
        for s in series:
            off = (ENTRY_S - s["first"]) / s["step"]
            if off < 0 or abs(off - round(off)) > 0.01:
                continue
            i = int(round(off))
            if i >= len(s["asks"]):
                continue
            ya, yb = s["asks"][i], s["bids"][i]
            if ya is None or yb is None:
                continue
            no_ask = 100.0 - yb
            cheap_yes, cheap_no = ya <= cheap, no_ask <= cheap
            if cheap_yes == cheap_no:
                continue                 # neither side cheap, or the book is wide
            n += 1
            entry = ya if cheap_yes else no_ask
            # Highest price this side was ever bid at, after entry.
            peak = 0.0
            for k in range(i + 1, len(s["bids"])):
                b, a = s["bids"][k], s["asks"][k]
                if b is None or a is None:
                    continue
                peak = max(peak, b if cheap_yes else (100.0 - a))
            for t in TAKE:
                if entry > 0 and peak >= entry * t:
                    hits[t] += 1
        setup[str(int(cheap))] = {
            "n": n,
            "touch": {str(t): hits[t] for t in TAKE},
        }

    # A sample of paths for the chart, as the CHEAP side's own price so every
    # line starts low and the question "did it come back" is the shape.
    shown = []
    for s in series[-60:]:
        off = (ENTRY_S - s["first"]) / s["step"]
        if off < 0 or abs(off - round(off)) > 0.01:
            continue
        i = int(round(off))
        if i >= len(s["asks"]):
            continue
        ya, yb = s["asks"][i], s["bids"][i]
        if ya is None or yb is None:
            continue
        no_ask = 100.0 - yb
        cheap_yes = ya <= no_ask
        entry = ya if cheap_yes else no_ask
        if entry > 45.0:
            continue                      # not the setup; keep the chart legible
        pts = []
        for k in range(i, len(s["bids"])):
            b, a = s["bids"][k], s["asks"][k]
            pts.append(None if (b is None or a is None)
                       else round(b if cheap_yes else (100.0 - a), 1))
        shown.append({"sym": s["sym"], "ts": s["ts"], "entry": round(entry, 1),
                      "t0": ENTRY_S, "step": s["step"], "p": pts})

    out = {
        "built": None,
        "windows": len(windows),
        "rows": coverage,
        "full_start": full,
        "entry_s": ENTRY_S,
        "cheap": [int(c) for c in CHEAP],
        "take": list(TAKE),
        "setup": setup,
        "paths": shown[-40:],
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
