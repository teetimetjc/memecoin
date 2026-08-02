"""
Trailing-stop backtest against historical meme coin alerts.

Usage:
    python backtest.py                  # Sheet1 only
    python backtest.py --dip            # include Dip Watch
    python backtest.py --tab dip        # Dip Watch only
    python backtest.py --stop 2 5 10   # custom stop % values

Reads GOOGLE_CREDENTIALS from environment (same secret as the scanner).
"""

import os, sys, json, argparse
from itertools import chain

# ── column indices (mirrors memecoinscanner.py SHEET_HEADERS) ────────────────

# v1 legacy columns used for historical simulation
SHEET_PCT_COLS_V1 = ["% +3m","% +6m","% +9m","% +12m","% +15m","% +30m","% +1h","% +2h","% +4h"]
# v3 granular columns used for new rows
SHEET_PCT_COLS_V3 = ["% +1m","% +2m","% +4m","% +6m","% +8m","% +10m","% +12m","% +15m"]
SHEET_PCT_COLS    = SHEET_PCT_COLS_V1  # default for _pct_path fallback
SHEET_HEADERS    = [
    "Alert Timestamp","Name","Symbol","Address","Alert Score","Alert Price (USD)",
    "Alert Age (h)","Has Liquidity","Alert Market Cap (USD)","Alert Liquidity (USD)",
    "Alert Volume 24h (USD)","Alert Buy %","Alert 1h %","Alert 24h %","Green Flags",
    "Chart URL","Rugcheck Risk","Top 10 Holders %","LP Locked",
    "Price +3m","% +3m","Price +6m","% +6m","Price +9m","% +9m","Price +12m","% +12m",
    "Price +15m","% +15m","Price +30m","% +30m","Price +1h","% +1h",
    "Price +2h","% +2h","Price +4h","% +4h",
    "Peak % gain","Rugged?","Auto Stop-Loss?","Exit Strategy","Stop %",
    "Price +1m","% +1m","Price +2m","% +2m","Price +4m","% +4m",
    "Price +8m","% +8m","Price +10m","% +10m",
]
DIP_SHEET_HEADERS = [
    "Alert Timestamp","Strategy","Name","Symbol","Address","Alert Score",
    "Alert Price (USD)","Dip % (1h)","24h Change %","Alert Age (h)",
    "Alert Liquidity (USD)","Alert Volume 24h (USD)","Alert Buy %",
    "Rugcheck Risk","LP Locked","Chart URL",
    "Price +3m","% +3m","Price +6m","% +6m","Price +9m","% +9m","Price +12m","% +12m",
    "Price +15m","% +15m","Price +30m","% +30m","Price +1h","% +1h",
    "Price +2h","% +2h","Price +4h","% +4h",
    "Peak % gain","Rugged?","Auto Stop-Loss?","Exit Strategy","Stop %",
    "Price +1m","% +1m","Price +2m","% +2m","Price +4m","% +4m",
    "Price +8m","% +8m","Price +10m","% +10m",
]

SPREADSHEET_ID = "1PjtaTxSW1AKZ4rAUeIoHSfrV8Imh6WV_XM9uErXunQc"
BUY_SIZE       = 10.0   # $ per alert


def _get_client():
    raw = os.environ.get("GOOGLE_CREDENTIALS", "")
    if not raw:
        sys.exit("GOOGLE_CREDENTIALS env var not set.")
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(
        json.loads(raw),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return gspread.authorize(creds)


def _pct_path(row, headers):
    """Return list of floats for the price-snapshot % columns.
    Prefers v3 granular columns if the row has data there; falls back to v1 legacy."""
    def _read_cols(col_list):
        path = []
        for col in col_list:
            if col not in headers:
                continue
            idx = headers.index(col)
            val = row[idx] if idx < len(row) else ""
            if val == "" or val is None:
                break
            try:
                path.append(float(str(val).replace("%", "").strip()))
            except ValueError:
                break
        return path

    v3 = _read_cols(SHEET_PCT_COLS_V3)
    if v3:
        return v3
    return _read_cols(SHEET_PCT_COLS_V1)


def _simulate_trailing(pct_path, stop_pct):
    """
    Walk pct_path. Track running peak. Sell when drawdown from peak >= stop_pct.
    Return (exit_pct, exit_label).
    """
    if not pct_path:
        return None, "no data"
    peak = 0.0
    labels = ["% +3m","% +6m","% +9m","% +12m","% +15m","% +30m","% +1h","% +2h","% +4h"]
    for i, p in enumerate(pct_path):
        if p > peak:
            peak = p
        drawdown = peak - p
        if drawdown >= stop_pct:
            return p, labels[i] if i < len(labels) else f"snap{i}"
    return pct_path[-1], labels[len(pct_path)-1] if len(pct_path)-1 < len(labels) else "last"


def _stats(exits, buy=BUY_SIZE):
    """exits: list of (exit_pct or None). Return dict of summary stats."""
    valid  = [e for e in exits if e is not None]
    if not valid:
        return {"n": 0, "pl": 0, "roi": 0, "win_rate": 0}
    invested = len(valid) * buy
    final    = sum(buy * (1 + p / 100) for p in valid)
    pl       = final - invested
    roi      = pl / invested * 100
    wins     = sum(1 for p in valid if p > 0)
    return {
        "n":        len(valid),
        "pl":       pl,
        "roi":      roi,
        "win_rate": wins / len(valid) * 100,
    }


def run_backtest(rows, headers, tab_label):
    exit_col  = headers.index("Exit Strategy") if "Exit Strategy" in headers else None
    stop_pcts = [2, 3, 5, 10, 15, 20]

    v1_rows = []
    v2_rows = []

    for row in rows:
        strategy = (row[exit_col].strip() if exit_col and exit_col < len(row) else "")
        path = _pct_path(row, headers)
        if strategy == "v1-dumptrigger":
            v1_rows.append(path)
        elif strategy.startswith("v2-"):
            v2_rows.append(path)

    print(f"\n{'='*68}")
    print(f"  {tab_label}  |  v1 rows: {len(v1_rows)}   v2 rows (real): {len(v2_rows)}")
    print(f"{'='*68}")

    if not v1_rows:
        print("  No v1-dumptrigger rows found — nothing to simulate.")
        return

    # ── v1 actual baseline: last available snapshot per coin ─────────────────
    v1_actual_exits = [path[-1] if path else None for path in v1_rows]
    v1_actual = _stats(v1_actual_exits)

    # ── trailing-stop simulations on v1 rows ─────────────────────────────────
    sim_results = {}
    for stop in stop_pcts:
        exits = [_simulate_trailing(p, stop)[0] for p in v1_rows]
        sim_results[stop] = _stats(exits)

    # ── v2 actual (real data, not simulated) ─────────────────────────────────
    v2_actual_exits = [path[-1] if path else None for path in v2_rows]
    v2_actual = _stats(v2_actual_exits)

    # ── print table ──────────────────────────────────────────────────────────
    col_w = 14
    sep   = "-" * (col_w * 5 + 2)

    def row_fmt(label, s, note=""):
        if s["n"] == 0:
            return f"  {label:<22}  {'—':>{col_w}}  {'—':>{col_w}}  {'—':>{col_w}}  {'—':>{col_w}}"
        return (
            f"  {label:<22}"
            f"  {s['n']:>{col_w}}"
            f"  {'${:,.2f}'.format(s['pl']):>{col_w}}"
            f"  {'{:.1f}%'.format(s['roi']):>{col_w}}"
            f"  {'{:.1f}%'.format(s['win_rate']):>{col_w}}"
            + (f"   {note}" if note else "")
        )

    header_row = (
        f"  {'Strategy':<22}"
        f"  {'Coins':>{col_w}}"
        f"  {'P/L ($10 ea)':>{col_w}}"
        f"  {'ROI %':>{col_w}}"
        f"  {'Win Rate':>{col_w}}"
    )
    print(header_row)
    print("  " + sep)
    print(row_fmt("v1 actual (last snap)", v1_actual, "← baseline"))
    print("  " + sep)
    for stop in stop_pcts:
        label = f"sim trailing {stop}%"
        print(row_fmt(label, sim_results[stop]))
    print("  " + sep)
    if v2_actual["n"] > 0:
        print(row_fmt("v2 actual (real data)", v2_actual))
    else:
        print(f"  {'v2 actual (real data)':<22}  {'no v2 rows yet':>{col_w}}")

    print()
    print("  Notes:")
    print("  · v1 actual uses the last filled price snapshot per coin as the exit.")
    print("  · Simulations walk snapshots in order, sell at first trailing-stop breach.")
    print("  · $10 buy assumed per alert. Coins with no snapshot data are excluded.")
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dip", action="store_true", help="Include Dip Watch tab")
    parser.add_argument("--tab", choices=["sheet1","dip","both"], default="sheet1")
    args = parser.parse_args()

    if args.dip:
        args.tab = "both"

    client = _get_client()
    sh     = client.open_by_key(SPREADSHEET_ID)

    tabs = []
    if args.tab in ("sheet1", "both"):
        tabs.append(("Sheet1", SHEET_HEADERS))
    if args.tab in ("dip", "both"):
        tabs.append(("Dip Watch", DIP_SHEET_HEADERS))

    for tab_name, headers in tabs:
        try:
            ws   = sh.worksheet(tab_name)
            rows = ws.get_all_values()[1:]   # skip header
        except Exception as e:
            print(f"Could not read {tab_name}: {e}")
            continue
        run_backtest(rows, headers, tab_name)


if __name__ == "__main__":
    main()
