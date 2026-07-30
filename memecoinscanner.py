"""
Solana Meme Coin Scanner
Dependencies: pip install requests colorama gspread google-auth pytz
"""

import os, sys, json, time, argparse, requests, pytz, re
from datetime import datetime, timedelta
from colorama import Fore, Style, init

init(autoreset=True)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ─── CONFIG ──────────────────────────────────────────────────────────────────

SPREADSHEET_ID     = "1PjtaTxSW1AKZ4rAUeIoHSfrV8Imh6WV_XM9uErXunQc"
SHEET_NAME         = "Sheet1"
PUSHOVER_APP_TOKEN = os.environ.get("PUSHOVER_APP_TOKEN", "")
PUSHOVER_USER_KEY  = os.environ.get("PUSHOVER_USER_KEY", "")
CT                 = pytz.timezone("America/Chicago")

PUSHOVER_ALERT_SCORE = 7
BUY_COOLDOWN_HOURS   = 4
SELL_MONITOR_HOURS   = 24

# Set True while testing to suppress all push notifications without touching
# your Pushover secrets. Scanning, scoring, and Google Sheets logging are
# completely unaffected - only the _pushover() call becomes a no-op.
DISABLE_NOTIFICATIONS_FOR_TESTING = True

SELL_TRIGGERS = {
    "max_score":   4,
    "max_buy_pct": 40,
}

# Trailing stop: sell if price pulls back this many percentage points from peak.
# e.g. TRAILING_STOP_PCT = 5 means if peak was +20%, sell at +15% or below.
# Tune this to test different values (2, 3, 5, 10).
TRAILING_STOP_PCT = 5

# Version tag stamped on every alert row — auto-updates when TRAILING_STOP_PCT changes.
# Bump the prefix (v2 → v3 etc.) if the scoring/filter logic itself changes.
EXIT_STRATEGY = f"v2-trailing{TRAILING_STOP_PCT}pct"

# Scoring thresholds (8 criteria)
THRESHOLDS = {
    "min_liquidity_usd":   10_000,
    "max_liquidity_usd":   5_000_000,
    "min_volume_24h_usd":  5_000,
    "min_market_cap_usd":  10_000,
    "max_age_hours":       24,
    "min_txns_24h":        50,
    "min_price_change_1h": -10,
    "min_score":           6,
}

# Hard filters — ALL must pass before an alert is sent / row is logged.
# NOTE: this volume minimum is a separate, *final* gate on top of the
# THRESHOLDS["min_volume_24h_usd"] scoring criterion above. It was previously
# set to 1_000_000, which combined with max_age_hours=24 meant a token had to
# do $1M+ in 24h volume *and* be under a day old to ever get logged - almost
# nothing clears that bar, which is why the sheet stayed empty. Lowered to a
# more realistic level for catching fresh meme coins. Tune to taste.
#
# min_buy_pct was previously 75. In live data, every 7-8/8 candidate sat at
# 59-74% buy pressure (real momentum coins see steady profit-taking even
# while pumping) - so 75% silenced every single qualifying coin. The scoring
# system's own internal bar for "good" buy pressure is 55% (see score_token).
# 60% keeps the hard gate a bit stricter than that scoring bar - still real
# confirmation - without re-creating the same all-or-nothing problem.
ALERT_FILTERS = {
    "min_buy_pct":       60,
    "min_volume_usd":    20_000,
    "require_liquidity": True,
}

# Rug / stop-loss thresholds
RUG_THRESHOLD_PCT  = -50
STOPLOSS_THRESHOLD = -15

WATCH_INTERVAL_SECONDS = 60

# Follow-up windows: (price_col, pct_col, min_minutes_elapsed)
FOLLOWUP_WINDOWS = [
    ("Price +3m",  "% +3m",    2),
    ("Price +6m",  "% +6m",    5),
    ("Price +9m",  "% +9m",    8),
    ("Price +12m", "% +12m",  11),
    ("Price +15m", "% +15m",  12),
    ("Price +30m", "% +30m",  25),
    ("Price +1h",  "% +1h",   55),
    ("Price +2h",  "% +2h",  115),
    ("Price +4h",  "% +4h",  235),
]
FOLLOWUP_MAX_HOURS = 5

DIP_SHEET_NAME          = "Dip Watch"
DIP_RECOVERY_MIN_SCORE  = 6
PUMP_PULLBACK_MIN_SCORE = 5

DIP_RECOVERY_THRESHOLDS = {
    "min_drop_1h":        -60,
    "max_drop_1h":        -25,
    "min_liquidity_usd":  10_000,
    "min_volume_h1_usd":  10_000,
    "min_volume_24h_usd": 30_000,
    "min_buy_pct":        65,
    "min_age_hours":       1,
    "max_age_hours":      50,
    "avoid_age_min":       2,
    "avoid_age_max":      10,
}

PUMP_PULLBACK_THRESHOLDS = {
    "min_pump_24h":       100,
    "min_drop_1h":        -40,
    "max_drop_1h":        -10,
    "min_liquidity_usd":  20_000,
    "min_volume_24h_usd": 200_000,
    "min_buy_pct":        60,
}

# ─── SHEET SCHEMA ────────────────────────────────────────────────────────────

SHEET_HEADERS = [
    "Alert Timestamp",         # A
    "Name",                    # B
    "Symbol",                  # C
    "Address",                 # D
    "Alert Score",             # E
    "Alert Price (USD)",       # F
    "Alert Age (h)",           # G
    "Has Liquidity",           # H
    "Alert Market Cap (USD)",  # I
    "Alert Liquidity (USD)",   # J
    "Alert Volume 24h (USD)",  # K
    "Alert Buy %",             # L
    "Alert 1h %",              # M
    "Alert 24h %",             # N
    "Green Flags",             # O
    "Chart URL",               # P
    "Rugcheck Risk",           # Q
    "Top 10 Holders %",        # R
    "LP Locked",               # S
    "Price +3m",               # T
    "% +3m",                   # U
    "Price +6m",               # V
    "% +6m",                   # W
    "Price +9m",               # X
    "% +9m",                   # Y
    "Price +12m",              # Z
    "% +12m",                  # AA
    "Price +15m",              # AB
    "% +15m",                  # AC
    "Price +30m",              # AD
    "% +30m",                  # AE
    "Price +1h",               # AF
    "% +1h",                   # AG
    "Price +2h",               # AH
    "% +2h",                   # AI
    "Price +4h",               # AJ
    "% +4h",                   # AK
    "Peak % gain",             # AL
    "Rugged?",                 # AM
    "Auto Stop-Loss?",         # AN
    "Exit Strategy",           # AO
    "Stop %",                  # AP
]

SELL_LOG_HEADERS = [
    "Timestamp", "Name", "Symbol", "Address",
    "Price at sell signal", "% at sell", "Peak % gain", "Drawdown from peak", "Time held", "Triggers",
]

DIP_SHEET_HEADERS = [
    "Alert Timestamp",         # A
    "Strategy",                # B
    "Name",                    # C
    "Symbol",                  # D
    "Address",                 # E
    "Alert Score",             # F
    "Alert Price (USD)",       # G
    "Dip % (1h)",              # H
    "24h Change %",            # I
    "Alert Age (h)",           # J
    "Alert Liquidity (USD)",   # K
    "Alert Volume 24h (USD)",  # L
    "Alert Buy %",             # M
    "Rugcheck Risk",           # N
    "LP Locked",               # O
    "Chart URL",               # P
    "Price +3m",               # Q
    "% +3m",                   # R
    "Price +6m",               # S
    "% +6m",                   # T
    "Price +9m",               # U
    "% +9m",                   # V
    "Price +12m",              # W
    "% +12m",                  # X
    "Price +15m",              # Y
    "% +15m",                  # Z
    "Price +30m",              # AA
    "% +30m",                  # AB
    "Price +1h",               # AC
    "% +1h",                   # AD
    "Price +2h",               # AE
    "% +2h",                   # AF
    "Price +4h",               # AG
    "% +4h",                   # AH
    "Peak % gain",             # AI
    "Rugged?",                 # AJ
    "Auto Stop-Loss?",         # AK
    "Exit Strategy",           # AL
    "Stop %",                  # AM
]


def _col(h):
    return SHEET_HEADERS.index(h)


def _col_dip(h):
    return DIP_SHEET_HEADERS.index(h)


def _col_letter(idx):
    """Convert 0-indexed column number to A1 column letter (handles AA, AB...)."""
    result = ""
    n = idx + 1
    while n:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result

# ─── GOOGLE SHEETS ───────────────────────────────────────────────────────────

def _get_gspread_client():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print(f"{Fore.YELLOW}gspread not installed."); return None
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=scopes)
    else:
        f = os.environ.get("GOOGLE_CREDENTIALS_FILE", "meme-coin-creds.json")
        if not os.path.exists(f):
            print(f"{Fore.YELLOW}No Google credentials found."); return None
        creds = Credentials.from_service_account_file(f, scopes=scopes)
    import gspread
    return gspread.authorize(creds)


def _ensure_header(ws, expected):
    """Update header row in place - never inserts a new row."""
    if ws.row_values(1) != expected:
        ws.update([expected], "A1")
        print(f"{Fore.YELLOW}  Header row updated.")


def _append_row_with_retry(ws, row, attempts=3, delay_seconds=3):
    """
    Append a row to a worksheet, retrying on transient failures (e.g. Google
    Sheets API rate limits, which are plausible given how often this script
    runs). Returns True on success, False if all attempts failed.
    """
    for attempt in range(1, attempts + 1):
        try:
            ws.append_row(row, value_input_option="USER_ENTERED")
            return True
        except Exception as e:
            if attempt < attempts:
                print(f"{Fore.YELLOW}  Sheet write attempt {attempt}/{attempts} failed "
                      f"({e}); retrying in {delay_seconds}s...")
                time.sleep(delay_seconds)
            else:
                print(f"{Fore.RED}  Sheet write failed after {attempts} attempts: {e}")
    return False


def open_sheet():
    """Open worksheets once per run. Returns (client, ws, ws_sell, all_rows)."""
    client = _get_gspread_client()
    if not client: return None, None, None, []
    try:
        sh  = client.open_by_key(SPREADSHEET_ID)
        ws  = sh.worksheet(SHEET_NAME)
        _ensure_header(ws, SHEET_HEADERS)
        try:
            ws_sell = sh.worksheet("Sell Log")
        except Exception:
            ws_sell = sh.add_worksheet(title="Sell Log", rows=1000, cols=10)
        _ensure_header(ws_sell, SELL_LOG_HEADERS)
        return client, ws, ws_sell, ws.get_all_values()
    except Exception as e:
        print(f"{Fore.YELLOW}Sheet open failed: {e}")
        return None, None, None, []


def open_dip_sheet(client):
    """Open or create the Dip Watch worksheet. Returns (ws_dip, all_dip_rows)."""
    if not client: return None, []
    try:
        sh = client.open_by_key(SPREADSHEET_ID)
        try:
            ws_dip = sh.worksheet(DIP_SHEET_NAME)
        except Exception:
            ws_dip = sh.add_worksheet(title=DIP_SHEET_NAME, rows=1000, cols=30)
        _ensure_header(ws_dip, DIP_SHEET_HEADERS)
        return ws_dip, ws_dip.get_all_values()
    except Exception as e:
        print(f"{Fore.YELLOW}Dip sheet open failed: {e}")
        return None, []


def was_recently_dip_alerted(all_dip_rows, address):
    """True if this address has a dip alert row within BUY_COOLDOWN_HOURS."""
    cutoff   = datetime.now(CT) - timedelta(hours=BUY_COOLDOWN_HOURS)
    ts_col   = _col_dip("Alert Timestamp")
    addr_col = _col_dip("Address")
    for row in all_dip_rows[1:]:
        if len(row) <= addr_col or row[addr_col] != address: continue
        try:
            ts = CT.localize(datetime.strptime(row[ts_col], "%Y-%m-%d %H:%M CT"))
            if ts >= cutoff: return True
        except: pass
    return False


def was_recently_alerted(all_rows, address):
    """True if this address has an alert row logged within BUY_COOLDOWN_HOURS."""
    cutoff   = datetime.now(CT) - timedelta(hours=BUY_COOLDOWN_HOURS)
    ts_col   = _col("Alert Timestamp")
    addr_col = _col("Address")
    for row in all_rows[1:]:
        if len(row) <= addr_col or row[addr_col] != address: continue
        try:
            ts = CT.localize(datetime.strptime(row[ts_col], "%Y-%m-%d %H:%M CT"))
            if ts >= cutoff: return True
        except: pass
    return False


def get_alert_price(all_rows, address):
    """Return the alert price for the most recent alert row for this address."""
    ts_col    = _col("Alert Timestamp")
    addr_col  = _col("Address")
    price_col = _col("Alert Price (USD)")
    best_ts, best_price = None, None
    for row in all_rows[1:]:
        if len(row) <= addr_col or row[addr_col] != address: continue
        try:
            ts = CT.localize(datetime.strptime(row[ts_col], "%Y-%m-%d %H:%M CT"))
            if best_ts is None or ts > best_ts:
                best_ts    = ts
                best_price = float(row[price_col]) if len(row) > price_col and row[price_col] else None
        except: pass
    return best_price


def log_alert_row(ws, all_rows, pair, score, green, rugcheck_data=None):
    """Log a fresh alert row with all baseline data including rugcheck fields.
    Returns True if the row was actually written, False otherwise."""
    try:
        name    = pair.get("baseToken", {}).get("name", "Unknown")
        symbol  = pair.get("baseToken", {}).get("symbol", "???")
        address = pair.get("baseToken", {}).get("address", "")
        price   = pair.get("priceUsd", "")
        mcap    = round(float(pair.get("marketCap", 0) or 0))
        liq     = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        created = pair.get("pairCreatedAt", 0)
        age_h   = round((time.time() * 1000 - created) / 3_600_000, 1) if created else ""
        buys    = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
        sells   = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
        buy_pct = round(buys / (buys + sells) * 100) if (buys + sells) > 0 else ""
        p1h     = pair.get("priceChange", {}).get("h1", "")
        p24h    = pair.get("priceChange", {}).get("h24", "")
        dex_url = pair.get("url", f"https://dexscreener.com/solana/{address}")
        has_liq = "Yes" if liq > 0 else "No"
        ts      = datetime.now(CT).strftime("%Y-%m-%d %H:%M CT")

        rug_score, top10_pct, lp_locked = rugcheck_data if rugcheck_data else ("", "", "")

        row = [
            ts, name, symbol, address, score,
            price, age_h, has_liq, mcap, round(liq),
            round(float(pair.get("volume", {}).get("h24", 0) or 0)),
            buy_pct, p1h, p24h,
            " | ".join(green), dex_url,
            rug_score, top10_pct, lp_locked,
            "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "",
            "",
            "", "",
            EXIT_STRATEGY, TRAILING_STOP_PCT,
        ]
        ok = _append_row_with_retry(ws, row)
        if ok:
            all_rows.append(row)
            print(f"{Fore.GREEN}  -> Alert logged: {name} ({symbol})")
        return ok
    except Exception as e:
        print(f"{Fore.YELLOW}  Sheet write failed: {e}")
        return False


def fill_followups(ws, all_rows):
    """
    For every alert row, check if any follow-up price columns are due and empty.
    Also flags rugged / stop-loss conditions when the +15m or +30m column is first filled.
    Batch-fetches prices and updates all cells in one API call.
    """
    if not all_rows: return

    now      = datetime.now(CT)
    ts_col   = _col("Alert Timestamp")
    addr_col = _col("Address")
    ap_col   = _col("Alert Price (USD)")
    pk_col   = _col("Peak % gain")
    rug_col  = _col("Rugged?")
    sl_col   = _col("Auto Stop-Loss?")

    needs = {}
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= addr_col: continue
        try:
            alert_ts = CT.localize(datetime.strptime(row[ts_col], "%Y-%m-%d %H:%M CT"))
        except: continue

        elapsed_min = (now - alert_ts).total_seconds() / 60
        if elapsed_min > FOLLOWUP_MAX_HOURS * 60: continue

        address = row[addr_col]
        for price_col, pct_col, threshold_min in FOLLOWUP_WINDOWS:
            pc_idx = _col(price_col)
            val    = row[pc_idx] if len(row) > pc_idx else ""
            if elapsed_min >= threshold_min and not val:
                needs.setdefault(address, []).append((i, row, price_col, pct_col))

    if not needs:
        return

    print(f"\n{Fore.CYAN}Filling follow-up prices for {len(needs)} coin(s)...")
    price_map = _batch_fetch_prices(set(needs.keys()))

    updates = []
    for address, checks in needs.items():
        pair          = price_map.get(address)
        current_price = pair.get("priceUsd", "") if pair else ""

        for row_idx, row, price_col, pct_col in checks:
            try: alert_price = float(row[ap_col]) if len(row) > ap_col and row[ap_col] else None
            except: alert_price = None

            pch     = ""
            pct_val = None
            if alert_price and current_price:
                try:
                    pct_val = round((float(current_price) - alert_price) / alert_price * 100, 1)
                    pch     = f"{pct_val:+.1f}%"
                except: pass

            price_letter  = _col_letter(_col(price_col))
            pct_letter    = _col_letter(_col(pct_col))
            display_price = current_price if current_price else "N/A"

            updates.append({"range": f"{price_letter}{row_idx}", "values": [[display_price]]})
            updates.append({"range": f"{pct_letter}{row_idx}",   "values": [[pch]]})

            name = row[_col("Name")] if len(row) > _col("Name") else address[:8]
            print(f"  {Fore.CYAN}{name} {price_col}: ${display_price} {pch}")

            # Update peak gain if this is a new high
            if pct_val is not None:
                try:
                    cur_peak_str = row[pk_col] if len(row) > pk_col else ""
                    cur_peak = float(cur_peak_str.replace("%","").replace("+","")) if cur_peak_str else None
                    if cur_peak is None or pct_val > cur_peak:
                        pk_letter = _col_letter(pk_col)
                        updates.append({"range": f"{pk_letter}{row_idx}", "values": [[f"{pct_val:+.1f}%"]]})
                except: pass

            # Rug / stop-loss detection — runs on every window, flagged only once
            if pct_val is not None:
                rug_letter  = _col_letter(rug_col)
                sl_letter   = _col_letter(sl_col)
                cur_rug_val = row[rug_col] if len(row) > rug_col else ""
                cur_sl_val  = row[sl_col]  if len(row) > sl_col  else ""
                if pct_val <= RUG_THRESHOLD_PCT and not cur_rug_val:
                    updates.append({"range": f"{rug_letter}{row_idx}", "values": [[f"Yes ({pch})"]]})
                    print(f"  {Fore.RED}*** RUG DETECTED: {name} dropped {pch} in {price_col}")
                    dex_url = row[_col("Chart URL")] if len(row) > _col("Chart URL") else ""
                    _pushover(f"RUG: {name}", f"Dropped {pch} at {price_col}", dex_url, "View Chart")
                if pct_val <= STOPLOSS_THRESHOLD and not cur_sl_val:
                    updates.append({"range": f"{sl_letter}{row_idx}", "values": [[f"Yes ({pch})"]]})
                    print(f"  {Fore.YELLOW}  Stop-loss triggered: {name} {pch} at {price_col}")

    if updates:
        try:
            ws.batch_update(updates)
            print(f"  {Fore.GREEN}Updated {len(updates)} cells.")
        except Exception as e:
            print(f"{Fore.YELLOW}  Follow-up batch update failed: {e}")


def log_sell_signal(ws_sell, pair, alert_price, signals,
                    peak_pct=None, current_pct=None, drawdown=None, hours_held=None):
    if not ws_sell: return
    try:
        name    = pair.get("baseToken", {}).get("name", "Unknown")
        symbol  = pair.get("baseToken", {}).get("symbol", "???")
        address = pair.get("baseToken", {}).get("address", "")
        price   = pair.get("priceUsd", "N/A")
        ts      = datetime.now(CT).strftime("%Y-%m-%d %H:%M CT")
        pch     = f"{current_pct:+.1f}%" if current_pct is not None else ""
        peak_s  = f"{peak_pct:+.1f}%"   if peak_pct   is not None else ""
        draw_s  = f"{drawdown:.1f}pp"    if drawdown   is not None else ""
        held_s  = f"{hours_held:.1f}h"   if hours_held is not None else ""
        ws_sell.append_row(
            [ts, name, symbol, address, price, pch, peak_s, draw_s, held_s, " | ".join(signals)],
            value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"{Fore.YELLOW}  Sell log write failed: {e}")

# ─── RUGCHECK ────────────────────────────────────────────────────────────────

def get_rugcheck_data(address):
    """
    Fetch rugcheck.xyz risk data for a Solana token.
    Returns (risk_score_str, top10_holders_pct_str, lp_locked_str).
    All strings - empty string on failure.
    """
    try:
        url = f"https://api.rugcheck.xyz/v1/tokens/{address}/report/summary"
        r   = requests.get(url, timeout=8)
        if r.status_code == 404:
            return "Not indexed", "", ""
        r.raise_for_status()
        data = r.json()

        risk_raw = data.get("score", "")
        if isinstance(risk_raw, (int, float)):
            if risk_raw < 5_000:    risk_label = f"{risk_raw} (Low)"
            elif risk_raw < 20_000: risk_label = f"{risk_raw} (Med)"
            else:                   risk_label = f"{risk_raw} (HIGH)"
        else:
            risk_label = str(risk_raw) if risk_raw != "" else ""

        top_holders = data.get("topHolders", [])
        if top_holders:
            raw_sum = sum(h.get("pct", 0) for h in top_holders[:10])
            if raw_sum > 1.5:
                top10_str = f"{raw_sum:.1f}%"
            else:
                top10_str = f"{raw_sum * 100:.1f}%"
        else:
            top10_str = ""

        lp_locked_str = ""
        markets = data.get("markets", [])
        if markets:
            lp = markets[0].get("lp", {})
            if isinstance(lp, dict):
                locked     = lp.get("lpLocked", False)
                locked_pct = lp.get("lpLockedPct", 0) or 0
                lp_locked_str = f"Yes ({locked_pct:.0f}%)" if locked else "No"
        if not lp_locked_str:
            risks = data.get("risks", [])
            lp_risk = next((r for r in risks if "liquidity" in r.get("name","").lower()), None)
            if lp_risk:
                lp_locked_str = "No" if lp_risk.get("level","") in ("warn","danger") else "Yes"

        return risk_label, top10_str, lp_locked_str

    except Exception as e:
        print(f"  {Fore.YELLOW}Rugcheck failed ({address[:8]}...): {e}")
        return "", "", ""

# ─── PUSHOVER ────────────────────────────────────────────────────────────────

def _pushover(title, message, url, url_title, priority=0):
    if DISABLE_NOTIFICATIONS_FOR_TESTING:
        print(f"{Fore.YELLOW}  [notifications disabled for testing] would have sent: {title}")
        return
    if not PUSHOVER_APP_TOKEN or not PUSHOVER_USER_KEY: return
    try:
        requests.post("https://api.pushover.net/1/messages.json", data={
            "token": PUSHOVER_APP_TOKEN, "user": PUSHOVER_USER_KEY,
            "title": title, "message": message,
            "url": url, "url_title": url_title, "priority": priority,
        }, timeout=10)
    except Exception as e:
        print(f"{Fore.YELLOW}  Pushover failed: {e}")


def send_buy_alert(pair, score, green, rugcheck_data=None, logged=True):
    name    = pair.get("baseToken", {}).get("name", "Unknown")
    symbol  = pair.get("baseToken", {}).get("symbol", "???")
    address = pair.get("baseToken", {}).get("address", "")
    price   = pair.get("priceUsd", "N/A")
    dex_url = pair.get("url", f"https://dexscreener.com/solana/{address}")
    prefix  = "" if logged else "[NOT LOGGED] "
    title   = f"{prefix}BUY: {name} ({symbol}) {score}/8"

    rug_lines = ""
    if rugcheck_data:
        rug_score, top10, lp = rugcheck_data
        rug_lines = (f"\nRugcheck: {rug_score}" if rug_score else "") + \
                    (f"\nTop 10: {top10}" if top10 else "") + \
                    (f"\nLP Locked: {lp}" if lp else "")

    msg = (f"Price: ${price}\nContract: {address}\n"
           "(copy -> paste into Phantom)\n"
           + rug_lines + "\n\n"
           + "\n".join(f"* {g}" for g in green))
    _pushover(title, msg, dex_url, "View Chart")
    print(f"{Fore.GREEN}  -> Buy alert sent for {name}")

# ─── PORTFOLIO MONITOR ───────────────────────────────────────────────────────

def monitor_portfolio(ws, ws_sell, all_rows):
    """Check coins from the last 24h for sell signals using trailing stop from peak."""
    if not all_rows: return
    print(f"\n{Fore.CYAN}Checking portfolio for sell signals...")
    now      = datetime.now(CT)
    cutoff   = now - timedelta(hours=SELL_MONITOR_HOURS)
    ts_col   = _col("Alert Timestamp")
    addr_col = _col("Address")
    ap_col   = _col("Alert Price (USD)")
    pk_col   = _col("Peak % gain")
    seen, to_check = set(), []

    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= addr_col: continue
        addr = row[addr_col]
        if addr in seen: continue
        seen.add(addr)
        try:
            ts = CT.localize(datetime.strptime(row[ts_col], "%Y-%m-%d %H:%M CT"))
            if ts >= cutoff:
                peak_str = row[pk_col] if len(row) > pk_col else ""
                try:    peak_pct = float(peak_str.replace("%","").replace("+","")) if peak_str else 0.0
                except: peak_pct = 0.0
                try:    alert_price = float(row[ap_col]) if len(row) > ap_col and row[ap_col] else None
                except: alert_price = None
                to_check.append({
                    "row_idx":     i,
                    "address":     addr,
                    "name":        row[_col("Name")],
                    "symbol":      row[_col("Symbol")],
                    "alert_price": alert_price,
                    "alert_ts":    ts,
                    "peak_pct":    peak_pct,
                })
        except: pass

    if not to_check:
        print("  No coins in monitoring window."); return
    print(f"  Monitoring {len(to_check)} coin(s)...")

    for coin in to_check:
        pair = get_pair_by_address(coin["address"])
        if not pair: continue

        cur_str     = pair.get("priceUsd", "")
        alert_price = coin["alert_price"]
        peak_pct    = coin["peak_pct"]
        hours_held  = (now - coin["alert_ts"]).total_seconds() / 3600

        current_pct = None
        if alert_price and cur_str:
            try: current_pct = round((float(cur_str) - alert_price) / alert_price * 100, 1)
            except: pass

        # Update peak if new high — write back to sheet
        peak_updates = []
        if current_pct is not None and current_pct > peak_pct:
            peak_pct = current_pct
            peak_updates.append({"range": f"{_col_letter(pk_col)}{coin['row_idx']}",
                                  "values": [[f"{peak_pct:+.1f}%"]]})
            try: ws.batch_update(peak_updates)
            except Exception as e: print(f"  {Fore.YELLOW}Peak update failed: {e}")

        # Trailing stop check
        signals  = []
        drawdown = None
        if current_pct is not None:
            drawdown = peak_pct - current_pct
            if drawdown >= TRAILING_STOP_PCT:
                signals.append(
                    f"Trailing stop: {drawdown:.1f}pp off peak "
                    f"(peak {peak_pct:+.1f}% → now {current_pct:+.1f}%)")

        # Score / buy-pressure checks (kept as secondary signals)
        score, _, _ = score_token(pair)
        buys    = pair.get("txns", {}).get("h24", {}).get("buys",  0) or 0
        sells   = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
        buy_pct = round(buys / (buys + sells) * 100) if (buys + sells) > 0 else 50
        if score   <= SELL_TRIGGERS["max_score"]:   signals.append(f"Score collapsed to {score}/8")
        if buy_pct <= SELL_TRIGGERS["max_buy_pct"]: signals.append(f"Sell pressure: {buy_pct}% buys")

        if signals:
            log_sell_signal(ws_sell, pair, alert_price, signals,
                            peak_pct=peak_pct, current_pct=current_pct,
                            drawdown=drawdown, hours_held=hours_held)
            print(f"  {Fore.RED}{coin['name']}: sell triggered — "
                  f"peak {peak_pct:+.1f}% | now {current_pct:+.1f}% | "
                  f"drawdown {drawdown:.1f}pp | held {hours_held:.1f}h")
        else:
            gain_str = f"{current_pct:+.1f}% (peak {peak_pct:+.1f}%)" if current_pct is not None else ""
            print(f"  {Fore.GREEN}{coin['name']}: ${cur_str} {gain_str} — holding")

# ─── DEXSCREENER ─────────────────────────────────────────────────────────────
#
# IMPORTANT: the original implementation called
#   GET https://api.dexscreener.com/latest/dex/pairs/solana
# to fetch a firehose of "all new Solana pairs". That endpoint does not exist
# in DexScreener's current public API (confirmed: it now 404s) - it never
# returned any pairs, which is the primary reason nothing was ever logged to
# the sheet, independent of the alert-filter threshold issue.
#
# DexScreener's current API has no direct "list every new pair on a chain"
# endpoint. The closest free substitute is the token-profiles feed (tokens
# whose creators filled out a DexScreener profile - name/description/links),
# which we then resolve to full pair data via the current /tokens/v1 endpoint.
# Caveat: this will miss bare-bones launches that never filled out a profile,
# so it's narrower coverage than a true "all new pairs" firehose - there isn't
# a documented free endpoint that does that as of this writing.

DEXSCREENER_BASE = "https://api.dexscreener.com"


def _extract_pairs(payload):
    """DexScreener's /tokens/v1 endpoints return a bare list of pair objects;
    older endpoints wrapped them in {"pairs": [...]}. Handle both shapes."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("pairs", []) or []
    return []


def get_new_solana_pairs():
    """Discover candidate new Solana tokens via the token-profiles feed, then
    fetch full pair data (price/volume/liquidity/txns) for each candidate."""
    try:
        r = requests.get(f"{DEXSCREENER_BASE}/token-profiles/latest/v1", timeout=15)
        r.raise_for_status()
        profiles = r.json() or []
    except Exception as e:
        print(f"{Fore.RED}Fetch error (token profiles): {e}")
        return []

    addrs = [p.get("tokenAddress") for p in profiles
             if p.get("chainId") == "solana" and p.get("tokenAddress")]
    addrs = list(dict.fromkeys(addrs))  # de-dupe, preserve order
    if not addrs:
        return []

    pairs = _batch_fetch_pairs(addrs)
    # Sort by creation time descending so newest are first
    pairs.sort(key=lambda p: p.get("pairCreatedAt", 0) or 0, reverse=True)
    return pairs


def _batch_fetch_pairs(addr_list):
    """Fetch pair data for a list of addresses (30 per request)."""
    pairs = []
    for i in range(0, len(addr_list), 30):
        batch = addr_list[i:i+30]
        try:
            r = requests.get(f"{DEXSCREENER_BASE}/tokens/v1/solana/{','.join(batch)}", timeout=15)
            r.raise_for_status()
            pairs.extend(_extract_pairs(r.json()))
        except Exception as e:
            print(f"{Fore.RED}Batch pairs fetch error: {e}")
    return pairs


def _batch_fetch_prices(addresses):
    """Fetch best Solana pair for each address. Returns dict of address -> pair."""
    result = {}
    pairs  = _batch_fetch_pairs(list(addresses))
    for pair in pairs:
        if pair.get("chainId") != "solana": continue
        addr = pair.get("baseToken", {}).get("address", "")
        liq  = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        if addr not in result or liq > float(result[addr].get("liquidity", {}).get("usd", 0) or 0):
            result[addr] = pair
    return result


def get_pair_by_address(token_address):
    try:
        r = requests.get(f"{DEXSCREENER_BASE}/tokens/v1/solana/{token_address}", timeout=10)
        r.raise_for_status()
        pairs = [p for p in _extract_pairs(r.json()) if p.get("chainId") == "solana"]
        return max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0)) if pairs else None
    except Exception as e:
        print(f"{Fore.RED}DexScreener error: {e}"); return None

# ─── SCORING (8 criteria) ────────────────────────────────────────────────────

def is_junk_token(pair):
    name   = pair.get("baseToken", {}).get("name", "")
    symbol = pair.get("baseToken", {}).get("symbol", "")
    if not name or not symbol: return True
    if re.fullmatch(r"[\d\s]+", name): return True
    if re.fullmatch(r"[\d\s]+", symbol): return True
    return False


def passes_alert_filter(pair):
    """
    Hard gate applied before logging / sending Pushover.
    Returns (passed: bool, reasons: list[str]).
    All conditions must pass.
    """
    buys  = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
    sells = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
    buy_pct = buys / (buys + sells) * 100 if (buys + sells) > 0 else 0
    vol     = float(pair.get("volume", {}).get("h24", 0) or 0)
    liq     = float(pair.get("liquidity", {}).get("usd", 0) or 0)

    failed = []
    if buy_pct <= ALERT_FILTERS["min_buy_pct"]:
        failed.append(f"Buy pressure {buy_pct:.0f}% <= {ALERT_FILTERS['min_buy_pct']}%")
    if vol < ALERT_FILTERS["min_volume_usd"]:
        failed.append(f"Volume ${vol:,.0f} < ${ALERT_FILTERS['min_volume_usd']:,.0f}")
    if ALERT_FILTERS["require_liquidity"] and liq <= 0:
        failed.append("No liquidity")

    return (len(failed) == 0), failed


def score_token(pair):
    score, green, red = 0, [], []
    liq   = float(pair.get("liquidity", {}).get("usd", 0) or 0)
    vol   = float(pair.get("volume", {}).get("h24", 0) or 0)
    mcap  = float(pair.get("marketCap", 0) or 0)
    txns  = (pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0) + \
            (pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0)
    p1h   = float(pair.get("priceChange", {}).get("h1", 0) or 0)
    p24h  = float(pair.get("priceChange", {}).get("h24", 0) or 0)
    age   = (time.time()*1000 - (pair.get("pairCreatedAt") or 0)) / 3_600_000 \
            if pair.get("pairCreatedAt") else None
    buys  = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
    sells = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0

    # 1. Liquidity
    if THRESHOLDS["min_liquidity_usd"] <= liq <= THRESHOLDS["max_liquidity_usd"]:
        score+=1; green.append(f"Liquidity ${liq:,.0f}")
    elif liq < THRESHOLDS["min_liquidity_usd"]: red.append(f"Liquidity too low: ${liq:,.0f}")
    else: red.append("Very high liquidity")

    # 2. Market cap
    if mcap >= THRESHOLDS["min_market_cap_usd"]:
        score+=1; green.append(f"Market cap ${mcap:,.0f}")
    else: red.append(f"Market cap too low: ${mcap:,.0f}")

    # 3. Volume
    if vol >= THRESHOLDS["min_volume_24h_usd"]:
        score+=1; green.append(f"24h volume ${vol:,.0f}")
    else: red.append(f"Low 24h volume: ${vol:,.0f}")

    # 4. Transaction activity
    if txns >= THRESHOLDS["min_txns_24h"]:
        score+=1; green.append(f"Active: {txns} txns in 24h")
    else: red.append(f"Low activity: {txns} txns")

    # 5. Price not in freefall
    if p1h >= THRESHOLDS["min_price_change_1h"]:
        score+=1; green.append(f"1h: {p1h:+.1f}%")
    else: red.append(f"Dumping: {p1h:+.1f}% in 1h")

    # 6. Not a straight vertical pump
    if p24h < 1000:
        score+=1; green.append(f"24h: {p24h:+.1f}%")
    else: red.append(f"Vertical pump: {p24h:+.1f}%")

    # 7. Age - skip coins under 6 minutes old
    if age is not None:
        if age < 0.1:
            red.append(f"Too new ({age*60:.0f} min) - skipping")
            return 0, [], red
        if 1 <= age <= THRESHOLDS["max_age_hours"]:
            score+=1; green.append(f"Age: {age:.1f}h")
        elif age < 1: red.append(f"Too new ({age*60:.0f} min)")
        else: red.append(f"Older token ({age:.0f}h)")

    # 8. Buy/sell ratio
    if buys > 0 and sells > 0:
        r = buys/(buys+sells)
        if r >= 0.55: score+=1; green.append(f"Buy pressure: {r*100:.0f}%")
        else: red.append(f"Sell pressure: {r*100:.0f}% buys")

    return score, green, red

# ─── DIP SCANNER ─────────────────────────────────────────────────────────────

def score_dip_recovery(pair):
    """Score a token for a dip-recovery buy. Returns (score, green, red, dip_pct)."""
    score, green, red = 0, [], []
    p1h   = float(pair.get("priceChange", {}).get("h1",  0) or 0)
    p24h  = float(pair.get("priceChange", {}).get("h24", 0) or 0)
    liq   = float(pair.get("liquidity", {}).get("usd", 0) or 0)
    vol24 = float(pair.get("volume", {}).get("h24", 0) or 0)
    volh1 = float(pair.get("volume", {}).get("h1",  0) or 0)
    buys  = pair.get("txns", {}).get("h24", {}).get("buys",  0) or 0
    sells = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
    buy_pct = buys / (buys + sells) * 100 if (buys + sells) > 0 else 0
    created = pair.get("pairCreatedAt", 0) or 0
    age_h   = (time.time() * 1000 - created) / 3_600_000 if created else None
    t = DIP_RECOVERY_THRESHOLDS

    # 1. Drop is in the "deep but alive" range
    if t["min_drop_1h"] <= p1h <= t["max_drop_1h"]:
        score += 1; green.append(f"Sharp dip: {p1h:+.1f}% in 1h")
    elif p1h > t["max_drop_1h"]:
        red.append(f"Drop too shallow: {p1h:+.1f}%")
    else:
        red.append(f"Drop too severe: {p1h:+.1f}%")

    # 2. Liquidity still intact
    if liq >= t["min_liquidity_usd"]:
        score += 1; green.append(f"Liquidity intact: ${liq:,.0f}")
    else:
        red.append(f"Liquidity too low: ${liq:,.0f}")

    # 3. 1h volume shows recovery activity
    if volh1 >= t["min_volume_h1_usd"]:
        score += 1; green.append(f"1h volume: ${volh1:,.0f}")
    else:
        red.append(f"Low 1h volume: ${volh1:,.0f}")

    # 4. 24h volume - established token
    if vol24 >= t["min_volume_24h_usd"]:
        score += 1; green.append(f"24h volume: ${vol24:,.0f}")
    else:
        red.append(f"Low 24h volume: ${vol24:,.0f}")

    # 5. Buy pressure returning
    if buy_pct >= t["min_buy_pct"]:
        score += 1; green.append(f"Buy pressure: {buy_pct:.0f}%")
    else:
        red.append(f"Sell pressure: {buy_pct:.0f}% buys")

    # 6. Token has history, not in danger zone, not too old
    if age_h is not None:
        if t["avoid_age_min"] <= age_h <= t["avoid_age_max"]:
            return 0, [], [f"Age danger zone: {age_h:.1f}h (skip {t['avoid_age_min']}-{t['avoid_age_max']}h)"], p1h
        if t["min_age_hours"] <= age_h <= t["max_age_hours"]:
            score += 1; green.append(f"Age: {age_h:.1f}h")
        elif age_h < t["min_age_hours"]:
            red.append(f"Too new: {age_h:.1f}h")
        else:
            red.append(f"Too old: {age_h:.0f}h")

    # 7. 24h still positive (dip on an uptrend, not a dying coin)
    if p24h > 0:
        score += 1; green.append(f"24h still positive: {p24h:+.1f}%")
    else:
        red.append(f"24h also negative: {p24h:+.1f}%")

    return score, green, red, p1h


def score_pump_pullback(pair):
    """Score a token for a pump-pullback re-entry. Returns (score, green, red, dip_pct)."""
    score, green, red = 0, [], []
    p1h   = float(pair.get("priceChange", {}).get("h1",  0) or 0)
    p6h   = float(pair.get("priceChange", {}).get("h6",  0) or 0)
    p24h  = float(pair.get("priceChange", {}).get("h24", 0) or 0)
    liq   = float(pair.get("liquidity", {}).get("usd", 0) or 0)
    vol24 = float(pair.get("volume", {}).get("h24", 0) or 0)
    buys  = pair.get("txns", {}).get("h24", {}).get("buys",  0) or 0
    sells = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
    buy_pct = buys / (buys + sells) * 100 if (buys + sells) > 0 else 0
    t = PUMP_PULLBACK_THRESHOLDS

    # 1. Significant 24h pump
    if p24h >= t["min_pump_24h"]:
        score += 1; green.append(f"Pumped {p24h:+.1f}% in 24h")
    else:
        red.append(f"24h pump too weak: {p24h:+.1f}%")

    # 2. Pulling back in healthy range
    if t["min_drop_1h"] <= p1h <= t["max_drop_1h"]:
        score += 1; green.append(f"Healthy pullback: {p1h:+.1f}% in 1h")
    elif p1h > t["max_drop_1h"]:
        red.append(f"Pullback too shallow: {p1h:+.1f}%")
    else:
        red.append(f"Crashing too hard: {p1h:+.1f}%")

    # 3. Strong liquidity
    if liq >= t["min_liquidity_usd"]:
        score += 1; green.append(f"Strong liquidity: ${liq:,.0f}")
    else:
        red.append(f"Weak liquidity: ${liq:,.0f}")

    # 4. Volume confirms real interest
    if vol24 >= t["min_volume_24h_usd"]:
        score += 1; green.append(f"Volume: ${vol24:,.0f}")
    else:
        red.append(f"Weak volume: ${vol24:,.0f}")

    # 5. Buy pressure not fully flipped to sells
    if buy_pct >= t["min_buy_pct"]:
        score += 1; green.append(f"Buy pressure: {buy_pct:.0f}%")
    else:
        red.append(f"Heavy selling: {buy_pct:.0f}% buys")

    # 6. 6h trend still intact
    if p6h > 0:
        score += 1; green.append(f"6h trend intact: {p6h:+.1f}%")
    else:
        red.append(f"6h also negative: {p6h:+.1f}%")

    return score, green, red, p1h


def log_dip_row(ws_dip, all_dip_rows, pair, strategy, score, green, dip_pct, rugcheck_data=None):
    """Log a dip alert row to the Dip Watch sheet.
    Returns True if the row was actually written, False otherwise."""
    try:
        name    = pair.get("baseToken", {}).get("name", "Unknown")
        symbol  = pair.get("baseToken", {}).get("symbol", "???")
        address = pair.get("baseToken", {}).get("address", "")
        price   = pair.get("priceUsd", "")
        liq     = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        vol24   = float(pair.get("volume", {}).get("h24", 0) or 0)
        p24h    = pair.get("priceChange", {}).get("h24", "")
        created = pair.get("pairCreatedAt", 0)
        age_h   = round((time.time() * 1000 - created) / 3_600_000, 1) if created else ""
        buys    = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
        sells   = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
        buy_pct = round(buys / (buys + sells) * 100) if (buys + sells) > 0 else ""
        dex_url = pair.get("url", f"https://dexscreener.com/solana/{address}")
        ts      = datetime.now(CT).strftime("%Y-%m-%d %H:%M CT")
        rug_score, _, lp_locked = rugcheck_data if rugcheck_data else ("", "", "")

        row = [
            ts, strategy, name, symbol, address, score,
            price, f"{dip_pct:+.1f}%", p24h, age_h,
            round(liq), round(vol24), buy_pct,
            rug_score, lp_locked, dex_url,
            "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "",
            "", "", "",
            EXIT_STRATEGY, TRAILING_STOP_PCT,
        ]
        ok = _append_row_with_retry(ws_dip, row)
        if ok:
            all_dip_rows.append(row)
            print(f"{Fore.GREEN}  -> Dip alert logged: {name} ({symbol}) [{strategy}]")
        return ok
    except Exception as e:
        print(f"{Fore.YELLOW}  Dip sheet write failed: {e}")
        return False


def send_dip_alert(pair, strategy, score, green, dip_pct, rugcheck_data=None, logged=True):
    name    = pair.get("baseToken", {}).get("name", "Unknown")
    symbol  = pair.get("baseToken", {}).get("symbol", "???")
    address = pair.get("baseToken", {}).get("address", "")
    price   = pair.get("priceUsd", "N/A")
    p24h    = pair.get("priceChange", {}).get("h24", "?")
    dex_url = pair.get("url", f"https://dexscreener.com/solana/{address}")
    max_score = 7 if strategy == "Dip Recovery" else 6
    prefix    = "" if logged else "[NOT LOGGED] "
    title     = f"{prefix}{strategy.upper()}: {name} ({symbol}) {score}/{max_score}"

    rug_lines = ""
    if rugcheck_data:
        rug_score, _, lp = rugcheck_data
        rug_lines = (f"\nRugcheck: {rug_score}" if rug_score else "") + \
                    (f"\nLP Locked: {lp}" if lp else "")

    msg = (f"Price: ${price}  |  {dip_pct:+.1f}% (1h)  |  {p24h}% (24h)\n"
           f"Contract: {address}\n"
           "(copy -> paste into Phantom)\n"
           + rug_lines + "\n\n"
           + "\n".join(f"* {g}" for g in green))
    _pushover(title, msg, dex_url, "View Chart")
    print(f"{Fore.CYAN}  -> {strategy} alert sent for {name}")


def fill_dip_followups(ws_dip, all_dip_rows):
    """Fill follow-up price columns for dip alert rows."""
    if not all_dip_rows: return

    now      = datetime.now(CT)
    ts_col   = _col_dip("Alert Timestamp")
    addr_col = _col_dip("Address")
    ap_col   = _col_dip("Alert Price (USD)")
    pk_col   = _col_dip("Peak % gain")
    rug_col  = _col_dip("Rugged?")
    sl_col   = _col_dip("Auto Stop-Loss?")

    needs = {}
    for i, row in enumerate(all_dip_rows[1:], start=2):
        if len(row) <= addr_col: continue
        try:
            alert_ts = CT.localize(datetime.strptime(row[ts_col], "%Y-%m-%d %H:%M CT"))
        except: continue

        elapsed_min = (now - alert_ts).total_seconds() / 60
        if elapsed_min > FOLLOWUP_MAX_HOURS * 60: continue

        address = row[addr_col]
        for price_col, pct_col, threshold_min in FOLLOWUP_WINDOWS:
            pc_idx = _col_dip(price_col)
            val    = row[pc_idx] if len(row) > pc_idx else ""
            if elapsed_min >= threshold_min and not val:
                needs.setdefault(address, []).append((i, row, price_col, pct_col))

    if not needs:
        return

    print(f"\n{Fore.CYAN}Filling dip follow-up prices for {len(needs)} coin(s)...")
    price_map = _batch_fetch_prices(set(needs.keys()))

    updates = []
    for address, checks in needs.items():
        pair          = price_map.get(address)
        current_price = pair.get("priceUsd", "") if pair else ""

        for row_idx, row, price_col, pct_col in checks:
            try: alert_price = float(row[ap_col]) if len(row) > ap_col and row[ap_col] else None
            except: alert_price = None

            pch     = ""
            pct_val = None
            if alert_price and current_price:
                try:
                    pct_val = round((float(current_price) - alert_price) / alert_price * 100, 1)
                    pch     = f"{pct_val:+.1f}%"
                except: pass

            price_letter  = _col_letter(_col_dip(price_col))
            pct_letter    = _col_letter(_col_dip(pct_col))
            display_price = current_price if current_price else "N/A"

            updates.append({"range": f"{price_letter}{row_idx}", "values": [[display_price]]})
            updates.append({"range": f"{pct_letter}{row_idx}",   "values": [[pch]]})

            name = row[_col_dip("Name")] if len(row) > _col_dip("Name") else address[:8]
            print(f"  {Fore.CYAN}{name} {price_col}: ${display_price} {pch}")

            if pct_val is not None:
                try:
                    cur_peak_str = row[pk_col] if len(row) > pk_col else ""
                    cur_peak = float(cur_peak_str.replace("%","").replace("+","")) if cur_peak_str else None
                    if cur_peak is None or pct_val > cur_peak:
                        pk_letter = _col_letter(pk_col)
                        updates.append({"range": f"{pk_letter}{row_idx}", "values": [[f"{pct_val:+.1f}%"]]})
                except: pass

            # Rug / stop-loss detection — runs on every window, flagged only once
            if pct_val is not None:
                rug_letter  = _col_letter(rug_col)
                sl_letter   = _col_letter(sl_col)
                cur_rug_val = row[rug_col] if len(row) > rug_col else ""
                cur_sl_val  = row[sl_col]  if len(row) > sl_col  else ""
                if pct_val <= RUG_THRESHOLD_PCT and not cur_rug_val:
                    updates.append({"range": f"{rug_letter}{row_idx}", "values": [[f"Yes ({pch})"]]})
                    print(f"  {Fore.RED}*** RUG (dip play): {name} dropped {pch} at {price_col}")
                    dex_url = row[_col_dip("Chart URL")] if len(row) > _col_dip("Chart URL") else ""
                    _pushover(f"RUG: {name}", f"Dropped {pch} at {price_col}", dex_url, "View Chart")
                if pct_val <= STOPLOSS_THRESHOLD and not cur_sl_val:
                    updates.append({"range": f"{sl_letter}{row_idx}", "values": [[f"Yes ({pch})"]]})
                    print(f"  {Fore.YELLOW}  Stop-loss triggered: {name} {pch} at {price_col}")

    if updates:
        try:
            ws_dip.batch_update(updates)
            print(f"  {Fore.GREEN}Updated {len(updates)} dip follow-up cells.")
        except Exception as e:
            print(f"{Fore.YELLOW}  Dip follow-up batch update failed: {e}")


def scan_dip_opportunities(ws_dip, all_dip_rows, pairs=None):
    """Scan for dip recovery and pump pullback opportunities."""
    print(f"\n{Fore.CYAN}Scanning for dip opportunities...")
    if pairs is None:
        pairs = get_new_solana_pairs()
    if not pairs:
        print(f"{Fore.RED}No pairs returned for dip scan."); return

    seen_addrs = set()
    candidates = []

    for pair in pairs:
        if pair.get("chainId") != "solana": continue
        addr = pair.get("baseToken", {}).get("address", "")
        if addr in seen_addrs or is_junk_token(pair): continue
        seen_addrs.add(addr)

        # Skip coins under 6 minutes old
        created = pair.get("pairCreatedAt", 0) or 0
        age_h   = (time.time() * 1000 - created) / 3_600_000 if created else None
        if age_h is not None and age_h < 0.1:
            continue

        p1h  = float(pair.get("priceChange", {}).get("h1",  0) or 0)
        p24h = float(pair.get("priceChange", {}).get("h24", 0) or 0)
        dr_t = DIP_RECOVERY_THRESHOLDS
        pp_t = PUMP_PULLBACK_THRESHOLDS

        if dr_t["min_drop_1h"] <= p1h <= dr_t["max_drop_1h"]:
            s, g, r, dip_pct = score_dip_recovery(pair)
            if s >= DIP_RECOVERY_MIN_SCORE:
                candidates.append(("Dip Recovery", s, pair, g, r, dip_pct))

        if p24h >= pp_t["min_pump_24h"] and pp_t["min_drop_1h"] <= p1h <= pp_t["max_drop_1h"]:
            s, g, r, dip_pct = score_pump_pullback(pair)
            if s >= PUMP_PULLBACK_MIN_SCORE:
                candidates.append(("Pump Pullback", s, pair, g, r, dip_pct))

    candidates.sort(key=lambda x: x[1], reverse=True)
    print(f"Found {len(candidates)} dip candidate(s).\n")

    alerted = 0
    for strategy, score, pair, green, red, dip_pct in candidates[:10]:
        addr      = pair.get("baseToken", {}).get("address", "")
        name      = pair.get("baseToken", {}).get("name", "Unknown")
        symbol    = pair.get("baseToken", {}).get("symbol", "???")
        liq       = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        max_score = 7 if strategy == "Dip Recovery" else 6
        color     = Fore.CYAN if strategy == "Dip Recovery" else Fore.MAGENTA

        print(f"\n{'─'*60}")
        print(f"{color}{Style.BRIGHT}[{strategy}] {name} ({symbol})  Score: {score}/{max_score}")
        print(f"{Style.RESET_ALL}Dip: {dip_pct:+.1f}% (1h)")
        if green: [print(f"  {Fore.GREEN}* {g}") for g in green]
        if red:   [print(f"  {Fore.RED}* {r}") for r in red]
        print(f"{'─'*60}")

        if liq <= 0:
            print(f"  {Fore.RED}No liquidity - skipping"); continue
        if was_recently_dip_alerted(all_dip_rows, addr):
            print(f"  {Fore.YELLOW}Dip cooldown active -- skipping"); continue

        rug = get_rugcheck_data(addr)
        if rug and "HIGH" in str(rug[0]):
            print(f"  {Fore.RED}Skipping HIGH rugcheck risk: {name}"); continue
        logged = log_dip_row(ws_dip, all_dip_rows, pair, strategy, score, green, dip_pct, rugcheck_data=rug) if ws_dip else False
        if not logged:
            print(f"  {Fore.RED}WARNING: dip alert will be sent but was NOT logged to the sheet")
        send_dip_alert(pair, strategy, score, green, dip_pct, rugcheck_data=rug, logged=logged)
        alerted += 1

    if alerted == 0:
        print(f"\n{Fore.YELLOW}No dip alerts sent this run.")
    else:
        print(f"\n{Fore.CYAN}{alerted} dip alert(s) sent this run.")

# ─── DISPLAY ─────────────────────────────────────────────────────────────────

def display_result(pair, score, green, red):
    name    = pair.get("baseToken", {}).get("name", "Unknown")
    symbol  = pair.get("baseToken", {}).get("symbol", "???")
    address = pair.get("baseToken", {}).get("address", "")
    price   = pair.get("priceUsd", "N/A")
    dex_url = pair.get("url", f"https://dexscreener.com/solana/{address}")
    color   = Fore.GREEN if score >= 6 else Fore.YELLOW if score >= THRESHOLDS["min_score"] else Fore.RED
    print(f"\n{'─'*60}")
    print(f"{color}{Style.BRIGHT}{name} ({symbol})  Score: {score}/8")
    print(f"{Style.RESET_ALL}Address : {address}\nPrice   : ${price}\nChart   : {dex_url}")
    if green: print(f"\n{Fore.GREEN}Green:"); [print(f"  {Fore.GREEN}* {g}") for g in green]
    if red:   print(f"\n{Fore.RED}Red:");   [print(f"  {Fore.RED}* {r}") for r in red]
    if score >= THRESHOLDS["min_score"]: print(f"\n{Fore.CYAN}{Style.BRIGHT}ALERT: {score}/8")
    print(f"{'─'*60}")

# ─── MODES ───────────────────────────────────────────────────────────────────

def analyze_token(address):
    print(f"\n{Fore.CYAN}Fetching data for {address}...")
    pair = get_pair_by_address(address)
    if not pair: print(f"{Fore.RED}No Solana pair found."); return
    score, green, red = score_token(pair)
    display_result(pair, score, green, red)
    if score >= PUSHOVER_ALERT_SCORE:
        passed, reasons = passes_alert_filter(pair)
        if not passed:
            print(f"  {Fore.YELLOW}Hard filter blocked: {'; '.join(reasons)}")
            return
        _, ws, ws_sell, all_rows = open_sheet()
        if ws and not was_recently_alerted(all_rows, address):
            rug = get_rugcheck_data(address)
            logged = log_alert_row(ws, all_rows, pair, score, green, rugcheck_data=rug)
            if not logged:
                print(f"  {Fore.RED}WARNING: alert will be sent but was NOT logged to the sheet")
            send_buy_alert(pair, score, green, rugcheck_data=rug, logged=logged)
        elif ws:
            print(f"  {Fore.YELLOW}Buy cooldown active -- skipping")


def watch_token(address):
    print(f"\n{Fore.CYAN}Watching {address} every {WATCH_INTERVAL_SECONDS}s...")
    while True:
        analyze_token(address); time.sleep(WATCH_INTERVAL_SECONDS)


def scan_new_tokens():
    client, ws, ws_sell, all_rows = open_sheet()
    ws_dip, all_dip_rows = open_dip_sheet(client)

    # 1. Fill follow-up prices for existing alert rows
    if ws:
        fill_followups(ws, all_rows)

    # 2. Fill follow-up prices for dip alert rows
    if ws_dip:
        fill_dip_followups(ws_dip, all_dip_rows)

    # 3. Check portfolio for sell signals
    if ws:
        monitor_portfolio(ws, ws_sell, all_rows)

    # 4. Fetch pairs once - shared by both scanners
    print(f"\n{Fore.CYAN}Scanning for new Solana meme coins...")
    pairs = get_new_solana_pairs()
    if not pairs:
        print(f"{Fore.RED}No pairs returned.")
        print(f"{Fore.CYAN}Run summary: 0 candidates fetched, 0 scored, 0 logged.")
        return

    seen_addrs, results = set(), []
    for pair in pairs:
        if pair.get("chainId") != "solana": continue
        addr = pair.get("baseToken", {}).get("address", "")
        if addr in seen_addrs or is_junk_token(pair): continue
        seen_addrs.add(addr)

        # Skip coins under 6 minutes old
        created = pair.get("pairCreatedAt", 0) or 0
        age_h   = (time.time() * 1000 - created) / 3_600_000 if created else None
        if age_h is not None and age_h < 0.1:
            continue

        score, green, red = score_token(pair)
        if score == 0: continue
        results.append((score, pair, green, red))

    results.sort(key=lambda x: x[0], reverse=True)
    qualifying = [(s,p,g,r) for s,p,g,r in results if s >= THRESHOLDS["min_score"]]
    print(f"\nFound {len(qualifying)} tokens scoring {THRESHOLDS['min_score']}+/8 "
          f"out of {len(results)} scanned.\n")

    alerted, blocked_by_filter, on_cooldown, not_logged = 0, 0, 0, 0
    for score, pair, green, red in qualifying[:10]:
        display_result(pair, score, green, red)
        if score >= PUSHOVER_ALERT_SCORE:
            addr = pair.get("baseToken", {}).get("address", "")
            passed, reasons = passes_alert_filter(pair)
            if not passed:
                print(f"  {Fore.YELLOW}Hard filter blocked: {'; '.join(reasons)}")
                blocked_by_filter += 1
                continue
            if was_recently_alerted(all_rows, addr):
                print(f"  {Fore.YELLOW}Buy cooldown active -- skipping Pushover")
                on_cooldown += 1
                continue
            rug = get_rugcheck_data(addr)
            logged = log_alert_row(ws, all_rows, pair, score, green, rugcheck_data=rug) if ws else False
            if not logged:
                print(f"  {Fore.RED}WARNING: alert will be sent but was NOT logged to the sheet")
                not_logged += 1
            send_buy_alert(pair, score, green, rugcheck_data=rug, logged=logged)
            alerted += 1

    if alerted == 0:
        print(f"\n{Fore.YELLOW}No coins passed hard filters this run.")
    else:
        print(f"\n{Fore.GREEN}{alerted} alert(s) sent this run.")

    # One-line summary so a silent run is never ambiguous in the logs
    print(f"{Fore.CYAN}Run summary: {len(pairs)} candidates fetched, {len(results)} scored, "
          f"{len(qualifying)} qualifying, {alerted} alert(s) sent, "
          f"{alerted - not_logged} logged to sheet, {not_logged} sent but NOT logged, "
          f"{blocked_by_filter} blocked by alert filter, {on_cooldown} on cooldown.")

    # 5. Dip scanner - reuses already-fetched pairs
    scan_dip_opportunities(ws_dip, all_dip_rows, pairs=pairs)

# ─── FAST FOLLOW-UP SCAN ─────────────────────────────────────────────────────

def fast_followup_scan():
    """
    Lightweight job for the 3-minute cron: fills early price checkpoints
    (+3m/+6m/+9m/+12m and any other due windows) for alerts from the last 30
    minutes. No discovery, no scoring, no portfolio monitoring.
    """
    print(f"\n{Fore.CYAN}[Fast follow-up] Checking recent alerts for early price data...")
    client, ws, _, all_rows = open_sheet()
    ws_dip, all_dip_rows = open_dip_sheet(client)

    # Restrict to rows from the last 30 minutes to keep API calls minimal
    now     = datetime.now(CT)
    cutoff  = now - timedelta(minutes=30)
    ts_col  = _col("Alert Timestamp")

    recent_rows = [all_rows[0]] if all_rows else []
    for row in all_rows[1:]:
        try:
            ts = CT.localize(datetime.strptime(row[ts_col], "%Y-%m-%d %H:%M CT"))
            if ts >= cutoff:
                recent_rows.append(row)
        except:
            pass

    dip_ts_col = _col_dip("Alert Timestamp")
    recent_dip_rows = [all_dip_rows[0]] if all_dip_rows else []
    for row in all_dip_rows[1:]:
        try:
            ts = CT.localize(datetime.strptime(row[dip_ts_col], "%Y-%m-%d %H:%M CT"))
            if ts >= cutoff:
                recent_dip_rows.append(row)
        except:
            pass

    if ws:
        fill_followups(ws, recent_rows)
    if ws_dip:
        fill_dip_followups(ws_dip, recent_dip_rows)

    main_count = max(0, len(recent_rows) - 1)
    dip_count  = max(0, len(recent_dip_rows) - 1)
    print(f"{Fore.CYAN}[Fast follow-up] Done — checked {main_count} main alert(s), "
          f"{dip_count} dip alert(s).")


# ─── SHEET RESET ─────────────────────────────────────────────────────────────

def reset_sheet():
    """
    Clear all data rows from Sheet1, Sell Log, and Dip Watch (headers kept).
    Run this once after schema changes so all future rows use the new column layout.
    """
    client = _get_gspread_client()
    if not client:
        print(f"{Fore.RED}No Google credentials — cannot reset sheet."); return
    sh = client.open_by_key(SPREADSHEET_ID)

    for tab_name, headers in [
        (SHEET_NAME,     SHEET_HEADERS),
        ("Sell Log",     SELL_LOG_HEADERS),
        (DIP_SHEET_NAME, DIP_SHEET_HEADERS),
    ]:
        try:
            try:
                ws = sh.worksheet(tab_name)
            except Exception:
                print(f"  {Fore.YELLOW}{tab_name} tab not found — skipping"); continue

            all_vals = ws.get_all_values()
            if len(all_vals) <= 1:
                print(f"  {Fore.YELLOW}{tab_name}: already empty (no data rows)")
                _ensure_header(ws, headers)
                continue

            # Delete every row after the header (bottom-up to keep indices valid)
            last_row = len(all_vals)
            ws.delete_rows(2, last_row)
            _ensure_header(ws, headers)
            print(f"  {Fore.GREEN}{tab_name}: cleared {last_row - 1} data row(s), header updated.")
        except Exception as e:
            print(f"  {Fore.RED}Failed to reset {tab_name}: {e}")

    print(f"\n{Fore.GREEN}Sheet reset complete. All tabs start fresh with updated headers.")


def backfill_strategy():
    """
    One-time backfill: set Exit Strategy = 'v1-dumptrigger' for every row
    in Sheet1 and Dip Watch where the Exit Strategy column is currently blank.
    Leaves Stop % untouched and skips rows that already have an Exit Strategy value.
    """
    client = _get_gspread_client()
    if not client:
        print(f"{Fore.RED}No Google credentials — cannot backfill."); return
    sh = client.open_by_key(SPREADSHEET_ID)

    tabs = [
        (SHEET_NAME,     SHEET_HEADERS),
        (DIP_SHEET_NAME, DIP_SHEET_HEADERS),
    ]

    for tab_name, headers in tabs:
        try:
            ws = sh.worksheet(tab_name)
        except Exception:
            print(f"  {Fore.YELLOW}{tab_name}: tab not found — skipping"); continue

        try:
            exit_col_idx = headers.index("Exit Strategy")
        except ValueError:
            print(f"  {Fore.YELLOW}{tab_name}: 'Exit Strategy' column not in headers — skipping"); continue

        all_vals = ws.get_all_values()
        if len(all_vals) <= 1:
            print(f"  {Fore.YELLOW}{tab_name}: no data rows — skipping"); continue

        # Sheet rows are 1-indexed; row 1 is the header
        import gspread as _gs
        updates = []
        for row_idx, row in enumerate(all_vals[1:], start=2):
            cell_val = row[exit_col_idx] if exit_col_idx < len(row) else ""
            if cell_val.strip() == "":
                updates.append(_gs.Cell(row_idx, exit_col_idx + 1, "v1-dumptrigger"))

        if updates:
            ws.update_cells(updates, value_input_option="RAW")
            print(f"  {Fore.GREEN}{tab_name}: backfilled {len(updates)} row(s) → 'v1-dumptrigger'")
        else:
            print(f"  {Fore.CYAN}{tab_name}: no blank Exit Strategy rows found — nothing to update")

    print(f"\n{Fore.GREEN}Backfill complete.")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Solana Meme Coin Scanner")
    parser.add_argument("address", nargs="?", help="Token address to analyze")
    parser.add_argument("--watch", metavar="ADDRESS", help="Watch a token continuously")
    parser.add_argument("--fast-followup", action="store_true",
                        help="Fill early price checkpoints for recent alerts (run every 3 min)")
    parser.add_argument("--reset-sheet", action="store_true",
                        help="Clear all data rows from Sheet1/Sell Log/Dip Watch and update headers")
    parser.add_argument("--backfill-strategy", action="store_true",
                        help="One-time: set Exit Strategy=v1-dumptrigger for all blank rows in Sheet1 and Dip Watch")
    args = parser.parse_args()
    if args.reset_sheet:           reset_sheet()
    elif args.backfill_strategy:   backfill_strategy()
    elif args.fast_followup:       fast_followup_scan()
    elif args.watch:           watch_token(args.watch)
    elif args.address:         analyze_token(args.address)
    else:                      scan_new_tokens()

if __name__ == "__main__":
    main()
