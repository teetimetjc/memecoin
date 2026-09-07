"""
15-Minute Crypto Direction Predictor
Targets: BTC, ETH, SOL, XRP, DOGE -- uses Kraken public API (no geo-restriction)
Logs predictions to Google Sheets; resolves outcomes 15 min later.

Usage:
    python predictor.py              # generate new predictions
    python predictor.py --resolve    # fill in outcomes for predictions due
    python predictor.py --backtest   # backtest composite score on last 24h of data
    python predictor.py --report     # score the current model version vs fixed thresholds
"""

import os, sys, json, math, time, argparse, requests
from datetime import datetime, timedelta, timezone

# --- CONFIG ---

# Stamped onto every logged row so a run's data can be tied to the code that
# produced it. Bump whenever a change alters composite output or what a column
# means; never pool accuracy figures across versions.
#
#   v1  (through 2026-09-06)
#       calc_stoch_rsi returned None on every call (window one element short of
#       what calc_rsi needs) and calc_vol_spike compared the in-progress candle
#       against completed ones -- 0.20 of the weight vector dead or inverted.
#       Alerts fired on confidence >= 40, measured at 6/17 = 35.3%.
#       Recorded baseline: 1231/2521 = 48.8% over Sep 1-6.
#   v2  (2026-09-07)
#       Both indicators fixed. Alerts off. EMA-divergence rule and its Kalshi
#       entry price logged in parallel for out-of-sample validation.
#       Result over 309 priced rows: the EMA rule did not replicate (42.6%
#       out-of-sample against 58.8% in-sample, z=-2.55), and a logistic fit of
#       actual_up ~ kalshi_price + composite put the composite at z=+0.53,
#       likelihood-ratio p=0.594. The composite carries no information the
#       Kalshi price does not already have, and Kalshi's prices are well
#       calibrated (41c->38.5% actual, 59c->56.2%, 70c->75.9%). Reweighting or
#       adding indicators of the same kind therefore cannot produce an edge.
#   v3  (2026-09-08 onward)
#       Stops trying to out-predict the price and tests whether the price
#       itself is briefly wrong. Kalshi is now sampled twice per window: once
#       immediately at the boundary, while the new market is still thin, and
#       again KALSHI_LATE_DELAY seconds in once liquidity has arrived. If the
#       early quote is measurably worse calibrated than the late one, the gap
#       between them is tradeable; if the two are equally calibrated, this
#       approach is finished and the negative result is worth having.
#       Also aligns the prediction window to the Kalshi 15-min boundary and
#       grades against the Kalshi strike, so accuracy means the same thing
#       here as settlement does there.
MODEL_VERSION       = "v3"

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

# Alerting mode.
#   "ema"        -- fire on the EMA-divergence rule (see ema_only_call). Backtest
#                   over 2,521 logged predictions (Sep 1-6 2026): 188/320 = 58.8%,
#                   z=+3.13. This is IN-SAMPLE and day-to-day unstable (44%-84%);
#                   treat as provisional until out-of-sample data confirms it.
#   "confidence" -- legacy: fire when confidence >= ALERT_THRESHOLD. Measured at
#                   6/17 = 35.3% over the same window, and confidence correlates
#                   with correctness at r=+0.011 (t=0.53, i.e. no signal at all).
#                   Kept only for comparison; do not enable to trade on.
#   "off"        -- no alerts. Currently selected: the EMA rule is still
#                   unvalidated out-of-sample, and the two indicator fixes below
#                   change composite output, so both need a clean week of data
#                   before anything is worth being woken up for.
# Seconds after the 15-min boundary at which the second Kalshi quote is taken.
# The early quote is captured as fast as the API allows (typically 2-6s in).
KALSHI_LATE_DELAY   = 60

ALERT_MODE          = "off"
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

# Columns A-R: prediction data
PRED_HEADERS = [
    "Timestamp", "Symbol", "Price at Pred", "Direction", "Confidence",
    "RSI(7)", "Stoch RSI", "EMA Signal", "MACD Signal", "BB Position",
    "OB Imbalance", "Vol Spike Ratio", "VWAP Dev %", "Composite Score",
    "Eval Time", "Price at Eval", "Actual Change %", "Correct?",
]

# Columns S-W: Kalshi 15-min up/down market
# K Target = Kalshi's reference price at market open
# K Up% / K Down% = live market probability (always sum to 100)
# K $10 Up/Down Profit = profit on a $10 bet if correct
KALSHI_HEADERS = [
    "K Target", "K Up%", "K Down%", "K $10 Up Profit", "K $10 Down Profit",
]

# Columns X-AE: present in the live sheet but never populated by this script.
# Declared so header sync does not clobber them and so new columns append after.
LEGACY_HEADERS = [
    "Contrarian?", "Strike", "Strike YES¢", "Strike NO¢",
    "Strike Bet Side", "Strike Bet Price¢", "Strike Payout ($10)",
    "Strike Contrarian?",
]

# Columns AF-AG: EMA-divergence rule, logged alongside the composite so its
# accuracy accrues out-of-sample without changing what the composite predicts.
EMA_HEADERS = ["EMA-Only Call", "EMA-Only Entry ¢", "EMA-Only Correct?", "Model Version"]

# v3: the early/late quote pair. "K Up%" above remains the late quote, so v2
# and v3 stay comparable on that column; these add the early one beside it
# plus the liquidity measures that would explain any gap between them.
V3_HEADERS = [
    "K Early Up%", "K Early Spread ¢", "K Early Secs",
    "K Late Spread ¢", "K Late Secs", "K Volume", "K Open Interest",
]

ALL_HEADERS = PRED_HEADERS + KALSHI_HEADERS + LEGACY_HEADERS + EMA_HEADERS + V3_HEADERS

# Kalshi 15-min up/down series tickers
KALSHI_BASE   = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_SERIES = {
    "BTCUSDT":  "KXBTC15M",
    "ETHUSDT":  "KXETH15M",
    "SOLUSDT":  "KXSOL15M",
    "XRPUSDT":  "KXXRP15M",
    "DOGEUSDT": "KXDOGE15M",
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


def _extract_odds(market):
    """Pull up_cents/down_cents/profits/target from a market dict, or return None."""
    last_d = market.get("last_price_dollars")
    if last_d is None:
        yes_ask = market.get("yes_ask_dollars")
        yes_bid = market.get("yes_bid_dollars")
        if yes_ask is not None and yes_bid is not None:
            last_d = (float(yes_ask) + float(yes_bid)) / 2
        elif yes_ask is not None:
            last_d = yes_ask
        elif yes_bid is not None:
            last_d = yes_bid
        else:
            return None

    up_cents   = round(float(last_d) * 100)
    down_cents = 100 - up_cents
    if up_cents <= 0 or down_cents <= 0:
        return None

    up_profit   = round(1000 / up_cents   - 10, 2)
    down_profit = round(1000 / down_cents - 10, 2)
    target      = market.get("floor_strike") or market.get("custom_strike") or ""

    # Spread is the liquidity proxy: a wide book is where a stale or lazy quote
    # would show up, so it is what any early/late gap should correlate with.
    bid = market.get("yes_bid_dollars")
    ask = market.get("yes_ask_dollars")
    try:
        spread = round((float(ask) - float(bid)) * 100, 1) if bid is not None and ask is not None else ""
    except (TypeError, ValueError):
        spread = ""

    return {
        "target":        target,
        "up_cents":      up_cents,
        "down_cents":    down_cents,
        "up_profit":     up_profit,
        "down_profit":   down_profit,
        "spread_cents":  spread,
        "volume":        market.get("volume", ""),
        "open_interest": market.get("open_interest", ""),
    }


def _fetch_kalshi_markets(series, hdrs, status=None, limit=200):
    """One /markets call. Returns (markets, error_string)."""
    params = {"series_ticker": series, "limit": limit}
    if status:
        params["status"] = status
    try:
        r = requests.get(f"{KALSHI_BASE}/markets", params=params,
                         headers=hdrs, timeout=10)
    except Exception as e:
        return None, f"request failed: {e}"
    if not r.ok:
        return None, f"HTTP {r.status_code} -- {r.text[:120]}"
    try:
        return r.json().get("markets", []), None
    except Exception as e:
        return None, f"bad JSON: {e}"


def get_kalshi_odds(symbol):
    """Fetch the best available Kalshi 15-min up/down market for symbol.

    Tries status=open first, then an unfiltered call, because the series holds
    hundreds of settled markets and a small unfiltered page can miss the live
    one entirely. Within a response, prefers open markets (>=1 min to close,
    most time first) and falls back to ones that closed in the last 10 minutes,
    skipping any with no price data -- brand-new markets often have none.

    Every failure path logs why. Silent returns here are what made an earlier
    round of this bug undiagnosable from the Actions logs.
    """
    series = KALSHI_SERIES.get(symbol)
    if not series:
        print(f"  [Kalshi] {symbol}: no series ticker mapped")
        return None
    if not os.environ.get("KALSHI_KEY_ID") or not os.environ.get("KALSHI_API_KEY"):
        print(f"  [Kalshi] {symbol}: KALSHI_KEY_ID / KALSHI_API_KEY not set")
        return None

    hdrs = _kalshi_headers("GET", "/trade-api/v2/markets")
    if hdrs is None:
        print(f"  [Kalshi] {symbol}: could not build signed headers")
        return None

    now = datetime.now(timezone.utc)

    for status in ("open", None):
        label = status or "unfiltered"
        markets, err = _fetch_kalshi_markets(series, hdrs, status=status)
        if err:
            print(f"  [Kalshi] {symbol}: {label} -- {err}")
            continue
        if not markets:
            print(f"  [Kalshi] {symbol}: {label} -- 0 markets returned")
            continue

        timed, undated = [], 0
        for m in markets:
            close_str = m.get("close_time") or m.get("expiration_time")
            if not close_str:
                undated += 1
                continue
            try:
                close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
            except Exception:
                undated += 1
                continue
            timed.append((m, (close_dt - now).total_seconds() / 60))

        open_c = sorted([t for t in timed if t[1] >= 1],
                        key=lambda x: x[1], reverse=True)
        recent_c = sorted([t for t in timed if -10 <= t[1] < 1],
                          key=lambda x: x[1], reverse=True)

        for m, mins in open_c + recent_c:
            odds = _extract_odds(m)
            if odds is not None:
                print(
                    f"  [Kalshi] {symbol}: {label} {m.get('ticker', '?')} "
                    f"({mins:+.1f}m) target={odds['target']} "
                    f"Up={odds['up_cents']}% Down={odds['down_cents']}% "
                    f"$10 Up=${odds['up_profit']} $10 Down=${odds['down_profit']}"
                )
                return odds

        # Nothing usable -- say what was actually in the response so the next
        # run's log answers the question instead of raising it again.
        nearest = sorted(timed, key=lambda x: abs(x[1]))[:3]
        detail = ", ".join(
            f"{m.get('ticker', '?')} {mins:+.0f}m "
            f"last={m.get('last_price_dollars')} "
            f"bid={m.get('yes_bid_dollars')} ask={m.get('yes_ask_dollars')}"
            for m, mins in nearest
        ) or "none dated"
        print(
            f"  [Kalshi] {symbol}: {label} -- {len(markets)} markets, "
            f"{len(open_c)} open / {len(recent_c)} recent / {undated} undated, "
            f"no price data. nearest: {detail}"
        )

    return None


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
        # Walk a window forward one candle at a time, oldest first, ending with
        # the window that closes on the latest candle. Each needs rsi_period + 1
        # closes so calc_rsi can form rsi_period diffs.
        end   = len(closes) - (stoch_period - 1 - i)
        start = end - (rsi_period + 1)
        if start < 0:
            continue
        r = calc_rsi(closes[start:end], rsi_period)
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


def calc_vol_spike(volumes, window=20, drop_in_progress=True):
    """Ratio of the latest completed candle's volume to the mean of the prior `window`.

    Kraken's OHLC feed returns the in-progress candle last. Including it compares
    a partially-filled minute against complete minutes, which pushed this to a
    median of 0.10 across 2,540 logged predictions (87% of readings below 1.0)
    and meant the >=1.5 spike threshold effectively never fired on a real spike.
    """
    v = volumes[:-1] if drop_in_progress and len(volumes) > 1 else volumes
    if len(v) < window + 1:
        return 1.0
    avg = sum(v[-window - 1:-1]) / window
    return v[-1] / avg if avg else 1.0


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

    ema_only_call = {"BEAR": "UP", "BULL": "DOWN"}.get(indicators["ema_label"], "")

    return {
        "symbol":        symbol,
        "price":         price,
        "direction":     direction,
        "confidence":    confidence,
        "trend":         trend_label,
        **indicators,
        "composite":     round(composite, 4),
        "ema_only_call": ema_only_call,
    }


# --- PREDICTION LOGGING ---

def run_predictions():
    # Align to the Kalshi 15-min window rather than to wall-clock arrival time.
    # v2 timestamped rows at :16 for a market running :15-:30, so its accuracy
    # column and Kalshi's settlement were scoring slightly different windows.
    now      = datetime.now(timezone.utc)
    boundary = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    eval_t   = boundary + timedelta(minutes=PREDICT_HORIZON)
    ts_str   = boundary.strftime("%Y-%m-%d %H:%M UTC")
    eval_str = eval_t.strftime("%Y-%m-%d %H:%M UTC")

    def secs_in():
        return round((datetime.now(timezone.utc) - boundary).total_seconds(), 1)

    # --- EARLY quote: hit Kalshi before anything else, while the new market is
    # still thin. This is the window v1/v2 never observed, because they slept
    # 30s first. The whole v3 hypothesis lives in this snapshot.
    print(f"  [v3] early Kalshi sweep at +{secs_in():.0f}s")
    early = {}
    for symbol in SYMBOLS:
        try:
            early[symbol] = (get_kalshi_odds(symbol), secs_in())
        except Exception as e:
            print(f"  [Kalshi] {symbol}: early sweep failed -- {e}")
            early[symbol] = (None, secs_in())

    # --- wait out the rest of the window before the late quote.
    remaining = KALSHI_LATE_DELAY - (datetime.now(timezone.utc) - boundary).total_seconds()
    if remaining > 0:
        print(f"  [v3] waiting {remaining:.0f}s for liquidity before late sweep")
        time.sleep(remaining)

    client = _get_client()
    ws     = open_pred_sheet(client)

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

            early_odds, early_secs = early.get(symbol, (None, ""))
            late_secs = secs_in()
            kalshi    = get_kalshi_odds(symbol)

            if kalshi and sig["ema_only_call"] == "UP":
                ema_entry = kalshi["up_cents"]
            elif kalshi and sig["ema_only_call"] == "DOWN":
                ema_entry = kalshi["down_cents"]
            else:
                ema_entry = ""

            kalshi_row = [
                kalshi["target"]      if kalshi else "",
                kalshi["up_cents"]    if kalshi else "",
                kalshi["down_cents"]  if kalshi else "",
                kalshi["up_profit"]   if kalshi else "",
                kalshi["down_profit"] if kalshi else "",
            ]

            v3_row = [
                early_odds["up_cents"]     if early_odds else "",
                early_odds["spread_cents"] if early_odds else "",
                early_secs,
                kalshi["spread_cents"]     if kalshi else "",
                late_secs,
                kalshi["volume"]           if kalshi else "",
                kalshi["open_interest"]    if kalshi else "",
            ]

            row = [
                ts_str, symbol, sig["price"], sig["direction"], sig["confidence"],
                sig["rsi"], sig["stoch_rsi"], sig["ema_label"], sig["macd_label"],
                sig["bb_position"], sig["ob_ratio"], sig["vol_ratio"], sig["vwap_dev"],
                sig["composite"], eval_str, "", "", "",
            ] + kalshi_row + [""] * len(LEGACY_HEADERS) + [
                sig["ema_only_call"], ema_entry, "", MODEL_VERSION,
            ] + v3_row

            ws.append_row(row, value_input_option="USER_ENTERED")

            drift = ""
            if early_odds and kalshi:
                drift = f"  early={early_odds['up_cents']}c -> late={kalshi['up_cents']}c"
            print(
                f"  {symbol}: {sig['direction']} {sig['confidence']:.1f}% conf "
                f"[trend={sig['trend']}]{drift}"
            )

            if ALERT_MODE == "ema":
                should_alert = bool(sig["ema_only_call"])
                alert_dir    = sig["ema_only_call"]
            elif ALERT_MODE == "confidence":
                should_alert = sig["confidence"] >= ALERT_THRESHOLD
                alert_dir    = sig["direction"]
            else:
                should_alert = False
                alert_dir    = ""

            if should_alert:
                kalshi_line = ""
                if kalshi:
                    kalshi_line = (
                        f"\nKalshi (target ${kalshi['target']}): "
                        f"Up={kalshi['up_cents']}% Down={kalshi['down_cents']}%"
                        f" | $10 Up profit=${kalshi['up_profit']}"
                        f" | $10 Down profit=${kalshi['down_profit']}"
                    )
                send_pushover(
                    title=f"{symbol} {alert_dir} ({ALERT_MODE})",
                    message=(
                        f"{symbol} @ ${sig['price']:,.4f}\n"
                        f"Call: {alert_dir}  [rule={ALERT_MODE}]\n"
                        f"EMA={sig['ema_label']} -> {sig['ema_only_call'] or 'no call'}\n"
                        f"Composite says: {sig['direction']} ({sig['confidence']:.1f}% conf)\n"
                        f"Trend: {sig['trend']} | MACD={sig['macd_label']} | RSI={sig['rsi']}"
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
    ema_call_col = ALL_HEADERS.index("EMA-Only Call")
    ema_cor_col  = ALL_HEADERS.index("EMA-Only Correct?")

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

            ema_call = row[ema_call_col] if len(row) > ema_call_col else ""
            if ema_call in ("UP", "DOWN"):
                ema_correct = (
                    "Yes"
                    if (ema_call == "UP" and change_pct > 0)
                    or (ema_call == "DOWN" and change_pct < 0)
                    else "No"
                )
                updates.append(gspread.Cell(i, ema_cor_col + 1, ema_correct))

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


# --- VALIDATION REPORT ---

# Thresholds fixed in advance, before v2 data existed, so the verdict cannot be
# talked into existence after the fact. Rationale for each is in VALIDATION.md.
MIN_SAMPLE       = 300     # below this, nothing is called either way
BREAKEVEN_MARGIN = 1.0     # pp above entry-implied breakeven to count as a pass


def _kalshi_pnl(entry_cents, won, stake=10.0):
    """P&L on a $10 binary at entry_cents, net of Kalshi's 0.07*C*P*(1-P) fee."""
    price     = entry_cents / 100.0
    contracts = stake / price
    fee       = 0.07 * contracts * price * (1 - price)
    return (contracts - stake - fee) if won else (-stake - fee)


def _score(name, rows, call_of, entry_of):
    """Print accuracy, realised P&L and a pass/fail against the entry-implied bar."""
    graded = [r for r in rows if call_of(r) in ("Yes", "No")]
    n = len(graded)
    if not n:
        print(f"  {name}: no graded rows yet")
        return
    wins = sum(1 for r in graded if call_of(r) == "Yes")
    acc  = wins / n * 100

    priced = [(r, entry_of(r)) for r in graded]
    priced = [(r, e) for r, e in priced if e is not None]

    print(f"  {name}")
    print(f"    graded      : {wins}/{n} = {acc:.1f}%")

    if not priced:
        print("    priced      : 0 rows -- cannot judge profitability, only accuracy")
        print("    verdict     : INCONCLUSIVE (no Kalshi prices captured)")
        return

    avg_entry = sum(e for _, e in priced) / len(priced)
    pnl       = sum(_kalshi_pnl(e, call_of(r) == "Yes") for r, e in priced)
    pw        = sum(1 for r, _ in priced if call_of(r) == "Yes")
    pacc      = pw / len(priced) * 100

    print(f"    priced      : {pw}/{len(priced)} = {pacc:.1f}%  avg entry {avg_entry:.1f}c")
    print(f"    P&L ($10)   : ${pnl:+.2f}   EV/bet ${pnl / len(priced):+.2f}")
    print(f"    bar to beat : {avg_entry:.1f}% (entry-implied) + {BREAKEVEN_MARGIN:.1f}pp")

    if len(priced) < MIN_SAMPLE:
        print(f"    verdict     : TOO EARLY ({len(priced)}/{MIN_SAMPLE} priced rows)")
    elif pacc >= avg_entry + BREAKEVEN_MARGIN and pnl > 0:
        print("    verdict     : PASS -- clears its entry-implied breakeven")
    else:
        print("    verdict     : FAIL -- does not clear breakeven at the prices paid")


def report(version=None):
    """Score the logged predictions for one model version against fixed thresholds."""
    version = version or MODEL_VERSION
    ws   = open_pred_sheet(_get_client())
    rows = ws.get_all_values()
    if len(rows) < 2:
        print("  No rows.")
        return

    idx = {h: i for i, h in enumerate(ALL_HEADERS)}
    ver_col = idx["Model Version"]

    def cell(r, name):
        i = idx[name]
        return r[i] if len(r) > i else ""

    def num(r, name):
        try:
            return float(cell(r, name))
        except (TypeError, ValueError):
            return None

    scoped = [r for r in rows[1:] if cell(r, "Model Version") == version]
    print(f"\n=== {version} validation report ===")
    print(f"  rows logged as {version}: {len(scoped)}")
    if not scoped:
        print("  Nothing logged under this version yet.")
        return
    print(f"  window: {cell(scoped[0], 'Timestamp')}  ->  {cell(scoped[-1], 'Timestamp')}\n")

    # Indicator health -- both of these were broken in v1 and should now be live.
    stoch = [r for r in scoped if cell(r, "Stoch RSI") not in ("", None)]
    print(f"  Stoch RSI populated : {len(stoch)}/{len(scoped)}"
          f"   {'OK' if len(stoch) > 0.9 * len(scoped) else 'STILL BROKEN'}")

    vols = sorted(v for v in (num(r, "Vol Spike Ratio") for r in scoped) if v is not None)
    if vols:
        med = vols[len(vols) // 2]
        over = sum(1 for v in vols if v >= 1.5) / len(vols) * 100
        print(f"  Vol Spike median    : {med:.2f}   (v1 was 0.10; ~1.0 expected)"
              f"   {'OK' if 0.5 <= med <= 2.0 else 'STILL SKEWED'}")
        print(f"  Vol Spike >=1.5     : {over:.1f}% of rows")

    kal = sum(1 for r in scoped if cell(r, "K Up%") not in ("", None))
    print(f"  Kalshi priced       : {kal}/{len(scoped)} = {kal / len(scoped) * 100:.1f}%"
          f"   {'OK' if kal > 0.9 * len(scoped) else 'INCOMPLETE -- profitability cannot be judged'}")
    print()

    def composite_entry(r):
        side = cell(r, "Direction")
        return num(r, "K Up%") if side == "UP" else num(r, "K Down%")

    # --- v3: is the early quote worse calibrated than the late one? ---------
    # The whole v3 hypothesis. If the market is briefly mispriced at the open,
    # the early quote should predict settlement worse than the late one, and
    # the gap between them is the edge. Equal calibration means no edge here.
    pairs = []
    for r in scoped:
        e, l = num(r, "K Early Up%"), num(r, "K Up%")
        chg = num(r, "Actual Change %")
        if e is None or l is None or chg is None:
            continue
        pairs.append((e, l, 1.0 if chg > 0 else 0.0, num(r, "K Early Spread ¢")))

    if pairs:
        print(f"  EARLY vs LATE QUOTE  (n={len(pairs)})")

        def brier(qs, ys):
            return sum((q / 100.0 - y) ** 2 for q, y in zip(qs, ys)) / len(ys)

        ys = [y for _, _, y, _ in pairs]
        be = brier([e for e, _, _, _ in pairs], ys)
        bl = brier([l for _, l, _, _ in pairs], ys)
        print(f"    Brier early {be:.4f}   late {bl:.4f}   "
              f"(lower = better calibrated)")

        moved = [(e, l, y) for e, l, y, _ in pairs if abs(e - l) >= 3]
        if moved:
            # When the two quotes disagree, which one was right more often?
            lw = sum(1 for e, l, y in moved if (l > 50) == (y > 0.5))
            ew = sum(1 for e, l, y in moved if (e > 50) == (y > 0.5))
            print(f"    quotes differ by >=3c on {len(moved)} rows: "
                  f"late side right {lw}/{len(moved)} ({lw / len(moved) * 100:.1f}%), "
                  f"early side right {ew}/{len(moved)} ({ew / len(moved) * 100:.1f}%)")
            print(f"    -> {'EARLY QUOTE IS STALE - the drift is tradeable' if lw > ew else 'no exploitable gap'}")

        sp = [s for _, _, _, s in pairs if s is not None]
        if sp:
            sp.sort()
            print(f"    early spread: median {sp[len(sp) // 2]:.1f}c  "
                  f"p90 {sp[int(len(sp) * .9) - 1]:.1f}c")
        print()

    _score("COMPOSITE", scoped, lambda r: cell(r, "Correct?"), composite_entry)
    print()
    _score(
        "EMA-ONLY RULE",
        [r for r in scoped if cell(r, "EMA-Only Call") in ("UP", "DOWN")],
        lambda r: cell(r, "EMA-Only Correct?"),
        lambda r: num(r, "EMA-Only Entry ¢"),
    )
    print()


# --- ENTRYPOINT ---

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resolve",  action="store_true")
    parser.add_argument("--backtest", action="store_true")
    parser.add_argument("--report",   action="store_true")
    parser.add_argument("--version",  type=str, default=None,
                        help="model version to score with --report (default: current)")
    parser.add_argument("--hours",    type=int, default=24)
    args = parser.parse_args()

    if args.report:
        report(args.version)
    elif args.backtest:
        backtest(args.hours)
    elif args.resolve:
        resolve_outcomes()
    else:
        print(f"=== Crypto Predictor ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}) ===")
        run_predictions()


if __name__ == "__main__":
    main()
