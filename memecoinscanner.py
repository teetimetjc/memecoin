"""
Solana Meme Coin Scanner

Uses DexScreener (no API key) + Solscan (free tier) to:
1. Scan for new Solana tokens matching green flags
2. Analyze a specific token address
3. Alert when scoring criteria are met

Results are appended to Google Sheets and high-scoring tokens trigger Pushover alerts.

Usage:
python memecoinscanner.py                       # Scan mode (find new coins)
python memecoinscanner.py <TOKEN_ADDRESS>       # Analyze specific token
python memecoinscanner.py --watch <TOKEN_ADDRESS>  # Alert/watch mode

Dependencies:
pip install requests colorama gspread google-auth
"""

import os
import sys
import json
import time
import tempfile
import argparse
import requests
from datetime import datetime, timezone
import pytz
from colorama import Fore, Style, init

init(autoreset=True)

# Windows terminals default to cp1252; force UTF-8 for box-drawing characters
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ─── CONFIG ──────────────────────────────────────────────────────────────────

SOLSCAN_API_KEY = ""  # Optional: add your free Solscan key for higher rate limits

SPREADSHEET_ID = "1PjtaTxSW1AKZ4rAUeIoHSfrV8Imh6WV_XM9uErXunQc"
SHEET_NAME = "Sheet1"

# Pushover — loaded from env vars (set as GitHub secrets)
PUSHOVER_APP_TOKEN = os.environ.get("PUSHOVER_APP_TOKEN", "")
PUSHOVER_USER_KEY  = os.environ.get("PUSHOVER_USER_KEY", "")

# Score threshold for Pushover alerts (sheet gets everything >= min_score)
PUSHOVER_ALERT_SCORE = 7

# Scoring thresholds — tune these to your risk tolerance
THRESHOLDS = {
    "min_liquidity_usd": 10_000,
    "max_liquidity_usd": 5_000_000,
    "min_volume_24h_usd": 5_000,
    "min_holders": 100,
    "max_top10_pct": 30,
    "max_age_hours": 72,
    "min_txns_24h": 50,
    "min_price_change_1h": -10,
    "min_score": 5,
}

WATCH_INTERVAL_SECONDS = 60

# ─── GOOGLE SHEETS ───────────────────────────────────────────────────────────

SHEET_HEADERS = [
    "Timestamp", "Name", "Symbol", "Address", "Score",
    "Price (USD)", "Liquidity (USD)", "Volume 24h (USD)",
    "Age (h)", "Buy %", "Green Flags", "Chart URL",
]

def _get_gspread_client():
    """Build a gspread client from GOOGLE_CREDENTIALS env var or local file."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print(f"{Fore.YELLOW}gspread not installed — skipping Sheets. Run: pip install gspread google-auth")
        return None

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]

    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        # GitHub Actions: secret contains the raw JSON string
        info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=scopes)
    else:
        # Local dev: point GOOGLE_CREDENTIALS_FILE at the downloaded JSON
        creds_file = os.environ.get("GOOGLE_CREDENTIALS_FILE", "meme-coin-creds.json")
        if not os.path.exists(creds_file):
            print(f"{Fore.YELLOW}No Google credentials found — skipping Sheets.")
            return None
        creds = Credentials.from_service_account_file(creds_file, scopes=scopes)

    return gspread.authorize(creds)


def ensure_header(ws):
    """Write the header row if the sheet is empty."""
    if ws.row_values(1) != SHEET_HEADERS:
        ws.insert_row(SHEET_HEADERS, index=1)


def append_to_sheet(pair: dict, score: int, green: list):
    """Append one qualifying token as a new row in the Google Sheet."""
    client = _get_gspread_client()
    if client is None:
        return

    try:
        sh = client.open_by_key(SPREADSHEET_ID)
        ws = sh.worksheet(SHEET_NAME)
        ensure_header(ws)

        name     = pair.get("baseToken", {}).get("name", "Unknown")
        symbol   = pair.get("baseToken", {}).get("symbol", "???")
        address  = pair.get("baseToken", {}).get("address", "")
        price    = pair.get("priceUsd", "")
        liq      = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        vol      = float(pair.get("volume", {}).get("h24", 0) or 0)
        created  = pair.get("pairCreatedAt", 0)
        age_h    = round((time.time() * 1000 - created) / 3_600_000, 1) if created else ""
        buys     = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
        sells    = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
        buy_pct  = round(buys / (buys + sells) * 100) if (buys + sells) > 0 else ""
        dex_url  = pair.get("url", f"https://dexscreener.com/solana/{address}")
        ts       = datetime.now(pytz.timezone('America/Chicago')).strftime("%Y-%m-%d %H:%M CT")

        row = [
            ts, name, symbol, address, score,
            price, round(liq), round(vol),
            age_h, buy_pct,
            " | ".join(green),
            dex_url,
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")
        print(f"{Fore.GREEN}  → Logged to Sheets: {name} ({symbol})")
    except Exception as e:
        print(f"{Fore.YELLOW}  Sheets write failed: {e}")


# ─── PUSHOVER ────────────────────────────────────────────────────────────────

def send_pushover(pair: dict, score: int, green: list):
    """Send a Pushover notification for a high-scoring token."""
    if not PUSHOVER_APP_TOKEN or not PUSHOVER_USER_KEY:
        return

    name    = pair.get("baseToken", {}).get("name", "Unknown")
    symbol  = pair.get("baseToken", {}).get("symbol", "???")
    address = pair.get("baseToken", {}).get("address", "")
    price   = pair.get("priceUsd", "N/A")
    dex_url = pair.get("url", f"https://dexscreener.com/solana/{address}")
    jup_url = f"https://jup.ag/swap/SOL-{address}"

    title   = f"Meme Coin Alert: {name} ({symbol}) — {score}/10"
    message = f"Price: ${price}\n" + "\n".join(f"• {g}" for g in green) + f"\n\n{jup_url}"

    try:
        requests.post(
            "https://api.pushover.net/1/messages.json",
            data={
                "token":   PUSHOVER_APP_TOKEN,
                "user":    PUSHOVER_USER_KEY,
                "title":   title,
                "message": message,
                "url":       jup_url,
                "url_title": "Swap on Jupiter",
            },
            timeout=10,
        )
        print(f"{Fore.CYAN}  → Pushover sent for {name}")
    except Exception as e:
        print(f"{Fore.YELLOW}  Pushover failed: {e}")


# ─── DEXSCREENER ─────────────────────────────────────────────────────────────

DEXSCREENER_BASE = "https://api.dexscreener.com/latest/dex"

def get_new_solana_pairs():
    """Fetch recently listed Solana token pairs via DexScreener token profiles."""
    try:
        r = requests.get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=10)
        r.raise_for_status()
        profiles = r.json()
    except Exception as e:
        print(f"{Fore.RED}DexScreener profiles fetch error: {e}")
        return []

    solana_addrs = [p["tokenAddress"] for p in profiles if p.get("chainId") == "solana"]
    if not solana_addrs:
        return []

    try:
        joined = ",".join(solana_addrs[:30])
        r = requests.get(f"{DEXSCREENER_BASE}/tokens/{joined}", timeout=15)
        r.raise_for_status()
        return r.json().get("pairs", []) or []
    except Exception as e:
        print(f"{Fore.RED}DexScreener pairs fetch error: {e}")
        return []

def get_pair_by_address(token_address: str):
    """Fetch pair data for a specific token address."""
    url = f"{DEXSCREENER_BASE}/tokens/{token_address}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        pairs = r.json().get("pairs", [])
        solana_pairs = [p for p in pairs if p.get("chainId") == "solana"]
        if not solana_pairs:
            return None
        return max(solana_pairs,
                   key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
    except Exception as e:
        print(f"{Fore.RED}DexScreener fetch error: {e}")
        return None

# ─── SOLSCAN ─────────────────────────────────────────────────────────────────

SOLSCAN_BASE = "https://public-api.solscan.io"

def get_holder_info(token_address: str):
    """Get holder count and top holder concentration from Solscan."""
    headers = {}
    if SOLSCAN_API_KEY:
        headers["token"] = SOLSCAN_API_KEY

    holder_count = None
    top10_pct = None

    try:
        url = f"{SOLSCAN_BASE}/token/holders?tokenAddress={token_address}&limit=1&offset=0"
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            holder_count = r.json().get("total", None)
    except Exception:
        pass

    try:
        url = f"{SOLSCAN_BASE}/token/holders?tokenAddress={token_address}&limit=10&offset=0"
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            holders = r.json().get("data", [])
            if holders:
                total = sum(float(h.get("amount", 0)) for h in holders)
                if total > 0:
                    first = float(holders[0].get("amount", 0)) if holders else 0
                    top10_pct = round((first / total) * 100, 1)
    except Exception:
        pass

    return holder_count, top10_pct

# ─── SCORING ─────────────────────────────────────────────────────────────────

def score_token(pair: dict, holder_count=None, top10_pct=None) -> tuple[int, list, list]:
    """Score a token 0–10. Returns (score, green_flags, red_flags)."""
    score = 0
    green = []
    red = []

    liquidity_usd   = float(pair.get("liquidity", {}).get("usd", 0) or 0)
    volume_24h      = float(pair.get("volume", {}).get("h24", 0) or 0)
    txns_24h        = (pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0) + \
                      (pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0)
    price_change_1h  = float(pair.get("priceChange", {}).get("h1", 0) or 0)
    price_change_24h = float(pair.get("priceChange", {}).get("h24", 0) or 0)
    pair_created_at  = pair.get("pairCreatedAt", 0)
    age_hours        = (time.time() * 1000 - pair_created_at) / 3_600_000 if pair_created_at else None

    # 1. Liquidity
    if THRESHOLDS["min_liquidity_usd"] <= liquidity_usd <= THRESHOLDS["max_liquidity_usd"]:
        score += 1
        green.append(f"Liquidity ${liquidity_usd:,.0f} (healthy range)")
    elif liquidity_usd < THRESHOLDS["min_liquidity_usd"]:
        red.append(f"Liquidity too low: ${liquidity_usd:,.0f}")
    else:
        red.append("Very high liquidity (likely not a new meme coin)")

    # 2. Volume
    if volume_24h >= THRESHOLDS["min_volume_24h_usd"]:
        score += 1
        green.append(f"24h volume ${volume_24h:,.0f}")
    else:
        red.append(f"Low 24h volume: ${volume_24h:,.0f}")

    # 3. Transaction activity
    if txns_24h >= THRESHOLDS["min_txns_24h"]:
        score += 1
        green.append(f"Active: {txns_24h} txns in 24h")
    else:
        red.append(f"Low activity: only {txns_24h} txns in 24h")

    # 4. Price not in freefall
    if price_change_1h >= THRESHOLDS["min_price_change_1h"]:
        score += 1
        green.append(f"1h price change: {price_change_1h:+.1f}% (not dumping)")
    else:
        red.append(f"Dumping hard: {price_change_1h:+.1f}% in 1h")

    # 5. Not a straight vertical pump
    if price_change_24h < 1000:
        score += 1
        green.append(f"24h change {price_change_24h:+.1f}% (not a straight vert pump)")
    else:
        red.append(f"Straight vertical: {price_change_24h:+.1f}% in 24h — classic pump setup")

    # 6. Age
    if age_hours is not None:
        if 1 <= age_hours <= THRESHOLDS["max_age_hours"]:
            score += 1
            green.append(f"Age: {age_hours:.1f}h (fresh but past initial chaos)")
        elif age_hours < 1:
            red.append(f"Too new ({age_hours*60:.0f} min) — wait for initial chaos to settle")
        else:
            red.append(f"Older token ({age_hours:.0f}h) — may have already peaked")

    # 7. Holder count
    if holder_count is not None:
        if holder_count >= THRESHOLDS["min_holders"]:
            score += 1
            green.append(f"Holders: {holder_count:,}")
        else:
            red.append(f"Low holder count: {holder_count} (easy to rug)")
    else:
        red.append("Holder count unavailable")

    # 8. Whale concentration
    if top10_pct is not None:
        if top10_pct <= THRESHOLDS["max_top10_pct"]:
            score += 1
            green.append(f"Top holder owns ~{top10_pct:.1f}% (reasonable)")
        else:
            red.append(f"Top holder owns ~{top10_pct:.1f}% — whale risk")
    else:
        red.append("Whale concentration data unavailable")

    # 9. Buy/sell ratio
    buys  = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
    sells = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
    if buys > 0 and sells > 0:
        ratio = buys / (buys + sells)
        if ratio >= 0.55:
            score += 1
            green.append(f"Buy pressure: {ratio*100:.0f}% buys in 24h")
        else:
            red.append(f"Sell pressure: only {ratio*100:.0f}% buys")

    # 10. Volume/liquidity ratio
    if liquidity_usd > 0:
        vol_liq_ratio = volume_24h / liquidity_usd
        if 0.5 <= vol_liq_ratio <= 20:
            score += 1
            green.append(f"Vol/Liq ratio: {vol_liq_ratio:.1f}x (healthy turnover)")
        elif vol_liq_ratio > 20:
            red.append(f"Vol/Liq ratio extremely high ({vol_liq_ratio:.0f}x) — possible wash trading")

    return score, green, red

# ─── DISPLAY ─────────────────────────────────────────────────────────────────

def display_result(pair: dict, score: int, green: list, red: list):
    name    = pair.get("baseToken", {}).get("name", "Unknown")
    symbol  = pair.get("baseToken", {}).get("symbol", "???")
    address = pair.get("baseToken", {}).get("address", "")
    price   = pair.get("priceUsd", "N/A")
    dex_url = pair.get("url", f"https://dexscreener.com/solana/{address}")

    color = Fore.GREEN if score >= 7 else Fore.YELLOW if score >= THRESHOLDS["min_score"] else Fore.RED
    print(f"\n{'─'*60}")
    print(f"{color}{Style.BRIGHT}{name} ({symbol})  Score: {score}/10")
    print(f"{Style.RESET_ALL}Address : {address}")
    print(f"Price   : ${price}")
    print(f"Chart   : {dex_url}")

    if green:
        print(f"\n{Fore.GREEN}✓ Green Flags:")
        for g in green:
            print(f"  {Fore.GREEN}• {g}")

    if red:
        print(f"\n{Fore.RED}✗ Red Flags:")
        for r in red:
            print(f"  {Fore.RED}• {r}")

    if score >= THRESHOLDS["min_score"]:
        print(f"\n{Fore.CYAN}{Style.BRIGHT}⚡ ALERT: Score {score}/10 — meets your criteria")
    print(f"{'─'*60}")

# ─── MODES ───────────────────────────────────────────────────────────────────

def analyze_token(address: str):
    """Analyze a specific token address once."""
    print(f"\n{Fore.CYAN}Fetching data for {address}...")
    pair = get_pair_by_address(address)
    if not pair:
        print(f"{Fore.RED}No Solana pair found for that address.")
        return
    holder_count, top10_pct = get_holder_info(address)
    score, green, red = score_token(pair, holder_count, top10_pct)
    display_result(pair, score, green, red)
    if score >= THRESHOLDS["min_score"]:
        append_to_sheet(pair, score, green)
    if score >= PUSHOVER_ALERT_SCORE:
        send_pushover(pair, score, green)

def watch_token(address: str):
    """Continuously monitor a token and alert when score hits threshold."""
    print(f"\n{Fore.CYAN}Watching {address} every {WATCH_INTERVAL_SECONDS}s... (Ctrl+C to stop)")
    while True:
        analyze_token(address)
        time.sleep(WATCH_INTERVAL_SECONDS)

def scan_new_tokens():
    """Scan DexScreener for new Solana tokens matching green flags."""
    print(f"\n{Fore.CYAN}Scanning for new Solana meme coins...")
    pairs = get_new_solana_pairs()

    if not pairs:
        print(f"{Fore.RED}No pairs returned. Check your connection.")
        return

    results = []
    for pair in pairs:
        if pair.get("chainId") != "solana":
            continue
        address = pair.get("baseToken", {}).get("address", "")
        holder_count, top10_pct = get_holder_info(address)
        score, green, red = score_token(pair, holder_count, top10_pct)
        results.append((score, pair, green, red))

    results.sort(key=lambda x: x[0], reverse=True)

    qualifying = [(s, p, g, r) for s, p, g, r in results if s >= THRESHOLDS["min_score"]]
    print(f"\nFound {len(qualifying)} tokens scoring {THRESHOLDS['min_score']}+/10 out of {len(results)} scanned.\n")

    for score, pair, green, red in qualifying[:10]:
        display_result(pair, score, green, red)
        append_to_sheet(pair, score, green)
        if score >= PUSHOVER_ALERT_SCORE:
            send_pushover(pair, score, green)

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Solana Meme Coin Scanner")
    parser.add_argument("address", nargs="?", help="Token address to analyze")
    parser.add_argument("--watch", metavar="ADDRESS", help="Watch a token continuously")
    args = parser.parse_args()

    if args.watch:
        watch_token(args.watch)
    elif args.address:
        analyze_token(args.address)
    else:
        scan_new_tokens()

if __name__ == "__main__":
    main()
