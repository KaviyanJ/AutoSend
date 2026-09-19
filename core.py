"""
AutoSend core: config, logging, blocklist, email discovery, SMTP sending, template.

Everything that isn't Flask routing/UI lives here so app.py stays readable.
"""

import os
import csv
import re
import json
import time
import datetime
import smtplib
import threading
from email.message import EmailMessage
from typing import List, Dict, Any, Set, Tuple, Optional
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_DIR       = os.environ.get("AUTOSEND_DATA_DIR", ".autosend_data")
CFG_PATH       = "autosend_config.json"
LISTS_PATH     = os.path.join(DATA_DIR, "saved_lists.json")
BLOCKLIST_PATH = os.path.join(DATA_DIR, "blocklist.json")
CACHE_PATH     = os.path.join(DATA_DIR, "email_cache.json")
DRAFTS_DIR     = os.path.join(DATA_DIR, "drafts")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(DRAFTS_DIR, exist_ok=True)

# Legacy location — migrated on first run.
_LEGACY_LISTS = "saved_lists.json"

GMAIL_USER  = os.environ.get("GMAIL_USER", "")
GMAIL_PASS  = os.environ.get("GMAIL_APP_PASSWORD", "")
RESUME_PATH = os.environ.get("RESUME_PATH", "Resume - Jeyakumar Kaviyan.pdf")
LOG_PATH    = os.environ.get("EMAIL_LOG_PATH", "email_log.csv")
_ENV_LIMIT  = int(os.environ.get("DAILY_EMAIL_LIMIT", "100"))

# ── Tunables ───────────────────────────────────────────────────────────────────
HTTP_TIMEOUT      = 6          # was 15 — most dead sites hang the full timeout
SCRAPE_WORKERS    = 8          # companies scraped in parallel
CACHE_TTL_DAYS    = 30
MAX_PAGES_PER_SITE = 6


# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════
_CFG_DEFAULTS = {
    "daily_limit": _ENV_LIMIT,
    "internship_term": "Winter 2027 (January - April)",
    "your_name": "Kaviyan Jeyakumar",
    "portfolio_url": "https://kaviyanj.github.io/KaviyanJeyakumarPortfolio.github.io/",
    "location": "Waterloo, Ontario",
    "default_focus": "hardware",
}


def load_cfg() -> dict:
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        for k, v in _CFG_DEFAULTS.items():
            d.setdefault(k, v)
        return d
    except Exception:
        return dict(_CFG_DEFAULTS)


def save_cfg(d: dict) -> None:
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


def daily_limit() -> int:
    """Single source of truth. Previously the UI read an env var while Settings
    wrote to JSON, so the number on screen could disagree with the one enforced."""
    try:
        return max(1, int(load_cfg().get("daily_limit", _ENV_LIMIT)))
    except (TypeError, ValueError):
        return _ENV_LIMIT


# ══════════════════════════════════════════════════════════════════════════════
# Small JSON helpers
# ══════════════════════════════════════════════════════════════════════════════
def _read_json(path: str, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def _write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


# ══════════════════════════════════════════════════════════════════════════════
# Saved company lists
# ══════════════════════════════════════════════════════════════════════════════
def load_saved_lists() -> list:
    if not os.path.exists(LISTS_PATH) and os.path.exists(_LEGACY_LISTS):
        _write_json(LISTS_PATH, _read_json(_LEGACY_LISTS, []))
    return _read_json(LISTS_PATH, [])


def save_list(name: str, content: str) -> None:
    lists = [l for l in load_saved_lists() if l["name"] != name]
    lists.insert(0, {"name": name, "content": content})
    _write_json(LISTS_PATH, lists[:20])


def delete_saved_list(name: str) -> None:
    _write_json(LISTS_PATH, [l for l in load_saved_lists() if l["name"] != name])


# ══════════════════════════════════════════════════════════════════════════════
# Domain helpers + blocklist
# ══════════════════════════════════════════════════════════════════════════════
def domain_of(value: str) -> str:
    """Normalised registrable-ish domain from a URL or an email address."""
    s = (value or "").strip().lower()
    if not s:
        return ""
    if "@" in s:
        s = s.rsplit("@", 1)[1]
    else:
        if not s.startswith(("http://", "https://")):
            s = "https://" + s
        s = urlparse(s).netloc
    s = s.split(":")[0].strip(".")
    if s.startswith("www."):
        s = s[4:]
    return s


_BLOCKLIST_SEED = {
    "domains": [
        "gridgear.ca",
        # TODO: add Electrans' real domain — grab it from any email they sent you.
        # Left blank deliberately rather than guessed at.
    ],
    "notes": {
        "gridgear.ca": "Already have an offer — do not contact",
    },
}


def load_blocklist() -> dict:
    if not os.path.exists(BLOCKLIST_PATH):
        _write_json(BLOCKLIST_PATH, _BLOCKLIST_SEED)
        return dict(_BLOCKLIST_SEED)
    d = _read_json(BLOCKLIST_PATH, dict(_BLOCKLIST_SEED))
    d.setdefault("domains", [])
    d.setdefault("notes", {})
    return d


def save_blocklist(domains: List[str], notes: Optional[Dict[str, str]] = None) -> None:
    clean, seen = [], set()
    for raw in domains:
        d = domain_of(raw)
        if d and d not in seen:
            seen.add(d)
            clean.append(d)
    existing_notes = load_blocklist().get("notes", {})
    if notes:
        existing_notes.update(notes)
    _write_json(BLOCKLIST_PATH, {
        "domains": sorted(clean),
        "notes": {k: v for k, v in existing_notes.items() if k in seen},
    })


def add_to_blocklist(value: str, note: str = "") -> str:
    d = domain_of(value)
    if not d:
        return ""
    bl = load_blocklist()
    if d not in bl["domains"]:
        bl["domains"].append(d)
    if note:
        bl["notes"][d] = note
    _write_json(BLOCKLIST_PATH, {"domains": sorted(bl["domains"]), "notes": bl["notes"]})
    return d


def blocked_reason(*values: str) -> Optional[str]:
    """Returns the blocklist entry that matched, or None.

    Matches a domain and all its subdomains, so blocking `gridgear.ca` also
    catches `careers.gridgear.ca` and `hr@mail.gridgear.ca`.
    """
    blocked = load_blocklist().get("domains", [])
    if not blocked:
        return None
    for v in values:
        d = domain_of(v)
        if not d:
            continue
        for b in blocked:
            if d == b or d.endswith("." + b):
                return b
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Log
# ══════════════════════════════════════════════════════════════════════════════
LOG_FIELDS = ["date", "term", "company", "email", "domain", "subject", "status", "source_url"]
_TERM_RE = re.compile(r"(Winter|Spring|Summer|Fall)\s+20\d\d", re.I)


def migrate_log() -> Optional[str]:
    """Older logs were written without `term` (and now `domain`).

    Appending new rows to a file whose header has fewer columns silently shifts
    every value one place left on read, so the log has to be rewritten before
    anything else touches it. Returns a message if a migration happened.
    """
    if not os.path.exists(LOG_PATH):
        return None
    with open(LOG_PATH, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return None
        if [h.strip() for h in header] == LOG_FIELDS:
            return None
        rows = [dict(zip([h.strip() for h in header], r)) for r in reader if r]

    backup = LOG_PATH + f".bak-{datetime.date.today().isoformat()}"
    if not os.path.exists(backup):
        os.replace(LOG_PATH, backup)

    with open(LOG_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            term = r.get("term", "")
            if not term:
                m = _TERM_RE.search(r.get("subject", ""))
                term = m.group(0) if m else ""
            r["term"] = term
            r["domain"] = r.get("domain") or domain_of(r.get("email", ""))
            w.writerow(r)
    return f"Log migrated to the current format ({len(rows)} rows). Backup: {os.path.basename(backup)}"


def iter_log():
    if not os.path.exists(LOG_PATH):
        return
    with open(LOG_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            yield row


def read_log() -> List[Dict[str, str]]:
    return list(iter_log())


def contacted_keys(rows: List[Dict[str, str]], term: str) -> Set[Tuple[str, str]]:
    """(company, email) pairs already handled for this term."""
    out: Set[Tuple[str, str]] = set()
    for r in rows:
        if r.get("status", "").upper() in ("SENT", "REJECTED") and r.get("term", "") == term:
            c = r.get("company", "").strip().lower()
            e = r.get("email", "").strip().lower()
            if c and e:
                out.add((c, e))
    return out


def contacted_domains(rows: List[Dict[str, str]], term: str) -> Set[str]:
    """Domains already emailed this term.

    The old (company, email) key let the same inbox get two emails when one
    company appeared under two names — e.g. 'Clearpath Robotics' and
    'Clearpath Robotics (Otto Motors)' both resolved to info@clearpathrobotics.com
    and both went out on the same day.
    """
    out: Set[str] = set()
    for r in rows:
        if r.get("status", "").upper() == "SENT" and r.get("term", "") == term:
            d = r.get("domain") or domain_of(r.get("email", ""))
            if d:
                out.add(d)
    return out


def count_sent_today(rows: List[Dict[str, str]]) -> int:
    today = datetime.date.today().isoformat()
    return sum(1 for r in rows
               if r.get("date") == today and r.get("status", "").upper() == "SENT")


def append_log(entries: List[Dict[str, Any]]) -> None:
    if not entries:
        return
    exists = os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
        if not exists:
            w.writeheader()
        for row in entries:
            row.setdefault("domain", domain_of(row.get("email", "")))
            w.writerow(row)


def make_log_row(cfg: dict, draft: dict, status: str) -> Dict[str, str]:
    return {
        "date": datetime.date.today().isoformat(),
        "term": cfg["internship_term"],
        "company": draft.get("company", ""),
        "email": draft.get("email", ""),
        "domain": domain_of(draft.get("email", "")),
        "subject": draft.get("subject", ""),
        "status": status,
        "source_url": draft.get("url", ""),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Email discovery
# ══════════════════════════════════════════════════════════════════════════════
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_BANNED_LOCAL = (
    "noreply", "no-reply", "donotreply", "sales", "bizdev", "media", "press",
    "investor", "support", "help", "billing", "accounting", "marketing",
    "newsletter", "advertising", "unsubscribe", "abuse", "postmaster",
    "webmaster", "privacy", "legal", "returns", "orders", "wixpress",
)

# Domains that show up in scraped page text but are never a real contact.
_JUNK_DOMAINS = {
    "example.com", "example.org", "example.net", "domain.com", "yourdomain.com",
    "yoursite.com", "company.com", "email.com", "test.com", "sentry.io",
    "wixpress.com", "sentry.wixpress.com", "godaddy.com", "squarespace.com",
    "wordpress.com", "shopify.com", "hubspot.com", "mailchimp.com",
    "proofofclaims.com", "schema.org", "w3.org", "sentry-next.wixpress.com",
}

# Higher index = better. One list, used by both scoring paths (there used to be
# two lists that disagreed with each other).
_LOCAL_PRIORITY = [
    "contact", "info", "hello", "engineering", "students", "campus",
    "university", "talent", "hr", "recruit", "intern", "internship",
    "jobs", "job", "career", "careers",
]

# A same-domain careers/jobs/intern address is good enough to stop looking.
_STRONG_LOCALS = ("career", "jobs", "job", "intern", "recruit", "talent", "hr")

_PAGES = ["", "/careers", "/contact", "/jobs", "/contact-us", "/about"]

_http = requests.Session()
_http.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-CA,en;q=0.9",
})
_http.mount("https://", requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=32))
_http.mount("http://", requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=32))


def score_email(addr: str, company_domain: str = "") -> int:
    """Negative score = reject."""
    addr = addr.strip()
    if "@" not in addr:
        return -1
    local, _, dom = addr.lower().partition("@")
    if dom in _JUNK_DOMAINS or any(dom.endswith("." + j) for j in _JUNK_DOMAINS):
        return -1
    if any(b in local for b in _BANNED_LOCAL):
        return -1
    if len(local) > 40 or re.fullmatch(r"[0-9a-f]{16,}", local):
        return -1                      # tracking / build hashes, not humans
    score = 0
    for i, token in enumerate(_LOCAL_PRIORITY):
        if token in local:
            score = i + 1
    if company_domain and (dom == company_domain or dom.endswith("." + company_domain)):
        score += 100                   # strongly prefer the company's own domain
    return score


def _is_strong(addr: str, company_domain: str) -> bool:
    local = addr.lower().split("@", 1)[0]
    return (score_email(addr, company_domain) >= 100
            and any(t in local for t in _STRONG_LOCALS))


def _scrape_page(url: str) -> Set[str]:
    try:
        r = _http.get(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
        if r.status_code >= 400 or "html" not in r.headers.get("Content-Type", "text/html"):
            return set()
    except Exception:
        return set()
    soup = BeautifulSoup(r.text, "html.parser")
    found = set(_EMAIL_RE.findall(soup.get_text(" ", strip=True)))
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            addr = href.split(":", 1)[1].split("?", 1)[0].strip()
            if _EMAIL_RE.fullmatch(addr):
                found.add(addr)
    return found


# ── Scrape cache ───────────────────────────────────────────────────────────────
_cache_lock = threading.Lock()


def _cache_get(dom: str) -> Optional[List[str]]:
    entry = _read_json(CACHE_PATH, {}).get(dom)
    if not entry:
        return None
    if time.time() - entry.get("ts", 0) > CACHE_TTL_DAYS * 86400:
        return None
    return entry.get("emails", [])


def _cache_put(dom: str, emails: List[str]) -> None:
    with _cache_lock:
        cache = _read_json(CACHE_PATH, {})
        cache[dom] = {"emails": emails, "ts": time.time()}
        _write_json(CACHE_PATH, cache)


def clear_cache() -> int:
    n = len(_read_json(CACHE_PATH, {}))
    _write_json(CACHE_PATH, {})
    return n


def find_emails(url: str, use_cache: bool = True) -> List[str]:
    """Scrape a company site for contact addresses, best first.

    Stops early once a same-domain careers/jobs address turns up instead of
    always walking all six pages.
    """
    dom = domain_of(url)
    if use_cache and dom:
        cached = _cache_get(dom)
        if cached is not None:
            return cached

    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    seen: Set[str] = set()
    keep: List[str] = []
    for path in _PAGES[:MAX_PAGES_PER_SITE]:
        page = url if path == "" else base + path
        for addr in _scrape_page(page):
            low = addr.lower()
            if low in seen:
                continue
            seen.add(low)
            if score_email(addr, dom) >= 0:
                keep.append(addr)
        if any(_is_strong(a, dom) for a in keep):
            break

    keep.sort(key=lambda a: (score_email(a, dom), a.lower()), reverse=True)
    if dom:
        _cache_put(dom, keep)
    return keep


def best_email(emails: List[str], company_domain: str = "") -> Optional[str]:
    ranked = [e for e in emails if score_email(e, company_domain) >= 0]
    if not ranked:
        return None
    return max(ranked, key=lambda e: (score_email(e, company_domain), -len(e)))


def find_emails_bulk(companies: List[dict], use_cache: bool = True) -> Dict[str, List[str]]:
    """Scrape many sites in parallel. This is where the old build step spent
    almost all its time: six sequential requests per company at a 15s timeout,
    one company at a time."""
    urls = []
    seen = set()
    for co in companies:
        u = co["url"]
        if u not in seen:
            seen.add(u)
            urls.append(u)
    out: Dict[str, List[str]] = {}
    if not urls:
        return out
    with ThreadPoolExecutor(max_workers=min(SCRAPE_WORKERS, len(urls))) as pool:
        for u, emails in zip(urls, pool.map(lambda x: find_emails(x, use_cache), urls)):
            out[u] = emails
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Sending
# ══════════════════════════════════════════════════════════════════════════════
class GmailBatch:
    """One SMTP connection and one login for the whole batch.

    The previous version opened a fresh SMTP_SSL connection and re-authenticated
    for every single email — roughly 1-2s of pure handshake per message.
    """

    def __init__(self):
        self.smtp = None
        self.resume: Optional[bytes] = None
        self.resume_name = os.path.basename(RESUME_PATH)

    def __enter__(self):
        if os.path.exists(RESUME_PATH):
            with open(RESUME_PATH, "rb") as f:
                self.resume = f.read()          # read once, not per email
        self._connect()
        return self

    def _connect(self):
        if not GMAIL_USER or not GMAIL_PASS:
            raise RuntimeError("GMAIL_USER / GMAIL_APP_PASSWORD not set in .env")
        self.smtp = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30)
        self.smtp.login(GMAIL_USER, GMAIL_PASS)

    def send(self, to: str, subject: str, body: str) -> Tuple[bool, str]:
        msg = EmailMessage()
        msg["From"] = GMAIL_USER
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        if self.resume:
            msg.add_attachment(self.resume, maintype="application",
                               subtype="pdf", filename=self.resume_name)
        for attempt in (1, 2):
            try:
                self.smtp.send_message(msg)
                return True, ""
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError) as e:
                if attempt == 1:
                    try:
                        self._connect()          # one transparent reconnect
                        continue
                    except Exception as e2:
                        return False, f"reconnect failed: {e2}"
                return False, str(e)
            except Exception as e:
                return False, str(e)
        return False, "unknown send failure"

    def __exit__(self, *exc):
        try:
            if self.smtp:
                self.smtp.quit()
        except Exception:
            pass


def send_one(to: str, subject: str, body: str) -> Tuple[bool, str]:
    try:
        with GmailBatch() as sender:
            return sender.send(to, subject, body)
    except Exception as e:
        return False, str(e)


# ══════════════════════════════════════════════════════════════════════════════
# Email template
# ══════════════════════════════════════════════════════════════════════════════
_FOCUS_HOOKS = {
    "power":         "power electronics and energy systems",
    "renewable":     "power electronics and energy systems",
    "pcb":           "PCB design and hardware bring-up",
    "hardware":      "PCB design and hardware bring-up",
    "robotics":      "robotics, motion control, and embedded systems",
    "control":       "robotics, motion control, and embedded systems",
    "automation":    "test automation and embedded systems",
    "semiconductor": "semiconductor and mixed-signal hardware",
    "vlsi":          "semiconductor and mixed-signal hardware",
    "embedded":      "embedded systems and firmware",
    "test":          "hardware test, validation, and instrumentation",
}


def focus_hook(text: str, cfg: Optional[dict] = None) -> str:
    """Maps an optional focus keyword to a phrase.

    Note: the old code fed the *location* column into this, so it never matched
    anything and every email got the same generic fallback. Focus is now its own
    optional 4th column on the company line.
    """
    t = (text or "").lower()
    for key, phrase in _FOCUS_HOOKS.items():
        if key in t:
            return phrase
    if cfg:
        return _FOCUS_HOOKS.get(cfg.get("default_focus", "hardware"),
                                "hardware-focused electrical engineering")
    return "hardware-focused electrical engineering"


def make_subject(company: str, cfg: dict) -> str:
    term = cfg.get("internship_term", "")
    short_term = _TERM_RE.search(term)
    short_term = short_term.group(0) if short_term else term
    return f"{short_term} Electrical Engineering Co-op — {company[:60]}"


def make_body(company: str, hook: str, cfg: dict) -> str:
    name      = cfg.get("your_name", "Kaviyan Jeyakumar")
    term      = cfg.get("internship_term", "")
    portfolio = cfg.get("portfolio_url", "")
    location  = cfg.get("location", "Waterloo, Ontario")
    first     = name.split()[0] if name else "Kaviyan"
    short     = _TERM_RE.search(term)
    short     = short.group(0) if short else term

    lines = [
        "Hi there,",
        "",
        f"I'm {first}, an Electrical Engineering student at the University of Waterloo, "
        f"and I'm looking for a paid {term} co-op in {hook}. I'm writing to ask whether "
        f"{company} takes on co-op students for that term.",
        "",
        "Recent work, briefly:",
        "",
        "- At GridGear Solutions I built a Raspberry Pi calibration station "
        "(Python/Tkinter over serial) for production electricity meters, cutting board "
        "load time from 60s to under 10s. I also root-caused a surge test failure under "
        "ANSI/UL 61010-2-030 by tracing a 2.32 kV transient at the SMPS input — 132% over "
        "the part's rating — and scoped the MOV/TVS redesign path.",
        "",
        "- At Electrans Technology I replaced LDOs with LTC3115-1 buck-boost converters on "
        "a telematics unit, dropping heat dissipation from 4.8 W to 0.47 W and clearing the "
        "thermal shutdowns. I also took a 2-layer test fixture from LTSpice simulation "
        "through Altium layout and Python validation, including a window comparator that "
        "automated every termination check.",
        "",
        "- On Waterloo's Orbital team I built an LTSpice digital twin of a buck converter "
        "that hit 84.8% conversion efficiency at 1.5 A with 33 mV of undershoot on a load step.",
        "",
        f"My resume is attached. If there's a {short} opening — or one you expect to post — "
        "I'd appreciate a pointer to the right person.",
        "",
        "Thanks for your time,",
        name,
        location,
    ]
    if portfolio:
        lines.append(f"Portfolio: {portfolio}")
    return "\n".join(lines) + "\n"


# ══════════════════════════════════════════════════════════════════════════════
# Company line parsing
# ══════════════════════════════════════════════════════════════════════════════
def parse_company_lines(raw: str) -> List[dict]:
    """`Company Name | https://url.com | City, Region | focus`

    Location and focus are both optional. Focus is new — it's what actually
    drives the opening line of the email now.
    """
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        url = parts[1]
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        out.append({
            "name":  parts[0],
            "url":   url,
            "loc":   parts[2] if len(parts) > 2 else "",
            "focus": parts[3] if len(parts) > 3 else "",
        })
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Draft store (server side)
# ══════════════════════════════════════════════════════════════════════════════
def _drafts_path(sid: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", sid)[:64] or "default"
    return os.path.join(DRAFTS_DIR, f"{safe}.json")


def load_drafts(sid: str) -> List[dict]:
    """Drafts used to live in the Flask session, which is a signed cookie with a
    hard ~4KB browser limit. A single draft body is over 1KB, so more than two or
    three would quietly fail to persist."""
    return _read_json(_drafts_path(sid), [])


def save_drafts(sid: str, drafts: List[dict]) -> None:
    _write_json(_drafts_path(sid), drafts)


def purge_old_drafts(max_age_days: int = 14) -> None:
    cutoff = time.time() - max_age_days * 86400
    for fn in os.listdir(DRAFTS_DIR):
        p = os.path.join(DRAFTS_DIR, fn)
        try:
            if os.path.getmtime(p) < cutoff:
                os.remove(p)
        except OSError:
            pass
