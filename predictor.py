"""
15-Minute Crypto Direction Predictor
Targets: BTC, ETH, SOL — uses Binance public API (no auth required)
Logs predictions to Google Sheets; resolves outcomes 15 min later.

Usage:
    python predictor.py              # generate new predictions
    python predictor.py --resolve    # fill in outcomes for predictions due
    python predictor.py --backtest   # backtest composite score on last 24h of data
"""

import os, sys, json, time, argparse, requests
from datetime import datetime, timedelta, timezone

# ─── CONFIG ──────────────────────────────────────────────────────────────────

SPREADSHEET_ID    = "1PjtaTxSW1AKZ4rAUeIoHSfrV8Imh6WV_XM9uErXunQc"
PRED_SHEET        = "Predictions"
SYMBOLS           = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
PREDICT_HORIZON   = 15          # minutes ahead we're predicting
CANDLE_INTERVAL   = "1m"
CANDLE_LOOKBACK   = 60          # candles (= 60 min of 1m data)
OB_DEPTH          = 20          # order book levels to fetch
RSI_PERIOD        = 7
EMA_FAST          = 9
EMA_SLOW          = 21
VOL_SPIKE_WINDOW  = 20          # candles for average volume baseline

# Composite score weights (must sum to 1.0)
WEIGHTS = {
    "rsi":        0.25,
    "ema":        0.25,
    "ob_imbal":   0.25,
    "vol_spike":  0.15,
    "vwap_dev":   0.10,
}

PRED_HEADERS = [
    "Timestamp",          # A — when prediction was made
    "Symbol",             # B
    "Price at Pred",      # C
    "Direction",          # D — UP or DOWN
    "Confidence",         # E — 0-100
    "RSI(7)",             # F
    "EMA Signal",         # G — BULL / BEAR / FLAT
    "OB Imbalance",       # H — bid/ask ratio
    "Vol Spike Ratio",    # I
    "VWAP Dev %",         # J
    "Composite Score",    # K — -1 to +1
    "Eval Time",          # L — when to check the outcome
    "Price at Eval",      # M — filled by --resolve
    "Actual Change %",    # N
    "Correct?",           # O — Yes / No
]

# ─── GOOGLE SHEETS ───────────────────────────────────────────────────────────

def _get_client():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        sys.exit("gspread not installed")
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    raw = os.environ.get("GOOGLE_CREDENTIALS")
    if raw:
        creds = Credentials.from_service_account_info(json.loads(raw), scopes=scopes)
    else:
        f = os.environ.get("GOOGLE_CREDENTIALS_FILE", "meme-coin-creds.json")
        if not os.path.exists(f):
            sys.exit("No Google credentials found")
        creds = Credentials.from_service_account_file(f, scopes=scopes)
    return gspread.authorize(creds)


def open_pred_sheet(client):
    sh = client.open_by_key(SPREADSHEET_ID)
    try:
        ws = sh.worksheet(PRED_SHEET)
    except Exception:
        ws = sh.add_worksheet(title=PRED_SHEET, rows=5000, cols=len(PRED_HEADERS))
    existing = ws.row_values(1)
    if existing != PRED_HEADERS:
        if ws.col_count < len(PRED_HEADERS):
            ws.add_cols(len(PRED_HEADERS) - ws.col_count)
        ws.update([PRED_HEADERS], "A1")
    return ws


# ─── BINANCE API ─────────────────────────────────────────────────────────────

BASE = "https://api.binance.com"

def get_klines(symbol, interval="1m", limit=60):
    r = requests.get(
        f"{BASE}/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def get_orderbook(symbol, limit=20):
    r = requests.get(
        f"{BASE}/api/v3/depth",
        params={"symbol": symbol, "limit": limit},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def get_price(symbol):
    r = requests.get(
        f"{BASE}/api/v3/ticker/price",
        params={"symbol": symbol},
        timeout=10,
    )
    r.raise_for_status()
    return float(r.json()["price"])


# ─── INDICATORS ──────────────────────────────────────────────────────────────

def calc_rsi(closes, period=7):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[-(period + 1 - i)] - closes[-(period + 2 - i)]
        (gains if diff > 0 else losses).append(abs(diff))
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 1e-9
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def calc_vwap(klines):
    total_vol, total_pv = 0, 0
    for k in klines:
        high, low, close, vol = float(k[2]), float(k[3]), float(k[4]), float(k[5])
        typical = (high + low + close) / 3
        total_pv  += typical * vol
        total_vol += vol
    return total_pv / total_vol if total_vol else None


def calc_ob_imbalance(ob, levels=10):
    bids = sum(float(b[1]) for b in ob["bids"][:levels])
    asks = sum(float(a[1]) for a in ob["asks"][:levels])
    total = bids + asks
    return bids / total if total else 0.5


def calc_vol_spike(volumes, window=20):
    if len(volumes) < window + 1:
        return 1.0
    avg = sum(volumes[-window-1:-1]) / window
    return volumes[-1] / avg if avg else 1.0


# ─── COMPOSITE SCORE ─────────────────────────────────────────────────────────

def compute_signal(symbol):
    klines = get_klines(symbol, CANDLE_INTERVAL, CANDLE_LOOKBACK + 5)
    ob     = get_orderbook(symbol, OB_DEPTH)

    closes  = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]
    price   = closes[-1]

    rsi = calc_rsi(closes, RSI_PERIOD)
    if rsi is None:
        rsi_sig = 0.0
    elif rsi > 70:
        rsi_sig = -1.0
    elif rsi > 60:
        rsi_sig = -0.5
    elif rsi < 30:
        rsi_sig = 1.0
    elif rsi < 40:
        rsi_sig = 0.5
    else:
        rsi_sig = 0.0

    ema_fast = calc_ema(closes, EMA_FAST)
    ema_slow = calc_ema(closes, EMA_SLOW)
    if ema_fast is None or ema_slow is None:
        ema_sig = 0.0
        ema_label = "FLAT"
    else:
        diff_pct = (ema_fast - ema_slow) / ema_slow * 100
        if diff_pct > 0.1:
            ema_sig, ema_label = 1.0, "BULL"
        elif diff_pct < -0.1:
            ema_sig, ema_label = -1.0, "BEAR"
        else:
            ema_sig, ema_label = 0.0, "FLAT"

    ob_ratio = calc_ob_imbalance(ob, OB_DEPTH)
    ob_sig = (ob_ratio - 0.5) * 2

    vol_ratio = calc_vol_spike(volumes, VOL_SPIKE_WINDOW)
    if vol_ratio >= 2.0:
        vol_sig = ema_sig
    elif vol_ratio >= 1.5:
        vol_sig = ema_sig * 0.5
    else:
        vol_sig = 0.0

    vwap = calc_vwap(klines)
    if vwap:
        vwap_dev_pct = (price - vwap) / vwap * 100
        if vwap_dev_pct > 1.0:
            vwap_sig = -0.5
        elif vwap_dev_pct > 0.5:
            vwap_sig = -0.25
        elif vwap_dev_pct < -1.0:
            vwap_sig = 0.5
        elif vwap_dev_pct < -0.5:
            vwap_sig = 0.25
        else:
            vwap_sig = 0.0
    else:
        vwap_dev_pct = 0.0
        vwap_sig = 0.0

    composite = (
        WEIGHTS["rsi"]       * rsi_sig  +
        WEIGHTS["ema"]       * ema_sig  +
        WEIGHTS["ob_imbal"]  * ob_sig   +
        WEIGHTS["vol_spike"] * vol_sig  +
        WEIGHTS["vwap_dev"]  * vwap_sig
    )

    direction  = "UP" if composite > 0 else "DOWN"
    confidence = round(abs(composite) * 100, 1)

    return {
        "symbol":     symbol,
        "price":      price,
        "direction":  direction,
        "confidence": confidence,
        "rsi":        round(rsi, 1) if rsi else "",
        "ema_label":  ema_label,
        "ob_ratio":   round(ob_ratio, 3),
        "vol_ratio":  round(vol_ratio, 2),
        "vwap_dev":   round(vwap_dev_pct, 3) if vwap else "",
        "composite":  round(composite, 4),
    }


# ─── PREDICTION LOGGING ──────────────────────────────────────────────────────

def run_predictions():
    client = _get_client()
    ws     = open_pred_sheet(client)
    now    = datetime.now(timezone.utc)
    eval_t = now + timedelta(minutes=PREDICT_HORIZON)

    ts_str   = now.strftime("%Y-%m-%d %H:%M UTC")
    eval_str = eval_t.strftime("%Y-%m-%d %H:%M UTC")

    for symbol in SYMBOLS:
        try:
            sig = compute_signal(symbol)
            row = [
                ts_str,
                symbol,
                sig["price"],
                sig["direction"],
                sig["confidence"],
                sig["rsi"],
                sig["ema_label"],
                sig["ob_ratio"],
                sig["vol_ratio"],
                sig["vwap_dev"],
                sig["composite"],
                eval_str,
                "", "", "",
            ]
            ws.append_row(row, value_input_option="USER_ENTERED")
            print(f"  {symbol}: {sig['direction']} {sig['confidence']:.1f}% conf "
                  f"(RSI={sig['rsi']}, EMA={sig['ema_label']}, OB={sig['ob_ratio']:.2f}, "
                  f"Vol={sig['vol_ratio']:.1f}x, composite={sig['composite']:.3f})")
        except Exception as e:
            print(f"  {symbol}: ERROR — {e}")


# ─── OUTCOME RESOLUTION ──────────────────────────────────────────────────────

def resolve_outcomes():
    client = _get_client()
    ws     = open_pred_sheet(client)
    rows   = ws.get_all_values()
    if len(rows) < 2:
        print("  No predictions to resolve.")
        return

    import gspread
    now     = datetime.now(timezone.utc)
    updates = []

    ts_col    = PRED_HEADERS.index("Timestamp")
    sym_col   = PRED_HEADERS.index("Symbol")
    price_col = PRED_HEADERS.index("Price at Pred")
    dir_col   = PRED_HEADERS.index("Direction")
    eval_col  = PRED_HEADERS.index("Eval Time")
    res_col   = PRED_HEADERS.index("Price at Eval")
    chg_col   = PRED_HEADERS.index("Actual Change %")
    cor_col   = PRED_HEADERS.index("Correct?")

    resolved = 0
    for i, row in enumerate(rows[1:], start=2):
        if len(row) <= eval_col: continue
        if len(row) > res_col and row[res_col]: continue

        try:
            eval_time = datetime.strptime(row[eval_col], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if eval_time > now:
            continue

        symbol = row[sym_col] if len(row) > sym_col else ""
        if not symbol: continue

        try:
            actual_price = get_price(symbol)
            pred_price   = float(row[price_col])
            change_pct   = round((actual_price - pred_price) / pred_price * 100, 3)
            pred_dir     = row[dir_col] if len(row) > dir_col else ""
            correct      = "Yes" if (pred_dir == "UP" and change_pct > 0) or \
                                    (pred_dir == "DOWN" and change_pct < 0) else "No"

            updates.append(gspread.Cell(i, res_col + 1, actual_price))
            updates.append(gspread.Cell(i, chg_col + 1, change_pct))
            updates.append(gspread.Cell(i, cor_col + 1, correct))
            resolved += 1
        except Exception as e:
            print(f"  Row {i} ({symbol}): resolve error — {e}")

    if updates:
        ws.update_cells(updates, value_input_option="RAW")
    print(f"  Resolved {resolved} prediction(s).")


# ─── BACKTEST ────────────────────────────────────────────────────────────────

def backtest(lookback_hours=24):
    print(f"\nBacktest: last {lookback_hours}h, predicting {PREDICT_HORIZON}min direction\n")
    for symbol in SYMBOLS:
        klines = get_klines(symbol, "1m", min(lookback_hours * 60 + 30, 1000))
        closes = [float(k[4]) for k in klines]
        vols   = [float(k[5]) for k in klines]

        correct, total = 0, 0
        for i in range(CANDLE_LOOKBACK, len(closes) - PREDICT_HORIZON, 5):
            window_c = closes[max(0, i - CANDLE_LOOKBACK):i + 1]
            window_v = vols[max(0, i - CANDLE_LOOKBACK):i + 1]

            rsi   = calc_rsi(window_c, RSI_PERIOD)
            ema_f = calc_ema(window_c, EMA_FAST)
            ema_s = calc_ema(window_c, EMA_SLOW)
            vol_r = calc_vol_spike(window_v, VOL_SPIKE_WINDOW)

            if rsi is None or ema_f is None or ema_s is None:
                continue

            rsi_sig  = -1 if rsi > 70 else (-0.5 if rsi > 60 else (1 if rsi < 30 else (0.5 if rsi < 40 else 0)))
            diff_pct = (ema_f - ema_s) / ema_s * 100
            ema_sig  = 1 if diff_pct > 0.1 else (-1 if diff_pct < -0.1 else 0)
            vol_sig  = ema_sig if vol_r >= 2.0 else (ema_sig * 0.5 if vol_r >= 1.5 else 0)

            total_pv  = sum(((float(klines[max(0,i-CANDLE_LOOKBACK)+j][2]) +
                              float(klines[max(0,i-CANDLE_LOOKBACK)+j][3]) +
                              float(klines[max(0,i-CANDLE_LOOKBACK)+j][4])) / 3) *
                            float(klines[max(0,i-CANDLE_LOOKBACK)+j][5])
                            for j in range(len(window_c)))
            total_vol = sum(window_v)
            vwap      = total_pv / total_vol if total_vol else window_c[-1]
            vwap_dev  = (window_c[-1] - vwap) / vwap * 100
            vwap_sig  = (-0.5 if vwap_dev > 1 else (-0.25 if vwap_dev > 0.5 else
                         (0.5 if vwap_dev < -1 else (0.25 if vwap_dev < -0.5 else 0))))

            composite = (WEIGHTS["rsi"] * rsi_sig + WEIGHTS["ema"] * ema_sig +
                         WEIGHTS["vol_spike"] * vol_sig + WEIGHTS["vwap_dev"] * vwap_sig)

            direction    = "UP" if composite > 0 else "DOWN"
            future_price = closes[i + PREDICT_HORIZON]
            actual_dir   = "UP" if future_price > closes[i] else "DOWN"

            if direction == actual_dir:
                correct += 1
            total += 1

        acc = correct / total * 100 if total else 0
        print(f"  {symbol}: {correct}/{total} correct = {acc:.1f}% accuracy (baseline ~50%)")

    print(f"\nNote: OB imbalance excluded from backtest (no historical order book data).\n"
          f"Live accuracy will differ. Treat as directional signal, not certainty.\n")


# ─── ENTRYPOINT ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resolve",  action="store_true", help="Fill in outcomes for due predictions")
    parser.add_argument("--backtest", action="store_true", help="Backtest on last 24h of data")
    parser.add_argument("--hours",    type=int, default=24, help="Lookback hours for backtest")
    args = parser.parse_args()

    if args.backtest:
        backtest(args.hours)
    elif args.resolve:
        resolve_outcomes()
    else:
        print(f"=== Crypto Predictor ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}) ===")
        run_predictions()


if __name__ == "__main__":
    main()
