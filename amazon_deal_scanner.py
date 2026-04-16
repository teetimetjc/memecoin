import sys
sys.stdout.reconfigure(encoding='utf-8')

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains

import re
import time
import requests

# --------------------------------------------------
# Config
# --------------------------------------------------
MIN_DISCOUNT_PCT       = 50      # Deals page slider threshold (Today's Deals mode)
SEARCH_MIN_DISCOUNT    = 49      # Minimum discount % when using SEARCH_QUERY mode
MAX_PRICE              = None    # Price filter disabled; set e.g. 500 to cap at $500
SEARCH_QUERY           = "pellet smoker"  # Leave empty for Today's Deals; set to search a specific item
MIN_STARS              = 4.0     # Minimum star rating (e.g. 4.0); set None to skip
MIN_REVIEWS            = 50      # Minimum number of ratings; set None to skip
PUSHOVER_SOUND         = "cashregister"  # Pushover sound: cashregister, magic, siren, alien, climb, etc. or "pushover" for default
AMAZON_DEALS_URL       = "https://www.amazon.com/deals"
SCROLL_PAUSE           = 2.0

# --------------------------------------------------
# Pushover config
# --------------------------------------------------
PUSHOVER_USER  = "unra8yddj48h5utjouiq7iy3ryow4v"
PUSHOVER_TOKEN = "a7vojj3ia84csiknr1grn86uatxiz7"

def send_pushover(title, message, priority=0):
    try:
        resp = requests.post("https://api.pushover.net/1/messages.json", data={
            "token":    PUSHOVER_TOKEN,
            "user":     PUSHOVER_USER,
            "title":    title,
            "message":  message,
            "priority": priority,
            "sound":    PUSHOVER_SOUND,
        }, timeout=10)
        if resp.status_code == 200:
            print("📲 Pushover notification sent.")
        else:
            print(f"⚠️  Pushover responded with {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"⚠️  Could not send Pushover notification: {e}")

# --------------------------------------------------
# Chrome setup
# --------------------------------------------------
chrome_options = Options()
# Auto-detect headless: GitHub Actions sets CI=true, local dev runs headed
import os
if os.environ.get("CI"):
    chrome_options.add_argument("--headless=new")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--disable-extensions")
chrome_options.add_argument("--incognito")
chrome_options.add_argument("--window-size=1920,1080")
chrome_options.add_argument(
    "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

try:
    driver = webdriver.Chrome(options=chrome_options)
except Exception as launch_err:
    print(f"❌ Chrome failed to launch: {launch_err}")
    send_pushover("❌ Amazon Deal Scanner Failed", f"Chrome failed to launch:\n\n{launch_err}", priority=1)
    sys.exit(1)

def screenshot(name):
    path = f"screenshot_{name}.png"
    driver.save_screenshot(path)
    print(f"📸 Screenshot saved: {path}")

def parse_price(text):
    try:
        cleaned = re.sub(r'[^\d.]', '', text)
        return float(cleaned) if cleaned else None
    except:
        return None

def parse_discount(text):
    try:
        match = re.search(r'(\d+)', text)
        return int(match.group(1)) if match else None
    except:
        return None

def get_amazon_price_context(asin, driver):
    """
    Visit the Amazon product page and scrape all price context Amazon shows:
    - Typical price (what Amazon considers normal)
    - List price (manufacturer's suggested price)
    - "Lowest price in X days" badge if present
    - CamelCamelCamel link for the full chart
    Returns a dict of whatever was found.
    """
    result = {
        "typical_price": None,
        "list_price":    None,
        "lowest_badge":  None,
        "camel_url":     f"https://camelcamelcamel.com/product/{asin}",
    }

    try:
        print(f"   📊 Visiting product page for price history...")
        driver.get(f"https://www.amazon.com/dp/{asin}")
        time.sleep(4)

        def get_offscreen(selector):
            els = driver.find_elements(By.CSS_SELECTOR, selector)
            for el in els:
                val = parse_price(driver.execute_script("return arguments[0].textContent;", el))
                if val and val > 0:
                    return val
            return None

        # Typical price — what Amazon shows as the "normal" price
        result["typical_price"] = get_offscreen("#tp_price_block_total_price_ww .a-offscreen")

        # List / basis price — manufacturer's suggested retail
        if result["typical_price"] is None:
            result["typical_price"] = get_offscreen(".basisPrice .a-offscreen")

        # List price (separate field, sometimes both exist)
        result["list_price"] = get_offscreen(".basisPrice .a-offscreen") or get_offscreen("#listPrice")

        # "Lowest price in X days" badge
        for xpath in [
            "//*[contains(text(),'Lowest price in')]",
            "//*[contains(text(),'lowest price in')]",
            "//*[contains(text(),'Lowest in')]",
        ]:
            els = driver.find_elements(By.XPATH, xpath)
            for el in els:
                txt = el.text.strip()
                if txt and len(txt) < 80:
                    result["lowest_badge"] = txt
                    break
            if result["lowest_badge"]:
                break

        # Log what we found
        found = {k: v for k, v in result.items() if v and k != "camel_url"}
        print(f"      Found: {found}")

    except Exception as e:
        print(f"   ⚠️  Product page scrape failed: {e}")

    return result

def keyboard_slide(slider_el, target_val):
    """
    Focus a range input via JS (bypasses click interception) then
    use arrow keys to move it to target_val.
    """
    from selenium.webdriver.common.keys import Keys
    current_val = int(slider_el.get_attribute("value") or 0)
    steps = target_val - current_val
    key = Keys.ARROW_RIGHT if steps > 0 else Keys.ARROW_LEFT
    driver.execute_script("arguments[0].focus();", slider_el)
    time.sleep(0.2)
    for _ in range(abs(steps)):
        slider_el.send_keys(key)
    time.sleep(1.5)
    return int(slider_el.get_attribute("value") or 0)

def set_discount_filter(target_min_pct):
    """Move the Minimum discount slider to target_min_pct using keyboard navigation."""
    try:
        print(f"🎚️  Setting discount filter to {target_min_pct}%+...")
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)

        # Find by aria-label — reliable across layout changes
        sliders = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "input[type='range']"))
        )

        # Find the "Minimum discount" input
        min_discount = next(
            (s for s in sliders if (s.get_attribute("aria-label") or "").lower() in ("minimum discount", "min discount")),
            None
        )
        if min_discount is None:
            print("⚠️  Could not find 'Minimum discount' slider — skipping.")
            return False

        slider_min = int(min_discount.get_attribute("min") or 0)
        slider_max = int(min_discount.get_attribute("max") or 100)
        print(f"   Discount slider: min={slider_min}, max={slider_max}")

        # Map target_min_pct (0–100) to internal slider value (0–slider_max)
        target_val = round((target_min_pct / 100) * slider_max)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", min_discount)
        time.sleep(0.3)

        new_val = keyboard_slide(min_discount, target_val)
        actual_pct = round((new_val / slider_max) * 100)
        print(f"   ✅ Discount slider set to internal value {new_val} (~{actual_pct}% off minimum)")
        return True

    except Exception as e:
        print(f"⚠️  Discount filter failed: {e}")
        return False

def set_price_filter(max_price_dollars):
    """Move the Maximum Price slider left until aria-valuetext matches target price."""
    try:
        print(f"💲 Setting price filter to $0–${max_price_dollars}...")
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.5)

        from selenium.webdriver.common.keys import Keys
        sliders = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "input[type='range']"))
        )

        max_price_slider = next(
            (s for s in sliders if (s.get_attribute("aria-label") or "").lower() in ("maximum price", "max price")),
            None
        )
        if max_price_slider is None:
            print("⚠️  Could not find 'Maximum Price' slider — skipping.")
            return False

        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", max_price_slider)
        time.sleep(0.3)
        driver.execute_script("arguments[0].focus();", max_price_slider)
        time.sleep(0.2)

        # Press ArrowLeft repeatedly, checking aria-valuetext each step
        # Stop when the displayed value is <= max_price_dollars
        max_steps = int(max_price_slider.get_attribute("max") or 193)
        for _ in range(max_steps):
            val_text = max_price_slider.get_attribute("aria-valuetext") or ""
            price_val = parse_price(val_text)
            if price_val is not None and price_val <= max_price_dollars:
                break
            max_price_slider.send_keys(Keys.ARROW_LEFT)
            time.sleep(0.05)

        final_text = max_price_slider.get_attribute("aria-valuetext") or "?"
        print(f"   ✅ Price slider max set to: {final_text}")
        return True

    except Exception as e:
        print(f"⚠️  Price filter failed: {e}")
        return False

# --------------------------------------------------
# Main scraping logic
# --------------------------------------------------
qualifying_deals = []

try:
    # --------------------------------------------------
    # MODE A: Search for specific item (amazon.com/s)
    # MODE B: Scan Today's Deals page (amazon.com/deals)
    # --------------------------------------------------
    if SEARCH_QUERY:
        url = f"https://www.amazon.com/s?k={SEARCH_QUERY.replace(' ', '+')}"
        print(f"🔎 Searching Amazon for: {SEARCH_QUERY}")
        driver.get(url)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "[data-component-type='s-search-result']"))
        )
        print("✅ Search results loaded.")
        screenshot("01_search_results")
    else:
        print("🌐 Loading Amazon Today's Deals page...")
        driver.get(AMAZON_DEALS_URL)
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR,
                    "[class*='ProductCard-module__card_'], [class*='DealCard']"
                ))
            )
            print("✅ Deal cards detected.")
        except:
            print("⚠️  Deal card wait timed out — proceeding anyway...")
            time.sleep(5)
        screenshot("01_initial_load")

        # Apply discount slider filter (deals page only)
        set_discount_filter(MIN_DISCOUNT_PCT)
        screenshot("02_after_discount_filter")

        if MAX_PRICE is not None:
            set_price_filter(MAX_PRICE)
            screenshot("03_after_price_filter")

        # Wait for filtered results
        print("⏳ Waiting for filtered results...")
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR,
                    "[class*='ProductCard-module__card_'], [class*='ProductCard-module__cardContainingLink_']"
                ))
            )
            print("   ✅ Cards detected after filter.")
        except:
            print("   ⚠️  Card wait timed out — giving extra 5s...")
            time.sleep(5)

    # Scroll to load lazy content
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(1)
    print("📜 Scrolling to load all results...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    for i in range(15):
        driver.execute_script("window.scrollBy(0, 800);")
        time.sleep(SCROLL_PAUSE)
        new_height = driver.execute_script("return document.body.scrollHeight")
        print(f"   Scroll {i+1}: {last_height} → {new_height}")
        if new_height == last_height and i > 3:
            print("   ↳ No new content, stopping scroll.")
            break
        last_height = new_height

    screenshot("04_after_scroll")

    # --------------------------------------------------
    # Scrape — two strategies depending on page type
    # --------------------------------------------------
    print("\n🔍 Scraping results...")
    seen_titles = set()

    if SEARCH_QUERY:
        # --- Search results page scraping ---
        cards = driver.find_elements(By.CSS_SELECTOR, "[data-component-type='s-search-result']")
        print(f"   Found {len(cards)} search result items")

        for card in cards:
            try:
                # Title — try multiple selectors since Amazon A/B tests layouts
                title = ""
                for sel in ["h2 .a-text-normal", "h2 a span", "h2 span"]:
                    els = card.find_elements(By.CSS_SELECTOR, sel)
                    if els:
                        title = els[0].text.strip()
                        if title:
                            break
                # Last resort: get via JS textContent on h2
                if not title:
                    h2s = card.find_elements(By.CSS_SELECTOR, "h2")
                    if h2s:
                        title = (driver.execute_script("return arguments[0].textContent;", h2s[0]) or "").strip()
                if not title:
                    continue

                # Prices — collect all .a-offscreen values, strip label prefixes like "Typical: " "List: "
                current_price = original_price = None
                all_offscreen = card.find_elements(By.CSS_SELECTOR, ".a-offscreen")
                raw_prices = []
                for o in all_offscreen:
                    txt = (driver.execute_script("return arguments[0].textContent;", o) or "").strip()
                    # Strip label prefixes
                    txt = re.sub(r'^(Typical|List|Was|Save|Reg)[\.\:]?\s*', '', txt, flags=re.IGNORECASE)
                    val = parse_price(txt)
                    if val and val > 0:
                        raw_prices.append(val)

                if raw_prices:
                    current_price = min(raw_prices)
                    if len(raw_prices) > 1:
                        original_price = max(raw_prices)

                # Calculate discount from prices, or find a visible % badge
                discount_pct = None
                if current_price and original_price and original_price > current_price:
                    discount_pct = round((1 - current_price / original_price) * 100)
                if discount_pct is None:
                    for b in card.find_elements(By.XPATH, ".//*[contains(text(),'% off')]"):
                        d = parse_discount(b.text)
                        if d:
                            discount_pct = d
                            break

                if discount_pct is None or discount_pct < SEARCH_MIN_DISCOUNT:
                    continue

                # Max price filter (optional)
                if MAX_PRICE is not None and current_price and current_price > MAX_PRICE:
                    continue

                # --- Star rating ---
                stars = None
                stars_el = card.find_elements(By.CSS_SELECTOR, "a[aria-label*='out of 5 stars']")
                if stars_el:
                    m = re.search(r'([\d\.]+)\s+out of 5', stars_el[0].get_attribute("aria-label") or "")
                    if m:
                        stars = float(m.group(1))
                if MIN_STARS is not None and (stars is None or stars < MIN_STARS):
                    print(f"   ⛔ Skipped (rating {stars}/5): {title[:50]}")
                    continue

                # --- Review count ---
                review_count = None
                count_el = card.find_elements(By.CSS_SELECTOR, "a[aria-label*='ratings']")
                if count_el:
                    m = re.search(r'([\d,]+)', count_el[0].get_attribute("aria-label") or "")
                    if m:
                        review_count = int(m.group(1).replace(",", ""))
                if MIN_REVIEWS is not None and (review_count is None or review_count < MIN_REVIEWS):
                    print(f"   ⛔ Skipped ({review_count} reviews): {title[:50]}")
                    continue

                title = title[:100]
                key = title[:30].lower().strip()
                if key in seen_titles:
                    continue
                seen_titles.add(key)

                # ASIN + Link — data-asin is most reliable; fallback to href
                asin = card.get_attribute("data-asin") or ""
                if asin:
                    link = f"https://www.amazon.com/dp/{asin}"
                else:
                    link = AMAZON_DEALS_URL
                    try:
                        a_els = card.find_elements(By.CSS_SELECTOR, "a[href*='/dp/']")
                        if a_els:
                            raw = a_els[0].get_attribute("href") or ""
                            m = re.search(r'/dp/([A-Z0-9]{10})', raw)
                            if m:
                                asin = m.group(1)
                                link = f"https://www.amazon.com/dp/{asin}"
                    except:
                        pass

                # Typical price from card itself (Amazon sometimes shows it)
                card_typical = None
                try:
                    typ_els = card.find_elements(By.CSS_SELECTOR, ".a-price.a-text-price .a-offscreen")
                    if typ_els:
                        card_typical = parse_price(driver.execute_script("return arguments[0].textContent;", typ_els[0]))
                except:
                    pass

                deal = {
                    "title":          title,
                    "discount_pct":   discount_pct,
                    "current_price":  f"${current_price:.2f}" if current_price else "N/A",
                    "original_price": f"${original_price:.2f}" if original_price else "N/A",
                    "typical_price":  f"${card_typical:.2f}" if card_typical else None,
                    "stars":          f"{stars}⭐" if stars else "N/A",
                    "reviews":        f"{review_count:,}" if review_count else "N/A",
                    "asin":           asin,
                    "link":           link,
                }
                qualifying_deals.append(deal)
                print(f"   🔥 {discount_pct}% off | {stars}⭐ ({review_count:,} reviews) — {title[:55]}")

            except:
                continue

    else:
        # --- Today's Deals page scraping ---
        all_cards = []
        for sel in ["[class*='ProductCard-module__card_']", "[class*='ProductCard-module__cardContainingLink_']"]:
            found = driver.find_elements(By.CSS_SELECTOR, sel)
            print(f"   Selector '{sel[:55]}': {len(found)} found")
            if found and not all_cards:
                all_cards = found

        for card in all_cards:
            try:
                card_text = card.text.strip()

                # Discount badge
                discount_pct = None
                for b in card.find_elements(By.CSS_SELECTOR,
                        ".a-size-mini, [class*='filledRoundedBadgeLabel']"):
                    txt = b.text.strip()
                    if "%" in txt:
                        discount_pct = parse_discount(txt)
                        if discount_pct:
                            break
                if discount_pct is None:
                    price_matches = re.findall(r'\$[\d,]+\.?\d*', card_text)
                    prices = sorted(set(filter(None, [parse_price(p) for p in price_matches])))
                    if len(prices) >= 2:
                        discount_pct = round((1 - prices[0] / prices[-1]) * 100)
                if discount_pct is None or discount_pct < MIN_DISCOUNT_PCT:
                    continue

                # Title — pick the element without "%" in it
                title = ""
                for el in card.find_elements(By.CSS_SELECTOR, "[class*='ProductCard-module__title_']"):
                    txt = (driver.execute_script("return arguments[0].textContent;", el) or "").strip()
                    if txt and "%" not in txt and len(txt) > 5:
                        title = txt
                        break
                if not title:
                    lines = [l.strip() for l in card_text.splitlines() if len(l.strip()) > 10]
                    text_lines = [l for l in lines if not re.match(r'^[\$\d\.\,\%\-\s]+$', l)
                                  and "% off" not in l.lower() and "time deal" not in l.lower()]
                    title = max(text_lines, key=len) if text_lines else (lines[0] if lines else "Unknown")

                title = title[:100]
                key = title[:30].lower().strip()
                if not title or key in seen_titles:
                    continue
                seen_titles.add(key)

                # Prices via .a-offscreen
                current_price = original_price = None
                pay = card.find_elements(By.CSS_SELECTOR, "[class*='ProductCard-module__priceToPay_']")
                if pay:
                    os_els = pay[0].find_elements(By.CSS_SELECTOR, ".a-offscreen")
                    if os_els:
                        current_price = parse_price(driver.execute_script("return arguments[0].textContent;", os_els[0]))
                orig = card.find_elements(By.CSS_SELECTOR, ".a-text-price")
                if orig:
                    os_els = orig[0].find_elements(By.CSS_SELECTOR, ".a-offscreen")
                    if os_els:
                        original_price = parse_price(driver.execute_script("return arguments[0].textContent;", os_els[0]))

                # Link
                link = AMAZON_DEALS_URL
                try:
                    a = card.find_element(By.CSS_SELECTOR,
                        "[class*='ProductCard-module__cardContainingLink_'], a[href*='/dp/']")
                    raw = a.get_attribute("href") or ""
                    m = re.search(r'/dp/([A-Z0-9]{10})', raw)
                    link = f"https://www.amazon.com/dp/{m.group(1)}" if m else raw or AMAZON_DEALS_URL
                except:
                    pass

                deal = {
                    "title":          title,
                    "discount_pct":   discount_pct,
                    "current_price":  f"${current_price:.2f}" if current_price else "N/A",
                    "original_price": f"${original_price:.2f}" if original_price else "N/A",
                    "link":           link,
                }
                qualifying_deals.append(deal)
                print(f"   🔥 {discount_pct}% off — {title[:70]}")

            except:
                continue

    # --------------------------------------------------
    # Deduplicate, sort, notify
    # --------------------------------------------------
    unique_deals = list({d["title"][:30].lower().strip(): d for d in qualifying_deals}.values())
    unique_deals.sort(key=lambda x: x["discount_pct"], reverse=True)

    print(f"\n{'='*50}")
    threshold = SEARCH_MIN_DISCOUNT if SEARCH_QUERY else MIN_DISCOUNT_PCT
    print(f"✅ Found {len(unique_deals)} deals at {threshold}%+ off")
    print(f"{'='*50}")

    if unique_deals:
        for d in unique_deals:
            print(f"  🔥 {d['discount_pct']}% off | {d['current_price']} (was {d['original_price']}) | {d['title']}")

        if len(unique_deals) <= 4:
            for deal in unique_deals:
                asin = deal.get('asin', '')

                # Scrape Amazon product page for price history context
                price_ctx = get_amazon_price_context(asin, driver) if asin else {}

                stars_line   = f"⭐ {deal['stars']} ({deal['reviews']} reviews)\n" if deal.get('stars') and deal['stars'] != 'N/A' else ""

                # Build price history block
                history_lines = []
                if price_ctx.get("typical_price"):
                    history_lines.append(f"  Typical price: ${price_ctx['typical_price']:.2f}")
                if price_ctx.get("list_price") and price_ctx.get("list_price") != price_ctx.get("typical_price"):
                    history_lines.append(f"  List price:    ${price_ctx['list_price']:.2f}")
                if price_ctx.get("lowest_badge"):
                    history_lines.append(f"  🏆 {price_ctx['lowest_badge']}")
                history_block = "📊 Price context:\n" + "\n".join(history_lines) + "\n" if history_lines else ""

                camel_line = f"📉 Full history: {price_ctx.get('camel_url', '')}" if asin else ""

                msg = (
                    f"{deal['title']}\n\n"
                    f"💰 {deal['current_price']} (was {deal['original_price']})\n"
                    f"🏷️ {deal['discount_pct']}% off\n"
                    f"{stars_line}\n"
                    f"{history_block}\n"
                    f"🛒 {deal['link']}\n"
                    f"{camel_line}"
                )
                send_pushover(f"🔥 {deal['discount_pct']}% Off on Amazon!", msg, priority=1)
                time.sleep(1)
        else:
            lines = [f"• {d['discount_pct']}% off — {d['title'][:50]} ({d['current_price']})" for d in unique_deals[:10]]
            send_pushover(
                f"🔥 {len(unique_deals)} Amazon Deals {MIN_DISCOUNT_PCT}%+ Off!",
                "\n".join(lines),
                priority=1,
            )
    else:
        print(f"😴 No deals found at {MIN_DISCOUNT_PCT}%+ off this scan.")
        send_pushover(
            "😴 No Amazon Deals Found",
            f"Scan complete. No deals at {MIN_DISCOUNT_PCT}%+ off today.",
            priority=-1,
        )

except Exception as e:
    print(f"❌ Error: {e}")
    screenshot("error")
    send_pushover("❌ Amazon Deal Scanner Failed", f"Script error:\n\n{e}", priority=1)
    sys.exit(1)

finally:
    driver.quit()
    print("\n🏁 Browser closed. Scan complete.")
