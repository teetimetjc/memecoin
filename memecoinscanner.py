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

SELL_TRIGGERS = {
    "max_score":           4,
    "min_price_change_1h": -20,
    "max_buy_pct":         40,
}

THRESHOLDS = {
    "min_liquidity_usd":   10_000,
    "max_liquidity_usd":   5_000_000,
    "min_volume_24h_usd":  5_000,
    "min_market_cap_usd":  10_000,
    "max_age_hours":       72,
    "min_txns_24h":        50,
    "min_price_change_1h": -10,
    "min_score":           5,
}

WATCH_INTERVAL_SECONDS = 60

# Follow-up windows: (price_col, pct_col, min_minutes_elapsed)
# Generous lower bound so cron timing variations don't cause missed windows
FOLLOWUP_WINDOWS = [
    ("Price +30m", "% +30m",  25),
    ("Price +1h",  "% +1h",   55),
    ("Price +2h",  "% +2h",  115),
    ("Price +4h",  "% +4h",  235),
]
FOLLOWUP_MAX_HOURS = 5  # stop trying to fill after this long

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
    "Price +30m",              # Q
    "% +30m",                  # R
    "Price +1h",               # S
    "% +1h",                   # T
    "Price +2h",               # U
    "% +2h",                   # V
    "Price +4h",               # W
    "% +4h",                   # X
    "Peak % gain",             # Y
]

SELL_LOG_HEADERS = [
    "Timestamp", "Name", "Symbol", "Address",
    "Price at sell signal", "Change from alert %", "Triggers",
]

def _col(h):
    return SHEET_HEADERS.index(h)

def _col_letter(idx):
    """Convert 0-indexed column number to A1 column letter (supports AA, AB...)."""
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
    """Update header row in place — never inserts a new row."""
    if ws.row_values(1) != expected:
        ws.update("A1", [expected])
        print(f"{Fore.YELLOW}  Header row updated.")


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


def was_recently_alerted(all_rows, address):
    """True if this address has an alert row logged within BUY_COOLDOWN_HOURS."""
    cutoff = datetime.now(CT) - timedelta(hours=BUY_COOLDOWN_HOURS)
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


def log_alert_row(ws, all_rows, pair, score, green):
    """Log a fresh alert row with all baseline data."""
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

        row = [
            ts, name, symbol, address, score,
            price, age_h, has_liq, mcap, round(liq),
            round(float(pair.get("volume", {}).get("h24", 0) or 0)),
            buy_pct, p1h, p24h,
            " | ".join(green), dex_url,
            "", "", "", "", "", "", "", "", "",  # follow-up cols + peak (empty at alert time)
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")
        all_rows.append(row)
        print(f"{Fore.GREEN}  -> Alert logged: {name} ({symbol})")
    except Exception as e:
        print(f"{Fore.YELLOW}  Sheet write failed: {e}")


def fill_followups(ws, all_rows):
    """
    For every alert row, check if any follow-up price columns are due and empty.
    Batch-fetches prices and updates cells in one API call.
    """
    if not all_rows: return

    now      = datetime.now(CT)
    ts_col   = _col("Alert Timestamp")
    addr_col = _col("Address")
    ap_col   = _col("Alert Price (USD)")
    pk_col   = _col("Peak % gain")

    # Build a map: address -> list of (sheet_row_idx, col_name, pct_col_name)
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
            pc_idx  = _col(price_col)
            val     = row[pc_idx] if len(row) > pc_idx else ""
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

            pch = ""
            if alert_price and current_price:
                try:
                    pct = round((float(current_price) - alert_price) / alert_price * 100, 1)
                    pch = f"{pct:+.1f}%"
                except: pass

            price_letter = _col_letter(_col(price_col))
            pct_letter   = _col_letter(_col(pct_col))
            display_price = current_price if current_price else "N/A"

            updates.append({"range": f"{price_letter}{row_idx}", "values": [[display_price]]})
            updates.append({"range": f"{pct_letter}{row_idx}",   "values": [[pch]]})

            name = row[_col("Name")] if len(row) > _col("Name") else address[:8]
            print(f"  {Fore.CYAN}{name} {price_col}: ${display_price} {pch}")

            # Update peak gain if this is a new high
            if pch:
                try:
                    pct_val = float(pch.replace("%","").replace("+",""))
                    cur_peak_str = row[pk_col] if len(row) > pk_col else ""
                    cur_peak = float(cur_peak_str.replace("%","").replace("+","")) if cur_peak_str else None
                    if cur_peak is None or pct_val > cur_peak:
                        pk_letter = _col_letter(pk_col)
                        updates.append({"range": f"{pk_letter}{row_idx}", "values": [[f"{pct_val:+.1f}%"]]})
                except: pass

    if updates:
        try:
            ws.batch_update(updates)
            print(f"  {Fore.GREEN}Updated {len(updates)} cells.")
        except Exception as e:
            print(f"{Fore.YELLOW}  Follow-up batch update failed: {e}")


def log_sell_signal(ws_sell, pair, alert_price, signals):
    if not ws_sell: return
    try:
        name    = pair.get("baseToken", {}).get("name", "Unknown")
        symbol  = pair.get("baseToken", {}).get("symbol", "???")
        address = pair.get("baseToken", {}).get("address", "")
        price   = pair.get("priceUsd", "N/A")
        ts      = datetime.now(CT).strftime("%Y-%m-%d %H:%M CT")
        pch     = ""
        if alert_price and price:
            try: pch = f"{round((float(price)-alert_price)/alert_price*100,1):+.1f}%"
            except: pass
        ws_sell.append_row([ts, name, symbol, address, price, pch, " | ".join(signals)],
                           value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"{Fore.YELLOW}  Sell log write failed: {e}")

# ─── PUSHOVER ────────────────────────────────────────────────────────────────

def _pushover(title, message, url, url_title, priority=0):
    if not PUSHOVER_APP_TOKEN or not PUSHOVER_USER_KEY: return
    try:
        requests.post("https://api.pushover.net/1/messages.json", data={
            "token": PUSHOVER_APP_TOKEN, "user": PUSHOVER_USER_KEY,
            "title": title, "message": message,
            "url": url, "url_title": url_title, "priority": priority,
        }, timeout=10)
    except Exception as e:
        print(f"{Fore.YELLOW}  Pushover failed: {e}")


def send_buy_alert(pair, score, green):
    name    = pair.get("baseToken", {}).get("name", "Unknown")
    symbol  = pair.get("baseToken", {}).get("symbol", "???")
    address = pair.get("baseToken", {}).get("address", "")
    price   = pair.get("priceUsd", "N/A")
    dex_url = pair.get("url", f"https://dexscreener.com/solana/{address}")
    title   = f"BUY: {name} ({symbol}) {score}/8"
    msg     = (f"Price: ${price}\nContract: {address}\n"
               "(copy -> paste into Phantom)\n\n"
               + "\n".join(f"* {g}" for g in green))
    _pushover(title, msg, dex_url, "View Chart")
    print(f"{Fore.GREEN}  -> Buy alert sent for {name}")

# ─── PORTFOLIO MONITOR ───────────────────────────────────────────────────────

def monitor_portfolio(ws, ws_sell, all_rows):
    """Check coins from the last 24h for sell signals (logs to sheet, no Pushover)."""
    if not all_rows: return
    print(f"\n{Fore.CYAN}Checking portfolio for sell signals...")
    cutoff   = datetime.now(CT) - timedelta(hours=SELL_MONITOR_HOURS)
    ts_col   = _col("Alert Timestamp")
    addr_col = _col("Address")
    seen, to_check = set(), []

    for row in all_rows[1:]:
        if len(row) <= addr_col: continue
        addr = row[addr_col]
        if addr in seen: continue
        seen.add(addr)
        try:
            ts = CT.localize(datetime.strptime(row[ts_col], "%Y-%m-%d %H:%M CT"))
            if ts >= cutoff:
                to_check.append({
                    "address":     addr,
                    "name":        row[_col("Name")],
                    "symbol":      row[_col("Symbol")],
                    "alert_price": get_alert_price(all_rows, addr),
                })
        except: pass

    if not to_check:
        print("  No coins in monitoring window."); return
    print(f"  Monitoring {len(to_check)} coin(s)...")

    for coin in to_check:
        pair = get_pair_by_address(coin["address"])
        if not pair: continue
        score, green, red = score_token(pair)
        p1h     = float(pair.get("priceChange", {}).get("h1", 0) or 0)
        buys    = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
        sells   = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
        buy_pct = round(buys / (buys + sells) * 100) if (buys + sells) > 0 else 50
        signals = []
        if score   <= SELL_TRIGGERS["max_score"]:           signals.append(f"Score collapsed to {score}/8")
        if p1h     <= SELL_TRIGGERS["min_price_change_1h"]: signals.append(f"Dumping {p1h:+.1f}% in 1h")
        if buy_pct <= SELL_TRIGGERS["max_buy_pct"]:         signals.append(f"Sell pressure: only {buy_pct}% buys")

        cur = pair.get("priceUsd", "N/A")
        if signals:
            log_sell_signal(ws_sell, pair, coin["alert_price"], signals)
            print(f"  {Fore.RED}{coin['name']}: sell signals logged (Pushover off)")
        else:
            ap = coin["alert_price"]
            pch = ""
            if ap and cur not in ("N/A", ""):
                try: pch = f"  ({round((float(cur)-ap)/ap*100,1):+.1f}% from alert)"
                except: pass
            print(f"  {Fore.GREEN}{coin['name']}: ${cur} score {score}/8 holding{pch}")

# ─── DEXSCREENER ─────────────────────────────────────────────────────────────

DEXSCREENER_BASE = "https://api.dexscreener.com/latest/dex"


def get_new_solana_pairs():
    """Fetch from token profiles + top boosts for ~50 coins per run."""
    addrs = set()
    for url in ["https://api.dexscreener.com/token-profiles/latest/v1",
                "https://api.dexscreener.com/token-boosts/top/v1"]:
        try:
            r = requests.get(url, timeout=10); r.raise_for_status()
            for p in r.json():
                if p.get("chainId") == "solana": addrs.add(p["tokenAddress"])
        except Exception as e:
            print(f"{Fore.RED}Fetch error ({url}): {e}")
    if not addrs: return []
    return _batch_fetch_pairs(list(addrs))


def _batch_fetch_pairs(addr_list):
    """Fetch pair data for a list of addresses (30 per request)."""
    pairs = []
    for i in range(0, len(addr_list), 30):
        batch = addr_list[i:i+30]
        try:
            r = requests.get(f"{DEXSCREENER_BASE}/tokens/{','.join(batch)}", timeout=15)
            r.raise_for_status()
            pairs.extend(r.json().get("pairs", []) or [])
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
        r = requests.get(f"{DEXSCREENER_BASE}/tokens/{token_address}", timeout=10)
        r.raise_for_status()
        pairs = [p for p in r.json().get("pairs", []) if p.get("chainId") == "solana"]
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

    # 7. Age
    if age is not None:
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
        _, ws, ws_sell, all_rows = open_sheet()
        if ws and not was_recently_alerted(all_rows, address):
            log_alert_row(ws, all_rows, pair, score, green)
            send_buy_alert(pair, score, green)
        elif ws:
            print(f"  {Fore.YELLOW}Buy cooldown active -- skipping")


def watch_token(address):
    print(f"\n{Fore.CYAN}Watching {address} every {WATCH_INTERVAL_SECONDS}s...")
    while True:
        analyze_token(address); time.sleep(WATCH_INTERVAL_SECONDS)


def scan_new_tokens():
    _, ws, ws_sell, all_rows = open_sheet()

    # 1. Fill follow-up prices for existing alert rows
    if ws:
        fill_followups(ws, all_rows)

    # 2. Check portfolio for sell signals
    if ws:
        monitor_portfolio(ws, ws_sell, all_rows)

    # 3. Scan for new coins
    print(f"\n{Fore.CYAN}Scanning for new Solana meme coins...")
    pairs = get_new_solana_pairs()
    if not pairs: print(f"{Fore.RED}No pairs returned."); return

    seen_addrs, results = set(), []
    for pair in pairs:
        if pair.get("chainId") != "solana": continue
        addr = pair.get("baseToken", {}).get("address", "")
        if addr in seen_addrs or is_junk_token(pair): continue
        seen_addrs.add(addr)
        score, green, red = score_token(pair)
        results.append((score, pair, green, red))

    results.sort(key=lambda x: x[0], reverse=True)
    qualifying = [(s,p,g,r) for s,p,g,r in results if s >= THRESHOLDS["min_score"]]
    print(f"\nFound {len(qualifying)} tokens scoring {THRESHOLDS['min_score']}+/8 "
          f"out of {len(results)} scanned.\n")

    for score, pair, green, red in qualifying[:10]:
        display_result(pair, score, green, red)
        if score >= PUSHOVER_ALERT_SCORE:
            addr = pair.get("baseToken", {}).get("address", "")
            if not was_recently_alerted(all_rows, addr):
                if ws: log_alert_row(ws, all_rows, pair, score, green)
                send_buy_alert(pair, score, green)
            else:
                print(f"  {Fore.YELLOW}Buy cooldown active -- skipping Pushover")

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Solana Meme Coin Scanner")
    parser.add_argument("address", nargs="?", help="Token address to analyze")
    parser.add_argument("--watch", metavar="ADDRESS", help="Watch a token continuously")
    args = parser.parse_args()
    if args.watch:     watch_token(args.watch)
    elif args.address: analyze_token(args.address)
    else:              scan_new_tokens()

if __name__ == "__main__":
    main()
