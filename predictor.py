"""
15-Minute Crypto Direction Predictor
Targets: BTC, ETH, SOL, XRP, DOGE -- uses Kraken public API (no geo-restriction)
Logs predictions to Google Sheets; resolves outcomes 15 min later.

Usage:
    python predictor.py              # generate new predictions
    python predictor.py --resolve    # fill in outcomes for predictions due
    python predictor.py --backtest   # backtest composite score on last 24h of data
"""

import os, sys, json, math, argparse, requests
from datetime import datetime, timedelta, timezone

# --- CONFIG ---

SPREADSHEET_ID      = "1PjtaTxSW1AKZ4rAUeIoHSfrV8Imh6WV_XM9uErXunQc"
PRED_SHEET          = "Predictions"
SYMBOLS             = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
KRAKEN_PAIRS        = {"BTCUSDT": "XBTUSD", "ETHUSDT": "ETHUSD", "SOLUSDT": "SOLUSD",
                       "XRPUSDT": "XRPUSD", "DOGEUSDT": "XDGUSD"}
ALTCOINS            = {"ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"}
PREDICT_HORIZON     = 15
CANDLE_INTERVAL     = "1m"
CANDLE_LOOKBACK     = 60
OB_DEPTH            = 20
RSI_PERIOD          = 7
STOCH_RSI_PERIOD    = 14
EMA_FAST            = 9
EMA_SLOW            = 21
MACD_FAST           = 12
MACD_SLOW           = 26
MACD_SIGNAL         = 9
BB_PERIOD           = 20
BB_STDDEV           = 2.0
VOL_SPIKE_WINDOW    = 20
DAILY_EMA_PERIOD    = 50
BTC_FILTER_STRENGTH = 0.3
ALERT_THRESHOLD     = 40.0

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

# Columns A-R: untouched prediction data
PRED_HEADERS = [
    "Timestamp", "Symbol", "Price at Pred", "Direction", "Confidence",
    "RSI(7)", "Stoch RSI", "EMA Signal", "MACD Signal", "BB Position",
    "OB Imbalance", "Vol Spike Ratio", "VWAP Dev %", "Composite Score",
    "Eval Time", "Price at Eval", "Actual Change %", "Correct?",
]

# Columns S-X: nearest-expiry Kalshi market
KALSHI_HEADERS = [
    "Kalshi YES¢", "Kalshi NO¢", "Bet Side", "Bet Price¢", "Bet Payout ($10)", "Contrarian?",
]

# Columns Y-AE: nearest-strike Kalshi market
KALSHI_STRIKE_HEADERS = [
    "Strike", "Strike YES¢", "Strike NO¢", "Strike Bet Side", "Strike Bet Price¢",
    "Strike Payout ($10)", "Strike Contrarian?",
]

ALL_HEADERS = PRED_HEADERS + KALSHI_HEADERS + KALSHI_STRIKE_HEADERS

# Kalshi series tickers for each symbol
KALSHI_BASE   = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_SERIES = {
    "BTCUSDT":  "KXBTC",
    "ETHUSDT":  "KXETH",
    "SOLUSDT":  "KXSOL",
    "XRPUSDT":  "KXXRP",
    "DOGEUSDT": None,
}


# --- PUSHOVER ---

def send_pushover(title, message):
    token = os.environ.get("PUSHOVER_APP_TOKEN")
    user  = os.environ.get("PUSHOVER_USER_KEY")
    if not token or not user:
        print("  [Pushover] Skipped -- PUSHOVER_APP_TOKEN or PUSHOVER_USER_KEY not set")
        return
    try:
        r = requests.post(
            "https://api.pushover.net/1/messages.json",
            data={"token": token, "user": user, "title": title, "message": message},
            timeout=10,
        )
        r.raise_for_status()
        print(f"  [Pushover] Alert sent: {title}")
    except Exception as e:
        print(f"  [Pushover] Failed: {e}")


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
        ws = sh.add_worksheet(title=PRED_SHEET, rows=5000, cols=len(ALL_HEADERS))
    existing = ws.row_values(1)
    if existing != ALL_HEADERS:
        if ws.col_count < len(ALL_HEADERS):
            ws.add_cols(len(ALL_HEADERS) - ws.col_count)
        ws.update([ALL_HEADERS], "A1")
    return ws


# --- KRAKEN API ---

KRAKEN_BASE = "https://api.kraken.com/0/public"


def get_klines(symbol, interval="1m", limit=60):
    pair = KRAKEN_PAIRS[symbol]
    r = requests.get(f"{KRAKEN_BASE}/OHLC", params={"pair": pair, "interval": 1}, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise ValueError(f"Kraken error: {data['error']}")
    result_key = [k for k in data["result"] if k != "last"][0]
    candles = data["result"][result_key]
    normalized = [[int(c[0]) * 1000, c[1], c[2], c[3], c[4], c[6]] for c in candles]
    return normalized[-limit:] if limit else normalized


def get_daily_klines(symbol, limit=60):
    pair = KRAKEN_PAIRS[symbol]
    r = requests.get(f"{KRAKEN_BASE}/OHLC", params={"pair": pair, "interval": 1440}, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise ValueError(f"Kraken error: {data['error']}")
    result_key = [k for k in data["result"] if k != "last"][0]
    candles = data["result"][result_key]
    closes = [float(c[4]) for c in candles]
    return closes[-limit:]


def get_orderbook(symbol, limit=20):
    pair = KRAKEN_PAIRS[symbol]
    r = requests.get(f"{KRAKEN_BASE}/Depth", params={"pair": pair, "count": limit}, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise ValueError(f"Kraken error: {data['error']}")
    result_key = [k for k in data["result"]][0]
    ob = data["result"][result_key]
    return {"bids": ob["bids"], "asks": ob["asks"]}


def get_price(symbol):
    pair = KRAKEN_PAIRS[symbol]
    r = requests.get(f"{KRAKEN_BASE}/Ticker", params={"pair": pair}, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise ValueError(f"Kraken error: {data['error']}")
    result_key = [k for k in data["result"]][0]
    return float(data["result"][result_key]["c"][0])


# --- KALSHI ---

def _kalshi_headers(method, path):
    """Build RSA-signed headers for Kalshi API v2."""
    key_id      = os.environ.get("KALSHI_KEY_ID", "").strip()
    private_pem = os.environ.get("KALSHI_API_KEY", "").strip()
    if not key_id or not private_pem:
        return None
    if "\\n" in private_pem and "\n" not in private_pem:
        private_pem = private_pem.replace("\\n", "\n")
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        import base64
        ts = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        msg = (ts + method.upper() + path).encode()
        private_key = serialization.load_pem_private_key(private_pem.encode(), password=None)
        sig = private_key.sign(msg, padding.PKCS1v15(), hashes.SHA256())
        sig_b64 = base64.b64encode(sig).decode()
        return {
            "KALSHI-ACCESS-KEY":       key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sig_b64,
        }
    except Exception as e:
        print(f"  [Kalshi] Auth error: {e}")
        return None


def _extract_prices(market, direction):
    """Extract YES/NO cents and betting info from a market dict. Returns dict or None."""
    yes_ask_d = market.get("yes_ask_dollars")
    yes_bid_d = market.get("yes_bid_dollars")
    no_ask_d  = market.get("no_ask_dollars")
    no_bid_d  = market.get("no_bid_dollars")

    yes_raw = yes_ask_d if yes_ask_d is not None else yes_bid_d
    no_raw  = no_ask_d  if no_ask_d  is not None else no_bid_d

    if yes_raw is None:
        return None

    yes_price = round(float(yes_raw) * 100)
    no_price  = round(float(no_raw) * 100) if no_raw is not None else 100 - yes_price

    bet_side  = "YES" if direction == "UP" else "NO"
    bet_price = yes_price if bet_side == "YES" else no_price
    payout    = round(10 * 100 / bet_price, 2) if bet_price > 0 else ""
    contrarian = "Yes" if (
        (direction == "UP" and yes_price < 50) or
        (direction == "DOWN" and yes_price > 50)
    ) else "No"

    return {
        "yes": yes_price, "no": no_price,
        "bet_side": bet_side, "bet_price": bet_price,
        "payout": payout, "contrarian": contrarian,
    }


def get_kalshi_odds(symbol, direction, current_price):
    """Fetch Kalshi odds for nearest-expiry and nearest-strike markets.
    Returns (by_expiry, by_strike) — each is a dict or None."""
    series = KALSHI_SERIES.get(symbol)
    if not series:
        return None, None
    if not os.environ.get("KALSHI_KEY_ID") or not os.environ.get("KALSHI_API_KEY"):
        return None, None

    api_path = "/trade-api/v2/markets"
    headers = _kalshi_headers("GET", api_path)
    if headers is None:
        return None, None

    now = datetime.now(timezone.utc)
    target_close = now + timedelta(minutes=PREDICT_HORIZON)

    try:
        r = requests.get(
            f"{KALSHI_BASE}/markets",
            params={"series_ticker": series, "status": "open", "limit": 20},
            headers=headers,
            timeout=10,
        )
        if not r.ok:
            print(f"  [Kalshi] {symbol}: HTTP {r.status_code} -- {r.text[:200]}")
            return None, None
        markets = r.json().get("markets", [])
        if not markets:
            return None, None

        # --- nearest-expiry market ---
        # Require >=10 min remaining so we always get a freshly-opened market,
        # not one that's almost expired from the previous window.
        best_expiry = None
        best_expiry_delta = None
        for m in markets:
            close_str = m.get("close_time") or m.get("expiration_time")
            if not close_str:
                continue
            try:
                close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
            except Exception:
                continue
            if (close_dt - now).total_seconds() < 600:
                continue  # skip markets with <10 min left
            delta = abs((close_dt - target_close).total_seconds())
            if best_expiry_delta is None or delta < best_expiry_delta:
                best_expiry_delta = delta
                best_expiry = m

        # --- nearest-strike market (among markets closing within 60 min) ---
        # Uses floor_strike field; pick the one whose strike is closest to current price.
        best_strike = None
        best_strike_delta = None
        for m in markets:
            close_str = m.get("close_time") or m.get("expiration_time")
            if not close_str:
                continue
            try:
                close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
            except Exception:
                continue
            minutes_away = (close_dt - now).total_seconds() / 60
            if minutes_away < 10 or minutes_away > 60:
                continue
            strike_raw = m.get("floor_strike")
            if strike_raw is None:
                continue
            try:
                strike = float(strike_raw)
            except (TypeError, ValueError):
                continue
            delta = abs(strike - current_price)
            if best_strike_delta is None or delta < best_strike_delta:
                best_strike_delta = delta
                best_strike = m

        by_expiry = None
        if best_expiry:
            by_expiry = _extract_prices(best_expiry, direction)
            if by_expiry:
                print(
                    f"  [Kalshi/expiry] {symbol}: YES={by_expiry['yes']}¢ NO={by_expiry['no']}¢ "
                    f"Bet={by_expiry['bet_side']}@{by_expiry['bet_price']}¢ "
                    f"Payout=${by_expiry['payout']} Contrarian={by_expiry['contrarian']}"
                )

        by_strike = None
        if best_strike:
            strike_val = best_strike.get("floor_strike", "")
            by_strike = _extract_prices(best_strike, direction)
            if by_strike:
                by_strike["strike"] = strike_val
                print(
                    f"  [Kalshi/strike] {symbol}: strike={strike_val} "
                    f"YES={by_strike['yes']}¢ NO={by_strike['no']}¢ "
                    f"Bet={by_strike['bet_side']}@{by_strike['bet_price']}¢ "
                    f"Payout=${by_strike['payout']} Contrarian={by_strike['contrarian']}"
                )

        return by_expiry, by_strike

    except Exception as e:
        print(f"  [Kalshi] {symbol}: {e}")
        return None, None


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
    if len(closes) < slow + signal:
        return None
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    if ema_fast is None or ema_slow is None:
        return None
    macd_line = ema_fast - ema_slow
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
    return macd_line, signal_line, macd_line - signal_line


def calc_bollinger(closes, period=20, num_std=2.0):
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


def get_daily_trend(symbol):
    try:
        daily_closes = get_daily_klines(symbol, limit=DAILY_EMA_PERIOD + 5)
        ema50 = calc_ema(daily_closes, DAILY_EMA_PERIOD)
        if ema50 is None:
            return 1.0, "UNKNOWN"
        price = daily_closes[-1]
        pct_above = (price - ema50) / ema50 * 100
        if pct_above > 5:    return 1.4, "BULL"
        elif pct_above > 1:  return 1.2, "BULL"
        elif pct_above > -1: return 1.0, "NEUTRAL"
        elif pct_above > -5: return 0.8, "BEAR"
        else:                return 0.6, "BEAR"
    except Exception:
        return 1.0, "UNKNOWN"


# --- COMPOSITE SCORE ---

def _raw_composite(closes, volumes, price, klines, ob):
    rsi = calc_rsi(closes, RSI_PERIOD)
    if rsi is None:             rsi_sig = 0.0
    elif rsi > 70:              rsi_sig = -1.0
    elif rsi > 60:              rsi_sig = -0.5
    elif rsi < 30:              rsi_sig = 1.0
    elif rsi < 40:              rsi_sig = 0.5
    else:                       rsi_sig = 0.0

    stoch = calc_stoch_rsi(closes, STOCH_RSI_PERIOD, STOCH_RSI_PERIOD)
    if stoch is None:           stoch_sig = 0.0
    elif stoch > 80:            stoch_sig = -1.0
    elif stoch > 65:            stoch_sig = -0.5
    elif stoch < 20:            stoch_sig = 1.0
    elif stoch < 35:            stoch_sig = 0.5
    else:                       stoch_sig = 0.0

    ema_fast = calc_ema(closes, EMA_FAST)
    ema_slow = calc_ema(closes, EMA_SLOW)
    if ema_fast is None or ema_slow is None:
        ema_sig, ema_label = 0.0, "FLAT"
    else:
        diff_pct = (ema_fast - ema_slow) / ema_slow * 100
        if diff_pct > 0.1:      ema_sig, ema_label = 1.0, "BULL"
        elif diff_pct < -0.1:   ema_sig, ema_label = -1.0, "BEAR"
        else:                   ema_sig, ema_label = 0.0, "FLAT"

    macd_result = calc_macd(closes, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    if macd_result is None:
        macd_sig, macd_label = 0.0, "FLAT"
    else:
        _, _, histogram = macd_result
        if histogram > 0.001 * price / 1000:    macd_sig, macd_label = 1.0, "BULL"
        elif histogram < -0.001 * price / 1000: macd_sig, macd_label = -1.0, "BEAR"
        else:                                   macd_sig, macd_label = 0.0, "FLAT"

    bb = calc_bollinger(closes, BB_PERIOD, BB_STDDEV)
    if bb is None:
        bb_sig, bb_position = 0.0, 0.0
    else:
        upper, middle, lower = bb
        band_width = upper - lower
        bb_position = round((price - middle) / (band_width / 2), 3) if band_width > 0 else 0.0
        if price > upper:                           bb_sig = -1.0
        elif price > middle + (band_width * 0.25):  bb_sig = -0.5
        elif price < lower:                         bb_sig = 1.0
        elif price < middle - (band_width * 0.25):  bb_sig = 0.5
        else:                                       bb_sig = 0.0

    ob_ratio = calc_ob_imbalance(ob, OB_DEPTH)
    ob_sig = (ob_ratio - 0.5) * 2

    vol_ratio = calc_vol_spike(volumes, VOL_SPIKE_WINDOW)
    if vol_ratio >= 2.0:        vol_sig = ema_sig
    elif vol_ratio >= 1.5:      vol_sig = ema_sig * 0.5
    else:                       vol_sig = 0.0

    vwap = calc_vwap(klines)
    if vwap:
        vwap_dev_pct = (price - vwap) / vwap * 100
        if vwap_dev_pct > 1.0:      vwap_sig = -0.5
        elif vwap_dev_pct > 0.5:    vwap_sig = -0.25
        elif vwap_dev_pct < -1.0:   vwap_sig = 0.5
        elif vwap_dev_pct < -0.5:   vwap_sig = 0.25
        else:                       vwap_sig = 0.0
    else:
        vwap_dev_pct, vwap_sig = 0.0, 0.0

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

    return composite, {
        "rsi":         round(rsi, 1) if rsi is not None else "",
        "stoch_rsi":   round(stoch, 1) if stoch is not None else "",
        "ema_label":   ema_label,
        "macd_label":  macd_label,
        "bb_position": bb_position,
        "ob_ratio":    round(ob_ratio, 3),
        "vol_ratio":   round(vol_ratio, 2),
        "vwap_dev":    round(vwap_dev_pct, 3) if vwap else "",
    }


def compute_signal(symbol, btc_composite=None):
    klines  = get_klines(symbol, CANDLE_INTERVAL, CANDLE_LOOKBACK + 35)
    ob      = get_orderbook(symbol, OB_DEPTH)
    closes  = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]
    price   = closes[-1]

    composite, indicators = _raw_composite(closes, volumes, price, klines, ob)

    trend_mult, trend_label = get_daily_trend(symbol)
    if (composite > 0 and trend_mult > 1.0) or (composite < 0 and trend_mult < 1.0):
        composite *= trend_mult
    else:
        composite *= (2.0 - trend_mult)

    if symbol in ALTCOINS and btc_composite is not None:
        composite = (1 - BTC_FILTER_STRENGTH) * composite + BTC_FILTER_STRENGTH * btc_composite

    composite = max(-1.0, min(1.0, composite))
    direction  = "UP" if composite > 0 else "DOWN"
    confidence = round(abs(composite) * 100, 1)

    return {
        "symbol":     symbol,
        "price":      price,
        "direction":  direction,
        "confidence": confidence,
        "trend":      trend_label,
        **indicators,
        "composite":  round(composite, 4),
    }


# --- PREDICTION LOGGING ---

def run_predictions():
    client   = _get_client()
    ws       = open_pred_sheet(client)
    now      = datetime.now(timezone.utc)
    eval_t   = now + timedelta(minutes=PREDICT_HORIZON)
    ts_str   = now.strftime("%Y-%m-%d %H:%M UTC")
    eval_str = eval_t.strftime("%Y-%m-%d %H:%M UTC")

    btc_sig = None
    try:
        btc_sig = compute_signal("BTCUSDT", btc_composite=None)
        btc_composite = btc_sig["composite"]
    except Exception as e:
        print(f"  BTCUSDT: ERROR computing macro -- {e}")
        btc_composite = None

    for symbol in SYMBOLS:
        try:
            if symbol == "BTCUSDT" and btc_sig is not None:
                sig = btc_sig
            else:
                sig = compute_signal(symbol, btc_composite=btc_composite)

            by_expiry, by_strike = get_kalshi_odds(symbol, sig["direction"], sig["price"])

            kalshi_row = [
                by_expiry["yes"] if by_expiry else "",
                by_expiry["no"]  if by_expiry else "",
                by_expiry["bet_side"]  if by_expiry else "",
                by_expiry["bet_price"] if by_expiry else "",
                by_expiry["payout"]    if by_expiry else "",
                by_expiry["contrarian"] if by_expiry else "",
            ]

            strike_row = [
                by_strike["strike"]    if by_strike else "",
                by_strike["yes"]       if by_strike else "",
                by_strike["no"]        if by_strike else "",
                by_strike["bet_side"]  if by_strike else "",
                by_strike["bet_price"] if by_strike else "",
                by_strike["payout"]    if by_strike else "",
                by_strike["contrarian"] if by_strike else "",
            ]

            row = [
                ts_str, symbol, sig["price"], sig["direction"], sig["confidence"],
                sig["rsi"], sig["stoch_rsi"], sig["ema_label"], sig["macd_label"],
                sig["bb_position"], sig["ob_ratio"], sig["vol_ratio"], sig["vwap_dev"],
                sig["composite"], eval_str, "", "", "",
            ] + kalshi_row + strike_row

            ws.append_row(row, value_input_option="USER_ENTERED")
            print(
                f"  {symbol}: {sig['direction']} {sig['confidence']:.1f}% conf "
                f"[trend={sig['trend']}] "
                f"(RSI={sig['rsi']}, EMA={sig['ema_label']}, MACD={sig['macd_label']}, composite={sig['composite']:.3f})"
            )

            if sig["confidence"] >= ALERT_THRESHOLD:
                kalshi_line = ""
                if by_strike:
                    kalshi_line = (
                        f"\nKalshi (strike ${by_strike['strike']}): "
                        f"Bet {by_strike['bet_side']} @ {by_strike['bet_price']}¢"
                        f" | Payout ${by_strike['payout']} on $10"
                        f" | Contrarian: {by_strike['contrarian']}"
                    )
                elif by_expiry:
                    kalshi_line = (
                        f"\nKalshi: Bet {by_expiry['bet_side']} @ {by_expiry['bet_price']}¢"
                        f" | Payout ${by_expiry['payout']} on $10"
                        f" | Contrarian: {by_expiry['contrarian']}"
                    )
                send_pushover(
                    title=f"{symbol} {sig['direction']} {sig['confidence']:.0f}%",
                    message=(
                        f"{symbol} @ ${sig['price']:,.4f}\n"
                        f"Direction: {sig['direction']} | Confidence: {sig['confidence']:.1f}%\n"
                        f"Trend: {sig['trend']} | EMA={sig['ema_label']} | MACD={sig['macd_label']}\n"
                        f"RSI={sig['rsi']}"
                        f"{kalshi_line}"
                    ),
                )
        except Exception as e:
            print(f"  {symbol}: ERROR -- {e}")


# --- OUTCOME RESOLUTION ---

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
            pred_price   = float(row[price_col])
            change_pct   = round((actual_price - pred_price) / pred_price * 100, 3)
            pred_dir     = row[dir_col] if len(row) > dir_col else ""
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
        vols   = [float(k[5]) for k in klines]

        correct, total = 0, 0
        start = max(CANDLE_LOOKBACK, MACD_SLOW + MACD_SIGNAL + 5)
        for i in range(start, len(closes) - PREDICT_HORIZON, 5):
            wc  = closes[max(0, i - CANDLE_LOOKBACK):i + 1]
            wv  = vols[max(0, i - CANDLE_LOOKBACK):i + 1]
            sub = klines[max(0, i - CANDLE_LOOKBACK):i + 1]
            ob_placeholder = {"bids": [], "asks": []}
            composite, _ = _raw_composite(wc, wv, wc[-1], sub, ob_placeholder)
            direction  = "UP" if composite > 0 else "DOWN"
            actual_dir = "UP" if closes[i + PREDICT_HORIZON] > closes[i] else "DOWN"
            if direction == actual_dir:
                correct += 1
            total += 1

        acc = correct / total * 100 if total else 0
        print(f"  {symbol}: {correct}/{total} correct = {acc:.1f}%")

    print("\nNote: OB imbalance, daily trend, and BTC filter excluded from backtest.")


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
