"""
AmbitionBox Company Data Scraper
==================================
Collects structured company data from:
  https://www.ambitionbox.com/list-of-companies

Steps:
  1. Scrape company name + profile URL from pages 1-5
  2. Visit each company's overview page
  3. Extract detailed fields (rating, reviews, industry, description,
     sub-ratings, metadata)
  4. Write everything to a clean CSV

Author  : Data Scraping Intern Assignment
Stack   : Python · requests · BeautifulSoup · csv · json
"""

# Standard library
import csv
import json
import logging
import random
import re
import time
from dataclasses import asdict, dataclass, fields
from typing import Optional
from urllib.parse import urljoin

# Third-party
import requests
from bs4 import BeautifulSoup

# ───────────────────────────────────────────────────────────────────────────
# CONFIGURATION  (change only here — no hard-coded values elsewhere)
# ───────────────────────────────────────────────────────────────────────────
BASE_URL        = "https://www.ambitionbox.com"
LISTING_PATH    = "/list-of-companies"
PAGES_TO_SCRAPE = 5          # pages 1-5  (~10 companies each → 50 total)
OUTPUT_CSV      = "ambitionbox_companies.csv"

DELAY_MIN, DELAY_MAX = 2.0, 4.5   # polite delay between requests (seconds)
MAX_RETRIES = 3

# ───────────────────────────────────────────────────────────────────────────
# LOGGING
# ───────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ab_scraper")

# ───────────────────────────────────────────────────────────────────────────
# DATA MODEL
# ───────────────────────────────────────────────────────────────────────────
@dataclass
class Company:
    """One row in the output CSV."""
    name:               str = ""
    profile_url:        str = ""
    overall_rating:     str = ""
    total_reviews:      str = ""
    industry:           str = ""
    description:        str = ""
    work_life_balance:  str = ""
    salary_benefits:    str = ""
    job_security:       str = ""
    company_culture:    str = ""
    skill_development:  str = ""
    work_satisfaction:  str = ""
    company_type:       str = ""
    headquarters:       str = ""
    founded:            str = ""
    employees:          str = ""

# ───────────────────────────────────────────────────────────────────────────
# HTTP UTILITIES
# ───────────────────────────────────────────────────────────────────────────
_USER_AGENTS = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
     "Gecko/20100101 Firefox/125.0"),
    ("Mozilla/5.0 (X11; Linux x86_64) "
     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
]


def make_session() -> requests.Session:
    """Build a requests.Session that mimics a real browser."""
    s = requests.Session()
    s.headers.update({
        "User-Agent":                random.choice(_USER_AGENTS),
        "Accept":                    ("text/html,application/xhtml+xml,"
                                      "application/xml;q=0.9,image/avif,"
                                      "image/webp,*/*;q=0.8"),
        "Accept-Language":           "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding":           "gzip, deflate, br",
        "Referer":                   BASE_URL + "/",
        "DNT":                       "1",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":            "document",
        "Sec-Fetch-Mode":            "navigate",
        "Sec-Fetch-Site":            "same-origin",
        "Sec-Fetch-User":            "?1",
        "Cache-Control":             "max-age=0",
    })
    return s


def _sleep():
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))


def fetch_page(session: requests.Session, url: str) -> Optional[BeautifulSoup]:
    """Fetch *url* with retries; return BeautifulSoup or None on failure."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            session.headers["User-Agent"] = random.choice(_USER_AGENTS)
            resp = session.get(url, timeout=20)

            if resp.status_code == 200:
                return BeautifulSoup(resp.text, "lxml")

            if resp.status_code == 429:
                wait = 15 * attempt
                log.warning("Rate-limited — waiting %ds", wait)
                time.sleep(wait)
                continue

            log.warning("HTTP %d for %s (attempt %d/%d)",
                        resp.status_code, url, attempt, MAX_RETRIES)

        except requests.exceptions.Timeout:
            log.warning("Timeout for %s (attempt %d/%d)", url, attempt, MAX_RETRIES)
        except requests.exceptions.ConnectionError as exc:
            log.warning("Connection error: %s (attempt %d/%d)", exc, attempt, MAX_RETRIES)
        except requests.RequestException as exc:
            log.warning("Request error: %s", exc)
            break

        _sleep()

    log.error("Permanently failed: %s", url)
    return None

# ───────────────────────────────────────────────────────────────────────────
# LISTING-PAGE PARSER
# ───────────────────────────────────────────────────────────────────────────
def _text(tag) -> str:
    return tag.get_text(strip=True) if tag else ""


def parse_listing_page(soup: BeautifulSoup) -> list:
    """
    Extract {name, profile_url} from one listing page.
    Three strategies, in priority order.
    """
    results = []
    seen    = set()

    # Strategy A: anchor tags whose href contains '/overview/'
    for a in soup.select("a[href*='/overview/']"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        url = urljoin(BASE_URL, href.split("?")[0])
        h   = a.find(["h2", "h3"])
        name = _text(h) or a.get("title", "") or _text(a)
        if name and url not in seen:
            seen.add(url)
            results.append({"name": name, "profile_url": url})

    if results:
        return results

    # Strategy B: JSON-LD ItemList
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if data.get("@type") == "ItemList":
                for item in data.get("itemListElement", []):
                    name = item.get("name", "").strip()
                    url  = item.get("url",  "").strip()
                    if name and url and url not in seen:
                        seen.add(url)
                        results.append({"name": name, "profile_url": url})
        except (json.JSONDecodeError, AttributeError):
            pass

    if results:
        return results

    # Strategy C: generic card scan
    for card in soup.find_all(["div", "article", "li"],
                               class_=re.compile(r"company|card|listing", re.I)):
        a = card.find("a", href=True)
        h = card.find(["h2", "h3", "h4"])
        if not a or not h:
            continue
        href = urljoin(BASE_URL, a["href"].split("?")[0])
        name = _text(h)
        if name and href not in seen and "ambitionbox.com" in href:
            seen.add(href)
            results.append({"name": name, "profile_url": href})

    return results

# ───────────────────────────────────────────────────────────────────────────
# DETAIL-PAGE PARSER
# ───────────────────────────────────────────────────────────────────────────
def _rating_num(text: str) -> str:
    m = re.search(r"\b(\d+\.\d+|\d+)\b", text)
    return m.group(1) if m else ""


def _extract_json_ld(soup: BeautifulSoup) -> dict:
    merged = {}
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            obj = json.loads(script.string or "")
            if isinstance(obj, list):
                for item in obj:
                    if isinstance(item, dict):
                        merged.update(item)
            elif isinstance(obj, dict):
                merged.update(obj)
        except (json.JSONDecodeError, AttributeError):
            pass
    return merged


_RATING_MAP = {
    "work life balance":            "work_life_balance",
    "work-life balance":            "work_life_balance",
    "worklife balance":             "work_life_balance",
    "salary":                       "salary_benefits",
    "salary & benefits":            "salary_benefits",
    "salary and benefits":          "salary_benefits",
    "salaries":                     "salary_benefits",
    "job security":                 "job_security",
    "job security & growth":        "job_security",
    "career growth":                "job_security",
    "company culture":              "company_culture",
    "culture":                      "company_culture",
    "work culture":                 "company_culture",
    "skill development":            "skill_development",
    "skill development & learning": "skill_development",
    "learning & development":       "skill_development",
    "work satisfaction":            "work_satisfaction",
    "job satisfaction":             "work_satisfaction",
}


def _set_sub_rating(company: Company, label: str, value: str):
    field = _RATING_MAP.get(label.lower().strip())
    if field and value and not getattr(company, field):
        try:
            if 0 < float(value) <= 5:
                setattr(company, field, value)
        except ValueError:
            pass


def parse_company_page(soup: BeautifulSoup, fallback_name: str = "") -> Company:
    """Parse a company overview page into a Company dataclass."""
    c = Company()

    # Name
    for sel in ["h1[class*='company-name']", "h1[class*='companyName']", "h1"]:
        tag = soup.select_one(sel)
        if tag:
            c.name = _text(tag)
            break
    c.name = c.name or fallback_name

    # JSON-LD (highest priority for rating/reviews/description)
    ld = _extract_json_ld(soup)
    c.name        = c.name or ld.get("name", "")
    c.description = ld.get("description", "")
    ar = ld.get("aggregateRating", {})
    c.overall_rating = str(ar.get("ratingValue", ""))
    c.total_reviews  = str(ar.get("reviewCount",  ""))

    # Overall rating — HTML fallback
    if not c.overall_rating:
        for sel in ["[class*='overallRating']", "[class*='overall-rating']",
                    "[class*='company-rating']", "[class*='rating-number']"]:
            tag = soup.select_one(sel)
            if tag:
                val = _rating_num(_text(tag))
                if val:
                    c.overall_rating = val
                    break
    if not c.overall_rating:
        for tag in soup.find_all(["div", "span"],
                                  class_=re.compile(r"rating|score", re.I)):
            val = _rating_num(_text(tag))
            if val and float(val) <= 5.0:
                c.overall_rating = val
                break

    # Total reviews — HTML fallback
    if not c.total_reviews:
        review_re = re.compile(r"([\d,\.]+[kKlL]?)\s*reviews?", re.I)
        for tag in soup.find_all(string=review_re):
            m = review_re.search(tag)
            if m:
                raw = m.group(1).replace(",", "")
                if raw[-1].lower() == "k":
                    raw = str(int(float(raw[:-1]) * 1_000))
                elif raw[-1].lower() == "l":
                    raw = str(int(float(raw[:-1]) * 100_000))
                c.total_reviews = raw
                break

    # Industry
    industry_links = soup.find_all(
        "a", href=re.compile(r"-companies-in-india|/industry/", re.I)
    )
    industries = list(dict.fromkeys(_text(t) for t in industry_links if _text(t)))
    c.industry = ", ".join(industries)
    if not c.industry:
        meta_kw = soup.find("meta", attrs={"name": "keywords"})
        if meta_kw:
            c.industry = meta_kw.get("content", "")

    # Description — HTML fallback
    if not c.description:
        for sel in ["[class*='about']", "[class*='description']",
                    "[class*='overview']", "#about", "#overview"]:
            section = soup.select_one(sel)
            if not section:
                continue
            paras = section.find_all("p")
            if paras:
                c.description = max((_text(p) for p in paras), key=len, default="")
            else:
                raw = _text(section)
                if len(raw) > 40:
                    c.description = raw[:600]
            if c.description:
                break

    c.description = re.sub(r"<[^>]+>", "", c.description).strip()
    if len(c.description) > 500:
        c.description = c.description[:497] + "..."

    # Sub-ratings — structured HTML
    for container in soup.find_all(
        ["div", "li", "tr"],
        class_=re.compile(r"sub.?rating|rating.?param|review.?categ|parameter", re.I)
    ):
        label_tag = (
            container.find(class_=re.compile(r"label|categ|name|title", re.I))
            or container.find(["span", "p", "td"])
        )
        val_tag = (
            container.find(class_=re.compile(r"value|score|number|rating", re.I))
            or container.find(["strong", "b", "em"])
        )
        if label_tag and val_tag:
            _set_sub_rating(c, _text(label_tag), _rating_num(_text(val_tag)))

    # Sub-ratings — body-text regex fallback
    body = soup.get_text(" ", strip=True)
    for label, field in _RATING_MAP.items():
        if getattr(c, field):
            continue
        pattern = re.compile(re.escape(label) + r".{0,80}?(\d+\.\d+|\d)", re.I)
        m = pattern.search(body)
        if m:
            val = m.group(1)
            try:
                if 0 < float(val) <= 5:
                    setattr(c, field, val)
            except ValueError:
                pass

    # Metadata
    for item in soup.find_all(
        ["p", "span", "li", "div"],
        class_=re.compile(r"infoEntity|company.?info|meta.?item|detail", re.I)
    ):
        txt = _text(item).lower()
        if not c.founded and re.search(r"founded|established|since", txt):
            m = re.search(r"\b(19|20)\d{2}\b", txt)
            if m:
                c.founded = m.group()
        if not c.employees and re.search(r"employee|strength|headcount", txt):
            m = re.search(r"[\d,]+", txt)
            if m:
                c.employees = m.group().replace(",", "")
        if not c.headquarters and re.search(r"headquarter|hq\b|based in", txt):
            c.headquarters = _text(item)
        if not c.company_type and re.search(
            r"\b(public|private|mnc|startup|government|listed)\b", txt
        ):
            c.company_type = _text(item)

    if not c.founded:
        m = re.search(r"founded\s+in\s+((?:19|20)\d{2})", body, re.I)
        if m:
            c.founded = m.group(1)
    if not c.employees:
        m = re.search(r"([\d,]+)\+?\s*employees?", body, re.I)
        if m:
            c.employees = m.group(1).replace(",", "")

    return c

# ───────────────────────────────────────────────────────────────────────────
# ORCHESTRATION
# ───────────────────────────────────────────────────────────────────────────
def scrape_listings(session: requests.Session) -> list:
    all_entries = []
    seen_urls   = set()

    for page in range(1, PAGES_TO_SCRAPE + 1):
        url = f"{BASE_URL}{LISTING_PATH}?page={page}"
        log.info("Listing page %d/%d → %s", page, PAGES_TO_SCRAPE, url)
        soup = fetch_page(session, url)
        if soup is None:
            log.warning("Skipping listing page %d", page)
            _sleep()
            continue
        entries = parse_listing_page(soup)
        new = 0
        for e in entries:
            if e["profile_url"] not in seen_urls:
                seen_urls.add(e["profile_url"])
                all_entries.append(e)
                new += 1
        log.info("  → %d new companies (total: %d)", new, len(all_entries))
        _sleep()

    return all_entries


def scrape_details(session: requests.Session, listings: list) -> list:
    companies = []
    total     = len(listings)

    for idx, entry in enumerate(listings, 1):
        url  = entry["profile_url"]
        log.info("[%d/%d] %s", idx, total, url)
        soup = fetch_page(session, url)
        if soup is None:
            log.warning("  Partial data only (profile unreachable)")
            co = Company(name=entry["name"], profile_url=url)
        else:
            co = parse_company_page(soup, fallback_name=entry["name"])
            co.profile_url = url
            co.name        = co.name or entry["name"]
        companies.append(co)
        log.info("  rating=%-4s  reviews=%-6s  industry=%s",
                 co.overall_rating or "—",
                 co.total_reviews  or "—",
                 (co.industry[:40] + "...") if len(co.industry) > 40 else co.industry or "—")
        _sleep()

    return companies


def save_csv(companies: list, path: str):
    if not companies:
        log.error("Nothing to write.")
        return
    col_names = [f.name for f in fields(Company)]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=col_names)
        writer.writeheader()
        for co in companies:
            writer.writerow(asdict(co))
    log.info("CSV saved → %s  (%d rows)", path, len(companies))

# ───────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ───────────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 65)
    log.info("AmbitionBox Scraper  |  pages 1-%d  |  output: %s",
             PAGES_TO_SCRAPE, OUTPUT_CSV)
    log.info("=" * 65)

    session  = make_session()
    listings = scrape_listings(session)

    if not listings:
        log.error("No company links found — aborting.")
        return

    log.info("Total companies from listings: %d", len(listings))
    companies = scrape_details(session, listings)
    save_csv(companies, OUTPUT_CSV)

    rated    = sum(1 for c in companies if c.overall_rating)
    log.info("=" * 65)
    log.info("Done — rows: %d  |  with rating: %d  |  without: %d",
             len(companies), rated, len(companies) - rated)
    log.info("=" * 65)


if __name__ == "__main__":
    main()
