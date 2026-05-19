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

PUSHOVER_ALERT_SCORE  = 7    # min score to log + send buy alert
BUY_COOLDOWN_HOURS    = 4    # skip buy Pushover if same coin alerted within window
SELL_COOLDOWN_HOURS   = 4    # skip sell Pushover if same coin sell-alerted within window
SELL_MONITOR_HOURS    = 24   # how far back to pull coins for sell monitoring

# Sell alert triggers — any one fires the notification
SELL_TRIGGERS = {
    "max_score":           4,
    "min_price_change_1h": -20,
    "max_buy_pct":         40,
}

# Scoring thresholds (8 criteria — Solscan removed, holder/whale dropped)
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

# ─── SHEET SCHEMA ────────────────────────────────────────────────────────────

SHEET_HEADERS = [
    "Timestamp", "Name", "Symbol", "Address", "Score",
    "Price (USD)", "Market Cap (USD)", "Liquidity (USD)", "Volume 24h (USD)",
    "Age (h)", "Buy %", "1h %", "24h %",
    "Change since first seen %", "Peak % gain",
    "Green Flags", "Chart URL",
]

SELL_LOG_HEADERS = [
    "Timestamp", "Name", "Symbol", "Address",
    "Price at sell signal", "Change since first seen %", "Triggers",
]

def _col(h): return SHEET_HEADERS.index(h)

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
    """Update header row in place if it doesn't match — never inserts a new row."""
    current = ws.row_values(1)
    if current != expected:
        ws.update("A1", [expected])
        print(f"{Fore.YELLOW}  Header row updated.")


def open_sheet():
    """Open main worksheet once per run. Returns (client, ws, ws_sell, all_rows)."""
    client = _get_gspread_client()
    if not client:
        return None, None, None, []
    try:
        sh     = client.open_by_key(SPREADSHEET_ID)
        ws     = sh.worksheet(SHEET_NAME)
        _ensure_header(ws, SHEET_HEADERS)

        # Sell log tab — create if missing
        try:
            ws_sell = sh.worksheet("Sell Log")
        except Exception:
            ws_sell = sh.add_worksheet(title="Sell Log", rows=1000, cols=10)
        _ensure_header(ws_sell, SELL_LOG_HEADERS)

        return client, ws, ws_sell, ws.get_all_values()
    except Exception as e:
        print(f"{Fore.YELLOW}Sheet open failed: {e}")
        return None, None, None, []


def get_first_seen_price(all_rows, address):
    for row in all_rows[1:]:
        if len(row) > _col("Address") and row[_col("Address")] == address:
            try: return float(row[_col("Price (USD)")])
            except: pass
    return None


def get_peak_gain(all_rows, address):
    """Return the highest 'Change since first seen %' ever logged for this address."""
    best = None
    for row in all_rows[1:]:
        if len(row) > _col("Address") and row[_col("Address")] == address:
            try:
                val = float(row[_col("Change since first seen %")].replace("%","").replace("+",""))
                if best is None or val > best:
                    best = val
            except: pass
    return best


def was_recently_notified(all_rows, address, hours, ts_col_name="Timestamp"):
    """True if this address has a row logged within the last `hours` hours."""
    cutoff = datetime.now(CT) - timedelta(hours=hours)
    for row in all_rows[1:]:
        col = SHEET_HEADERS.index(ts_col_name) if ts_col_name in SHEET_HEADERS else 0
        if len(row) <= _col("Address") or row[_col("Address")] != address: continue
        try:
            ts = CT.localize(datetime.strptime(row[col], "%Y-%m-%d %H:%M CT"))
            if ts >= cutoff: return True
        except: pass
    return False


def append_to_sheet(ws, all_rows, pair, score, green):
    """Append a buy-signal row. Updates peak gain if new high."""
    try:
        name    = pair.get("baseToken", {}).get("name", "Unknown")
        symbol  = pair.get("baseToken", {}).get("symbol", "???")
        address = pair.get("baseToken", {}).get("address", "")
        price   = pair.get("priceUsd", "")
        mcap    = round(float(pair.get("marketCap", 0) or 0))
        liq     = round(float(pair.get("liquidity", {}).get("usd", 0) or 0))
        vol     = round(float(pair.get("volume", {}).get("h24", 0) or 0))
        created = pair.get("pairCreatedAt", 0)
        age_h   = round((time.time() * 1000 - created) / 3_600_000, 1) if created else ""
        buys    = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
        sells   = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
        buy_pct = round(buys / (buys + sells) * 100) if (buys + sells) > 0 else ""
        p1h     = pair.get("priceChange", {}).get("h1", "")
        p24h    = pair.get("priceChange", {}).get("h24", "")
        dex_url = pair.get("url", f"https://dexscreener.com/solana/{address}")
        ts      = datetime.now(CT).strftime("%Y-%m-%d %H:%M CT")

        fp  = get_first_seen_price(all_rows, address)
        pch = ""
        if fp and price:
            try: pch = f"{round((float(price)-fp)/fp*100,1):+.1f}%"
            except: pass

        prev_peak = get_peak_gain(all_rows, address)
        try:    cur_pct = float(pch.replace("%","").replace("+","")) if pch else None
        except: cur_pct = None
        if cur_pct is not None:
            peak = max(filter(None, [prev_peak, cur_pct]))
        else:
            peak = prev_peak
        peak_str = f"{peak:+.1f}%" if peak is not None else ""

        row = [ts, name, symbol, address, score,
               price, mcap, liq, vol,
               age_h, buy_pct, p1h, p24h,
               pch, peak_str,
               " | ".join(green), dex_url]
        ws.append_row(row, value_input_option="USER_ENTERED")
        all_rows.append(row)
        print(f"{Fore.GREEN}  -> Logged: {name} ({symbol})  change={pch}  peak={peak_str}")
    except Exception as e:
        print(f"{Fore.YELLOW}  Sheet write failed: {e}")


def log_sell_signal(ws_sell, pair, first_price, signals):
    """Write a row to the Sell Log tab."""
    if not ws_sell: return
    try:
        name    = pair.get("baseToken", {}).get("name", "Unknown")
        symbol  = pair.get("baseToken", {}).get("symbol", "???")
        address = pair.get("baseToken", {}).get("address", "")
        price   = pair.get("priceUsd", "N/A")
        ts      = datetime.now(CT).strftime("%Y-%m-%d %H:%M CT")
        pch     = ""
        if first_price and price:
            try: pch = f"{round((float(price)-first_price)/first_price*100,1):+.1f}%"
            except: pass
        row = [ts, name, symbol, address, price, pch, " | ".join(signals)]
        ws_sell.append_row(row, value_input_option="USER_ENTERED")
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


def send_buy_alert(pair, score, green, pch=""):
    name    = pair.get("baseToken", {}).get("name", "Unknown")
    symbol  = pair.get("baseToken", {}).get("symbol", "???")
    address = pair.get("baseToken", {}).get("address", "")
    price   = pair.get("priceUsd", "N/A")
    dex_url = pair.get("url", f"https://dexscreener.com/solana/{address}")
    change  = f"  Change since first seen: {pch}\n" if pch else ""
    title   = f"BUY: {name} ({symbol}) {score}/8"
    msg     = (f"Price: ${price}\n{change}"
               f"Contract: {address}\n"
               "(copy -> paste into Phantom)\n\n"
               + "\n".join(f"* {g}" for g in green))
    _pushover(title, msg, dex_url, "View Chart")
    print(f"{Fore.GREEN}  -> Buy alert sent for {name}")


def send_sell_alert(name, symbol, address, current_price, first_price, signals, dex_url):
    pch = ""
    if first_price and current_price:
        try: pch = f"  ({round((float(current_price)-first_price)/first_price*100,1):+.1f}% from first seen)\n"
        except: pass
    title = f"SELL: {name} ({symbol})"
    msg   = (f"Price: ${current_price}\n{pch}"
             f"Contract: {address}\n\n"
             + "\n".join(f"! {s}" for s in signals))
    _pushover(title, msg, dex_url, "View Chart", priority=1)
    print(f"{Fore.RED}  -> Sell alert sent for {name}")

# ─── PORTFOLIO MONITOR ───────────────────────────────────────────────────────

def monitor_portfolio(ws, ws_sell, all_rows):
    """Re-check coins from the last 24h for sell signals."""
    if not all_rows: return
    print(f"\n{Fore.CYAN}Checking portfolio for sell signals...")
    cutoff = datetime.now(CT) - timedelta(hours=SELL_MONITOR_HOURS)
    seen, to_check = set(), []
    for row in all_rows[1:]:
        if len(row) <= _col("Address"): continue
        addr = row[_col("Address")]
        if addr in seen: continue
        seen.add(addr)
        try:
            ts = CT.localize(datetime.strptime(row[_col("Timestamp")], "%Y-%m-%d %H:%M CT"))
            if ts >= cutoff:
                to_check.append({
                    "address":     addr,
                    "name":        row[_col("Name")],
                    "symbol":      row[_col("Symbol")],
                    "first_price": get_first_seen_price(all_rows, addr),
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
        dex = pair.get("url", f"https://dexscreener.com/solana/{coin['address']}")

        if signals:
            if not was_recently_notified(all_rows, coin["address"], SELL_COOLDOWN_HOURS):
                send_sell_alert(coin["name"], coin["symbol"], coin["address"],
                                cur, coin["first_price"], signals, dex)
                log_sell_signal(ws_sell, pair, coin["first_price"], signals)
            else:
                print(f"  {Fore.YELLOW}{coin['name']}: sell signals but cooldown active")
        else:
            fp  = coin["first_price"]
            pch = f"  ({round((float(cur)-fp)/fp*100,1):+.1f}% from first seen)" if fp and cur not in ("N/A","") else ""
            print(f"  {Fore.GREEN}{coin['name']}: ${cur} score {score}/8 holding{pch}")

# ─── DEXSCREENER ─────────────────────────────────────────────────────────────

DEXSCREENER_BASE = "https://api.dexscreener.com/latest/dex"

def get_new_solana_pairs():
    """Fetch from both token profiles and top boosts for broader coverage (~50+ coins)."""
    addrs = set()

    # Source 1: latest token profiles
    try:
        r = requests.get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=10)
        r.raise_for_status()
        for p in r.json():
            if p.get("chainId") == "solana": addrs.add(p["tokenAddress"])
    except Exception as e:
        print(f"{Fore.RED}Profiles fetch error: {e}")

    # Source 2: top boosted tokens
    try:
        r = requests.get("https://api.dexscreener.com/token-boosts/top/v1", timeout=10)
        r.raise_for_status()
        for p in r.json():
            if p.get("chainId") == "solana": addrs.add(p["tokenAddress"])
    except Exception as e:
        print(f"{Fore.RED}Boosts fetch error: {e}")

    if not addrs:
        return []

    # Batch fetch pair data (max 30 per request)
    addr_list = list(addrs)
    all_pairs = []
    for i in range(0, len(addr_list), 30):
        batch = addr_list[i:i+30]
        try:
            r = requests.get(f"{DEXSCREENER_BASE}/tokens/{','.join(batch)}", timeout=15)
            r.raise_for_status()
            all_pairs.extend(r.json().get("pairs", []) or [])
        except Exception as e:
            print(f"{Fore.RED}Pairs fetch error (batch {i}): {e}")

    return all_pairs


def get_pair_by_address(token_address):
    try:
        r = requests.get(f"{DEXSCREENER_BASE}/tokens/{token_address}", timeout=10)
        r.raise_for_status()
        pairs = [p for p in r.json().get("pairs", []) if p.get("chainId") == "solana"]
        return max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0)) if pairs else None
    except Exception as e:
        print(f"{Fore.RED}DexScreener error: {e}"); return None

# ─── SCORING (8 criteria — Solscan removed) ──────────────────────────────────

def is_junk_token(pair):
    """Filter out tokens with garbage names/symbols."""
    name   = pair.get("baseToken", {}).get("name", "")
    symbol = pair.get("baseToken", {}).get("symbol", "")
    # Purely numeric names, empty names, or suspiciously short symbols
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
        pch = ""
        if ws:
            fp = get_first_seen_price(all_rows, address)
            price = pair.get("priceUsd", "")
            if fp and price:
                try: pch = f"{round((float(price)-fp)/fp*100,1):+.1f}%"
                except: pass
            append_to_sheet(ws, all_rows, pair, score, green)
        if not was_recently_notified(all_rows, address, BUY_COOLDOWN_HOURS):
            send_buy_alert(pair, score, green, pch)
        else:
            print(f"  {Fore.YELLOW}Buy cooldown active -- skipping Pushover")


def watch_token(address):
    print(f"\n{Fore.CYAN}Watching {address} every {WATCH_INTERVAL_SECONDS}s...")
    while True:
        analyze_token(address); time.sleep(WATCH_INTERVAL_SECONDS)


def scan_new_tokens():
    # Open sheet once for the whole run
    _, ws, ws_sell, all_rows = open_sheet()

    # 1. Portfolio monitor
    if ws:
        monitor_portfolio(ws, ws_sell, all_rows)

    # 2. Scan for new coins
    print(f"\n{Fore.CYAN}Scanning for new Solana meme coins...")
    pairs = get_new_solana_pairs()
    if not pairs: print(f"{Fore.RED}No pairs returned."); return

    # Deduplicate by address, filter junk, score
    seen_addrs = set()
    results = []
    for pair in pairs:
        if pair.get("chainId") != "solana": continue
        addr = pair.get("baseToken", {}).get("address", "")
        if addr in seen_addrs: continue
        seen_addrs.add(addr)
        if is_junk_token(pair): continue
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
            pch  = ""
            if ws:
                fp    = get_first_seen_price(all_rows, addr)
                price = pair.get("priceUsd", "")
                if fp and price:
                    try: pch = f"{round((float(price)-fp)/fp*100,1):+.1f}%"
                    except: pass
                append_to_sheet(ws, all_rows, pair, score, green)
            if not was_recently_notified(all_rows, addr, BUY_COOLDOWN_HOURS):
                send_buy_alert(pair, score, green, pch)
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
