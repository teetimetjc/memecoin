"""
15-Minute Crypto Direction Predictor
Targets: BTC, ETH, SOL -- uses Kraken public API (no geo-restriction)
Logs predictions to Google Sheets; resolves outcomes 15 min later.

Usage:
    python predictor.py              # generate new predictions
    python predictor.py --resolve    # fill in outcomes for predictions due
    python predictor.py --backtest   # backtest composite score on last 24h of data
"""

import os, sys, json, math, argparse, requests
from datetime import datetime, timedelta, timezone

# --- CONFIG ---

SPREADSHEET_ID   = "1PjtaTxSW1AKZ4rAUeIoHSfrV8Imh6WV_XM9uErXunQc"
PRED_SHEET       = "Predictions"
SYMBOLS          = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
KRAKEN_PAIRS     = {"BTCUSDT": "XBTUSD", "ETHUSDT": "ETHUSD", "SOLUSDT": "SOLUSD"}
PREDICT_HORIZON  = 15
CANDLE_INTERVAL  = "1m"
CANDLE_LOOKBACK  = 60
OB_DEPTH         = 20
RSI_PERIOD       = 7
STOCH_RSI_PERIOD = 14
EMA_FAST         = 9
EMA_SLOW         = 21
MACD_FAST        = 12
MACD_SLOW        = 26
MACD_SIGNAL      = 9
BB_PERIOD        = 20
BB_STDDEV        = 2.0
VOL_SPIKE_WINDOW = 20

# Weights must sum to 1.0
WEIGHTS = {
    "rsi":       0.12,
    "stoch_rsi": 0.10,
    "ema":       0.10,
    "macd":      0.15,
    "bb":        0.13,
    "ob_imbal":  0.20,
    "vol_spike": 0.10,
    "vwap_dev":  0.10,
}

PRED_HEADERS = [
    "Timestamp",       # A
    "Symbol",          # B
    "Price at Pred",   # C
    "Direction",       # D
    "Confidence",      # E
    "RSI(7)",          # F
    "Stoch RSI",       # G
    "EMA Signal",      # H
    "MACD Signal",     # I
    "BB Position",     # J  (-1 below lower, 0 mid, +1 above upper)
    "OB Imbalance",    # K
    "Vol Spike Ratio", # L
    "VWAP Dev %",      # M
    "Composite Score", # N
    "Eval Time",       # O
    "Price at Eval",   # P
    "Actual Change %", # Q
    "Correct?",        # R
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


def calc_stoch_rsi(closes, rsi_period=14, stoch_period=14):
    """Stochastic RSI: where current RSI sits within its recent range (0-100)."""
    if len(closes) < rsi_period + stoch_period + 1:
        return None
    rsi_values = []
    for i in range(stoch_period):
        window = closes[-(rsi_period + stoch_period - i):-(stoch_period - i) or None]
        r = calc_rsi(window, rsi_period)
        if r is not None:
            rsi_values.append(r)
    if not rsi_values:
        return None
    current_rsi = rsi_values[-1]
    min_rsi = min(rsi_values)
    max_rsi = max(rsi_values)
    if max_rsi == min_rsi:
        return 50.0
    return (current_rsi - min_rsi) / (max_rsi - min_rsi) * 100


def calc_ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def calc_macd(closes, fast=12, slow=26, signal=9):
    """Returns (macd_line, signal_line, histogram) or None."""
    if len(closes) < slow + signal:
        return None
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    if ema_fast is None or ema_slow is None:
        return None
    macd_line = ema_fast - ema_slow
    # Build recent MACD values for signal line
    macd_series = []
    for i in range(signal + 5):
        idx = len(closes) - signal - 5 + i
        if idx < slow:
            continue
        ef = calc_ema(closes[:idx + 1], fast)
        es = calc_ema(closes[:idx + 1], slow)
        if ef and es:
            macd_series.append(ef - es)
    if len(macd_series) < signal:
        return None
    signal_line = calc_ema(macd_series, signal)
    if signal_line is None:
        return None
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calc_bollinger(closes, period=20, num_std=2.0):
    """Returns (upper, middle, lower) bands."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((x - middle) ** 2 for x in window) / period
    std = math.sqrt(variance)
    return middle + num_std * std, middle, middle - num_std * std


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
    klines = get_klines(symbol, CANDLE_INTERVAL, CANDLE_LOOKBACK + 35)
    ob = get_orderbook(symbol, OB_DEPTH)

    closes = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]
    price = closes[-1]

    # RSI
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

    # Stochastic RSI
    stoch = calc_stoch_rsi(closes, STOCH_RSI_PERIOD, STOCH_RSI_PERIOD)
    if stoch is None:
        stoch_sig = 0.0
    elif stoch > 80:
        stoch_sig = -1.0
    elif stoch > 65:
        stoch_sig = -0.5
    elif stoch < 20:
        stoch_sig = 1.0
    elif stoch < 35:
        stoch_sig = 0.5
    else:
        stoch_sig = 0.0

    # EMA crossover
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

    # MACD
    macd_result = calc_macd(closes, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    if macd_result is None:
        macd_sig = 0.0
        macd_label = "FLAT"
    else:
        _, _, histogram = macd_result
        if histogram > 0.001 * price / 1000:
            macd_sig, macd_label = 1.0, "BULL"
        elif histogram < -0.001 * price / 1000:
            macd_sig, macd_label = -1.0, "BEAR"
        else:
            macd_sig, macd_label = 0.0, "FLAT"

    # Bollinger Bands
    bb = calc_bollinger(closes, BB_PERIOD, BB_STDDEV)
    if bb is None:
        bb_sig = 0.0
        bb_position = 0.0
    else:
        upper, middle, lower = bb
        band_width = upper - lower
        if band_width > 0:
            bb_position = round((price - middle) / (band_width / 2), 3)
        else:
            bb_position = 0.0
        # Above upper band = overbought (DOWN signal), below lower = oversold (UP)
        if price > upper:
            bb_sig = -1.0
        elif price > middle + (band_width * 0.25):
            bb_sig = -0.5
        elif price < lower:
            bb_sig = 1.0
        elif price < middle - (band_width * 0.25):
            bb_sig = 0.5
        else:
            bb_sig = 0.0

    # Order book imbalance
    ob_ratio = calc_ob_imbalance(ob, OB_DEPTH)
    ob_sig = (ob_ratio - 0.5) * 2

    # Volume spike
    vol_ratio = calc_vol_spike(volumes, VOL_SPIKE_WINDOW)
    if vol_ratio >= 2.0:
        vol_sig = ema_sig
    elif vol_ratio >= 1.5:
        vol_sig = ema_sig * 0.5
    else:
        vol_sig = 0.0

    # VWAP deviation
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
        WEIGHTS["rsi"]       * rsi_sig
        + WEIGHTS["stoch_rsi"] * stoch_sig
        + WEIGHTS["ema"]       * ema_sig
        + WEIGHTS["macd"]      * macd_sig
        + WEIGHTS["bb"]        * bb_sig
        + WEIGHTS["ob_imbal"]  * ob_sig
        + WEIGHTS["vol_spike"] * vol_sig
        + WEIGHTS["vwap_dev"]  * vwap_sig
    )

    direction = "UP" if composite > 0 else "DOWN"
    confidence = round(abs(composite) * 100, 1)

    return {
        "symbol":     symbol,
        "price":      price,
        "direction":  direction,
        "confidence": confidence,
        "rsi":        round(rsi, 1) if rsi is not None else "",
        "stoch_rsi":  round(stoch, 1) if stoch is not None else "",
        "ema_label":  ema_label,
        "macd_label": macd_label,
        "bb_position": bb_position,
        "ob_ratio":   round(ob_ratio, 3),
        "vol_ratio":  round(vol_ratio, 2),
        "vwap_dev":   round(vwap_dev_pct, 3) if vwap else "",
        "composite":  round(composite, 4),
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
                sig["stoch_rsi"],
                sig["ema_label"],
                sig["macd_label"],
                sig["bb_position"],
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
                f"(RSI={sig['rsi']}, StochRSI={sig['stoch_rsi']}, EMA={sig['ema_label']}, "
                f"MACD={sig['macd_label']}, BB={sig['bb_position']}, composite={sig['composite']:.3f})"
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

    sym_col   = PRED_HEADERS.index("Symbol")
    price_col = PRED_HEADERS.index("Price at Pred")
    dir_col   = PRED_HEADERS.index("Direction")
    eval_col  = PRED_HEADERS.index("Eval Time")
    res_col   = PRED_HEADERS.index("Price at Eval")
    chg_col   = PRED_HEADERS.index("Actual Change %")
    cor_col   = PRED_HEADERS.index("Correct?")

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
        klines = get_klines(symbol, "1m", min(lookback_hours * 60 + 60, 720))
        closes = [float(k[4]) for k in klines]
        vols = [float(k[5]) for k in klines]

        correct, total = 0, 0
        start = max(CANDLE_LOOKBACK, MACD_SLOW + MACD_SIGNAL + 5)
        for i in range(start, len(closes) - PREDICT_HORIZON, 5):
            wc = closes[max(0, i - CANDLE_LOOKBACK):i + 1]
            wv = vols[max(0, i - CANDLE_LOOKBACK):i + 1]

            rsi = calc_rsi(wc, RSI_PERIOD)
            stoch = calc_stoch_rsi(wc, STOCH_RSI_PERIOD, STOCH_RSI_PERIOD)
            ema_f = calc_ema(wc, EMA_FAST)
            ema_s = calc_ema(wc, EMA_SLOW)
            macd_r = calc_macd(wc, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
            bb = calc_bollinger(wc, BB_PERIOD, BB_STDDEV)
            vol_r = calc_vol_spike(wv, VOL_SPIKE_WINDOW)

            if rsi is None or ema_f is None or ema_s is None:
                continue

            rsi_sig = -1 if rsi > 70 else (-0.5 if rsi > 60 else (1 if rsi < 30 else (0.5 if rsi < 40 else 0)))

            stoch_sig = 0.0
            if stoch is not None:
                stoch_sig = -1 if stoch > 80 else (-0.5 if stoch > 65 else (1 if stoch < 20 else (0.5 if stoch < 35 else 0)))

            diff_pct = (ema_f - ema_s) / ema_s * 100
            ema_sig = 1 if diff_pct > 0.1 else (-1 if diff_pct < -0.1 else 0)

            macd_sig = 0.0
            if macd_r:
                _, _, hist = macd_r
                price_ref = wc[-1]
                if hist > 0.001 * price_ref / 1000:
                    macd_sig = 1.0
                elif hist < -0.001 * price_ref / 1000:
                    macd_sig = -1.0

            bb_sig = 0.0
            if bb:
                upper, middle, lower = bb
                p = wc[-1]
                band_width = upper - lower
                if p > upper:
                    bb_sig = -1.0
                elif p > middle + (band_width * 0.25):
                    bb_sig = -0.5
                elif p < lower:
                    bb_sig = 1.0
                elif p < middle - (band_width * 0.25):
                    bb_sig = 0.5

            vol_sig = ema_sig if vol_r >= 2.0 else (ema_sig * 0.5 if vol_r >= 1.5 else 0)

            # VWAP from window candles
            sub = klines[max(0, i - CANDLE_LOOKBACK):i + 1]
            total_pv = sum(
                ((float(c[2]) + float(c[3]) + float(c[4])) / 3) * float(c[5])
                for c in sub
            )
            total_vol = sum(float(c[5]) for c in sub)
            vwap = total_pv / total_vol if total_vol else wc[-1]
            vwap_dev = (wc[-1] - vwap) / vwap * 100
            vwap_sig = (-0.5 if vwap_dev > 1 else (-0.25 if vwap_dev > 0.5 else
                        (0.5 if vwap_dev < -1 else (0.25 if vwap_dev < -0.5 else 0))))

            composite = (
                WEIGHTS["rsi"]       * rsi_sig
                + WEIGHTS["stoch_rsi"] * stoch_sig
                + WEIGHTS["ema"]       * ema_sig
                + WEIGHTS["macd"]      * macd_sig
                + WEIGHTS["bb"]        * bb_sig
                + WEIGHTS["vol_spike"] * vol_sig
                + WEIGHTS["vwap_dev"]  * vwap_sig
            )

            direction = "UP" if composite > 0 else "DOWN"
            actual_dir = "UP" if closes[i + PREDICT_HORIZON] > closes[i] else "DOWN"
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
    parser.add_argument("--resolve",  action="store_true")
    parser.add_argument("--backtest", action="store_true")
    parser.add_argument("--hours",    type=int, default=24)
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
