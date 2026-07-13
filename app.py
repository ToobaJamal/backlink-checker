import streamlit as st
import json
from checker import check_site

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Backlink Checker — GMP",
    page_icon="⚡",
    layout="centered",
)

# ── Branding & styles ─────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Sans:wght@300;400;500&display=swap');

:root {
    --navy:   #0B0A27;
    --red:    #D0303A;
    --light:  #F0F0F0;
    --white:  #FFFFFF;
}

html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif;
    background-color: var(--navy);
    color: var(--white);
}

/* Hide default streamlit chrome */
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding-top: 2rem; max-width: 780px; }

/* Header */
.gmp-header {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-bottom: 2.5rem;
    padding-bottom: 1.5rem;
    border-bottom: 1px solid rgba(255,255,255,0.08);
}
.gmp-title {
    font-family: 'Syne', sans-serif;
    font-size: 1.1rem;
    font-weight: 600;
    color: #0B0A27;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    margin: 0;
}
.gmp-subtitle {
    font-size: 0.78rem;
    color: rgba(255,255,255,0.4);
    margin: 2px 0 0;
}

/* Inputs */
.stTextInput > div > div > input {
    background: rgba(255,255,255,0.05) !important;
    border: 1px solid rgba(255,255,255,0.12) !important;
    border-radius: 8px !important;
    color: #0B0A27 !important;
    font-family: 'DM Sans', sans-serif !important;
    padding: 0.6rem 1rem !important;
}
.stTextInput > div > div > input:focus {
    border-color: var(--red) !important;
    box-shadow: 0 0 0 2px rgba(208,48,58,0.2) !important;
}
label { color: var(--navy) !important; font-size: 0.82rem !important; }

/* Button */
.stButton > button {
    background: var(--red) !important;
    color: white !important;
    border: none !important;
    border-radius: 8px !important;
    font-family: 'Syne', sans-serif !important;
    font-weight: 700 !important;
    font-size: 0.9rem !important;
    letter-spacing: 0.04em !important;
    padding: 0.65rem 2rem !important;
    width: 100% !important;
    transition: opacity 0.2s !important;
}
.stButton > button:hover { opacity: 0.85 !important; }

/* Check result cards */
.check-card {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px;
    padding: 14px 18px;
    margin-bottom: 10px;
    display: flex;
    align-items: flex-start;
    gap: 14px;
}
.check-card.pass { border-left: 3px solid #2ecc71; }
.check-card.fail { border-left: 3px solid var(--red); }
.check-card.review { border-left: 3px solid #f39c12; }

.check-icon { font-size: 1rem; margin-top: 1px; flex-shrink: 0; }
.check-label {
    font-family: 'Syne', sans-serif;
    font-size: 0.88rem;
    font-weight: 600;
    color: var(--navy);
    margin-bottom: 2px;
}
.check-reason { font-size: 0.78rem; color: rgba(11,10,39,0.45); line-height: 1.4; }

/* Verdict banner */
.verdict-pass {
    background: rgba(46,204,113,0.12);
    border: 1px solid rgba(46,204,113,0.3);
    border-radius: 10px;
    padding: 20px 24px;
    text-align: center;
    margin-bottom: 1.5rem;
}
.verdict-fail {
    background: rgba(208,48,58,0.12);
    border: 1px solid rgba(208,48,58,0.3);
    border-radius: 10px;
    padding: 20px 24px;
    text-align: center;
    margin-bottom: 1.5rem;
}
.verdict-review {
    background: rgba(243,156,18,0.12);
    border: 1px solid rgba(243,156,18,0.3);
    border-radius: 10px;
    padding: 20px 24px;
    text-align: center;
    margin-bottom: 1.5rem;
}
.verdict-label {
    font-family: 'Syne', sans-serif;
    font-size: 1.6rem;
    font-weight: 800;
    letter-spacing: 0.08em;
}
.verdict-sub { font-size: 0.82rem; color: rgba(11,10,39,0.5); margin-top: 4px; }

/* Section label */
.section-label {
    font-family: 'Syne', sans-serif;
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: rgba(255,255,255,0.3);
    margin: 1.5rem 0 0.75rem;
}
</style>
""", unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────────────

col1, col2 = st.columns([1, 4])
with col1:
    st.image("logo.webp", width=80)
with col2:
    st.markdown("""
    <div style="padding-top:8px">
        <p class="gmp-title">Backlink Checker</p>
    </div>
    """, unsafe_allow_html=True)

st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

# ── Input form ────────────────────────────────────────────────────────────────

url = st.text_input("Site URL to vet", placeholder="https://example.com")

run = st.button("Run checks →")

# ── Results ───────────────────────────────────────────────────────────────────

CHECK_LABELS = {
    "guest_post_signals":        "Not a guest post network",
    "spam_keywords_in_source":   "No spam keywords/IPs in source",
    "spam_content":              "No spam content",
    "hidden_spam":                "No hidden spam links",
    "keyword_stuffing":          "No misspelling keyword stuffing",
    "topic_drift":               "Topic is consistent",
    "ai_content_farm":           "Not an AI content farm",
    "back_button_hijack_static": "No back-button hijack JS patterns",
    "back_button_hijack_dynamic":"Back button returns to same site",
}

def get_reason(key, result):
    if key == "guest_post_signals":
        if result.get("signals_found"):
            return f"Found: {', '.join(result['signals_found'])}"
        return ""
    if key == "spam_keywords_in_source":
        parts = []
        if result.get("known_keyword_hits"):
            parts.append(f"Keywords: {', '.join(result['known_keyword_hits'])}")
        if result.get("hidden_text_keyword_hits"):
            parts.append(f"Hidden keywords: {', '.join(result['hidden_text_keyword_hits'])}")
        if result.get("spam_tokens_found"):
            parts.append(f"Suspicious tokens: {', '.join(result['spam_tokens_found'][:5])}")
        if result.get("ip_addresses_found"):
            parts.append(f"{len(result['ip_addresses_found'])} IP(s) in source")
        if result.get("spam_links_found"):
            parts.append("Spam link(s) found")
        return " · ".join(parts) if parts else ""
    if key == "keyword_stuffing":
        clusters = result.get("suspicious_clusters")
        if clusters:
            return "Clusters: " + "; ".join(", ".join(c) for c in clusters[:3])
        return result.get("reason", "")
    if key == "ai_content_farm":
        parts = []
        if result.get("gibberish_slugs_found"):
            parts.append(f"Gibberish slugs: {', '.join(result['gibberish_slugs_found'])}")
        if result.get("reason"):
            parts.append(result["reason"])
        return " · ".join(parts) if parts else ""
    if key == "back_button_hijack_static":
        n = result.get("suspicious_js_patterns_found", 0)
        return f"{n} suspicious JS pattern(s)" if n else ""
    if key == "back_button_hijack_dynamic":
        if result.get("hijacked"):
            return f"Redirected to {result.get('url_after_back')}"
        return result.get("reason", "")
    signals = result.get("signals_found")
    if signals:
        return f"Found: {', '.join(signals)}"
    return result.get("reason") or result.get("error") or ""


if run:
    if not url:
        st.warning("Please enter a URL.")
    else:
        with st.spinner("Running checks..."):
            results = check_site(url=url, run_dynamic_back_button_check=False)

        if "error" in results:
            st.error(f"Could not fetch page: {results['error']}")
        else:
            verdict = results.get("_verdict", "REVIEW")
            failed = results.get("_failed_checks", [])
            review = results.get("_needs_review", [])

            # Verdict banner
            if verdict == "PASS":
                st.markdown(f"""
                <div class="verdict-pass">
                    <div class="verdict-label">Wowza! Great choice 😁</div>
                    <div class="verdict-sub">This site passed all checks</div>
                </div>""", unsafe_allow_html=True)
            elif verdict == "FAIL":
                failed_labels = ", ".join(CHECK_LABELS.get(k, k) for k in failed)
                st.markdown(f"""
                <div class="verdict-fail">
                    <div class="verdict-label">Likely not the best fit 😔</div>
                    <div class="verdict-sub">Failed: {failed_labels}</div>
                </div>""", unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div class="verdict-review">
                    <div class="verdict-label">⚠ REVIEW</div>
                    <div class="verdict-sub">Some checks need manual review</div>
                </div>""", unsafe_allow_html=True)

            # Check cards
            st.markdown('<div class="section-label">Check results</div>', unsafe_allow_html=True)

            for key, label in CHECK_LABELS.items():
                if key not in results:
                    continue
                result = results[key]
                status = result.get("passed")
                if status is True:
                    css_class, icon = "pass", "✓"
                elif status is False:
                    css_class, icon = "fail", "✕"
                else:
                    css_class, icon = "review", "⚠"

                reason = get_reason(key, result)
                reason_html = f'<div class="check-reason">{reason}</div>' if reason and str(reason).strip() else ""

                st.markdown(f"""
                <div class="check-card {css_class}">
                    <div class="check-icon">{icon}</div>
                    <div><div class="check-label">{label}</div>{reason_html}</div>
                </div>""", unsafe_allow_html=True)

            # Raw JSON expander
            with st.expander("Raw JSON output"):
                st.code(json.dumps(results, indent=2), language="json")
