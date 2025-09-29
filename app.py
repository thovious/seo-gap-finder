"""
Local SEO Sitemap Gap Analyzer — MVP (Streamlit)
Author: ChatGPT for Tim Hovious

How to run locally:
1) Save this file as app.py
2) Create a virtual environment (optional)
3) Install deps:  pip install -r requirements.txt
   If you don't want a separate file, install directly: pip install streamlit requests beautifulsoup4 tldextract python-slugify
4) Launch:  streamlit run app.py

Optional deploy:
- Streamlit Community Cloud: push to GitHub with app.py + requirements.txt (see REQUIREMENTS block below) and deploy.

REQUIREMENTS (put these lines into a requirements.txt file if desired):
streamlit
requests
beautifulsoup4
lxml
tldextract
python-slugify

Notes:
- This is an MVP intended for small/medium sites. It tries sitemap.xml first, then a light crawl fallback (same-domain, capped pages).
- The “ideal sitemap” is rules-based: base pages + (service x city) combos + optional city hubs + optional service hubs.
- Priority scoring is heuristic (service-city > service-only > city hub > support pages).
- Output: on-screen tables + CSV downloads.
- You can add CTAs, branding, and email gate around the results page.
"""

import re
import io
import time
from urllib.parse import urljoin, urlparse
import gzip

import requests
from bs4 import BeautifulSoup
import streamlit as st
from slugify import slugify
import tldextract

############################
# ---------- Utils ---------
############################

def normalize_domain(url: str) -> str:
    if not re.match(r"^https?://", url):
        url = "https://" + url.strip()
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def fetch(url: str, timeout: int = 10):
    try:
        return requests.get(url, timeout=timeout, headers={
            "User-Agent": "LocalSEO-Gap-Analyzer/0.1 (+https://example.com)"
        })
    except Exception:
        return None


def read_sitemap_xml(base_url: str):
    """Return list of URLs from /sitemap.xml if found (recursively handles sitemap indexes)."""
    urls = set()
    tried = set()

    def _parse_sitemap(url):
        if url in tried:
            return
        tried.add(url)
        resp = fetch(url)
        if not resp or resp.status_code != 200 or "xml" not in resp.headers.get("Content-Type", ""):
            return
        soup = BeautifulSoup(resp.text, "lxml-xml")
        # If it's a sitemap index
        for loc in soup.find_all("sitemap"):
            loc_url = loc.find("loc")
            if loc_url and loc_url.text:
                _parse_sitemap(loc_url.text.strip())
        # Regular urlset
        for loc in soup.find_all("url"):
            loc_url = loc.find("loc")
            if loc_url and loc_url.text:
                urls.add(loc_url.text.strip())

    _parse_sitemap(urljoin(base_url, "/sitemap.xml"))
    return sorted(urls)


def same_domain(url_a: str, url_b: str) -> bool:
    a = tldextract.extract(url_a)
    b = tldextract.extract(url_b)
    return (a.domain, a.suffix) == (b.domain, b.suffix)


def light_crawl(base_url: str, max_pages: int = 150):
    """Simple BFS crawl within the same domain; returns list of discovered URLs and page titles."""
    start = base_url
    visited = set()
    queue = [start]
    found = []

    while queue and len(visited) < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        resp = fetch(url)
        if not resp or resp.status_code != 200:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        title = (soup.title.string.strip() if soup.title and soup.title.string else "").strip()
        found.append({"url": url, "title": title})
        # enqueue links
        for a in soup.select("a[href]"):
            href = a.get("href").strip()
            # make absolute
            abs_url = urljoin(url, href)
            # keep within same domain
            if not same_domain(abs_url, base_url):
                continue
            # drop fragments/mailto/tel
            if any(abs_url.startswith(s) for s in ["mailto:", "tel:"]):
                continue
            if "#" in abs_url:
                abs_url = abs_url.split("#")[0]
            # throttle queue
            if abs_url not in visited and abs_url not in queue and len(visited) + len(queue) < max_pages:
                queue.append(abs_url)
        time.sleep(0.05)

    return found


def get_site_inventory(base_url: str):
    """Return list of dicts: [{url, title}] for discovered pages."""
    pages = []
    # Try sitemap first
    sm_urls = read_sitemap_xml(base_url)
    if sm_urls:
        for u in sm_urls[:500]:  # hard cap
            resp = fetch(u)
            if resp and resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                title = (soup.title.string.strip() if soup.title and soup.title.string else "").strip()
                pages.append({"url": u, "title": title})
        return pages
    # Fallback: light crawl
    return light_crawl(base_url, max_pages=150)


def to_list_from_text(text: str):
    if not text:
        return []
    # allow comma, newline, semicolon splitting
    parts = re.split(r"[,\n;]+", text)
    cleaned = [p.strip() for p in parts if p.strip()]
    return cleaned


def unique_slug(s: str):
    return slugify(s, lowercase=True)


############################
# ---- Ideal Sitemap Gen ---
############################

BASE_PAGES = [
    {"label": "Home", "path": "/"},
    {"label": "About", "path": "/about/"},
    {"label": "Contact", "path": "/contact/"},
    {"label": "Testimonials", "path": "/testimonials/"},
    {"label": "FAQ", "path": "/faq/"},
    {"label": "Blog", "path": "/blog/"},
]


def build_ideal_sitemap(base_url: str, services: list, cities: list, hub_mode: str = "city-service"):
    """
    hub_mode:
      - 'city-service': URL pattern /{city}/{service}/
      - 'service-city': URL pattern /{service}/{city}/
    Returns list of dicts: [{label, path, type, priority, reason}]
    """
    recs = []

    # Base pages
    for bp in BASE_PAGES:
        recs.append({
            **bp,
            "type": "base",
            "priority": 40,
            "reason": "Foundational trust & navigation pages",
        })

    # Service hub & city hub
    if services:
        recs.append({
            "label": "Services",
            "path": "/services/",
            "type": "hub",
            "priority": 60,
            "reason": "Hub for all services (internal linking)"
        })
        for s in services:
            s_slug = unique_slug(s)
            recs.append({
                "label": f"{s}",
                "path": f"/services/{s_slug}/",
                "type": "service",
                "priority": 70,
                "reason": "Core service landing page"
            })
    if cities:
        recs.append({
            "label": "Locations",
            "path": "/locations/",
            "type": "hub",
            "priority": 60,
            "reason": "Hub for all cities (internal linking)"
        })
        for c in cities:
            c_slug = unique_slug(c)
            recs.append({
                "label": f"{c}",
                "path": f"/locations/{c_slug}/",
                "type": "city",
                "priority": 65,
                "reason": "City/location landing page"
            })

    # Service x City combos
    for s in services:
        s_slug = unique_slug(s)
        for c in cities:
            c_slug = unique_slug(c)
            if hub_mode == "city-service":
                path = f"/{c_slug}/{s_slug}/"
            else:
                path = f"/{s_slug}/{c_slug}/"
            recs.append({
                "label": f"{s} in {c}",
                "path": path,
                "type": "service-city",
                "priority": 100,
                "reason": "Highest impact for local SEO intent"
            })

    return recs


def url_to_path(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if not path.endswith("/") and "." not in path.split("/")[-1]:
        path = path + "/"  # normalize trailing slash for content paths
    return path


def match_existing(recs: list, pages: list):
    existing_paths = {url_to_path(p["url"]) for p in pages}

    results = []
    for r in recs:
        exists = r["path"] in existing_paths
        results.append({
            **r,
            "exists": exists
        })
    return results


def to_csv_bytes(rows: list, field_order: list):
    import csv
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=field_order)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in field_order})
    return output.getvalue().encode("utf-8")


############################
# --------- UI -------------
############################

st.set_page_config(page_title="Local SEO Sitemap Gap Analyzer", layout="wide")

st.title("Local SEO Sitemap Gap Analyzer — MVP")
st.caption("Generate an ideal sitemap for local SEO, crawl your site, and see what's missing. Built as an MVP by ChatGPT for Tim Hovious.")

with st.form("inputs"):
    col1, col2 = st.columns(2)
    with col1:
        business_name = st.text_input("Business Name", placeholder="e.g., Hoosier Plumbing & Drain")
        industry = st.text_input("Industry", placeholder="e.g., Plumbing, Roofing, HVAC")
        website = st.text_input("Current Website", placeholder="e.g., https://example.com")
        hub_mode = st.selectbox("URL Pattern for Service-City Pages", ["city-service", "service-city"], index=0,
                                help="city-service → /indianapolis/drain-cleaning/ | service-city → /drain-cleaning/indianapolis/")
    with col2:
        services_text = st.text_area("Services Offered (comma/newline separated)", height=120, placeholder="e.g., Drain Cleaning, Water Heater Repair, Sewer Line Replacement")
        cities_text = st.text_area("Cities/States Served (comma/newline separated)", height=120, placeholder="e.g., Indianapolis, Carmel, Fishers, Zionsville")
        competitor = st.text_input("(Optional) Competitor Website", placeholder="e.g., https://competitor.com")

    submitted = st.form_submit_button("Generate Report")

if submitted:
    if not website:
        st.error("Please enter the current website URL.")
        st.stop()

    base = normalize_domain(website)
    services = to_list_from_text(services_text)
    cities = to_list_from_text(cities_text)

    st.subheader("1) Ideal Sitemap")
    ideal = build_ideal_sitemap(base, services, cities, hub_mode=hub_mode)
    st.write("The ideal sitemap below includes foundational pages, hubs, city pages, and high-impact service-in-city pages.")
    st.dataframe(ideal, use_container_width=True)

    st.download_button(
        label="Download Ideal Sitemap (CSV)",
        data=to_csv_bytes(ideal, ["label", "path", "type", "priority", "reason"]),
        file_name="ideal_sitemap.csv",
        mime="text/csv",
    )

    st.subheader("2) Crawling Your Site")
    with st.spinner("Discovering your existing pages..."):
        pages = get_site_inventory(base)
    if not pages:
        st.warning("No pages found. The site may block crawlers or be offline. Try again or provide a different URL.")
        st.stop()

    st.success(f"Discovered {len(pages)} pages")
    st.dataframe(pages, use_container_width=True)
    st.download_button(
        label="Download Discovered Pages (CSV)",
        data=to_csv_bytes(pages, ["url", "title"]),
        file_name="discovered_pages.csv",
        mime="text/csv",
    )

    st.subheader("3) Gap Analysis")
    matched = match_existing(ideal, pages)
    missing = [r for r in matched if not r["exists"]]
    present = [r for r in matched if r["exists"]]

    st.markdown(f"**Missing pages:** {len(missing)} | **Already present:** {len(present)}")

    # Sort missing by priority desc, then path
    missing_sorted = sorted(missing, key=lambda x: (-x["priority"], x["path"]))
    present_sorted = sorted(present, key=lambda x: (-x["priority"], x["path"]))

    st.write("### Highest-Impact Missing Pages")
    st.dataframe(missing_sorted, use_container_width=True)

    st.download_button(
        label="Download Missing Pages (CSV)",
        data=to_csv_bytes(missing_sorted, ["label", "path", "type", "priority", "reason"]),
        file_name="missing_pages.csv",
        mime="text/csv",
    )

    st.write("### Pages Detected That Match the Ideal Sitemap")
    st.dataframe(present_sorted, use_container_width=True)

    # Quick summary & CTA copy block
    st.subheader("4) Summary & Suggested Next Steps")
    bn = business_name or "Your business"
    total_sc = sum(1 for r in ideal if r["type"] == "service-city")
    missing_sc = sum(1 for r in missing if r["type"] == "service-city")
    st.markdown(
        f"""
**{bn}** is missing **{missing_sc}/{total_sc}** high-impact service-in-city pages. Building these first typically drives the biggest local SEO lift.

**Next steps:**
- Prioritize creating the missing pages above with priority 80–100.
- Ensure unique on-page content (H1/title/meta), internal links from Services/Locations hubs, and local business schema.
- Want help building these quickly? **Book a free consult** and we’ll handle content + on-page SEO + publishing.
        """
    )

    # Optional simple competitor diff (MVP)
    if competitor:
        st.subheader("5) Competitor Snapshot (MVP)")
        comp_base = normalize_domain(competitor)
        with st.spinner("Crawling competitor..."):
            c_pages = get_site_inventory(comp_base)
        st.success(f"Competitor pages discovered: {len(c_pages)}")
        comp_paths = {url_to_path(p["url"]) for p in c_pages}
        # Which ideal pages do they appear to have (heuristic: path overlap)?
        comp_present = [r for r in ideal if r["path"] in comp_paths]
        comp_missing = [r for r in ideal if r["path"] not in comp_paths]
        st.write("### Competitor Pages Matching Ideal Sitemap")
        st.dataframe(sorted(comp_present, key=lambda x: (-x["priority"], x["path"])), use_container_width=True)
        st.write("### Competitor Pages Likely Missing (heuristic)")
        st.dataframe(sorted(comp_missing, key=lambda x: (-x["priority"], x["path"])), use_container_width=True)

st.markdown("---")
st.caption("MVP limitations: Lightweight crawl (bypasses JS-heavy sites), heuristic matching, no auth-only areas. For robust enterprise crawling, upgrade to a headless browser + queue + retry strategy.")
