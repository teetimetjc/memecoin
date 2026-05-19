"""
Solana Meme Coin Scanner
Dependencies: pip install requests colorama gspread google-auth pytz
"""

import os, sys, json, time, argparse, requests, pytz
from datetime import datetime, timedelta
from colorama import Fore, Style, init

init(autoreset=True)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SOLSCAN_API_KEY    = ""
SPREADSHEET_ID     = "1PjtaTxSW1AKZ4rAUeIoHSfrV8Imh6WV_XM9uErXunQc"
SHEET_NAME         = "Sheet1"
PUSHOVER_APP_TOKEN = os.environ.get("PUSHOVER_APP_TOKEN", "")
PUSHOVER_USER_KEY  = os.environ.get("PUSHOVER_USER_KEY", "")
CT                 = pytz.timezone("America/Chicago")
PUSHOVER_ALERT_SCORE = 7
BUY_COOLDOWN_HOURS   = 4
SELL_MONITOR_HOURS   = 24
SELL_TRIGGERS = {"max_score": 4, "min_price_change_1h": -20, "max_buy_pct": 40}
THRESHOLDS = {
    "min_liquidity_usd": 10_000, "max_liquidity_usd": 5_000_000,
    "min_volume_24h_usd": 5_000, "min_holders": 100, "max_top10_pct": 30,
    "max_age_hours": 72, "min_txns_24h": 50, "min_price_change_1h": -10, "min_score": 5,
}
WATCH_INTERVAL_SECONDS = 60
SHEET_HEADERS = [
    "Timestamp", "Name", "Symbol", "Address", "Score",
    "Price (USD)", "Liquidity (USD)", "Volume 24h (USD)",
    "Age (h)", "Buy %", "Green Flags", "Chart URL", "Change since first seen %",
]
DEXSCREENER_BASE = "https://api.dexscreener.com/latest/dex"
SOLSCAN_BASE     = "https://public-api.solscan.io"

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

def open_sheet():
    """Open worksheet once per run. Returns (client, ws, all_rows)."""
    client = _get_gspread_client()
    if not client: return None, None, []
    try:
        ws = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
        if ws.row_values(1) != SHEET_HEADERS:
            ws.insert_row(SHEET_HEADERS, index=1)
        return client, ws, ws.get_all_values()
    except Exception as e:
        print(f"{Fore.YELLOW}Sheet open failed: {e}"); return None, None, []

def was_recently_notified(all_rows, address):
    """Return True if this address was logged within BUY_COOLDOWN_HOURS."""
    cutoff = datetime.now(CT) - timedelta(hours=BUY_COOLDOWN_HOURS)
    for row in all_rows[1:]:
        if len(row) <= _col("Address") or row[_col("Address")] != address: continue
        try:
            ts = CT.localize(datetime.strptime(row[_col("Timestamp")], "%Y-%m-%d %H:%M CT"))
            if ts >= cutoff: return True
        except: pass
    return False

def get_first_seen_price(all_rows, address):
    for row in all_rows[1:]:
        if len(row) > _col("Address") and row[_col("Address")] == address:
            try: return float(row[_col("Price (USD)")])
            except: pass
    return None

def append_to_sheet(ws, all_rows, pair, score, green):
    try:
        name    = pair.get("baseToken", {}).get("name", "Unknown")
        symbol  = pair.get("baseToken", {}).get("symbol", "???")
        address = pair.get("baseToken", {}).get("address", "")
        price   = pair.get("priceUsd", "")
        liq     = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        vol     = float(pair.get("volume", {}).get("h24", 0) or 0)
        created = pair.get("pairCreatedAt", 0)
        age_h   = round((time.time() * 1000 - created) / 3_600_000, 1) if created else ""
        buys    = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
        sells   = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
        buy_pct = round(buys / (buys + sells) * 100) if (buys + sells) > 0 else ""
        dex_url = pair.get("url", f"https://dexscreener.com/solana/{address}")
        ts      = datetime.now(CT).strftime("%Y-%m-%d %H:%M CT")
        fp      = get_first_seen_price(all_rows, address)
        pch     = f"{round((float(price)-fp)/fp*100,1):+.1f}%" if fp and price else ""
        row     = [ts, name, symbol, address, score, price, round(liq), round(vol),
                   age_h, buy_pct, " | ".join(green), dex_url, pch]
        ws.append_row(row, value_input_option="USER_ENTERED")
        all_rows.append(row)
        print(f"{Fore.GREEN}  -> Logged: {name} ({symbol})")
    except Exception as e:
        print(f"{Fore.YELLOW}  Sheet write failed: {e}")

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
    title   = f"BUY: {name} ({symbol}) {score}/10"
    msg     = (f"Price: ${price}\nContract: {address}\n"
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
    msg   = (f"Price: ${current_price}\n{pch}Contract: {address}\n\n"
             + "\n".join(f"! {s}" for s in signals))
    _pushover(title, msg, dex_url, "View Chart", priority=1)
    print(f"{Fore.RED}  -> Sell alert sent for {name}")

# ─── PORTFOLIO MONITOR ───────────────────────────────────────────────────────

def monitor_portfolio(ws, all_rows):
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
                to_check.append({"address": addr, "name": row[_col("Name")],
                                 "symbol": row[_col("Symbol")],
                                 "first_price": get_first_seen_price(all_rows, addr)})
        except: pass
    if not to_check:
        print("  No coins in monitoring window."); return
    print(f"  Monitoring {len(to_check)} coin(s) from last {SELL_MONITOR_HOURS}h...")
    for coin in to_check:
        pair = get_pair_by_address(coin["address"])
        if not pair: continue
        score, green, red = score_token(pair)
        p1h     = float(pair.get("priceChange", {}).get("h1", 0) or 0)
        buys    = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
        sells   = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
        buy_pct = round(buys / (buys + sells) * 100) if (buys + sells) > 0 else 50
        signals = []
        if score   <= SELL_TRIGGERS["max_score"]:           signals.append(f"Score collapsed to {score}/10")
        if p1h     <= SELL_TRIGGERS["min_price_change_1h"]: signals.append(f"Dumping {p1h:+.1f}% in 1h")
        if buy_pct <= SELL_TRIGGERS["max_buy_pct"]:         signals.append(f"Sell pressure: only {buy_pct}% buys")
        cur = pair.get("priceUsd", "N/A")
        dex = pair.get("url", f"https://dexscreener.com/solana/{coin['address']}")
        if signals:
            send_sell_alert(coin["name"], coin["symbol"], coin["address"],
                            cur, coin["first_price"], signals, dex)
        else:
            print(f"  {Fore.GREEN}{coin['name']}: ${cur} score {score}/10 holding")

# ─── DEXSCREENER ─────────────────────────────────────────────────────────────

def get_new_solana_pairs():
    try:
        r = requests.get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=10)
        r.raise_for_status(); profiles = r.json()
    except Exception as e:
        print(f"{Fore.RED}Profiles fetch error: {e}"); return []
    addrs = [p["tokenAddress"] for p in profiles if p.get("chainId") == "solana"]
    if not addrs: return []
    try:
        r = requests.get(f"{DEXSCREENER_BASE}/tokens/{','.join(addrs[:30])}", timeout=15)
        r.raise_for_status(); return r.json().get("pairs", []) or []
    except Exception as e:
        print(f"{Fore.RED}Pairs fetch error: {e}"); return []

def get_pair_by_address(token_address):
    try:
        r = requests.get(f"{DEXSCREENER_BASE}/tokens/{token_address}", timeout=10)
        r.raise_for_status()
        pairs = [p for p in r.json().get("pairs", []) if p.get("chainId") == "solana"]
        return max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0)) if pairs else None
    except Exception as e:
        print(f"{Fore.RED}DexScreener error: {e}"); return None

# ─── SOLSCAN ─────────────────────────────────────────────────────────────────

def get_holder_info(token_address):
    hdrs = {"token": SOLSCAN_API_KEY} if SOLSCAN_API_KEY else {}
    hcount, top10 = None, None
    try:
        r = requests.get(f"{SOLSCAN_BASE}/token/holders?tokenAddress={token_address}&limit=1&offset=0", headers=hdrs, timeout=10)
        if r.status_code == 200: hcount = r.json().get("total")
    except: pass
    try:
        r = requests.get(f"{SOLSCAN_BASE}/token/holders?tokenAddress={token_address}&limit=10&offset=0", headers=hdrs, timeout=10)
        if r.status_code == 200:
            holders = r.json().get("data", [])
            if holders:
                total = sum(float(h.get("amount", 0)) for h in holders)
                if total > 0: top10 = round(float(holders[0].get("amount", 0)) / total * 100, 1)
    except: pass
    return hcount, top10

# ─── SCORING ─────────────────────────────────────────────────────────────────

def score_token(pair, holder_count=None, top10_pct=None):
    score, green, red = 0, [], []
    liq   = float(pair.get("liquidity", {}).get("usd", 0) or 0)
    vol   = float(pair.get("volume", {}).get("h24", 0) or 0)
    txns  = (pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0) + (pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0)
    p1h   = float(pair.get("priceChange", {}).get("h1", 0) or 0)
    p24h  = float(pair.get("priceChange", {}).get("h24", 0) or 0)
    age   = (time.time()*1000 - (pair.get("pairCreatedAt") or 0)) / 3_600_000 if pair.get("pairCreatedAt") else None
    buys  = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
    sells = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0

    if THRESHOLDS["min_liquidity_usd"] <= liq <= THRESHOLDS["max_liquidity_usd"]:
        score+=1; green.append(f"Liquidity ${liq:,.0f}")
    elif liq < THRESHOLDS["min_liquidity_usd"]: red.append(f"Liquidity too low: ${liq:,.0f}")
    else: red.append("Very high liquidity")

    if vol >= THRESHOLDS["min_volume_24h_usd"]: score+=1; green.append(f"24h volume ${vol:,.0f}")
    else: red.append(f"Low 24h volume: ${vol:,.0f}")

    if txns >= THRESHOLDS["min_txns_24h"]: score+=1; green.append(f"Active: {txns} txns in 24h")
    else: red.append(f"Low activity: {txns} txns")

    if p1h >= THRESHOLDS["min_price_change_1h"]: score+=1; green.append(f"1h: {p1h:+.1f}%")
    else: red.append(f"Dumping: {p1h:+.1f}% in 1h")

    if p24h < 1000: score+=1; green.append(f"24h: {p24h:+.1f}%")
    else: red.append(f"Vertical pump: {p24h:+.1f}%")

    if age is not None:
        if 1 <= age <= THRESHOLDS["max_age_hours"]: score+=1; green.append(f"Age: {age:.1f}h")
        elif age < 1: red.append(f"Too new ({age*60:.0f} min)")
        else: red.append(f"Older token ({age:.0f}h)")

    if holder_count is not None:
        if holder_count >= THRESHOLDS["min_holders"]: score+=1; green.append(f"Holders: {holder_count:,}")
        else: red.append(f"Low holders: {holder_count}")
    else: red.append("Holder count unavailable")

    if top10_pct is not None:
        if top10_pct <= THRESHOLDS["max_top10_pct"]: score+=1; green.append(f"Top holder: ~{top10_pct:.1f}%")
        else: red.append(f"Whale risk: {top10_pct:.1f}%")
    else: red.append("Whale data unavailable")

    if buys > 0 and sells > 0:
        r = buys/(buys+sells)
        if r >= 0.55: score+=1; green.append(f"Buy pressure: {r*100:.0f}%")
        else: red.append(f"Sell pressure: {r*100:.0f}% buys")

    if liq > 0:
        vlr = vol/liq
        if 0.5 <= vlr <= 20: score+=1; green.append(f"Vol/Liq: {vlr:.1f}x")
        elif vlr > 20: red.append(f"Vol/Liq too high: {vlr:.0f}x")

    return score, green, red

# ─── DISPLAY ─────────────────────────────────────────────────────────────────

def display_result(pair, score, green, red):
    name    = pair.get("baseToken", {}).get("name", "Unknown")
    symbol  = pair.get("baseToken", {}).get("symbol", "???")
    address = pair.get("baseToken", {}).get("address", "")
    price   = pair.get("priceUsd", "N/A")
    dex_url = pair.get("url", f"https://dexscreener.com/solana/{address}")
    color   = Fore.GREEN if score >= 7 else Fore.YELLOW if score >= THRESHOLDS["min_score"] else Fore.RED
    print(f"\n{'─'*60}")
    print(f"{color}{Style.BRIGHT}{name} ({symbol})  Score: {score}/10")
    print(f"{Style.RESET_ALL}Address : {address}\nPrice   : ${price}\nChart   : {dex_url}")
    if green: print(f"\n{Fore.GREEN}Green:"); [print(f"  {Fore.GREEN}* {g}") for g in green]
    if red:   print(f"\n{Fore.RED}Red:");   [print(f"  {Fore.RED}* {r}") for r in red]
    if score >= THRESHOLDS["min_score"]: print(f"\n{Fore.CYAN}{Style.BRIGHT}ALERT: {score}/10")
    print(f"{'─'*60}")

# ─── MODES ───────────────────────────────────────────────────────────────────

def analyze_token(address):
    print(f"\n{Fore.CYAN}Fetching data for {address}...")
    pair = get_pair_by_address(address)
    if not pair: print(f"{Fore.RED}No Solana pair found."); return
    hcount, top10 = get_holder_info(address)
    score, green, red = score_token(pair, hcount, top10)
    display_result(pair, score, green, red)
    if score >= PUSHOVER_ALERT_SCORE:
        _, ws, all_rows = open_sheet()
        if ws: append_to_sheet(ws, all_rows, pair, score, green)
        if not was_recently_notified(all_rows, address):
            send_buy_alert(pair, score, green)
        else:
            print(f"  {Fore.YELLOW}Cooldown active -- skipping Pushover")

def watch_token(address):
    print(f"\n{Fore.CYAN}Watching {address} every {WATCH_INTERVAL_SECONDS}s...")
    while True:
        analyze_token(address); time.sleep(WATCH_INTERVAL_SECONDS)

def scan_new_tokens():
    _, ws, all_rows = open_sheet()
    if ws: monitor_portfolio(ws, all_rows)
    print(f"\n{Fore.CYAN}Scanning for new Solana meme coins...")
    pairs = get_new_solana_pairs()
    if not pairs: print(f"{Fore.RED}No pairs returned."); return
    results = []
    for pair in pairs:
        if pair.get("chainId") != "solana": continue
        addr = pair.get("baseToken", {}).get("address", "")
        hcount, top10 = get_holder_info(addr)
        score, green, red = score_token(pair, hcount, top10)
        results.append((score, pair, green, red))
    results.sort(key=lambda x: x[0], reverse=True)
    qualifying = [(s,p,g,r) for s,p,g,r in results if s >= THRESHOLDS["min_score"]]
    print(f"\nFound {len(qualifying)} tokens scoring {THRESHOLDS['min_score']}+/10 out of {len(results)} scanned.\n")
    for score, pair, green, red in qualifying[:10]:
        display_result(pair, score, green, red)
        if score >= PUSHOVER_ALERT_SCORE:
            if ws: append_to_sheet(ws, all_rows, pair, score, green)
            addr = pair.get("baseToken", {}).get("address", "")
            if not was_recently_notified(all_rows, addr):
                send_buy_alert(pair, score, green)
            else:
                print(f"  {Fore.YELLOW}Cooldown active -- skipping Pushover")

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
