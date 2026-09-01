"""
15-Minute Crypto Direction Predictor
Targets: BTC, ETH, SOL — uses Kraken public API (no geo-restriction)
Logs predictions to Google Sheets; resolves outcomes 15 min later.

Usage:
    python predictor.py              # generate new predictions
    python predictor.py --resolve    # fill in outcomes for predictions due
    python predictor.py --backtest   # backtest composite score on last 24h of data
"""

import os, sys, json, time, argparse, requests
from datetime import datetime, timedelta, timezone

# --- CONFIG ---

SPREADSHEET_ID    = "1PjtaTxSW1AKZ4rAUeIoHSfrV8Imh6WV_XM9uErXunQc"
PRED_SHEET        = "Predictions"
SYMBOLS           = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
KRAKEN_PAIRS      = {"BTCUSDT": "XBTUSD", "ETHUSDT": "ETHUSD", "SOLUSDT": "SOLUSD"}
PREDICT_HORIZON   = 15
CANDLE_INTERVAL   = "1m"
CANDLE_LOOKBACK   = 60
OB_DEPTH          = 20
RSI_PERIOD        = 7
EMA_FAST          = 9
EMA_SLOW          = 21
VOL_SPIKE_WINDOW  = 20

WEIGHTS = {
    "rsi":        0.25,
    "ema":        0.25,
    "ob_imbal":   0.25,
    "vol_spike":  0.15,
    "vwap_dev":   0.10,
}

PRED_HEADERS = [
    "Timestamp",
    "Symbol",
    "Price at Pred",
    "Direction",
    "Confidence",
    "RSI(7)",
    "EMA Signal",
    "OB Imbalance",
    "Vol Spike Ratio",
    "VWAP Dev %",
    "Composite Score",
    "Eval Time",
    "Price at Eval",
    "Actual Change %",
    "Correct?",
]

# --- GOOGLE SHEETS ---

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


# --- KRAKEN API ---

KRAKEN_BASE = "https://api.kraken.com/0/public"


def get_klines(symbol, interval="1m", limit=60):
    pair = KRAKEN_PAIRS[symbol]
    r = requests.get(
        f"{KRAKEN_BASE}/OHLC",
        params={"pair": pair, "interval": 1},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise ValueError(f"Kraken error: {data['error']}")
    result_key = [k for k in data["result"] if k != "last"][0]
    candles = data["result"][result_key]
    normalized = [
        [int(c[0]) * 1000, c[1], c[2], c[3], c[4], c[6]]
        for c in candles
    ]
    return normalized[-limit:] if limit else normalized


def get_orderbook(symbol, limit=20):
    pair = KRAKEN_PAIRS[symbol]
    r = requests.get(
        f"{KRAKEN_BASE}/Depth",
        params={"pair": pair, "count": limit},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise ValueError(f"Kraken error: {data['error']}")
    result_key = [k for k in data["result"]][0]
    ob = data["result"][result_key]
    return {"bids": ob["bids"], "asks": ob["asks"]}


def get_price(symbol):
    pair = KRAKEN_PAIRS[symbol]
    r = requests.get(
        f"{KRAKEN_BASE}/Ticker",
        params={"pair": pair},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise ValueError(f"Kraken error: {data['error']}")
    result_key = [k for k in data["result"]][0]
    return float(data["result"][result_key]["c"][0])


# --- INDICATORS ---

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
        total_pv += typical * vol
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
    avg = sum(volumes[-window - 1:-1]) / window
    return volumes[-1] / avg if avg else 1.0


# --- COMPOSITE SCORE ---

def compute_signal(symbol):
    klines = get_klines(symbol, CANDLE_INTERVAL, CANDLE_LOOKBACK + 5)
    ob = get_orderbook(symbol, OB_DEPTH)

    closes = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]
    price = closes[-1]

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
        WEIGHTS["rsi"] * rsi_sig
        + WEIGHTS["ema"] * ema_sig
        + WEIGHTS["ob_imbal"] * ob_sig
        + WEIGHTS["vol_spike"] * vol_sig
        + WEIGHTS["vwap_dev"] * vwap_sig
    )

    direction = "UP" if composite > 0 else "DOWN"
    confidence = round(abs(composite) * 100, 1)

    return {
        "symbol": symbol,
        "price": price,
        "direction": direction,
        "confidence": confidence,
        "rsi": round(rsi, 1) if rsi else "",
        "ema_label": ema_label,
        "ob_ratio": round(ob_ratio, 3),
        "vol_ratio": round(vol_ratio, 2),
        "vwap_dev": round(vwap_dev_pct, 3) if vwap else "",
        "composite": round(composite, 4),
    }


# --- PREDICTION LOGGING ---

def run_predictions():
    client = _get_client()
    ws = open_pred_sheet(client)
    now = datetime.now(timezone.utc)
    eval_t = now + timedelta(minutes=PREDICT_HORIZON)

    ts_str = now.strftime("%Y-%m-%d %H:%M UTC")
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
            print(
                f"  {symbol}: {sig['direction']} {sig['confidence']:.1f}% conf "
                f"(RSI={sig['rsi']}, EMA={sig['ema_label']}, OB={sig['ob_ratio']:.2f}, "
                f"Vol={sig['vol_ratio']:.1f}x, composite={sig['composite']:.3f})"
            )
        except Exception as e:
            print(f"  {symbol}: ERROR -- {e}")


# --- OUTCOME RESOLUTION ---

def resolve_outcomes():
    client = _get_client()
    ws = open_pred_sheet(client)
    rows = ws.get_all_values()
    if len(rows) < 2:
        print("  No predictions to resolve.")
        return

    import gspread
    now = datetime.now(timezone.utc)
    updates = []

    sym_col = PRED_HEADERS.index("Symbol")
    price_col = PRED_HEADERS.index("Price at Pred")
    dir_col = PRED_HEADERS.index("Direction")
    eval_col = PRED_HEADERS.index("Eval Time")
    res_col = PRED_HEADERS.index("Price at Eval")
    chg_col = PRED_HEADERS.index("Actual Change %")
    cor_col = PRED_HEADERS.index("Correct?")

    resolved = 0
    for i, row in enumerate(rows[1:], start=2):
        if len(row) <= eval_col:
            continue
        if len(row) > res_col and row[res_col]:
            continue

        try:
            eval_time = datetime.strptime(row[eval_col], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if eval_time > now:
            continue

        symbol = row[sym_col] if len(row) > sym_col else ""
        if not symbol:
            continue

        try:
            actual_price = get_price(symbol)
            pred_price = float(row[price_col])
            change_pct = round((actual_price - pred_price) / pred_price * 100, 3)
            pred_dir = row[dir_col] if len(row) > dir_col else ""
            correct = (
                "Yes"
                if (pred_dir == "UP" and change_pct > 0) or (pred_dir == "DOWN" and change_pct < 0)
                else "No"
            )
            updates.append(gspread.Cell(i, res_col + 1, actual_price))
            updates.append(gspread.Cell(i, chg_col + 1, change_pct))
            updates.append(gspread.Cell(i, cor_col + 1, correct))
            resolved += 1
        except Exception as e:
            print(f"  Row {i} ({symbol}): resolve error -- {e}")

    if updates:
        ws.update_cells(updates, value_input_option="RAW")
    print(f"  Resolved {resolved} prediction(s).")


# --- BACKTEST ---

def backtest(lookback_hours=24):
    print(f"\nBacktest: last {lookback_hours}h, predicting {PREDICT_HORIZON}min direction\n")
    for symbol in SYMBOLS:
        klines = get_klines(symbol, "1m", min(lookback_hours * 60 + 30, 720))
        closes = [float(k[4]) for k in klines]
        vols = [float(k[5]) for k in klines]

        correct, total = 0, 0
        for i in range(CANDLE_LOOKBACK, len(closes) - PREDICT_HORIZON, 5):
            window_c = closes[max(0, i - CANDLE_LOOKBACK):i + 1]
            window_v = vols[max(0, i - CANDLE_LOOKBACK):i + 1]

            rsi = calc_rsi(window_c, RSI_PERIOD)
            ema_f = calc_ema(window_c, EMA_FAST)
            ema_s = calc_ema(window_c, EMA_SLOW)
            vol_r = calc_vol_spike(window_v, VOL_SPIKE_WINDOW)

            if rsi is None or ema_f is None or ema_s is None:
                continue

            rsi_sig = -1 if rsi > 70 else (-0.5 if rsi > 60 else (1 if rsi < 30 else (0.5 if rsi < 40 else 0)))
            diff_pct = (ema_f - ema_s) / ema_s * 100
            ema_sig = 1 if diff_pct > 0.1 else (-1 if diff_pct < -0.1 else 0)
            vol_sig = ema_sig if vol_r >= 2.0 else (ema_sig * 0.5 if vol_r >= 1.5 else 0)

            total_pv = sum(
                ((float(klines[max(0, i - CANDLE_LOOKBACK) + j][2])
                  + float(klines[max(0, i - CANDLE_LOOKBACK) + j][3])
                  + float(klines[max(0, i - CANDLE_LOOKBACK) + j][4])) / 3)
                * float(klines[max(0, i - CANDLE_LOOKBACK) + j][5])
                for j in range(len(window_c))
            )
            total_vol = sum(window_v)
            vwap = total_pv / total_vol if total_vol else window_c[-1]
            vwap_dev = (window_c[-1] - vwap) / vwap * 100
            vwap_sig = (
                -0.5 if vwap_dev > 1 else
                (-0.25 if vwap_dev > 0.5 else
                 (0.5 if vwap_dev < -1 else
                  (0.25 if vwap_dev < -0.5 else 0)))
            )

            composite = (
                WEIGHTS["rsi"] * rsi_sig
                + WEIGHTS["ema"] * ema_sig
                + WEIGHTS["vol_spike"] * vol_sig
                + WEIGHTS["vwap_dev"] * vwap_sig
            )

            direction = "UP" if composite > 0 else "DOWN"
            future_price = closes[i + PREDICT_HORIZON]
            actual_dir = "UP" if future_price > closes[i] else "DOWN"

            if direction == actual_dir:
                correct += 1
            total += 1

        acc = correct / total * 100 if total else 0
        print(f"  {symbol}: {correct}/{total} correct = {acc:.1f}% accuracy (baseline ~50%)")

    print("\nNote: OB imbalance excluded from backtest (no historical order book data).")
    print("Live accuracy will differ. This is directional signal only.\n")


# --- ENTRYPOINT ---

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resolve", action="store_true")
    parser.add_argument("--backtest", action="store_true")
    parser.add_argument("--hours", type=int, default=24)
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
