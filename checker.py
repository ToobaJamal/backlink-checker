import json
import re
import difflib
import os
import streamlit as st
from collections import Counter
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from groq import Groq

# ── Config ────────────────────────────────────────────────────────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY") or st.secrets.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY not set. Add it to a .env file locally, "
        "or to Streamlit Cloud's Secrets manager when deployed."
    )

client = Groq(api_key=GROQ_API_KEY)


GUEST_POST_SIGNALS = [
    "write for us", "submit a post", "submit a guest post",
    "become a contributor", "contribute to our blog",
    "we accept guest posts", "guest post guidelines",
    "advertise with us", "sponsored post",
]

SPAM_KEYWORDS = [
    "casino", "poker", "slots", "gambling", "adult", "xxx", "porn",
    "escort", "crypto", "bitcoin", "nft", "forex", "binance",
]

# Extra betting/gambling terms common in link-farm injections. Kept separate
# from SPAM_KEYWORDS (rather than merged) so the substring match doesn't
# collide with common English words.
SPAM_KEYWORDS_EXTENDED = [
    "judi", "togel", "gacor", "maxwin", "slot88", "situs slot",
    "situs judi", "rtp slot", "bandar togel", "sbobet", "joker123",
    "pragmatic play", "agen bola", "taruhan bola", "bet365",
]

# IPv4 addresses anywhere in the raw HTML (often hidden in comments, script
# blocks, or off-screen elements as part of a spam network's footprint).
IP_ADDRESS_PATTERN = re.compile(
    r'\b(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}'
    r'(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\b'
)

# Gibberish "word + digits" or "digits + word" tokens, e.g. "cyroket2585",
# "6666bet". This is deliberately generic so it isn't dependent on knowing
# the spam vocabulary in advance.
SPAM_TOKEN_PATTERN = re.compile(r'\b(?:[a-zA-Z]{3,15}\d{3,6}|\d{2,6}[a-zA-Z]{2,15})\b')

# Common legitimate tokens that would otherwise match SPAM_TOKEN_PATTERN.
SPAM_TOKEN_ALLOWLIST = {
    "covid19", "web3", "gpt4", "gpt3", "gpt5", "iphone15", "iphone16",
    "windows10", "windows11", "top10", "top5", "top100", "24x7", "24x365",
    "b2b", "b2c", "web2", "office365", "oauth2", "html5", "css3", "ps4",
    "ps5", "gta5", "gta6", "y2k", "utf8", "ipv4", "ipv6", "s3", "gpt2",
    "web4", "id3", "mp3", "mp4", "4k",
}

# CSS patterns used to visually hide text from users while keeping it in the DOM.
HIDDEN_STYLE_PATTERN = re.compile(
    r'(display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?:\.0+)?\b'
    r'|font-size\s*:\s*0(?:px)?\b|width\s*:\s*0(?:px)?\s*;\s*height\s*:\s*0)',
    re.IGNORECASE,
)
HIDDEN_CLASS_HINTS = ["sr-only", "visually-hidden", "visuallyhidden", "hidden", "d-none", "screen-reader-text"]

# JS patterns associated with back-button / history hijacking.
BACK_HIJACK_JS_PATTERNS = [
    re.compile(r"addEventListener\(\s*['\"](?:unload|beforeunload|pagehide)['\"]", re.IGNORECASE),
    re.compile(r"window\.onunload\s*=", re.IGNORECASE),
    re.compile(r"window\.onbeforeunload\s*=", re.IGNORECASE),
    re.compile(r"history\.pushState\([^)]*\)[\s\S]{0,200}?(setTimeout|location\.href|location\.replace)", re.IGNORECASE),
    re.compile(r"popstate[\s\S]{0,200}?(location\.href|location\.replace|window\.location\s*=)", re.IGNORECASE),
]


# ── Scraping ──────────────────────────────────────────────────────────────────

def fetch_page(url):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SiteChecker/1.0)"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    html = resp.text
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return html, text


def fetch_blog_posts_with_urls(base_url, limit=5):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SiteChecker/1.0)"}
    try:
        resp = requests.get(base_url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")

        links = []

        # 1. URL path segments that suggest a post
        path_signals = [
            "/blog/", "/post/", "/article/", "/news/", "/story/",
            "/opinion/", "/features/", "/press/", "/updates/",
        ]

        # 2. Date patterns in URL (e.g. /2024/03/, /2023/)
        date_pattern = re.compile(r'/20\d{2}/')

        for a in soup.find_all("a", href=True):
            href = a["href"]
            full_url = href if href.startswith("http") else base_url.rstrip("/") + href if href.startswith("/") else None
            if not full_url:
                continue
            if any(href.endswith(ext) for ext in [".jpg", ".png", ".pdf", ".gif", "#"]):
                continue
            if any(seg in full_url for seg in path_signals) or date_pattern.search(full_url):
                links.append(full_url)

        # 3. Fallback: any internal link with a long slug path
        if len(links) < 3:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                full_url = href if href.startswith("http") else base_url.rstrip("/") + href if href.startswith("/") else None
                if not full_url:
                    continue
                parsed = urlparse(full_url)
                path = parsed.path.rstrip("/")
                is_internal = urlparse(base_url).netloc in full_url
                is_long_slug = len(path) > 20 and path.count("/") >= 1
                is_not_category = not any(x in path for x in ["/tag/", "/category/", "/author/", "/page/"])
                if is_internal and is_long_slug and is_not_category:
                    links.append(full_url)

        links = list(dict.fromkeys(links))[:limit]

        posts = []
        for link in links:
            try:
                _, t = fetch_page(link)
                posts.append((link, t[:2000]))
            except Exception:
                continue
        return posts
    except Exception:
        return []


def fetch_blog_posts(base_url, limit=5):
    """Backwards-compatible wrapper: text excerpts only, no URLs."""
    return [text for _, text in fetch_blog_posts_with_urls(base_url, limit=limit)]


# ── SEMrush checks (disabled) ──────────────────────────────────────────────────
# Left fully commented out (every line prefixed) so the module stays valid
# Python even though these aren't wired into check_site() right now.

# def semrush_domain_overview(domain):  # costs 10 units per month
#     url = "https://api.semrush.com/"
#     params = {
#         "type": "domain_ranks",
#         "key": SEMRUSH_API_KEY,
#         "domain": domain,
#         "database": "us",
#         "export_columns": "Ot,Or",
#     }
#     resp = requests.get(url, params=params, timeout=15)
#     resp.raise_for_status()
#     lines = resp.text.strip().split("\n")
#     print("SEMrush raw response:", resp.text[:300])
#     if len(lines) < 2:
#         return {"organic_traffic": 0, "organic_keywords": 0}
#     headers_row = lines[0].split(";")
#     values_row = lines[1].split(";")
#     data = {k.strip(): v.strip() for k, v in zip(headers_row, values_row)}
#     return {
#         "organic_traffic": int(data.get("Organic Traffic", 0)),
#         "organic_keywords": int(data.get("Organic Keywords", 0)),
#     }


# def semrush_traffic_trend(domain, months=3):  # costs 40 units per month == 120 units
#     url = "https://api.semrush.com/"
#     params = {
#         "type": "domain_organic_organic",
#         "key": SEMRUSH_API_KEY,
#         "domain": domain,
#         "database": "us",
#         "display_limit": months,
#         "export_columns": "Dt,Ot",
#     }
#     resp = requests.get(url, params=params, timeout=15)
#     resp.raise_for_status()
#     lines = resp.text.strip().split("\n")
#     traffic = []
#     for line in lines[1:]:
#         parts = line.split(";")
#         if len(parts) >= 2:
#             try:
#                 traffic.append(int(parts[1]))
#             except ValueError:
#                 continue
#     return traffic


# def semrush_backlinks(domain, client_domain):  # culprit--my thought--skip this fn
#     url = "https://api.semrush.com/analytics/v1/"
#     clean_client = client_domain.replace("www.", "")
#     clean_domain = domain.replace("www.", "")
#     params = {
#         "key": SEMRUSH_API_KEY,
#         "type": "backlinks",
#         "target": clean_client,
#         "target_type": "root_domain",
#         "export_columns": "source_url",
#         "display_limit": 5000,
#     }
#     resp = requests.get(url, params=params, timeout=15)
#     resp.raise_for_status()
#     return clean_domain.lower() in resp.text.lower()


# ── Python-based checks ───────────────────────────────────────────────────────

def check_guest_post_signals(html, text):
    combined = (html + " " + text).lower()
    found = [sig for sig in GUEST_POST_SIGNALS if sig in combined]
    return {
        "passed": len(found) == 0,
        "signals_found": found,
    }


def extract_hidden_text(html):
    """Pull text out of elements that are hidden from users via CSS/attributes/
    common 'hidden' class names, so spam injected there doesn't slip past
    checks that only look at visible text."""
    soup = BeautifulSoup(html, "html.parser")
    hidden_chunks = []

    for tag in soup.find_all(style=True):
        if HIDDEN_STYLE_PATTERN.search(tag.get("style", "")):
            t = tag.get_text(" ", strip=True)
            if t:
                hidden_chunks.append(t)

    for tag in soup.find_all(attrs={"hidden": True}):
        t = tag.get_text(" ", strip=True)
        if t:
            hidden_chunks.append(t)

    for tag in soup.find_all(class_=True):
        classes = " ".join(tag.get("class", [])).lower()
        if any(hint in classes for hint in HIDDEN_CLASS_HINTS):
            t = tag.get_text(" ", strip=True)
            if t:
                hidden_chunks.append(t)

    return " ".join(hidden_chunks)


def check_spam_keywords_in_source(html):
    """Scans the RAW HTML (scripts, comments, attributes, hidden elements,
    link hrefs included) rather than the sanitized visible text, since that's
    exactly where spam networks hide injected IPs and gibberish keyword tokens
    like '6666bet' / 'cyroket2585'."""
    soup = BeautifulSoup(html, "html.parser")

    ip_addresses = sorted(set(IP_ADDRESS_PATTERN.findall(html)))

    spam_tokens = sorted({
        m.group(0) for m in SPAM_TOKEN_PATTERN.finditer(html)
        if m.group(0).lower() not in SPAM_TOKEN_ALLOWLIST
    })

    lower_html = html.lower()
    known_hits = sorted({kw for kw in SPAM_KEYWORDS + SPAM_KEYWORDS_EXTENDED if kw in lower_html})

    hidden_text = extract_hidden_text(html)
    hidden_hits = sorted({kw for kw in SPAM_KEYWORDS + SPAM_KEYWORDS_EXTENDED if kw in hidden_text.lower()})
    hidden_ips = sorted(set(IP_ADDRESS_PATTERN.findall(hidden_text)))

    spam_links = sorted({
        a["href"] for a in soup.find_all("a", href=True)
        if any(kw in a["href"].lower() for kw in SPAM_KEYWORDS + SPAM_KEYWORDS_EXTENDED)
        or SPAM_TOKEN_PATTERN.search(a["href"])
    })

    strong_signal = bool(known_hits or hidden_hits or spam_links)
    weak_signal = bool(ip_addresses or spam_tokens or hidden_ips)

    if strong_signal:
        passed = False
    elif weak_signal:
        passed = None  # ambiguous on its own (e.g. a stray IP) — flag for review
    else:
        passed = True

    return {
        "passed": passed,
        "ip_addresses_found": ip_addresses[:20],
        "spam_tokens_found": spam_tokens[:20],
        "known_keyword_hits": known_hits,
        "hidden_text_keyword_hits": hidden_hits,
        "hidden_text_ip_addresses": hidden_ips[:20],
        "spam_links_found": spam_links[:20],
    }


def _vowel_count(word):
    # Count a/e/i/o/u always, and 'y' only when it's not the first letter
    # (so real words like "rhythm"/"cryptocurrency" aren't undercounted).
    return sum(1 for i, c in enumerate(word) if c in "aeiou" or (c == "y" and i > 0))


def _is_gibberish_word(word):
    if len(word) < 3:
        return False
    vowels = _vowel_count(word)
    if vowels == 0:
        return True
    vowel_ratio = vowels / len(word)
    max_consonant_run = max((len(m) for m in re.findall(r'[^aeiouy]+', word)), default=0)
    if vowel_ratio < 0.25:
        return True
    if max_consonant_run >= 4 and max_consonant_run / len(word) >= 0.5:
        return True
    return False


def is_gibberish_slug(slug):
    """Heuristic: does this URL slug look like real word(s), or a randomly
    generated string? Segments are evaluated individually (split on
    hyphens/underscores/digits) rather than concatenated, so a legitimate
    multi-word slug like "best-running-shoes" isn't accidentally merged into
    "bestrunningshoes" and flagged on a fake cross-word consonant run."""
    segments = [s for s in re.split(r'[-_\d]+', slug.lower()) if s]
    if not segments:
        return False
    if len(segments) == 1:
        return _is_gibberish_word(segments[0])
    gibberish_segments = [s for s in segments if _is_gibberish_word(s)]
    return len(gibberish_segments) / len(segments) >= 0.6


def find_keyword_stuffing_clusters(text, min_count=3, min_cluster=2, similarity=0.75):
    """Groups repeated words that are near-identical in spelling (edit
    distance based). A cluster of 'raybans' / 'rayban' / 'raybanz' each
    repeated several times is a classic black-hat misspelling-stuffing
    pattern."""
    words = re.findall(r"[a-zA-Z']{4,}", text.lower())
    counts = Counter(words)
    candidates = sorted(w for w, c in counts.items() if c >= min_count)

    clusters = []
    used = set()
    for i, w1 in enumerate(candidates):
        if w1 in used:
            continue
        cluster = [w1]
        for w2 in candidates[i + 1:]:
            if w2 in used:
                continue
            if difflib.SequenceMatcher(None, w1, w2).ratio() >= similarity:
                cluster.append(w2)
                used.add(w2)
        if len(cluster) >= min_cluster:
            clusters.append(cluster)
            used.add(w1)
    return clusters


# ── LLM checks ─────────────────────────────────────────────────────────

def llm_check(prompt):
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content.strip()


def _parse_llm_json(raw):
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    return json.loads(match.group() if match else cleaned)


def check_spam_content(text):
    snippet = text[:4000]
    prompt = f"""You are a content quality checker for a link-building agency. You are an expert in SEO best practices and know which links can destroy a client's brand in the long run.

Read the following website text and answer:
1. Does it contain adult, pornographic, or escort content?
2. Does it contain gambling or casino content?
3. Does it contain crypto, NFT, or forex content (unless the site is clearly a legitimate finance publication)?

You MUST reply ONLY with a valid JSON object and nothing else. No explanation, no preamble.
Format: {{"adult": true/false, "gambling": true/false, "crypto": true/false, "reason": "one sentence"}}

Website text:
{snippet}"""

    raw = llm_check(prompt)
    try:
        result = _parse_llm_json(raw)
        spam_detected = result.get("adult") or result.get("gambling") or result.get("crypto")
        return {
            "passed": not spam_detected,
            "adult": result.get("adult", False),
            "gambling": result.get("gambling", False),
            "crypto": result.get("crypto", False),
            "reason": result.get("reason", ""),
        }
    except (json.JSONDecodeError, AttributeError):
        found = [kw for kw in SPAM_KEYWORDS if kw in text.lower()]
        return {"passed": len(found) == 0, "fallback": True, "keywords_found": found}


def check_hidden_spam(html):
    snippet = html[:5000]
    prompt = f"""You are a technical SEO auditor checking for spam manipulation.

Inspect this raw HTML and identify:
1. Links hidden with CSS (display:none, visibility:hidden, opacity:0, font-size:0, color matching background)
2. Keyword stuffing hidden from view
3. Suspicious outbound links to gambling, adult, or crypto sites

You MUST reply ONLY with a valid JSON object and nothing else. No explanation, no preamble.
Format: {{"hidden_spam_detected": true/false, "reason": "one sentence"}}

HTML:
{snippet}"""

    raw = llm_check(prompt)
    try:
        result = _parse_llm_json(raw)
        reason = result.get("reason", "")
        if not reason or reason.lower() == "none":
            reason = "No hidden spam detected"
        return {
            "passed": not result.get("hidden_spam_detected", False),
            "reason": reason,
        }
    except (json.JSONDecodeError, AttributeError):
        return {"passed": True, "fallback": True, "reason": "Could not parse LLM response"}


def check_keyword_stuffing(text):
    clusters = find_keyword_stuffing_clusters(text)
    if not clusters:
        return {"passed": True, "suspicious_clusters": []}

    sample = "\n".join(f"- {', '.join(c)}" for c in clusters[:10])
    prompt = f"""You are auditing a website for black-hat SEO keyword stuffing via intentional misspellings.

Below are clusters of similarly-spelled words that each appear multiple times on the page. Some clusters are innocent (plurals, verb tenses, common near-synonyms). Others are a deliberate tactic where a target keyword is repeated with slight misspellings to rank for more search variants.

Clusters found:
{sample}

You MUST reply ONLY with a valid JSON object and nothing else. No explanation, no preamble.
Format: {{"keyword_stuffing_detected": true/false, "reason": "one sentence"}}"""

    raw = llm_check(prompt)
    try:
        result = _parse_llm_json(raw)
        return {
            "passed": not result.get("keyword_stuffing_detected", False),
            "suspicious_clusters": clusters[:10],
            "reason": result.get("reason", ""),
        }
    except (json.JSONDecodeError, AttributeError):
        return {
            "passed": len(clusters) == 0,
            "suspicious_clusters": clusters[:10],
            "fallback": True,
        }


def check_topic_drift(base_url):
    posts = fetch_blog_posts(base_url)
    if not posts:
        return {"passed": None, "reason": "Could not find blog posts to sample"}

    combined = "\n\n---\n\n".join(posts)
    prompt = f"""You are evaluating a website for a link building campaign.

Below are excerpts from {len(posts)} blog posts on this site. Judge whether the site has a clear, consistent main topic or drifts wildly across unrelated subjects.

A site that covers many loosely related topics in one niche is fine.
A site that randomly covers cooking, crypto, travel, and legal advice is a red flag.

You MUST reply ONLY with a valid JSON object and nothing else. No explanation, no preamble.
Format: {{"consistent_topic": true/false, "main_topic": "one sentence", "reason": "one sentence of valid reson. Don't repeat sentences above."}}

Blog excerpts:
{combined[:6000]}"""

    raw = llm_check(prompt)
    try:
        result = _parse_llm_json(raw)
        return {
            "passed": result.get("consistent_topic", False),
            "main_topic": result.get("main_topic", ""),
            "reason": result.get("reason", ""),
        }
    except (json.JSONDecodeError, AttributeError):
        return {"passed": None, "reason": "Could not parse LLM response"}


def check_ai_content_farm(base_url):
    """Flags AI content-farm behavior: nonsense/misspelled URL slugs paired
    with articles that hallucinate a topic for that fake keyword."""
    posts = fetch_blog_posts_with_urls(base_url)
    if not posts:
        return {"passed": None, "reason": "Could not find blog posts to sample"}

    flagged_slugs = []
    excerpt_parts = []
    for link, text in posts:
        path = urlparse(link).path.rstrip("/")
        slug = path.split("/")[-1] if path else ""
        if slug and is_gibberish_slug(slug):
            flagged_slugs.append(slug)
        excerpt_parts.append(f"URL: {link}\nSlug: {slug}\nContent: {text[:1200]}")

    combined = "\n\n---\n\n".join(excerpt_parts)

    prompt = f"""You are an expert at detecting AI-generated content farms used in black-hat link building.

Below are {len(posts)} blog posts (URL, slug, and content excerpt) from a website.

Signs of an AI content farm to look for:
- The URL slug is a nonsense or intentionally misspelled string with no real meaning (e.g. "xlecz", "qwrtzop")
- The article "hallucinates" a definition or topic for that nonsense keyword and writes generic filler content around it
- The writing is generic, repetitive, templated, or doesn't cite real, checkable facts
- Multiple posts follow the same hollow pattern, just with a different nonsense keyword swapped in

You MUST reply ONLY with a valid JSON object and nothing else. No explanation, no preamble.
Format: {{"is_ai_content_farm": true/false, "confidence": "low/medium/high", "reason": "one to two sentences citing specific evidence from the excerpts"}}

Posts:
{combined[:7000]}"""

    raw = llm_check(prompt)
    llm_flag = None
    result = {}
    try:
        result = _parse_llm_json(raw)
        llm_flag = result.get("is_ai_content_farm", False)
    except (json.JSONDecodeError, AttributeError):
        pass

    slug_ratio = len(flagged_slugs) / len(posts) if posts else 0
    heuristic_flag = slug_ratio >= 0.4  # ~2+ of 5 slugs look like gibberish

    if llm_flag is None:
        passed = not heuristic_flag
        reason = "LLM response unparsable; fell back to gibberish-slug heuristic only."
    else:
        passed = not (llm_flag or heuristic_flag)
        reason = result.get("reason", "")

    return {
        "passed": passed,
        "gibberish_slugs_found": flagged_slugs,
        "gibberish_slug_ratio": round(slug_ratio, 2),
        "llm_flag": llm_flag,
        "confidence": result.get("confidence", ""),
        "reason": reason,
    }


# ── Back-button hijack checks ──────────────────────────────────────────────────

def check_back_button_hijack_static(html):
    """Cheap static pass: looks for JS patterns commonly used to hijack the
    back button (unload/popstate listeners that force a redirect). This is a
    heuristic only — presence doesn't prove hijacking, and absence doesn't
    rule it out (see check_back_button_hijack_dynamic for a real test)."""
    hits = [pat.pattern for pat in BACK_HIJACK_JS_PATTERNS if pat.search(html)]
    return {
        "passed": len(hits) == 0,
        "suspicious_js_patterns_found": len(hits),
        "note": (
            "Static pattern match only. For a real behavioral test, run "
            "check_back_button_hijack_dynamic() (requires Playwright)."
        ),
    }


def check_back_button_hijack_dynamic(url, timeout_ms=15000):
    """Actually opens the page in a headless browser, clicks an internal
    link, presses back, and checks whether the browser ended up on a
    different domain than it started on — which is what back-button
    hijacking does in practice."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "passed": None,
            "reason": (
                "playwright not installed. Run `pip install playwright` and "
                "`playwright install chromium` to enable this check."
            ),
        }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="load")
            start_url = page.url
            start_domain = urlparse(start_url).netloc

            raw_links = page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => e.href).filter(h => h.startsWith('http'))",
            )
            internal_links = [
                l for l in dict.fromkeys(raw_links)
                if urlparse(l).netloc == start_domain and l.rstrip("/") != start_url.rstrip("/")
            ]

            if not internal_links:
                browser.close()
                return {"passed": None, "reason": "No internal link found to click for the test"}

            target = internal_links[0]
            page.goto(target, timeout=timeout_ms, wait_until="load")
            page.go_back(timeout=timeout_ms, wait_until="load")
            page.wait_for_timeout(2000)  # give any hijack script time to fire

            end_url = page.url
            end_domain = urlparse(end_url).netloc
            browser.close()

            hijacked = end_domain != start_domain
            return {
                "passed": not hijacked,
                "start_url": start_url,
                "clicked": target,
                "url_after_back": end_url,
                "hijacked": hijacked,
            }
    except Exception as e:
        return {"passed": None, "reason": f"Dynamic check failed: {e}"}


# ── Main functions ───────────────────────────────────────────────────────────────

def check_site(url, run_dynamic_back_button_check=True):
    domain = urlparse(url).netloc.replace("www.", "")

    print(f"\n{'='*55}")
    print(f"  Checking: {url}")
    print(f"{'='*55}\n")

    results = {}

    # 1. Fetch the page
    print("Fetching page...")
    try:
        html, text = fetch_page(url)
    except Exception as e:
        return {"error": f"Could not fetch page: {e}"}

    # 2-4. SEMrush checks (disabled — see commented functions above)

    # 5. Guest post signals
    print("Checking for guest post / link farm signals...")
    gp = check_guest_post_signals(html, text)
    results["guest_post_signals"] = gp
    _print_check("Not a guest post network", gp)

    # 6. Spam keywords/IPs hidden in raw page source + spam links
    print("Checking for spam keywords/IPs in page source...")
    src_spam = check_spam_keywords_in_source(html)
    results["spam_keywords_in_source"] = src_spam
    _print_check("No spam keywords/IPs in source", src_spam)

    # 7. LLM: spam content (visible text)
    print("Checking spam content...")
    spam = check_spam_content(text)
    results["spam_content"] = spam
    _print_check("No spam content", spam)

    # 8. LLM: hidden spam links
    print("Checking hidden spam links...")
    hidden = check_hidden_spam(html)
    results["hidden_spam"] = hidden
    _print_check("No hidden spam links", hidden)

    # 9. Keyword stuffing via intentional misspellings
    print("Checking for misspelling-based keyword stuffing...")
    stuffing = check_keyword_stuffing(text)
    results["keyword_stuffing"] = stuffing
    _print_check("No misspelling keyword stuffing", stuffing)

    # 10. LLM: topic drift
    print("Checking topic consistency...")
    topic = check_topic_drift(url)
    results["topic_drift"] = topic
    _print_check("Topic is consistent", topic)

    # 11. AI content farm / hallucinated content on gibberish keywords
    print("Checking for AI content farm signals...")
    ai_farm = check_ai_content_farm(url)
    results["ai_content_farm"] = ai_farm
    _print_check("Not an AI content farm", ai_farm)

    # 12. Back-button hijacking (static)
    print("Checking for back-button hijacking (static)...")
    back_static = check_back_button_hijack_static(html)
    results["back_button_hijack_static"] = back_static
    _print_check("No back-button hijack JS patterns", back_static)

    # 13. Back-button hijacking (dynamic, requires Playwright)
    if run_dynamic_back_button_check:
        print("Checking for back-button hijacking (live browser test)...")
        back_dynamic = check_back_button_hijack_dynamic(url)
        results["back_button_hijack_dynamic"] = back_dynamic
        _print_check("Back button returns to same site", back_dynamic)

    # Final verdict
    failed = [k for k, v in results.items() if v.get("passed") is False]
    needs_review = [k for k, v in results.items() if v.get("passed") is None]

    verdict = "PASS" if not failed else "FAIL"
    if not failed and needs_review:
        verdict = "REVIEW"

    results["_verdict"] = verdict
    results["_failed_checks"] = failed
    results["_needs_review"] = needs_review

    print(f"\n{'='*55}")
    print(f"  VERDICT: {verdict}")
    if failed:
        print(f"  Failed:  {', '.join(failed)}")
    if needs_review:
        print(f"  Review:  {', '.join(needs_review)}")
    print(f"{'='*55}\n")

    return results


def _print_check(label, result):
    status = result.get("passed")
    icon = "PASS" if status is True else "FAIL" if status is False else "REVIEW"
    reason = result.get("reason") or result.get("signals_found") or result.get("error") or ""
    print(f"  [{icon}] {label}" + (f" — {reason}" if reason else ""))
