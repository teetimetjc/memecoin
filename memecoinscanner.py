# Switch Scanner to DexScreener New Pairs Endpoint

## The Problem
The current script uses two endpoints to find coins:
- `https://api.dexscreener.com/token-profiles/latest/v1`
- `https://api.dexscreener.com/token-boosts/top/v1`

These return coins that have already been promoted/boosted — meaning they are late in their cycle and often already dumping by the time we see them.

## The Fix
Replace `get_new_solana_pairs()` with a function that hits the new pairs endpoint instead:

```
https://api.dexscreener.com/latest/dex/pairs/solana
```

This returns the most recently created Solana trading pairs — catching coins at or near birth rather than after promotion.

## Specific Changes

### 1. Replace `get_new_solana_pairs()` entirely with this:

```python
def get_new_solana_pairs():
    """Fetch the most recently created Solana pairs from DexScreener."""
    try:
        r = requests.get(
            "https://api.dexscreener.com/latest/dex/pairs/solana",
            timeout=15
        )
        r.raise_for_status()
        pairs = r.json().get("pairs", []) or []
        # Sort by creation time descending so newest are first
        pairs.sort(key=lambda p: p.get("pairCreatedAt", 0) or 0, reverse=True)
        return pairs
    except Exception as e:
        print(f"{Fore.RED}Fetch error (new pairs): {e}")
        return []
```

### 2. Tighten the age filter to catch coins early
Since we are now seeing coins at birth, update THRESHOLDS to only look at very new coins:

```python
THRESHOLDS = {
    "max_age_hours": 24,  # was 72 — tighten to 24h since we want early entries
}
```

And in DIP_RECOVERY_THRESHOLDS, lower the minimum age since we want to catch dips on newer coins too:
```python
DIP_RECOVERY_THRESHOLDS = {
    "min_age_hours": 1,  # was 6 — lower since new pairs will be younger
}
```

### 3. Add a minimum age filter to avoid brand new rugs
Coins under 5 minutes old are extremely high risk. Add this check inside `scan_new_tokens()` and `scan_dip_opportunities()` before scoring:

```python
age_h = (time.time() * 1000 - (pair.get("pairCreatedAt") or 0)) / 3_600_000
if age_h < 0.1:  # skip coins under ~6 minutes old
    continue
```

### 4. Remove the old fetch URLs
Delete or comment out these two lines inside the old `get_new_solana_pairs()`:
```python
# "https://api.dexscreener.com/token-profiles/latest/v1"
# "https://api.dexscreener.com/token-boosts/top/v1"
```

## Summary
The only major change is the data source. Everything else — scoring, filters, sheet logging, Pushover alerts, follow-up prices — stays exactly the same. The goal is to see coins earlier in their lifecycle before they appear on promoted lists.
