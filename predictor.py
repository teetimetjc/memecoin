"""
15-Minute Crypto Direction Predictor
Targets: BTC, ETH, SOL, XRP, DOGE -- uses Kraken public API (no geo-restriction)
Logs predictions to Google Sheets; resolves outcomes 15 min later.

Usage:
    python predictor.py              # generate new predictions
    python predictor.py --resolve    # fill in outcomes for predictions due
    python predictor.py --backtest   # backtest composite score on last 24h of data
    python predictor.py --report     # score the current model version vs fixed thresholds
    python predictor.py --report --sheet   # ...and refresh the Report tab
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
#       Superseded after 10 rows. The early sweep could fall back to the
#       previous window's market, which quotes near 0 or 100 as it settles;
#       one row logged an early quote of 97c against a late quote of 46c for
#       what should have been the same contract. Those rows are not usable.
#   v4  (2026-09-07 onward)
#       Same hypothesis as v3, collected correctly. The early sweep refuses
#       closed markets, the late sweep pins to the contract the early sweep
#       saw, and both tickers are logged so any mismatch is excluded from the
#       comparison rather than averaged into it. Volume and open interest key
#       names are probed rather than assumed.
#       Known limit: the early sweep lands 17-22s into the window because that
#       is GitHub Actions runner startup. v4 therefore tests whether the ~20s
#       quote is stale against the ~70s one, not whether the opening tick is.
#       Result over 897 rows: NO EXPLOITABLE GAP. On the 389 rows where the two
#       quotes disagreed by >=3c, the early side was right 75 times against the
#       late side's 73 (McNemar z=+0.16), Brier scores tied at 0.2525 / 0.2544,
#       and the median spread was 1.0c from the moment the book opened. The
#       composite settled at 47.5% against a 47.2c average entry: breakeven.
#   v5  (2026-09-09 onward)
#       Everything measured so far says the composite carries no information
#       beyond the price, and that searching this data for a profitable subgroup
#       finds only noise: 1,567 rules screened on older rows produced hits at
#       68-73% that fell an average of 20.5 percentage points on unseen rows,
#       one of them from 72.1% to 22.2%.
#       v5 tests the only honest version of that idea. PREREGISTERED_RULES below
#       were fixed BEFORE any v5 data existed, chosen for clearing 58% on both a
#       training and a held-out slice of v1-v4 history. Each logs its own call
#       and is scored independently, so the question is settled by what happens
#       next rather than by re-slicing what already happened.
#       Caveat recorded up front: screening ~1,567 rules would be expected to
#       throw up roughly 50-90 such survivors by chance, and only 29 appeared.
#       These are candidates, not findings, and the forward test is what decides.
#       Result after 71 rule fires: all five failed. R1 73->55.6, R2 72->22.2,
#       R3 71->25.0, R4 69->50.0, R5 66->43.8, combined 26/71 = 36.6% (z=-2.25,
#       significantly worse than chance) for -$320. They fire on overbought
#       conditions, which the market already prices at 52-67c, so they needed
#       56-67% just to break even. The forward test did its job.
#   v6  (2026-09-10 onward)
#       Every input tried so far is derived from public OHLC, which is what the
#       Kalshi price is already built from -- hence the same answer five times.
#       v6 adds two inputs that are not in the candles at all:
#         - Order flow from the public trade tape. Each trade carries the taker
#           side, so aggressive buying can be separated from aggressive selling.
#           Candles say where price went; the tape says who pushed it, and a
#           drift on passive fills is a different animal from one lifted by
#           market buyers.
#         - Perpetual open interest and funding rate. Change in open interest
#           says whether positions are being opened or closed, which no spot
#           candle contains.
#       Collection only. No rules, no alerts, nothing acting on these until the
#       same logistic test that killed the composite has been run on them:
#           actual_up ~ kalshi_price + order_flow + oi_change
#       If order flow lands at z~0 like everything else, that is a clean answer
#       and the approach is finished. A brief "Predictions v5" tab was
#       used on 2026-09-09 and abandoned: the version stamp already separates the
#       generations, and every analysis filters on it rather than on tab name, so
#       a second sheet bought nothing and split the history in two.
MODEL_VERSION       = "v6"

SPREADSHEET_ID      = "1PjtaTxSW1AKZ4rAUeIoHSfrV8Imh6WV_XM9uErXunQc"
PRED_SHEET          = "Predictions"
REPORT_SHEET        = "Report"
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

# A prediction is graded by fetching the price *now*, so the fetch is only a
# fair reading of the eval moment if it happens shortly after it. Rows left
# unresolved longer than this are skipped rather than scored against an
# unrelated later price -- which is what would otherwise happen to any row
# stranded by a paused workflow or a change of sheet.
RESOLVE_GRACE_MIN   = 45

# Order flow: minutes of trade tape summarised per prediction.
ORDERFLOW_LOOKBACK_MIN = 15

# Kraken's futures venue publishes open interest and funding with no auth.
# Symbols are resolved at runtime against the live ticker list rather than
# hardcoded, since the naming differs by contract type and listing date.
KRAKEN_FUTURES_BASE = "https://futures.kraken.com/derivatives/api/v3"
FUTURES_CANDIDATES = {
    "BTCUSDT":  ["PF_XBTUSD", "PI_XBTUSD"],
    "ETHUSDT":  ["PF_ETHUSD", "PI_ETHUSD"],
    "SOLUSDT":  ["PF_SOLUSD", "PI_SOLUSD"],
    "XRPUSDT":  ["PF_XRPUSD", "PI_XRPUSD"],
    "DOGEUSDT": ["PF_DOGEUSD", "PF_XDGUSD", "PI_XDGUSD"],
}

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

# --- PREREGISTERED RULES (v5) ---
#
# Fixed 2026-09-09, before any v5 row was written. Do not add, drop or reword a
# rule while v5 is collecting: doing so turns a forward test back into a search
# and forfeits the only property that makes this evidence.
#
# Bin edges are part of the rule definition and must not drift.
RULE_BINS = {
    "RSI":  [30, 40, 50, 60, 70],      # bin 5 = RSI > 70 (overbought)
    "BB":   [-1, -0.5, 0, 0.5, 1],     # bin 5 = above the upper band
    "OB":   [0.40, 0.50, 0.60],        # bin 1 = 0.40-0.50
    "VWAP": [-0.5, 0, 0.5],            # bin 2 = 0 to +0.5
}

PREREGISTERED_RULES = [
    # name, side, conditions. Train/test accuracy from the v1-v4 screen is
    # recorded so later results can be read against what was claimed up front.
    {"name": "R1", "side": "UP",   "train": 58.5, "test": 73.1,
     "cond": {"BB": 5, "VWAP": 2, "EMA": "FLAT"}},
    {"name": "R2", "side": "UP",   "train": 59.6, "test": 71.8,
     "cond": {"RSI": 5, "OB": 1}},
    {"name": "R3", "side": "UP",   "train": 62.4, "test": 70.6,
     "cond": {"RSI": 5, "OB": 1, "EMA": "FLAT"}},
    {"name": "R4", "side": "UP",   "train": 58.3, "test": 68.6,
     "cond": {"BB": 5, "EMA": "FLAT"}},
    {"name": "R5", "side": "DOWN", "train": 58.1, "test": 65.8,
     "cond": {"BB": 3, "VWAP": 2, "coin": "BTCUSDT"}},
]


# --- CVD RULE, fixed 2026-09-11 --------------------------------------------
#
# The first input in this project to beat the Kalshi price. Over 426 v6 rows a
# logistic fit of actual_up on price plus the new columns put the CVD term at
# z=-2.45, likelihood-ratio p=0.007 against price alone. The composite managed
# p=0.594; every earlier candidate was in that territory.
#
# Direction: fade the aggressive side. When impatient sellers dominate the tape,
# price tends to rise afterwards, and vice versa -- short-term overreaction
# followed by reversion.
#
#   CVD ratio         actually went up
#   heavy selling     57.9%   (n=126)
#   mild selling      50.0%   (n= 76)
#   mild buying       39.3%   (n= 84)
#   heavy buying      44.6%   (n=121)
#
# In-sample at this threshold: 250 fires, 56.8%, average entry 50.1c,
# EV +$1.54 per $10 bet, 95% CI +$0.17 to +$2.92. Following the flow instead
# returns 43.2% and -$302, which is the inversion a real signal should show.
#
# THRESHOLD AND SIDE ARE FROZEN. Both were chosen after looking at those 426
# rows, so those rows cannot also judge the rule -- only fires logged after this
# was deployed count. Retuning either number mid-collection would turn the
# forward test back into a search, exactly as it did for R1-R5.
CVD_THRESHOLD = 0.30


def cvd_call(cvd_ratio):
    """UP / DOWN / "" for a given CVD ratio. Fades the aggressive side."""
    try:
        v = float(cvd_ratio)
    except (TypeError, ValueError):
        return ""
    if v <= -CVD_THRESHOLD:
        return "UP"       # sellers were aggressive -> expect reversion up
    if v >= CVD_THRESHOLD:
        return "DOWN"     # buyers were aggressive  -> expect reversion down
    return ""


def _bin(value, edges):
    for i, e in enumerate(edges):
        if value < e:
            return i
    return len(edges)


def rule_features(sig, symbol, when):
    """Feature dict a preregistered rule is evaluated against."""
    def num(key):
        try:
            return float(sig[key])
        except (TypeError, ValueError, KeyError):
            return None

    rsi, bb, ob, vw = num("rsi"), num("bb_position"), num("ob_ratio"), num("vwap_dev")
    if None in (rsi, bb, ob, vw):
        return None

    hour_ct = (when.hour - 5) % 24
    return {
        "RSI":     _bin(rsi, RULE_BINS["RSI"]),
        "BB":      _bin(bb,  RULE_BINS["BB"]),
        "OB":      _bin(ob,  RULE_BINS["OB"]),
        "VWAP":    _bin(vw,  RULE_BINS["VWAP"]),
        "EMA":     sig.get("ema_label"),
        "MACD":    sig.get("macd_label"),
        "coin":    symbol,
        "session": ("night" if hour_ct < 6 else "morning" if hour_ct < 12
                    else "afternoon" if hour_ct < 18 else "evening"),
    }


def evaluate_rules(sig, symbol, when):
    """Return {rule_name: "UP"/"DOWN"/""} -- blank where the rule does not fire."""
    f = rule_features(sig, symbol, when)
    out = {r["name"]: "" for r in PREREGISTERED_RULES}
    if f is None:
        return out
    for r in PREREGISTERED_RULES:
        if all(f.get(k) == v for k, v in r["cond"].items()):
            out[r["name"]] = r["side"]
    return out


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
    "K Early Ticker", "K Late Ticker",
]

# One call column and one outcome column per preregistered rule.
RULE_HEADERS = [h for r in PREREGISTERED_RULES
                for h in (f"{r['name']} Call", f"{r['name']} Correct?")]

# v6: inputs that are not derivable from the OHLC candles.
V6_HEADERS = [
    "OF Buy Vol", "OF Sell Vol", "OF CVD Ratio", "OF Trades",
    "OF Mkt Frac", "OF Window Min",
    "FUT Open Interest", "FUT Funding Rate", "FUT Symbol",
    # Blank on every row written before 2026-09-11, which is what marks the
    # start of the forward test -- no separate cutoff needs to be remembered.
    "CVD Call", "CVD Correct?",
]

ALL_HEADERS = (PRED_HEADERS + KALSHI_HEADERS + LEGACY_HEADERS
               + EMA_HEADERS + V3_HEADERS + RULE_HEADERS + V6_HEADERS)

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

    # Google caps a workbook at 10,000,000 cells across every tab, and empty
    # columns count. This tab drifted to 605 columns for 65 columns of data --
    # 2.9M cells, most of them blank -- and once the workbook hit the cap every
    # append_row failed with a 400 and collection stopped silently for a day.
    # Trim back to the header width so the allowance is spent on rows.
    if ws.col_count > len(ALL_HEADERS):
        freed = (ws.col_count - len(ALL_HEADERS)) * ws.row_count
        print(f"  Trimming {PRED_SHEET}: {ws.col_count} -> {len(ALL_HEADERS)} "
              f"columns, freeing ~{freed:,} cells.")
        ws.resize(rows=ws.row_count, cols=len(ALL_HEADERS))

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

    def first_of(*keys):
        for k in keys:
            v = market.get(k)
            if v is not None:
                return v
        return ""

    return {
        "ticker":        market.get("ticker", ""),
        "target":        target,
        "up_cents":      up_cents,
        "down_cents":    down_cents,
        "up_profit":     up_profit,
        "down_profit":   down_profit,
        "spread_cents":  spread,
        "volume":        first_of("volume", "volume_24h", "dollar_volume"),
        "open_interest": first_of("open_interest", "openInterest", "oi"),
    }


_KEYS_LOGGED = []


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


def get_kalshi_odds(symbol, allow_recent=True, want_ticker=None):
    """Fetch the best available Kalshi 15-min up/down market for symbol.

    allow_recent=False restricts the search to markets that are still open.
    The early v3 sweep sets this: a market that has already closed quotes near
    0 or 100, and pairing one of those against the live late quote manufactures
    a ~50c gap that has nothing to do with opening staleness.

    want_ticker pins the search to one market, so the late sweep can be made to
    quote the same contract the early sweep saw rather than whichever market
    happens to sort first.

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
        if markets and not _KEYS_LOGGED:
            _KEYS_LOGGED.append(1)
            print(f"  [Kalshi] market object keys: {sorted(markets[0].keys())}")
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

        if want_ticker:
            timed = [t for t in timed if t[0].get("ticker") == want_ticker]

        open_c = sorted([t for t in timed if t[1] >= 1],
                        key=lambda x: x[1], reverse=True)
        recent_c = sorted([t for t in timed if -10 <= t[1] < 1],
                          key=lambda x: x[1], reverse=True) if allow_recent else []

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


# --- ORDER FLOW / FUTURES (v6) ---

_SHAPE_LOGGED = []


def get_order_flow(symbol, since_dt):
    """Summarise the public trade tape since since_dt.

    Kraken returns each trade as
        [price, volume, time, buy/sell, market/limit, misc, trade_id]
    where the buy/sell flag is the *taker* side -- the side that crossed the
    spread. Splitting volume by it separates aggressive buying from aggressive
    selling, which is the part OHLC cannot express.
    """
    pair = KRAKEN_PAIRS.get(symbol)
    if not pair:
        return None
    since_ns = int(since_dt.timestamp() * 1_000_000_000)
    try:
        r = requests.get(f"{KRAKEN_BASE}/Trades",
                         params={"pair": pair, "since": since_ns}, timeout=15)
        if not r.ok:
            print(f"  [OrderFlow] {symbol}: HTTP {r.status_code}")
            return None
        data = r.json()
        if data.get("error"):
            print(f"  [OrderFlow] {symbol}: {data['error']}")
            return None
        result = data.get("result", {})
        key = next((k for k in result if k != "last"), None)
        if key is None:
            print(f"  [OrderFlow] {symbol}: no trade array in response")
            return None
        trades = result[key]
    except Exception as e:
        print(f"  [OrderFlow] {symbol}: {e}")
        return None

    if not trades:
        print(f"  [OrderFlow] {symbol}: 0 trades in window")
        return None

    if not _SHAPE_LOGGED:
        _SHAPE_LOGGED.append(1)
        print(f"  [OrderFlow] sample trade record: {trades[0]}")

    buy = sell = 0.0
    market = 0
    first_t = last_t = None
    for t in trades:
        try:
            vol = float(t[1]); ts = float(t[2])
        except (TypeError, ValueError, IndexError):
            continue
        side = t[3] if len(t) > 3 else ""
        typ  = t[4] if len(t) > 4 else ""
        if side == "b":
            buy += vol
        elif side == "s":
            sell += vol
        if typ == "m":
            market += 1
        first_t = ts if first_t is None else min(first_t, ts)
        last_t  = ts if last_t  is None else max(last_t, ts)

    total = buy + sell
    if total <= 0:
        return None
    window_min = round((last_t - first_t) / 60.0, 1) if first_t and last_t else ""
    return {
        "buy_vol":    round(buy, 6),
        "sell_vol":   round(sell, 6),
        # +1 = all aggressive buying, -1 = all aggressive selling.
        "cvd_ratio":  round((buy - sell) / total, 4),
        "trades":     len(trades),
        "mkt_frac":   round(market / len(trades), 3),
        "window_min": window_min,
    }


def get_futures_stats():
    """One call returning every Kraken futures ticker, keyed by symbol."""
    try:
        r = requests.get(f"{KRAKEN_FUTURES_BASE}/tickers", timeout=15)
        if not r.ok:
            print(f"  [Futures] HTTP {r.status_code} -- {r.text[:120]}")
            return {}
        tickers = r.json().get("tickers", [])
    except Exception as e:
        print(f"  [Futures] {e}")
        return {}
    if not tickers:
        print("  [Futures] empty ticker list")
        return {}
    by_symbol = {t.get("symbol"): t for t in tickers if t.get("symbol")}
    wanted = {s for cands in FUTURES_CANDIDATES.values() for s in cands}
    found = sorted(wanted & set(by_symbol))
    print(f"  [Futures] {len(by_symbol)} tickers; matched {found or 'NONE'}")
    if not found:
        sample = sorted(s for s in by_symbol if s.startswith(("PF_", "PI_")))[:12]
        print(f"  [Futures] no candidate matched. perp symbols seen: {sample}")
    return by_symbol


def pick_futures(symbol, by_symbol):
    """First listed contract for symbol that the venue actually returned."""
    for cand in FUTURES_CANDIDATES.get(symbol, []):
        t = by_symbol.get(cand)
        if t:
            return {
                "symbol":        cand,
                "open_interest": t.get("openInterest", ""),
                "funding_rate":  t.get("fundingRate", ""),
            }
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
            early[symbol] = (get_kalshi_odds(symbol, allow_recent=False), secs_in())
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

    futures = get_futures_stats()
    written = 0

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
            # Quote the same contract the early sweep saw, so the pair is a
            # before/after of one market rather than two different ones.
            kalshi    = get_kalshi_odds(
                symbol,
                want_ticker=early_odds["ticker"] if early_odds else None,
            )
            if kalshi is None and early_odds:
                kalshi = get_kalshi_odds(symbol)

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
                early_odds["ticker"]       if early_odds else "",
                kalshi["ticker"]           if kalshi else "",
            ]

            flow = get_order_flow(
                symbol, boundary - timedelta(minutes=ORDERFLOW_LOOKBACK_MIN))
            fut  = pick_futures(symbol, futures)
            v6_row = [
                flow["buy_vol"]    if flow else "",
                flow["sell_vol"]   if flow else "",
                flow["cvd_ratio"]  if flow else "",
                flow["trades"]     if flow else "",
                flow["mkt_frac"]   if flow else "",
                flow["window_min"] if flow else "",
                fut["open_interest"] if fut else "",
                fut["funding_rate"]  if fut else "",
                fut["symbol"]        if fut else "",
                cvd_call(flow["cvd_ratio"]) if flow else "",
                "",
            ]

            calls = evaluate_rules(sig, symbol, boundary)
            rule_row = []
            for r in PREREGISTERED_RULES:
                rule_row += [calls[r["name"]], ""]

            row = [
                ts_str, symbol, sig["price"], sig["direction"], sig["confidence"],
                sig["rsi"], sig["stoch_rsi"], sig["ema_label"], sig["macd_label"],
                sig["bb_position"], sig["ob_ratio"], sig["vol_ratio"], sig["vwap_dev"],
                sig["composite"], eval_str, "", "", "",
            ] + kalshi_row + [""] * len(LEGACY_HEADERS) + [
                sig["ema_only_call"], ema_entry, "", MODEL_VERSION,
            ] + v3_row + rule_row + v6_row

            ws.append_row(row, value_input_option="USER_ENTERED")
            written += 1

            if flow:
                call = cvd_call(flow["cvd_ratio"])
                print(f"    flow: CVD {flow['cvd_ratio']:+.3f} over "
                      f"{flow['trades']} trades / {flow['window_min']}min"
                      f"{'  -> CVD rule says ' + call if call else ''}")

            fired = [n for n, v in calls.items() if v]
            if fired:
                print(f"    rules fired: {', '.join(f'{n}={calls[n]}' for n in fired)}")

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
            if "above the limit" in str(e) and "cells" in str(e):
                print(f"  {symbol}: WORKBOOK FULL -- Google's 10,000,000-cell cap "
                      f"is reached, so no row could be written. Collection is "
                      f"stopped until a tab is trimmed or archived.")
            else:
                print(f"  {symbol}: ERROR -- {e}")

    if written == 0:
        sys.exit(
            f"FAILED: no rows written for any of {len(SYMBOLS)} symbols. "
            f"Collection is stopped -- see the per-symbol errors above."
        )
    if written < len(SYMBOLS):
        print(f"  WARNING: wrote {written}/{len(SYMBOLS)} symbols this run.")


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

    resolved = stale = 0
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
        late_min = (now - eval_time).total_seconds() / 60
        if late_min > RESOLVE_GRACE_MIN:
            stale += 1
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

            cvd_i = ALL_HEADERS.index("CVD Call")
            cvd_o = ALL_HEADERS.index("CVD Correct?")
            cvd_c = row[cvd_i] if len(row) > cvd_i else ""
            if cvd_c in ("UP", "DOWN"):
                ok = (cvd_c == "UP" and change_pct > 0) or (cvd_c == "DOWN" and change_pct < 0)
                updates.append(gspread.Cell(i, cvd_o + 1, "Yes" if ok else "No"))

            for r in PREREGISTERED_RULES:
                ci = ALL_HEADERS.index(f"{r['name']} Call")
                oi = ALL_HEADERS.index(f"{r['name']} Correct?")
                call = row[ci] if len(row) > ci else ""
                if call in ("UP", "DOWN"):
                    ok = (call == "UP" and change_pct > 0) or (call == "DOWN" and change_pct < 0)
                    updates.append(gspread.Cell(i, oi + 1, "Yes" if ok else "No"))

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
    if stale:
        print(f"  Skipped {stale} row(s) past the {RESOLVE_GRACE_MIN}min grace "
              f"window -- grading them now would use an unrelated price.")


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


def _pct(a, b):
    return f"{a}/{b} ({a / b * 100:.1f}%)" if b else "--"


def _score_rows(name, rows, call_of, entry_of):
    """Accuracy, realised P&L and a pass/fail against the entry-implied bar."""
    out = [[name, "", ""]]
    graded = [r for r in rows if call_of(r) in ("Yes", "No")]
    n = len(graded)
    if not n:
        return out + [["  graded", "no rows yet", ""]]

    wins = sum(1 for r in graded if call_of(r) == "Yes")
    out.append(["  graded", _pct(wins, n), ""])

    priced = [(r, entry_of(r)) for r in graded]
    priced = [(r, e) for r, e in priced if e is not None]
    if not priced:
        out.append(["  priced", "0 rows", "cannot judge profitability"])
        out.append(["  VERDICT", "INCONCLUSIVE", "no Kalshi prices captured"])
        return out

    avg   = sum(e for _, e in priced) / len(priced)
    pnl   = sum(_kalshi_pnl(e, call_of(r) == "Yes") for r, e in priced)
    pw    = sum(1 for r, _ in priced if call_of(r) == "Yes")
    pacc  = pw / len(priced) * 100

    out.append(["  priced", _pct(pw, len(priced)), f"avg entry {avg:.1f}c"])
    out.append(["  P&L ($10 flat)", f"${pnl:+.2f}", f"EV/bet ${pnl / len(priced):+.2f}"])
    out.append(["  bar to beat", f"{avg + BREAKEVEN_MARGIN:.1f}%",
                f"entry-implied {avg:.1f}% + {BREAKEVEN_MARGIN:.1f}pp"])

    if len(priced) < MIN_SAMPLE:
        out.append(["  VERDICT", "TOO EARLY", f"{len(priced)}/{MIN_SAMPLE} priced rows"])
    elif pacc >= avg + BREAKEVEN_MARGIN and pnl > 0:
        out.append(["  VERDICT", "PASS", "clears entry-implied breakeven"])
    else:
        out.append(["  VERDICT", "FAIL", "does not clear breakeven at prices paid"])
    return out


def build_report(rows, version):
    """Return the report as a list of [label, value, note] rows."""
    idx = {h: i for i, h in enumerate(ALL_HEADERS)}

    def cell(r, name):
        i = idx.get(name)
        return r[i] if i is not None and len(r) > i else ""

    def num(r, name):
        try:
            return float(cell(r, name))
        except (TypeError, ValueError):
            return None

    S = [r for r in rows[1:] if cell(r, "Model Version") == version]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    R = [[f"CRYPTO PREDICTOR -- {version} REPORT", "", ""],
         ["Last updated", now, ""],
         [f"Rows logged as {version}", len(S), ""]]
    if not S:
        return R + [["", "", ""], ["Nothing logged under this version yet.", "", ""]]
    R.append(["Window", f"{cell(S[0], 'Timestamp')} -> {cell(S[-1], 'Timestamp')}", ""])
    R.append(["", "", ""])

    # ---- data health -------------------------------------------------------
    R.append(["DATA HEALTH", "", ""])
    st = sum(1 for r in S if cell(r, "Stoch RSI") not in ("", None))
    R.append(["  Stoch RSI populated", _pct(st, len(S)),
              "OK" if st > 0.9 * len(S) else "STILL BROKEN"])

    vols = [v for v in (num(r, "Vol Spike Ratio") for r in S) if v is not None]
    if vols:
        mean = sum(vols) / len(vols)
        # Judged on the mean, not the median: 1-min crypto volume is heavily
        # right-skewed, so a healthy indicator sits well below 1.0 at the median.
        R.append(["  Vol Spike mean", f"{mean:.2f}",
                  "OK" if 0.6 <= mean <= 1.8 else "CHECK -- expected ~1.0"])
    ofc = sum(1 for r in S if cell(r, "OF CVD Ratio") not in ("", None))
    R.append(["  Order flow captured", _pct(ofc, len(S)),
              "OK" if ofc > 0.9 * len(S) else "INCOMPLETE"])
    futc = sum(1 for r in S if cell(r, "FUT Open Interest") not in ("", None))
    R.append(["  Futures OI captured", _pct(futc, len(S)),
              "OK" if futc > 0.9 * len(S) else "INCOMPLETE"])

    kal = sum(1 for r in S if cell(r, "K Up%") not in ("", None))
    R.append(["  Kalshi priced", _pct(kal, len(S)),
              "OK" if kal > 0.9 * len(S) else "INCOMPLETE"])
    R.append(["", "", ""])

    # ---- the v3 test -------------------------------------------------------
    pairs, mismatched = [], 0
    for r in S:
        e, l, chg = num(r, "K Early Up%"), num(r, "K Up%"), num(r, "Actual Change %")
        if e is None or l is None or chg is None:
            continue
        et, lt = cell(r, "K Early Ticker"), cell(r, "K Late Ticker")
        if et and lt and et != lt:
            mismatched += 1          # different contracts -- not a before/after
            continue
        pairs.append((e, l, 1.0 if chg > 0 else 0.0, num(r, "K Early Spread ¢")))

    R.append(["THE V3 TEST -- IS THE OPENING PRICE STALE?", "", ""])
    if not pairs:
        R.append(["  paired early/late rows", 0, "waiting for data"])
    else:
        brier = lambda qs: sum((q / 100.0 - y) ** 2 for q, (_, _, y, _) in zip(qs, pairs)) / len(pairs)
        be = brier([e for e, _, _, _ in pairs])
        bl = brier([l for _, l, _, _ in pairs])
        R.append(["  paired rows", len(pairs),
                  f"{mismatched} excluded (ticker mismatch)" if mismatched else ""])
        R.append(["  Brier early", f"{be:.4f}", "lower = better calibrated"])
        R.append(["  Brier late", f"{bl:.4f}", ""])

        moved = [(e, l, y) for e, l, y, _ in pairs if abs(e - l) >= 3]
        if moved:
            lw = sum(1 for e, l, y in moved if (l > 50) == (y > 0.5))
            ew = sum(1 for e, l, y in moved if (e > 50) == (y > 0.5))
            R.append(["  rows differing >=3c", len(moved), ""])
            R.append(["    late side right", _pct(lw, len(moved)), ""])
            R.append(["    early side right", _pct(ew, len(moved)), ""])
            if len(moved) < MIN_SAMPLE:
                R.append(["  VERDICT", "TOO EARLY",
                          f"{len(moved)}/{MIN_SAMPLE} disagreeing rows"])
            elif lw > ew and be > bl:
                R.append(["  VERDICT", "EARLY QUOTE IS STALE", "the drift is tradeable"])
            else:
                R.append(["  VERDICT", "NO EXPLOITABLE GAP",
                          "opening price is as good as the late one"])
        sp = sorted(s for _, _, _, s in pairs if s is not None)
        if sp:
            R.append(["  early spread median", f"{sp[len(sp) // 2]:.1f}c",
                      f"p90 {sp[int(len(sp) * .9) - 1]:.1f}c"])
    R.append(["", "", ""])

    # ---- strategies --------------------------------------------------------
    def comp_entry(r):
        return num(r, "K Up%") if cell(r, "Direction") == "UP" else num(r, "K Down%")

    R += _score_rows("COMPOSITE", S, lambda r: cell(r, "Correct?"), comp_entry)
    R.append(["", "", ""])
    R += _score_rows("EMA-ONLY RULE",
                     [r for r in S if cell(r, "EMA-Only Call") in ("UP", "DOWN")],
                     lambda r: cell(r, "EMA-Only Correct?"),
                     lambda r: num(r, "EMA-Only Entry ¢"))
    R.append(["", "", ""])

    # ---- preregistered rules ----------------------------------------------
    R.append(["PREREGISTERED RULES", "fired / correct", "claimed -> actual"])
    for rule in PREREGISTERED_RULES:
        fired = [r for r in S if cell(r, f"{rule['name']} Call") in ("UP", "DOWN")]
        graded = [r for r in fired if cell(r, f"{rule['name']} Correct?") in ("Yes", "No")]
        if not graded:
            R.append([f"  {rule['name']} ({rule['side']})", f"{len(fired)} fired, 0 graded",
                      f"claimed {rule['test']:.0f}%"])
            continue
        w = sum(1 for r in graded if cell(r, f"{rule['name']} Correct?") == "Yes")
        acc = w / len(graded) * 100
        ent = []
        for r in graded:
            e = num(r, "K Up%") if rule["side"] == "UP" else num(r, "K Down%")
            if e is not None:
                ent.append((e, cell(r, f"{rule['name']} Correct?") == "Yes"))
        if ent:
            avg = sum(e for e, _ in ent) / len(ent)
            pnl = sum(_kalshi_pnl(e, won) for e, won in ent)
            note = f"entry {avg:.1f}c  P&L ${pnl:+.2f}  EV ${pnl / len(ent):+.2f}"
        else:
            note = ""
        R.append([f"  {rule['name']} ({rule['side']})", _pct(w, len(graded)),
                  f"claimed {rule['test']:.0f}% -> {acc:.1f}%"])
        if note:
            R.append(["", "", f"    {note}"])
    R.append(["", "", ""])

    # ---- CVD rule ----------------------------------------------------------
    fired  = [r for r in S if cell(r, "CVD Call") in ("UP", "DOWN")]
    graded = [r for r in fired if cell(r, "CVD Correct?") in ("Yes", "No")]
    R.append([f"CVD RULE (|CVD| >= {CVD_THRESHOLD}, fades the flow)", "", ""])
    if not graded:
        R.append(["  status", f"{len(fired)} fired, 0 graded", "claimed 56.8%"])
    else:
        w = sum(1 for r in graded if cell(r, "CVD Correct?") == "Yes")
        ent = []
        for r in graded:
            side = cell(r, "CVD Call")
            e = num(r, "K Up%") if side == "UP" else num(r, "K Down%")
            if e is not None:
                ent.append((e, cell(r, "CVD Correct?") == "Yes"))
        R.append(["  graded", _pct(w, len(graded)),
                  f"claimed 56.8% -> {w / len(graded) * 100:.1f}%"])
        if ent:
            avg = sum(e for e, _ in ent) / len(ent)
            pnl = sum(_kalshi_pnl(e, won) for e, won in ent)
            R.append(["  economics", f"entry {avg:.1f}c",
                      f"P&L ${pnl:+.2f}  EV ${pnl / len(ent):+.2f}/bet"])
            if len(ent) < MIN_SAMPLE:
                R.append(["  VERDICT", "TOO EARLY", f"{len(ent)}/{MIN_SAMPLE} fires"])
            elif pnl > 0 and w / len(graded) * 100 >= avg + BREAKEVEN_MARGIN:
                R.append(["  VERDICT", "HOLDING", "clears breakeven at prices paid"])
            else:
                R.append(["  VERDICT", "FAILED FORWARD", "did not survive"])
    R.append(["", "", ""])

    # ---- drill-down --------------------------------------------------------
    G = [r for r in S if cell(r, "Correct?") in ("Yes", "No")]

    R.append(["BY COIN", "acc", "P&L / EV per bet"])
    for sym in sorted({cell(r, "Symbol") for r in G}):
        C = [r for r in G if cell(r, "Symbol") == sym]
        w = sum(1 for r in C if cell(r, "Correct?") == "Yes")
        P = [(r, comp_entry(r)) for r in C]
        P = [(r, e) for r, e in P if e is not None]
        if P:
            pnl = sum(_kalshi_pnl(e, cell(r, "Correct?") == "Yes") for r, e in P)
            note = f"${pnl:+.2f} / ${pnl / len(P):+.2f}"
        else:
            note = "--"
        R.append([f"  {sym}", _pct(w, len(C)), note])
    R.append(["", "", ""])

    R.append(["BY ENTRY PRICE", "acc", "breakeven / EV per bet"])
    for lo, hi, lab in [(0, 40, "<40c deep underdog"), (40, 50, "40-50c underdog"),
                        (50, 60, "50-60c favorite"), (60, 101, ">=60c strong favorite")]:
        B = [(r, comp_entry(r)) for r in G]
        B = [(r, e) for r, e in B if e is not None and lo <= e < hi]
        if len(B) < 10:
            continue
        w = sum(1 for r, _ in B if cell(r, "Correct?") == "Yes")
        avg = sum(e for _, e in B) / len(B)
        pnl = sum(_kalshi_pnl(e, cell(r, "Correct?") == "Yes") for r, e in B)
        R.append([f"  {lab}", _pct(w, len(B)),
                  f"{avg:.1f}% / ${pnl / len(B):+.2f}"])

    return R


def _write_report_tab(client, rows):
    sh = client.open_by_key(SPREADSHEET_ID)
    try:
        ws = sh.worksheet(REPORT_SHEET)
    except Exception:
        ws = sh.add_worksheet(title=REPORT_SHEET, rows=200, cols=6)
    width = max(len(r) for r in rows)
    padded = [[str(c) for c in r] + [""] * (width - len(r)) for r in rows]
    ws.clear()
    ws.update(padded, "A1")
    print(f"  Report tab updated ({len(padded)} rows).")


def report(version=None, to_sheet=False):
    """Score the logged predictions for one model version against fixed thresholds."""
    version = version or MODEL_VERSION
    client  = _get_client()
    ws      = open_pred_sheet(client)
    rows    = ws.get_all_values()
    if len(rows) < 2:
        print("  No rows.")
        return

    R = build_report(rows, version)
    print()
    for r in R:
        label = str(r[0])
        rest  = "  ".join(str(c) for c in r[1:] if str(c) != "")
        print(f"  {label:42} {rest}".rstrip())
    print()

    if to_sheet:
        _write_report_tab(client, R)


# --- ENTRYPOINT ---

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resolve",  action="store_true")
    parser.add_argument("--backtest", action="store_true")
    parser.add_argument("--report",   action="store_true")
    parser.add_argument("--version",  type=str, default=None,
                        help="model version to score with --report (default: current)")
    parser.add_argument("--sheet",    action="store_true",
                        help="with --report, also write the Report tab")
    parser.add_argument("--hours",    type=int, default=24)
    args = parser.parse_args()

    if args.report:
        report(args.version, to_sheet=args.sheet)
    elif args.backtest:
        backtest(args.hours)
    elif args.resolve:
        resolve_outcomes()
    else:
        print(f"=== Crypto Predictor ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}) ===")
        run_predictions()


if __name__ == "__main__":
    main()
