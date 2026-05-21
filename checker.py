import json
import re
import requests
from bs4 import BeautifulSoup
from groq import Groq

import os
# ── Config ────────────────────────────────────────────────────────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY") 
client = Groq(api_key=GROQ_API_KEY)
# model = genai.GenerativeModel("gemini-2.5-flash")
# SEMRUSH_API_KEY = os.environ.get("SEMRUSH_API_KEY")

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


def fetch_blog_posts(base_url, limit=5):
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
            # Skip same page anchors, images, PDFs
            if any(href.endswith(ext) for ext in [".jpg", ".png", ".pdf", ".gif", "#"]):
                continue
            # Match path signals or date pattern
            if any(seg in full_url for seg in path_signals) or date_pattern.search(full_url):
                links.append(full_url)

        # 3. Fallback: any internal link with a long slug path
        if len(links) < 3:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                full_url = href if href.startswith("http") else base_url.rstrip("/") + href if href.startswith("/") else None
                if not full_url:
                    continue
                from urllib.parse import urlparse
                parsed = urlparse(full_url)
                path = parsed.path.rstrip("/")
                # Must be internal, have a meaningful path, not a category/tag page
                is_internal = urlparse(base_url).netloc in full_url
                is_long_slug = len(path) > 20 and path.count("/") >= 1
                is_not_category = not any(x in path for x in ["/tag/", "/category/", "/author/", "/page/"])
                if is_internal and is_long_slug and is_not_category:
                    links.append(full_url)
        
        links = list(dict.fromkeys(links))[:limit]

        texts = []
        for link in links:
            try:
                _, t = fetch_page(link)
                texts.append(t[:2000])
            except Exception:
                continue
        return texts
    except Exception:
        return []


# ── SEMrush checks ────────────────────────────────────────────────────────────

# def semrush_domain_overview(domain): #costs 10 units per month
    url = "https://api.semrush.com/"
    params = {
        "type": "domain_ranks",
        "key": SEMRUSH_API_KEY,
        "domain": domain,
        "database": "us",
        "export_columns": "Ot,Or",
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    lines = resp.text.strip().split("\n")
    print("SEMrush raw response:", resp.text[:300])
    if len(lines) < 2:
        return {"organic_traffic": 0, "organic_keywords": 0}
    headers_row = lines[0].split(";")
    values_row = lines[1].split(";")
    data = {k.strip(): v.strip() for k, v in zip(headers_row, values_row)}
    return {
        "organic_traffic": int(data.get("Organic Traffic", 0)),
        "organic_keywords": int(data.get("Organic Keywords", 0)),
    }


# def semrush_traffic_trend(domain, months=3):  #costs 40 units per month == 120 units
    url = "https://api.semrush.com/"
    params = {
        "type": "domain_organic_organic",
        "key": SEMRUSH_API_KEY,
        "domain": domain,
        "database": "us",
        "display_limit": months,
        "export_columns": "Dt,Ot",
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    lines = resp.text.strip().split("\n")
    traffic = []
    for line in lines[1:]:
        parts = line.split(";")
        if len(parts) >= 2:
            try:
                traffic.append(int(parts[1]))
            except ValueError:
                continue
    return traffic


# def semrush_backlinks(domain, client_domain): #culprit--my thought--skip this fn
    url = "https://api.semrush.com/analytics/v1/"
    clean_client = client_domain.replace("www.", "")
    clean_domain = domain.replace("www.", "")
    params = {
        "key": SEMRUSH_API_KEY,
        "type": "backlinks",
        "target": clean_client,
        "target_type": "root_domain",
        "export_columns": "source_url",
        "display_limit": 5000,
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return clean_domain.lower() in resp.text.lower()


# ── Python-based checks ───────────────────────────────────────────────────────

def check_guest_post_signals(html, text):
    combined = (html + " " + text).lower()
    found = [sig for sig in GUEST_POST_SIGNALS if sig in combined]
    return {
        "passed": len(found) == 0,
        "signals_found": found,
    }


# ── LLM checks ─────────────────────────────────────────────────────────

def llm_check(prompt):
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content.strip()


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
        raw = re.sub(r"```(?:json)?", "", raw).strip()
        match = re.search(r'\{{.*\}}', raw, re.DOTALL)
        result = json.loads(match.group() if match else raw)
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
        raw = re.sub(r"```json|```", "", raw).strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        result = json.loads(match.group() if match else raw)
        reason = result.get("reason", "")
        if not reason or reason.lower() == "none":
            reason = "No hidden spam detected"
        return {
            "passed": not result.get("hidden_spam_detected", False),
            "reason": reason,
        }
    except (json.JSONDecodeError, AttributeError):
        return {"passed": True, "fallback": True, "reason": "Could not parse LLM response"}


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
        raw = re.sub(r"```(?:json)?", "", raw).strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        result = json.loads(match.group() if match else raw)
        return {
            "passed": result.get("consistent_topic", False),
            "main_topic": result.get("main_topic", ""),
            "reason": result.get("reason", ""),
        }
    except (json.JSONDecodeError, AttributeError):
        return {"passed": None, "reason": "Could not parse LLM response"}


# ── Main runner ───────────────────────────────────────────────────────────────

def check_site(url, client_domain):
    from urllib.parse import urlparse
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

    # 2. SEMrush: traffic & keywords
    # print("Checking traffic & keywords (SEMrush)...")
    # try:
    #     overview = semrush_domain_overview(domain)
    #     results["traffic"] = {
    #         "passed": overview["organic_traffic"] >= 10_000,
    #         "organic_traffic": overview["organic_traffic"],
    #         "required": 10_000,
    #     }
    #     results["keywords"] = {
    #         "passed": overview["organic_keywords"] >= 100,
    #         "organic_keywords": overview["organic_keywords"],
    #         "required": 100,
    #     }
    #     _print_check("Traffic >= 10K", results["traffic"])
    #     _print_check("Keywords >= 100", results["keywords"])
    # except Exception as e:
    #     results["traffic"] = {"passed": None, "error": str(e)}
    #     results["keywords"] = {"passed": None, "error": str(e)}
    #     print(f"  [!] SEMrush error: {e}")

    # 3. SEMrush: traffic trend
    # print("Checking traffic trend (SEMrush)...")
    # try:
    #     trend = semrush_traffic_trend(domain)
    #     trending_down = len(trend) >= 2 and trend[0] < trend[-1] * 0.7
    #     results["traffic_trend"] = {
    #         "passed": not trending_down,
    #         "monthly_traffic": trend,
    #         "trending_down": trending_down,
    #     }
    #     _print_check("Traffic not declining", results["traffic_trend"])
    # except Exception as e:
    #     results["traffic_trend"] = {"passed": None, "error": str(e)}
    #     print(f"  [!] SEMrush trend error: {e}")

    # 4. SEMrush: existing backlink
# print(f"Checking existing backlink to {client_domain}...")
#     try:
#         already_linked = semrush_backlinks(domain, client_domain)
#         results["existing_backlink"] = {
#             "passed": not already_linked,
#             "already_linked": already_linked,
#         }
#         label = "Found existing backlink" if already_linked else "No existing backlink"
#         _print_check(label, results["existing_backlink"])
#     except Exception as e:
#         results["existing_backlink"] = {"passed": None, "error": str(e)}
#         print(f"  [!] Backlink check error: {e}") 

    # 5. Guest post signals
    print("Checking for guest post / link farm signals...")
    gp = check_guest_post_signals(html, text)
    results["guest_post_signals"] = gp
    _print_check("Not a guest post network", gp) 

    # 6. LLM: spam content
    print("Checking spam content...")
    spam = check_spam_content(text)
    results["spam_content"] = spam
    _print_check("No spam content", spam)

    # 7. LLM: hidden spam links
    print("Checking hidden spam links...")
    hidden = check_hidden_spam(html)
    results["hidden_spam"] = hidden
    _print_check("No hidden spam links", hidden)

    # 8. LLM: topic drift
    print("Checking topic consistency...")
    topic = check_topic_drift(url)
    results["topic_drift"] = topic
    _print_check("Topic is consistent", topic)

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