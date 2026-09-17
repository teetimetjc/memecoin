"""Build the dashboard's data file from the Predictions tab.

Writes dashboard/equity_data.js -- the same payload that has been assembled by
hand from each exported workbook, so the published chart can be refreshed
without anyone downloading a copy.

Read-only against the sheet. It imports predictor.py for the credentials and
column layout but calls nothing that writes, so a failure here cannot affect
collection. It is also a SEPARATE workflow from the predictor for the same
reason: a bug in the dashboard must never stop the 15-minute run.

Usage:  python dashboard_data.py [output_path]
"""

import json, math, statistics, sys
from collections import defaultdict

import predictor as P

OUT_DEFAULT = "dashboard/equity_data.js"

# The champion went live here; rows before it have order flow logged but no
# call, and they are the rows that chose the threshold, so they cannot grade it.
FROZEN = P.CVD_FROZEN


def _pnl(entry_cents, won):
    """Net on a $10 bet at `entry_cents`, Kalshi fee rounded up to the cent."""
    p = entry_cents / 100.0
    contracts = 10.0 / p
    fee = math.ceil(0.07 * contracts * p * (1 - p) * 100) / 100
    return (contracts if won else 0.0) - 10.0 - fee


def build(rows):
    idx = {h: i for i, h in enumerate(P.ALL_HEADERS)}

    def cell(r, name):
        i = idx.get(name, -1)
        return r[i] if 0 <= i < len(r) else ""

    def num(r, name):
        try:
            return float(cell(r, name))
        except (TypeError, ValueError):
            return None

    live = [c for c in P.CHALLENGERS if not c.get("retired")]

    seen, sig = set(), []
    for r in rows[1:]:
        if cell(r, "Model Version") != P.MODEL_VERSION:
            continue
        side = cell(r, "CVD Call")
        if side not in ("UP", "DOWN") or cell(r, "CVD Correct?") not in ("Yes", "No"):
            continue
        ts = cell(r, "Timestamp")
        if not ts or ts < FROZEN:
            continue
        key = (ts, cell(r, "Symbol"))
        if key in seen:                       # legacy rows contain a few dupes
            continue
        seen.add(key)
        entry = num(r, "K Up%") if side == "UP" else num(r, "K Down%")
        if not entry or entry <= 0:
            continue
        mask = 0
        for bit, ch in enumerate(live):
            if cell(r, f"{ch['name']} Call") in ("UP", "DOWN"):
                mask |= (1 << bit)
        sig.append((ts, cell(r, "Symbol").replace("USDT", ""), side, entry,
                    cell(r, "CVD Correct?") == "Yes", mask))

    if not sig:
        raise SystemExit("FAILED: no graded champion signals found.")

    sig.sort(key=lambda x: (x[0], x[1]))
    T, S, Dd, E, W, N, Q, R = [], [], [], [], [], [], [], []
    eq = 0.0
    for ts, sym, side, entry, won, mask in sig:
        # Accumulate the ROUNDED per-bet net, not the raw one. The page recomputes
        # the running total from these same rounded values when it filters, so a
        # cumulative built from unrounded nets disagrees with its own day table --
        # 16 cents over 1,030 bets, and two numbers for one thing is a bug.
        net = round(_pnl(entry, won), 2)
        eq = round(eq + net, 2)
        T.append(ts[:16]); S.append(sym); Dd.append(side)
        E.append(round(entry, 1)); W.append(1 if won else 0)
        N.append(net); Q.append(eq); R.append(mask)

    # Clustered spread: five coins bet in one window are one market event, so a
    # per-bet standard deviation understates how far a worthless rule can drift.
    groups = defaultdict(list)
    for k, ts in enumerate(T):
        groups[ts].append(N[k])
    cl = [(sum(v), len(v)) for v in groups.values()]
    n_bets = sum(c[1] for c in cl)
    mean = sum(c[0] for c in cl) / n_bets
    if len(cl) > 1:
        var = sum(c[1] * ((c[0] / c[1]) - mean) ** 2 for c in cl) / (len(cl) - 1)
        sdc = (var / len(cl)) ** 0.5 * (n_bets ** 0.5)
    else:
        sdc = statistics.stdev(N) if len(N) > 1 else 0.0

    return {
        "t": T, "s": S, "d": Dd, "e": E, "w": W, "n": N, "q": Q, "r": R,
        "rules": [{"n": c["name"], "f": c["frozen"][:16], "why": _short(c)}
                  for c in live],
        "sd": round(statistics.stdev(N), 4) if len(N) > 1 else 0.0,
        "sdc": round(sdc, 4),
        "win": len(cl),
        "built": P.datetime.now(P.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# Short captions for the dashboard's rule filter. predictor.py's `why` is prose
# meant for the report; these are the rule in a few words. Kept here rather than
# added to CHALLENGERS so the collection-critical file stays untouched.
SHORT = {
    "C1": "Kalshi spread <= 1c",
    "C3": "|CVD| >= 0.50",
    "C4": "market-order share >= 0.15",
    "C5": "entry < 40c",
    "C6": "entry < 60c",
}


def _short(ch):
    """A few words naming the rule, for the filter caption."""
    if ch["name"] in SHORT:
        return SHORT[ch["name"]]
    why = ch.get("why", "")
    return (why.split(".")[0].strip() or ch["name"])[:40]


def build_dry(rows):
    """Summarise the Dry Run tab: what an order would actually have cost.

    The bets on the chart are priced at the mid quote captured when the signal
    fired. A real order crosses the spread, so the honest question is how far
    the fill sits from that mid. This reduces the tab to the few numbers that
    answer it; the per-order detail stays in the sheet.
    """
    if not rows or len(rows) < 2:
        return None
    idx = {h: i for i, h in enumerate(rows[0])}

    def cell(r, name):
        i = idx.get(name, -1)
        return r[i] if 0 <= i < len(r) else ""

    slips, fills, partial, failed, by_coin = [], 0, 0, 0, {}
    why = {}
    for r in rows[1:]:
        state = cell(r, "Fillable?")
        if state == "YES":
            fills += 1
        elif state == "PARTIAL":
            partial += 1
            why[_why(cell(r, "Note"))] = why.get(_why(cell(r, "Note")), 0) + 1
        else:
            failed += 1
            # Counting failures without their reasons said one in six orders
            # could not be priced and nothing about why -- which is the half of
            # the finding that would let us fix it. Bucket the note instead.
            k = _why(cell(r, "Note"))
            why[k] = why.get(k, 0) + 1
            continue
        try:
            s = float(cell(r, "Slippage ¢"))
        except (TypeError, ValueError):
            continue
        slips.append(s)
        by_coin.setdefault(cell(r, "Symbol").replace("USDT", ""), []).append(s)

    if not slips:
        return {"n": 0, "fills": fills, "partial": partial, "failed": failed,
                "why": why}

    slips.sort()
    mid = slips[len(slips) // 2]
    # Cost of slippage as a share of the stake: paying x cents more per
    # contract on a fixed $10 stake costs roughly stake * x / entry.
    return {
        "n": len(slips),
        "fills": fills, "partial": partial, "failed": failed,
        "mean": round(sum(slips) / len(slips), 3),
        "median": round(mid, 3),
        "p90": round(slips[min(len(slips) - 1, int(len(slips) * 0.9))], 3),
        "worst": round(slips[-1], 3),
        "coins": {k: round(sum(v) / len(v), 3) for k, v in sorted(by_coin.items())},
        "why": dict(sorted(why.items(), key=lambda kv: -kv[1])),
    }


# Free-text notes bucketed into the few causes that need different fixes. The
# raw note stays in the sheet; this is only what the dashboard needs to show.
def _why(note):
    n = (note or "").strip().lower()
    if not n:
        return "unexplained"
    if "no ticker" in n:
        return "no market resolved"
    if "no resting size" in n:
        return "empty book"
    if "http 404" in n:
        return "market not found (404)"
    if "http 4" in n or "http 5" in n or "book unavailable" in n:
        return "book fetch failed"
    if "only $" in n:
        return "not enough depth for $10"
    return n[:40]


def _read_dry(client):
    """Dry Run tab if it exists. Absent before step 2 starts logging."""
    try:
        sh = client.open_by_key(P.SPREADSHEET_ID)
        return sh.worksheet("Dry Run").get_all_values()
    except Exception:
        return None


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else OUT_DEFAULT
    client = P._get_client()
    ws = P.open_pred_sheet(client)
    rows = ws.get_all_values()
    data = build(rows)
    # Never let the dry-run summary break the chart it rides along with.
    try:
        data["dry"] = build_dry(_read_dry(client))
    except Exception as e:
        print(f"  (dry-run summary skipped: {e})")
        data["dry"] = None
    import pathlib
    p = pathlib.Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("const D=" + json.dumps(data, separators=(",", ":")) + ";")
    print(f"  {out}: {len(data['t'])} bets, {data['win']} windows, "
          f"net ${data['q'][-1]:+.2f}, {len(data['rules'])} challengers")


if __name__ == "__main__":
    main()
