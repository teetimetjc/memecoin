# memecoin

A Python script to scan, analyze, and monitor Solana meme coins using DexScreener and Solscan — no paid API keys required.

## What It Does

- **Scan mode** — finds new Solana tokens and scores them against green/red flag criteria
- **Analyze mode** — deep dive on a specific token address
- **Watch mode** — monitors a token on a loop and alerts when it hits your score threshold

## Scoring (0–10)

Each token is scored based on:

- Liquidity pool size (healthy range)
- 24h volume
- Transaction activity
- Price stability (not in freefall)
- No straight vertical pump
- Token age (fresh but past initial chaos)
- Holder count
- Whale concentration
- Buy/sell pressure ratio
- Volume/liquidity ratio

## Setup

```bash
pip install -r requirements.txt
```

Optionally, add a free [Solscan API key](https://solscan.io/) to the `SOLSCAN_API_KEY` variable in `meme_coin_scanner.py` for higher rate limits.

## Usage

```bash
# Scan for new meme coins
python meme_coin_scanner.py

# Analyze a specific token
python meme_coin_scanner.py <TOKEN_ADDRESS>

# Watch a token and alert when score threshold is met
python meme_coin_scanner.py --watch <TOKEN_ADDRESS>
```

## Configuration

All thresholds are tunable at the top of `meme_coin_scanner.py` in the `THRESHOLDS` dict:

|Setting             |Default   |Description                           |
|--------------------|----------|--------------------------------------|
|`min_liquidity_usd` |$10,000   |Minimum pool size                     |
|`max_liquidity_usd` |$5,000,000|Ignore large established coins        |
|`min_volume_24h_usd`|$5,000    |Minimum 24h volume                    |
|`min_holders`       |100       |Minimum unique holders                |
|`max_top10_pct`     |30%       |Max % held by top wallet (whale check)|
|`max_age_hours`     |72h       |Only look at coins under 3 days old   |
|`min_txns_24h`      |50        |Minimum transactions in 24h           |
|`min_score`         |5         |Alert threshold (out of 10)           |

## Data Sources

- [DexScreener](https://dexscreener.com) — price, volume, liquidity, transactions (no API key needed)
- [Solscan](https://solscan.io) — holder count, whale concentration (free tier)

## Disclaimer

This is a signal tool, not financial advice. Always do your own research before trading. Most meme coins go to zero.