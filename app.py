import os, csv, io, re, json, datetime, smtplib, random
from email.message import EmailMessage
from typing import List, Dict, Any, Set, Tuple
from urllib.parse import urlparse

from flask import Flask, request, redirect, url_for, session, jsonify, send_file
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-change-me")

GMAIL_USER     = os.environ.get("GMAIL_USER", "")
GMAIL_PASS     = os.environ.get("GMAIL_APP_PASSWORD", "")
RESUME_PATH    = os.environ.get("RESUME_PATH", "Resume - Jeyakumar Kaviyan.pdf")
DAILY_LIMIT    = int(os.environ.get("DAILY_EMAIL_LIMIT", "20"))
LOG_PATH       = os.environ.get("EMAIL_LOG_PATH", "email_log.csv")
CFG_PATH       = "autosend_config.json"
LISTS_PATH     = "saved_lists.json"

# ── Config helpers ─────────────────────────────────────────────────────────────
_CFG_DEFAULTS = {
    "daily_limit": DAILY_LIMIT,
    "internship_term": "Fall 2026 (September - December)",
    "your_name": "Kaviyan Jeyakumar",
    "portfolio_url": "https://kaviyanj.github.io/KaviyanJeyakumarPortfolio.github.io/",
    "location": "Waterloo, Ontario",
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

def save_cfg(d: dict):
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)

def load_saved_lists() -> list:
    try:
        with open(LISTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_list(name: str, content: str):
    lists = load_saved_lists()
    lists = [l for l in lists if l["name"] != name]
    lists.insert(0, {"name": name, "content": content})
    lists = lists[:20]
    with open(LISTS_PATH, "w", encoding="utf-8") as f:
        json.dump(lists, f, indent=2)

def delete_saved_list(name: str):
    lists = [l for l in load_saved_lists() if l["name"] != name]
    with open(LISTS_PATH, "w", encoding="utf-8") as f:
        json.dump(lists, f, indent=2)

# ── Log helpers ────────────────────────────────────────────────────────────────
LOG_FIELDS = ["date", "term", "company", "email", "subject", "status", "source_url"]

def _iter_log():
    if not os.path.exists(LOG_PATH):
        return
    with open(LOG_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            yield row

def already_contacted(term: str) -> Set[Tuple[str, str]]:
    out: Set[Tuple[str, str]] = set()
    for r in _iter_log():
        if r.get("status", "").upper() in ("SENT", "REJECTED") and r.get("term", "") == term:
            c = r.get("company", "").strip()
            e = r.get("email", "").strip().lower()
            if c and e:
                out.add((c, e))
    return out

def count_sent_today() -> int:
    today = datetime.date.today().isoformat()
    return min(sum(1 for r in _iter_log() if r.get("date") == today and r.get("status","").upper() == "SENT"), DAILY_LIMIT)

def append_log(rows: List[Dict[str, Any]]):
    exists = os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
        if not exists:
            w.writeheader()
        for row in rows:
            w.writerow(row)

# ── Email scraping ─────────────────────────────────────────────────────────────
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_BANNED   = ["noreply","no-reply","donotreply","sales","bizdev","media","press",
             "investor","ir@","support","help","billing","accounting","marketing",
             "newsletter","advertising","unsubscribe"]
_PREFERRED = ["careers","career","jobs","job","hr","recruit","talent","intern",
              "internship","students","university","campus","info","contact","engineering"]

def _scrape_emails(url: str) -> List[str]:
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code >= 400:
            return []
    except Exception:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    cands: set = set(re.findall(_EMAIL_RE, soup.get_text(" ", strip=True)))
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if h.startswith("mailto:"):
            addr = h.split("mailto:",1)[1].split("?",1)[0]
            if _EMAIL_RE.fullmatch(addr):
                cands.add(addr)
    prim, fall = [], []
    for e in cands:
        loc = e.split("@",1)[0].lower()
        if any(b in loc for b in _BANNED):
            continue
        (prim if any(p in loc for p in _PREFERRED) else fall).append(e)
    return prim if prim else fall

def find_emails(url: str) -> List[str]:
    seen: set = set()
    out: List[str] = []
    def _add(u):
        for e in _scrape_emails(u):
            if e.lower() not in seen:
                seen.add(e.lower()); out.append(e)
    try:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        base = ""
    _add(url)
    if base:
        for p in ["/contact","/contact-us","/careers","/jobs","/about"]:
            _add(base + p)
    return out

def best_email(emails: List[str]) -> str | None:
    if not emails:
        return None
    pri = ["careers","career","jobs","intern","hr","recruit","talent","engineering","info","contact"]
    def score(e):
        loc = e.split("@",1)[0].lower()
        for i, t in enumerate(pri):
            if t in loc:
                return len(pri) - i
        return 0
    return sorted(emails, key=lambda e: (score(e), e.lower()), reverse=True)[0]

# ── Email sending ──────────────────────────────────────────────────────────────
def send_gmail(to: str, subject: str, body: str) -> bool:
    if not GMAIL_PASS:
        return False
    try:
        msg = EmailMessage()
        msg["From"] = GMAIL_USER
        msg["To"]   = to
        msg["Subject"] = subject
        msg.set_content(body)
        if os.path.exists(RESUME_PATH):
            with open(RESUME_PATH, "rb") as f:
                msg.add_attachment(f.read(), maintype="application", subtype="pdf",
                                   filename=os.path.basename(RESUME_PATH))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, GMAIL_PASS)
            s.send_message(msg)
        return True
    except Exception as e:
        print("SMTP error:", e)
        return False

# ── Email body ─────────────────────────────────────────────────────────────────
def _role_hint(q: str) -> str:
    q = q.lower()
    if "power" in q or "renewable" in q: return "power systems and renewables"
    if "pcb" in q or "hardware" in q:    return "hardware design and PCB development"
    if "robotics" in q or "control" in q: return "robotics, automation, and controls"
    if "semiconductor" in q or "vlsi" in q: return "semiconductor and VLSI hardware"
    return "hardware-focused electrical engineering"

def make_body(company: str, role_hint: str, cfg: dict) -> str:
    name     = cfg.get("your_name", "Kaviyan Jeyakumar")
    term     = cfg.get("internship_term", "Fall 2026 (September - December)")
    portfolio= cfg.get("portfolio_url", "")
    location = cfg.get("location", "Waterloo, Ontario")
    return f"""Hi there,

My name is {name}, an Electrical Engineering student at the University of Waterloo seeking a paid {term} internship in hardware-focused electrical engineering.

I'm particularly interested in opportunities related to {role_hint} at {company}. I have hands-on experience with:
- Electrical validation and CAN/J1939-based product development at Electrans Technology, including power architecture optimization and PCB test fixture design in Altium and LTSpice.
- Hardware power electronics work on a solar power PCB and buck converter digital twin with the Orbital Electrical Team (LTSpice, Altium, lab testing).
- Practical lab skills such as soldering, PCB debug, and use of oscilloscopes and logic analyzers.

I've attached my resume with more detail on my background with LTSpice, Altium, and experience across power electronics, PCB design, and embedded systems.

If there are any upcoming {term} internship opportunities that could be a good fit, I'd greatly appreciate the chance to discuss them.

Thank you for your time.

Sincerely,
{name}
{location}
{('Portfolio: ' + portfolio) if portfolio else ''}
"""

# ── Layout ─────────────────────────────────────────────────────────────────────
def _layout(title: str, active: str, body: str, cfg: dict) -> str:
    term = cfg.get("internship_term","")
    today_sent = count_sent_today()
    remaining  = max(0, DAILY_LIMIT - today_sent)
    nav_items  = [("dashboard","&#127968;","Dashboard","/"),
                  ("campaign","&#9993;","Campaign","/campaign"),
                  ("map","&#128506;","Map Search","/map"),
                  ("preview","&#128203;","Preview","/preview"),
                  ("history","&#128202;","History","/history"),
                  ("settings","&#9881;","Settings","/settings")]
    nav_html = ""
    for key, icon, label, href in nav_items:
        cls = "nav-item active" if active == key else "nav-item"
        nav_html += f'<a href="{href}" class="{cls}">{icon}<span>{label}</span></a>'
    return (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title} – AutoSend</title>"
        "<style>"
        "*{box-sizing:border-box;margin:0;padding:0}"
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f1f5f9;color:#1e293b;display:flex;min-height:100vh}"
        ".sidebar{width:220px;min-height:100vh;background:#0f172a;display:flex;flex-direction:column;padding:0;flex-shrink:0}"
        ".sidebar-brand{padding:20px 18px 16px;border-bottom:1px solid rgba(255,255,255,.08)}"
        ".sidebar-brand h1{color:#f8fafc;font-size:17px;font-weight:700;letter-spacing:-.3px}"
        ".sidebar-brand p{color:#64748b;font-size:11px;margin-top:3px}"
        ".nav-item{display:flex;align-items:center;gap:10px;padding:10px 18px;color:#94a3b8;text-decoration:none;font-size:13.5px;transition:all .15s}"
        ".nav-item:hover{background:rgba(255,255,255,.06);color:#f1f5f9}"
        ".nav-item.active{background:rgba(59,130,246,.18);color:#60a5fa;border-right:3px solid #3b82f6}"
        ".nav-item span{font-size:13px}"
        ".sidebar-footer{margin-top:auto;padding:14px 18px;border-top:1px solid rgba(255,255,255,.06)}"
        ".sidebar-footer .term-badge{background:rgba(59,130,246,.15);color:#93c5fd;padding:5px 10px;border-radius:6px;font-size:11px;font-weight:600;display:block;text-align:center}"
        ".main{flex:1;display:flex;flex-direction:column;min-width:0}"
        ".topbar{background:#fff;border-bottom:1px solid #e2e8f0;padding:14px 28px;display:flex;align-items:center;justify-content:space-between}"
        ".topbar h2{font-size:18px;font-weight:700;color:#0f172a}"
        ".quota-pill{display:flex;gap:16px}"
        ".quota-stat{text-align:center}"
        ".quota-stat .val{font-size:18px;font-weight:700;color:#1e293b}"
        ".quota-stat .lbl{font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.5px}"
        ".page{padding:28px;flex:1}"
        ".card{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:22px;margin-bottom:18px}"
        ".card h3{font-size:14px;font-weight:700;color:#374151;margin-bottom:14px}"
        ".form-group{margin-bottom:16px}"
        ".form-label{display:block;font-size:12.5px;font-weight:600;color:#374151;margin-bottom:5px}"
        "input[type=text],input[type=number],input[type=email],textarea,select{width:100%;padding:8px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:13.5px;color:#1e293b;background:#fff;outline:none;transition:border .15s}"
        "input:focus,textarea:focus,select:focus{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.1)}"
        "textarea{resize:vertical;min-height:100px}"
        ".btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border:none;border-radius:7px;font-size:13px;font-weight:600;cursor:pointer;text-decoration:none;transition:all .15s}"
        ".btn-primary{background:#3b82f6;color:#fff}.btn-primary:hover{background:#2563eb}"
        ".btn-success{background:#059669;color:#fff}.btn-success:hover{background:#047857}"
        ".btn-danger{background:#dc2626;color:#fff}.btn-danger:hover{background:#b91c1c}"
        ".btn-secondary{background:#f1f5f9;color:#374151;border:1px solid #d1d5db}.btn-secondary:hover{background:#e2e8f0}"
        ".btn-sm{padding:5px 11px;font-size:12px}"
        ".btn:disabled{opacity:.55;cursor:not-allowed}"
        ".tbl-wrap{overflow-x:auto}"
        "table{width:100%;border-collapse:collapse;font-size:13px}"
        "th{background:#f8fafc;padding:9px 12px;text-align:left;font-weight:600;color:#374151;border-bottom:2px solid #e2e8f0;white-space:nowrap}"
        "td{padding:9px 12px;border-bottom:1px solid #f1f5f9;vertical-align:top}"
        "tr:hover td{background:#fafafa}"
        ".badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600}"
        ".badge-green{background:#dcfce7;color:#166534}"
        ".badge-red{background:#fee2e2;color:#991b1b}"
        ".badge-yellow{background:#fef3c7;color:#92400e}"
        ".badge-blue{background:#dbeafe;color:#1e40af}"
        ".text-muted{color:#64748b}"
        ".empty-state{text-align:center;padding:48px 20px;color:#64748b}"
        ".empty-state .icon{font-size:40px;margin-bottom:12px}"
        ".page-actions{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:10px}"
        ".form-hint{font-size:11.5px;color:#64748b;margin-top:4px}"
        ".alert{padding:10px 14px;border-radius:7px;font-size:13px;margin-bottom:14px}"
        ".alert-success{background:#f0fdf4;color:#166534;border:1px solid #bbf7d0}"
        ".alert-error{background:#fef2f2;color:#991b1b;border:1px solid #fecaca}"
        ".alert-info{background:#eff6ff;color:#1e40af;border:1px solid #bfdbfe}"
        ".spinner{display:inline-block;width:14px;height:14px;border:2px solid rgba(0,0,0,.1);border-top-color:#3b82f6;border-radius:50%;animation:spin .7s linear infinite;vertical-align:middle}"
        "@keyframes spin{to{transform:rotate(360deg)}}"
        ".tabs{display:flex;gap:2px;border-bottom:2px solid #e2e8f0;margin-bottom:18px}"
        ".tab{padding:8px 16px;font-size:13px;font-weight:600;color:#64748b;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-2px;text-decoration:none}"
        ".tab.active{color:#3b82f6;border-bottom-color:#3b82f6}"
        ".tab:hover{color:#1e293b}"
        ".map-wrap{display:flex;height:calc(100vh - 70px);overflow:hidden}"
        ".map-panel{width:320px;flex-shrink:0;background:#fff;border-right:1px solid #e2e8f0;display:flex;flex-direction:column;overflow:hidden}"
        ".map-panel-top{padding:16px;border-bottom:1px solid #e2e8f0}"
        ".map-panel-scroll{flex:1;overflow-y:auto;padding:12px 16px}"
        "#leaflet-map{flex:1}"
        ".panel-section-title{font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.7px;margin-bottom:8px}"
        ".panel-empty{color:#94a3b8;font-size:12.5px;text-align:center;padding:16px 0}"
        ".result-item{padding:10px;border:1px solid #e2e8f0;border-radius:7px;margin-bottom:6px;cursor:pointer;transition:all .15s}"
        ".result-item:hover{border-color:#93c5fd;background:#eff6ff}"
        ".ri-name{font-size:13px;font-weight:600;color:#1e293b}"
        ".ri-addr{font-size:11.5px;color:#64748b;margin-top:2px}"
        ".ri-actions{margin-top:7px}"
        ".queue-item{display:flex;align-items:center;justify-content:space-between;padding:8px 10px;background:#f8fafc;border-radius:6px;margin-bottom:5px}"
        ".qi-name{font-size:13px;font-weight:600}"
        ".qi-email{font-size:11px;color:#64748b}"
        ".queue-remove{background:none;border:none;cursor:pointer;font-size:16px;color:#94a3b8;padding:0 4px}"
        ".queue-remove:hover{color:#dc2626}"
        ".map-popup{min-width:210px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}"
        ".popup-addr{font-size:11.5px;color:#64748b;margin:4px 0 8px}"
        ".popup-input{width:100%;padding:6px 8px;border:1px solid #d1d5db;border-radius:5px;font-size:12.5px;margin-bottom:8px;box-sizing:border-box}"
        ".popup-actions{display:flex;gap:6px;margin-bottom:4px}"
        ".popup-btn{padding:5px 10px;border:none;border-radius:5px;font-size:12px;font-weight:600;cursor:pointer}"
        ".popup-btn-primary{background:#3b82f6;color:#fff}"
        ".popup-btn-secondary{background:#f1f5f9;color:#374151;border:1px solid #d1d5db}"
        ".map-msg{font-size:12px;padding:6px 10px;border-radius:5px}"
        ".map-msg-info{background:#eff6ff;color:#1e40af}"
        ".map-msg-ok{background:#f0fdf4;color:#166534}"
        ".map-msg-error{background:#fef2f2;color:#991b1b}"
        "</style></head><body>"
        f"<nav class='sidebar'>"
        "<div class='sidebar-brand'><h1>&#9889; AutoSend</h1><p>EE Internship Outreach</p></div>"
        + nav_html +
        f"<div class='sidebar-footer'><span class='term-badge'>{term}</span></div>"
        "</nav>"
        "<div class='main'>"
        "<div class='topbar'>"
        f"<h2>{title}</h2>"
        "<div class='quota-pill'>"
        f"<div class='quota-stat'><div class='val'>{today_sent}</div><div class='lbl'>Sent today</div></div>"
        f"<div class='quota-stat'><div class='val'>{remaining}</div><div class='lbl'>Remaining</div></div>"
        f"<div class='quota-stat'><div class='val'>{DAILY_LIMIT}</div><div class='lbl'>Daily limit</div></div>"
        "</div></div>"
        f"<div class='page'>{body}</div>"
        "</div></body></html>"
    )


# ── Dashboard ──────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    cfg = load_cfg()
    today_sent = count_sent_today()
    remaining  = max(0, DAILY_LIMIT - today_sent)
    drafts     = session.get("drafts", [])
    contacted  = already_contacted(cfg["internship_term"])
    log_rows   = list(_iter_log())
    body = (
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:24px">'
        f'<div class="card" style="margin:0;text-align:center"><div style="font-size:28px;font-weight:800;color:#3b82f6">{today_sent}</div><div style="font-size:12px;color:#64748b;margin-top:4px">Sent today</div></div>'
        f'<div class="card" style="margin:0;text-align:center"><div style="font-size:28px;font-weight:800;color:#059669">{remaining}</div><div style="font-size:12px;color:#64748b;margin-top:4px">Remaining</div></div>'
        f'<div class="card" style="margin:0;text-align:center"><div style="font-size:28px;font-weight:800;color:#7c3aed">{len(contacted)}</div><div style="font-size:12px;color:#64748b;margin-top:4px">Contacted this term</div></div>'
        f'<div class="card" style="margin:0;text-align:center"><div style="font-size:28px;font-weight:800;color:#0f172a">{len(log_rows)}</div><div style="font-size:12px;color:#64748b;margin-top:4px">All-time emails</div></div>'
        '</div>'
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px">'
        '<div class="card">'
        '<h3>&#9889; Quick actions</h3>'
        '<div style="display:flex;flex-direction:column;gap:10px">'
        '<a href="/campaign" class="btn btn-primary">&#9993; New campaign</a>'
        '<a href="/map" class="btn btn-secondary">&#128506; Map search</a>'
        + (f'<a href="/preview" class="btn btn-success">&#128203; Review {len(drafts)} drafts</a>' if drafts else '') +
        '</div></div>'
        '<div class="card">'
        '<h3>&#127775; Current term</h3>'
        f'<div style="font-size:22px;font-weight:700;color:#3b82f6;margin-bottom:8px">{cfg["internship_term"]}</div>'
        '<p style="font-size:13px;color:#64748b;margin-bottom:12px">Contacts from other terms are available to re-apply to.</p>'
        '<a href="/settings" class="btn btn-secondary btn-sm">Change term</a>'
        '</div></div>'
    )
    return _layout("Dashboard", "dashboard", body, cfg)


# ── Campaign ───────────────────────────────────────────────────────────────────
@app.route("/campaign", methods=["GET", "POST"])
def campaign():
    cfg       = load_cfg()
    msg       = ""
    msg_type  = ""
    saved     = load_saved_lists()
    pending   = session.get("pending_companies", "")

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "upload_csv":
            f = request.files.get("csv_file")
            if f and f.filename:
                raw = f.read().decode("utf-8", errors="replace")
                reader = csv.reader(io.StringIO(raw))
                rows = []
                for i, row in enumerate(reader):
                    if not row: continue
                    if i == 0 and row[0].strip().lower() in ("company","name","company name"): continue
                    if len(row) < 2 or not row[0].strip() or not row[1].strip(): continue
                    nm = row[0].strip(); ul = row[1].strip()
                    lc = row[2].strip() if len(row) > 2 else ""
                    if not ul.startswith("http"): ul = "https://" + ul
                    rows.append(f"{nm} | {ul} | {lc}" if lc else f"{nm} | {ul}")
                if rows:
                    session["pending_companies"] = "\n".join(rows)
                    pending = session["pending_companies"]
                    msg = f"Imported {len(rows)} companies from CSV."
                    msg_type = "success"
                else:
                    msg = "No valid rows found in CSV."; msg_type = "error"

        elif action == "save_list":
            list_name = request.form.get("list_name","").strip()
            content   = request.form.get("company_lines","").strip()
            if list_name and content:
                save_list(list_name, content)
                session["pending_companies"] = content
                pending = content
                msg = f'Saved list "{list_name}".'; msg_type = "success"
            else:
                msg = "Enter a name and some companies."; msg_type = "error"

        elif action == "load_list":
            list_name = request.form.get("load_name","")
            for l in saved:
                if l["name"] == list_name:
                    session["pending_companies"] = l["content"]
                    pending = l["content"]
                    msg = f'Loaded "{list_name}".'; msg_type = "success"
                    break
            saved = load_saved_lists()

        elif action == "delete_list":
            list_name = request.form.get("delete_name","")
            delete_saved_list(list_name)
            saved = load_saved_lists()
            msg = f'Deleted "{list_name}".'; msg_type = "success"

        elif action == "build":
            max_drafts = min(max(int(request.form.get("max_drafts","20") or 20), 1), 100)
            lines_raw  = request.form.get("company_lines","").strip()
            session["pending_companies"] = lines_raw
            contacted  = already_contacted(cfg["internship_term"])
            companies  = []
            for raw in lines_raw.splitlines():
                line = raw.strip()
                if not line or "|" not in line: continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 2 or not parts[0] or not parts[1]: continue
                url = parts[1]
                if not url.startswith("http"): url = "https://" + url
                companies.append({"name": parts[0], "url": url, "loc": parts[2] if len(parts) > 2 else ""})
            drafts = []
            seen_cos: set = set()
            for co in companies:
                if len(drafts) >= max_drafts: break
                key = co["name"].strip().lower()
                if key in seen_cos: continue
                emails = find_emails(co["url"])
                pick   = best_email(emails)
                if not pick: continue
                if (co["name"].strip(), pick.lower()) in contacted: continue
                drafts.append({
                    "company": co["name"], "email": pick,
                    "subject": f"Electrical Engineering Co-op - {cfg['internship_term']} | {co['name'][:60]}",
                    "body":    make_body(co["name"], _role_hint(co.get("loc","")), cfg),
                    "url":     co["url"],
                })
                seen_cos.add(key)
            random.shuffle(drafts)
            session["drafts"] = drafts
            if drafts:
                return redirect(url_for("preview"))
            msg = f"No usable emails found in {len(companies)} companies. Try different URLs."; msg_type = "error"

    alert_html = f'<div class="alert alert-{msg_type}">{msg}</div>' if msg else ""
    saves_html = ""
    if saved:
        saves_html = (
            '<div class="card"><h3>&#128190; Saved lists</h3>'
            '<div style="display:flex;flex-direction:column;gap:6px">'
        )
        for l in saved:
            saves_html += (
                f'<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 12px;background:#f8fafc;border-radius:6px;font-size:13px">'
                f'<span style="font-weight:600">{l["name"]}</span>'
                f'<div style="display:flex;gap:6px">'
                f'<form method="post" style="display:inline"><input type="hidden" name="load_name" value="{l["name"]}"><button class="btn btn-secondary btn-sm" name="action" value="load_list">Load</button></form>'
                f'<form method="post" style="display:inline"><input type="hidden" name="delete_name" value="{l["name"]}"><button class="btn btn-danger btn-sm" name="action" value="delete_list">Delete</button></form>'
                f'</div></div>'
            )
        saves_html += '</div></div>'

    body = (
        alert_html +
        '<div class="tabs">'
        '<a class="tab active" href="/campaign">&#128196; Paste / Upload</a>'
        '<a class="tab" href="/map">&#128506; Map search</a>'
        '</div>'
        '<div class="card">'
        '<h3>&#128196; Add companies</h3>'
        '<form method="post" enctype="multipart/form-data">'
        '<div class="form-group"><label class="form-label">Upload CSV</label>'
        '<input type="file" name="csv_file" accept=".csv" style="font-size:13px">'
        '<div class="form-hint">Columns: Company Name, URL, Location (optional). <a href="/csv_template" style="color:#3b82f6">Download template</a></div>'
        '</div>'
        '<button class="btn btn-secondary btn-sm" name="action" value="upload_csv">&#128196; Import CSV</button>'
        '</form>'
        '<hr style="margin:18px 0;border:none;border-top:1px solid #e2e8f0">'
        '<form method="post">'
        '<div class="form-group"><label class="form-label">Company list</label>'
        f'<textarea name="company_lines" rows="8" placeholder="Acme Power | https://acme.com | Waterloo, ON">{pending}</textarea>'
        '<div class="form-hint">Format: <code>Company Name | https://url.com | City, Region</code> — one per line</div>'
        '</div>'
        '<div style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;margin-bottom:14px">'
        '<div class="form-group" style="margin:0"><label class="form-label">Max drafts</label>'
        '<input type="number" name="max_drafts" min="1" max="100" value="20" style="width:80px"></div>'
        '<button class="btn btn-primary" name="action" value="build">&#128269; Build drafts</button>'
        '</div>'
        '<hr style="margin:14px 0;border:none;border-top:1px solid #e2e8f0">'
        '<div style="display:flex;gap:8px;align-items:flex-end">'
        '<div class="form-group" style="flex:1;margin:0"><label class="form-label">Save list as</label>'
        '<input type="text" name="list_name" placeholder="e.g. Waterloo Hardware"></div>'
        '<button class="btn btn-secondary" name="action" value="save_list">&#128190; Save</button>'
        '</div>'
        '</form></div>'
        + saves_html
    )
    return _layout("Campaign", "campaign", body, cfg)


# ── CSV template ───────────────────────────────────────────────────────────────
@app.route("/csv_template")
def csv_template():
    content = "Company Name,URL,Location\nAcme Power Systems,https://acmepow.com,Waterloo ON\nVolta Energy,https://volta.energy,Toronto ON\n"
    return send_file(io.BytesIO(content.encode()), mimetype="text/csv",
                     as_attachment=True, download_name="autosend_template.csv")


# ── Preview ────────────────────────────────────────────────────────────────────
@app.route("/preview", methods=["GET", "POST"])
def preview():
    cfg    = load_cfg()
    drafts = session.get("drafts", [])
    today_sent = count_sent_today()
    remaining  = max(0, DAILY_LIMIT - today_sent)

    if request.method == "POST":
        action = request.form.get("action","")
        updated = []
        for i, d in enumerate(drafts):
            d["subject"] = request.form.get(f"subject_{i}", d.get("subject","")).strip()
            d["body"]    = request.form.get(f"body_{i}", d.get("body",""))
            updated.append(d)
        drafts = updated
        sel = [int(x) for x in request.form.getlist("selected")]

        if action == "delete":
            logs = [{"date": datetime.date.today().isoformat(), "term": cfg["internship_term"],
                     "company": drafts[i]["company"], "email": drafts[i]["email"],
                     "subject": drafts[i]["subject"], "status": "REJECTED",
                     "source_url": drafts[i]["url"]} for i in sel if i < len(drafts)]
            if logs: append_log(logs)
            session["drafts"] = [d for i,d in enumerate(drafts) if i not in set(sel)]
            return redirect(url_for("preview"))

        if action == "send":
            if not sel:
                pass
            else:
                sel = sel[:remaining]
                logs = []
                for i in sel:
                    if i >= len(drafts): continue
                    d = drafts[i]
                    ok = send_gmail(d["email"], d["subject"], d["body"])
                    logs.append({"date": datetime.date.today().isoformat(), "term": cfg["internship_term"],
                                 "company": d["company"], "email": d["email"],
                                 "subject": d["subject"], "status": "SENT" if ok else "FAILED",
                                 "source_url": d["url"]})
                if logs: append_log(logs)
                session["drafts"] = [d for i,d in enumerate(drafts) if i not in set(sel)]
                return redirect(url_for("preview"))

    if not drafts:
        body = (
            '<div class="empty-state">'
            '<div class="icon">&#128237;</div>'
            '<p style="margin-bottom:16px">No drafts yet. Run a campaign first.</p>'
            '<a class="btn btn-primary" href="/campaign">New Campaign</a>'
            '</div>'
        )
        return _layout("Preview Drafts", "preview", body, cfg)

    rows_html = ""
    for i, d in enumerate(drafts):
        be = d["body"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        se = d["subject"].replace('"', "&quot;")
        rows_html += (
            "<tr>"
            f"<td style='width:36px'><input type='checkbox' name='selected' value='{i}'></td>"
            f"<td><strong>{d['company']}</strong></td>"
            f"<td style='white-space:nowrap;font-size:12.5px'>{d['email']}</td>"
            f"<td><input type='text' name='subject_{i}' value=\"{se}\" style='min-width:220px'></td>"
            f"<td><button type='button' class='btn btn-secondary btn-sm' onclick='toggleBody(this,{i})'>Edit body</button>"
            f"<textarea id='body_{i}' name='body_{i}' style='display:none;min-width:340px;margin-top:6px'>{be}</textarea></td>"
            f"<td><a href='{d['url']}' target='_blank' style='font-size:12px'>&#8599; Link</a></td>"
            "</tr>"
        )

    body = (
        '<div class="page-actions">'
        f'<span class="text-muted" style="font-size:13px">{len(drafts)} draft(s) ready &nbsp;&middot;&nbsp; {remaining} sends remaining today</span>'
        '</div>'
        '<div class="card mb-0"><form method="post">'
        '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px;align-items:center">'
        '<button class="btn btn-success" type="submit" name="action" value="send">&#9993; Send selected</button>'
        '<button class="btn btn-danger" type="submit" name="action" value="delete">&#128465; Reject selected</button>'
        '<a class="btn btn-secondary" href="/campaign">&#8592; Back</a>'
        '</div>'
        '<div class="tbl-wrap"><table>'
        '<thead><tr>'
        '<th><input type="checkbox" onclick="toggleAll(this)" title="Select all"></th>'
        '<th>Company</th><th>Email</th><th>Subject</th><th>Body</th><th>URL</th>'
        '</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        '</table></div></form></div>'
        '<script>'
        'function toggleAll(s){document.querySelectorAll(\'input[name="selected"]\').forEach(c=>c.checked=s.checked)}'
        'function toggleBody(btn,i){var t=document.getElementById("body_"+i);t.style.display=t.style.display==="none"?"block":"none";btn.textContent=t.style.display==="none"?"Edit body":"Hide body"}'
        '</script>'
    )
    return _layout("Preview Drafts", "preview", body, cfg)


# ── History ────────────────────────────────────────────────────────────────────
@app.route("/history")
def history():
    cfg  = load_cfg()
    rows = list(_iter_log())
    rows.reverse()
    if not rows:
        body = '<div class="empty-state"><div class="icon">&#128202;</div><p>No emails logged yet.</p></div>'
        return _layout("History", "history", body, cfg)
    rows_html = ""
    for r in rows:
        st = r.get("status","").upper()
        badge = (f'<span class="badge badge-green">{st}</span>' if st == "SENT"
                 else f'<span class="badge badge-red">{st}</span>' if st in ("FAILED","REJECTED")
                 else f'<span class="badge badge-yellow">{st}</span>')
        term_badge = f'<span class="badge badge-blue" style="font-size:10px">{r.get("term","")}</span>' if r.get("term") else ""
        rows_html += (
            f"<tr><td>{r.get('date','')}</td>"
            f"<td>{term_badge}</td>"
            f"<td><strong>{r.get('company','')}</strong></td>"
            f"<td style='font-size:12.5px'>{r.get('email','')}</td>"
            f"<td>{badge}</td>"
            f"<td style='font-size:12px'><a href='{r.get('source_url','')}' target='_blank'>&#8599;</a></td></tr>"
        )
    body = (
        '<div class="card">'
        '<div class="tbl-wrap"><table>'
        '<thead><tr><th>Date</th><th>Term</th><th>Company</th><th>Email</th><th>Status</th><th>URL</th></tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        '</table></div></div>'
    )
    return _layout("History", "history", body, cfg)


# ── Settings ───────────────────────────────────────────────────────────────────
@app.route("/settings", methods=["GET", "POST"])
def settings():
    cfg = load_cfg()
    msg = ""
    if request.method == "POST":
        cfg["internship_term"] = request.form.get("internship_term", cfg["internship_term"]).strip()
        cfg["your_name"]       = request.form.get("your_name", cfg["your_name"]).strip()
        cfg["portfolio_url"]   = request.form.get("portfolio_url", cfg["portfolio_url"]).strip()
        cfg["location"]        = request.form.get("location", cfg["location"]).strip()
        try:
            cfg["daily_limit"] = int(request.form.get("daily_limit","20"))
        except ValueError:
            pass
        save_cfg(cfg)
        msg = "Settings saved."
    alert = f'<div class="alert alert-success">{msg}</div>' if msg else ""
    body = (
        alert +
        '<div class="card">'
        '<h3>&#9881; Settings</h3>'
        '<form method="post">'
        '<div class="form-group"><label class="form-label">Internship term</label>'
        f'<input type="text" name="internship_term" value="{cfg["internship_term"]}">'
        '<div class="form-hint">e.g. "Fall 2026 (September - December)" — changing this lets you re-apply to same companies for a new term</div>'
        '</div>'
        '<div class="form-group"><label class="form-label">Your name</label>'
        f'<input type="text" name="your_name" value="{cfg["your_name"]}"></div>'
        '<div class="form-group"><label class="form-label">Location</label>'
        f'<input type="text" name="location" value="{cfg["location"]}"></div>'
        '<div class="form-group"><label class="form-label">Portfolio URL</label>'
        f'<input type="text" name="portfolio_url" value="{cfg["portfolio_url"]}"></div>'
        '<div class="form-group"><label class="form-label">Daily email limit</label>'
        f'<input type="number" name="daily_limit" value="{cfg["daily_limit"]}" min="1" max="100" style="width:100px"></div>'
        '<button class="btn btn-primary" type="submit">&#9989; Save settings</button>'
        '</form></div>'
    )
    return _layout("Settings", "settings", body, cfg)


# ── Legacy redirect ────────────────────────────────────────────────────────────
@app.route("/run_search", methods=["POST"])
def run_search():
    return redirect(url_for("campaign"))

# ── Map ────────────────────────────────────────────────────────────────────────
@app.route("/map")
def map_view():
    cfg = load_cfg()
    map_js = """
var map, cityLat=43.4723, cityLon=-80.5449;
var results=[], markers=[], draftQueue=[], idCounter=0;
var companyData = {};

function escHtml(s){
    return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

window.addEventListener('DOMContentLoaded', function(){
    map = L.map('leaflet-map').setView([cityLat, cityLon], 11);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxZoom: 19
    }).addTo(map);
    map.on('click', function(e){ showManualPopup(e.latlng); });
});

async function geocodeCity(){
    var city = document.getElementById('city-input').value.trim();
    if(!city) return;
    setBtnLoading('geo-btn', true);
    setMsg('Locating ' + city + '...', 'info');
    try {
        var r = await fetch('/api/geocode', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({city:city})});
        var d = await r.json();
        if(d.error){ setMsg(d.error, 'error'); return; }
        cityLat = d.lat; cityLon = d.lon;
        map.setView([d.lat, d.lon], 12);
        setMsg('Showing ' + d.display_name, 'ok');
    } catch(e){ setMsg('Geocoding failed: ' + e, 'error'); }
    finally { setBtnLoading('geo-btn', false); }
}

async function searchCompanies(){
    var query = document.getElementById('query-input').value.trim();
    setBtnLoading('find-btn', true);
    setMsg('Scanning for companies...', 'info');
    clearMarkers();
    document.getElementById('results-list').innerHTML = '<div class="panel-empty"><span class="spinner"></span> Searching...</div>';
    try {
        var r = await fetch('/api/company_search', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({query:query, lat:cityLat, lon:cityLon})});
        var d = await r.json();
        if(d.error){ setMsg(d.error, 'error'); renderResultsList([]); return; }
        results = (d.companies || []).map(function(c){ return Object.assign({}, c, {id:'co'+(++idCounter), status:'unknown', draft:null}); });
        results.forEach(function(co){ companyData[co.id] = co; });
        renderResultsList(results);
        results.forEach(addMarker);
        if(results.length === 0){
            setMsg('No companies found in OSM data here. Try clicking the map to add manually.', 'error');
        } else {
            setMsg('Found ' + results.length + ' companies. Click a pin or list item.', 'ok');
        }
    } catch(e){ setMsg('Search error: ' + e, 'error'); renderResultsList([]); }
    finally { setBtnLoading('find-btn', false); }
}

function makePopupHtml(co){
    return (
        '<div class="map-popup">'
        + '<strong>' + escHtml(co.name) + '</strong>'
        + (co.address ? '<div class="popup-addr">' + escHtml(co.address) + '</div>' : '')
        + '<input class="popup-input" type="text" id="pu-' + co.id + '" value="' + escHtml(co.website||'') + '" placeholder="Company website URL">'
        + '<div class="popup-actions">'
        + '<button class="popup-btn popup-btn-primary" data-action="find" data-coid="' + co.id + '">Find email</button>'
        + '<button class="popup-btn popup-btn-secondary" data-action="addlist" data-coid="' + co.id + '">Add to list</button>'
        + '</div>'
        + '<div class="popup-status" id="ps-' + co.id + '"></div>'
        + '</div>'
    );
}

function addMarker(co){
    var icon = L.divIcon({className:'', html:'<div style="font-size:22px;line-height:1;filter:drop-shadow(0 1px 2px rgba(0,0,0,.4))">&#128205;</div>', iconSize:[24,30], iconAnchor:[12,30], popupAnchor:[0,-28]});
    var m = L.marker([co.lat, co.lon], {icon:icon}).addTo(map);
    m.bindPopup(makePopupHtml(co), {minWidth:230});
    m.coId = co.id;
    markers.push(m);
    m.on('popupopen', function(){
        var popup = m.getPopup().getElement();
        if(!popup) return;
        popup.querySelectorAll('[data-action]').forEach(function(btn){
            btn.addEventListener('click', function(){
                var coid = btn.getAttribute('data-coid');
                var action = btn.getAttribute('data-action');
                if(action === 'find') findEmailPopup(coid);
                else if(action === 'addlist') addToListFromMap(coid);
            });
        });
    });
}

function clearMarkers(){ markers.forEach(function(m){ map.removeLayer(m); }); markers = []; }

function showManualPopup(latlng){
    var id = 'man' + (++idCounter);
    var co = {id:id, name:'', lat:latlng.lat, lon:latlng.lng, website:'', address:'', status:'unknown', draft:null};
    results.push(co); companyData[id] = co;
    var popup = L.popup({minWidth:240}).setLatLng(latlng);
    popup.setContent(
        '<div class="map-popup"><strong>Add company</strong>'
        + '<input class="popup-input" type="text" id="mn-name-' + id + '" placeholder="Company name" style="margin-top:8px">'
        + '<input class="popup-input" type="text" id="pu-' + id + '" placeholder="https://company.com" style="margin-top:4px">'
        + '<div class="popup-actions" style="margin-top:8px">'
        + '<button class="popup-btn popup-btn-primary" data-action="findmanual" data-coid="' + id + '">Find email</button>'
        + '</div><div class="popup-status" id="ps-' + id + '"></div></div>'
    );
    popup.openOn(map);
    map.once('popupopen', function(){
        var el = document.querySelector('.leaflet-popup-content [data-action="findmanual"][data-coid="' + id + '"]');
        if(el) el.addEventListener('click', function(){ findEmailManual(id); });
    });
}

async function findEmailManual(id){
    var name = document.getElementById('mn-name-' + id).value.trim();
    var url  = document.getElementById('pu-' + id).value.trim();
    if(!name){ setPopupStatus(id,'Enter a company name.','error'); return; }
    if(!url){  setPopupStatus(id,'Enter a website URL.','error');  return; }
    var co = companyData[id]; if(co){ co.name=name; co.website=url; }
    await findEmailForCo(id, name, url);
}

async function findEmailPopup(id){
    var urlEl = document.getElementById('pu-' + id);
    var url   = urlEl ? urlEl.value.trim() : '';
    var co    = companyData[id];
    var name  = co ? co.name : '';
    if(!url){ setPopupStatus(id,'Enter the company website URL first.','error'); return; }
    if(co) co.website = url;
    await findEmailForCo(id, name, url);
}

async function findEmailForCo(id, name, url){
    setPopupStatus(id, '<span class="spinner"></span> Scraping website...', 'info');
    updateResultStatus(id, 'searching');
    try {
        var r = await fetch('/api/quick_draft', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:name, url:url})});
        var d = await r.json();
        if(d.error){ setPopupStatus(id,'&#10060; '+d.error,'error'); updateResultStatus(id,'no-email'); return; }
        var co = companyData[id];
        if(co){ co.status='ready'; co.draft=d.draft; }
        setPopupStatus(id,
            '&#9989; Found: <strong>' + escHtml(d.draft.email) + '</strong>'
            + '&nbsp;<button class="popup-btn popup-btn-primary" data-action="queue" data-coid="' + id + '">Queue &amp; send</button>',
            'ok');
        var psEl = document.getElementById('ps-' + id);
        if(psEl){ psEl.querySelectorAll('[data-action="queue"]').forEach(function(btn){ btn.addEventListener('click', function(){ queueDraft(btn.getAttribute('data-coid')); }); }); }
        updateResultStatus(id,'ready'); renderResultsList(results);
    } catch(e){ setPopupStatus(id,'&#10060; Error: '+e,'error'); updateResultStatus(id,'error'); }
}

function setPopupStatus(id,html,type){
    var el=document.getElementById('ps-'+id); if(!el)return;
    var cls=type==='error'?'map-msg-error':type==='ok'?'map-msg-ok':'map-msg-info';
    el.innerHTML='<div class="map-msg '+cls+'" style="margin-top:8px">'+html+'</div>';
}
function updateResultStatus(id,status){ var co=companyData[id]; if(co)co.status=status; }

function queueDraft(id){
    var co=companyData[id]; if(!co||!co.draft)return;
    if(draftQueue.find(function(d){return d.coId===id;}))return;
    draftQueue.push({coId:id,name:co.name,draft:co.draft});
    renderQueue(); setPopupStatus(id,'&#128203; Added to send queue!','ok');
}
function removeFromQueue(coId){ draftQueue=draftQueue.filter(function(d){return d.coId!==coId;}); renderQueue(); }

function renderQueue(){
    var el=document.getElementById('draft-queue');
    var cnt=document.getElementById('queue-count');
    var act=document.getElementById('queue-actions');
    cnt.textContent=draftQueue.length;
    if(!draftQueue.length){ act.style.display='none'; el.innerHTML='<div class="panel-empty">No emails queued yet.</div>'; return; }
    act.style.display='';
    el.innerHTML=draftQueue.map(function(item){
        return('<div class="queue-item"><div><div class="qi-name">'+escHtml(item.name)+'</div><div class="qi-email">'+escHtml(item.draft.email)+'</div></div>'
            +'<button class="queue-remove" data-action="removequeue" data-coid="'+item.coId+'" title="Remove">&#215;</button></div>');
    }).join('');
    el.querySelectorAll('[data-action="removequeue"]').forEach(function(btn){ btn.addEventListener('click',function(){ removeFromQueue(btn.getAttribute('data-coid')); }); });
}

async function sendAllQueued(){
    if(!draftQueue.length)return;
    var btn=document.querySelector('#queue-actions .btn-success');
    btn.disabled=true; btn.innerHTML='<span class="spinner"></span> Sending...';
    var ok=0,fail=0; var items=[...draftQueue];
    for(var i=0;i<items.length;i++){
        var item=items[i];
        try {
            var r=await fetch('/api/send_single',{method:'POST',headers:{'Content-Type':'application/json'},
                body:JSON.stringify({name:item.name,email:item.draft.email,subject:item.draft.subject,body:item.draft.body,url:item.draft.url})});
            var d=await r.json(); if(d.ok)ok++;else fail++;
        } catch(e){fail++;}
    }
    draftQueue=[]; renderQueue();
    setMsg('Sent '+ok+' email(s).'+(fail?' '+fail+' failed.':''),'ok');
    btn.disabled=false; btn.innerHTML='&#9993; Send all queued';
}

async function addAllToBulkDrafts(){
    if(!draftQueue.length)return;
    var r=await fetch('/api/add_to_session_drafts',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({drafts:draftQueue.map(function(item){return item.draft;})})});
    var d=await r.json();
    if(d.ok){setMsg('Moved '+draftQueue.length+' draft(s) to Preview. Redirecting...','ok');draftQueue=[];renderQueue();setTimeout(function(){window.location.href='/preview';},1400);}
}

function addToListFromMap(id){
    var co=companyData[id]; if(!co)return;
    var urlEl=document.getElementById('pu-'+id);
    var url=urlEl?urlEl.value.trim():(co.website||'');
    if(!url){setPopupStatus(id,'Enter a website URL first.','error');return;}
    fetch('/api/add_to_paste_list',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({line:co.name+' | '+url})})
    .then(function(){setPopupStatus(id,'&#128203; Added to paste list.','ok');});
}

function renderResultsList(list){
    var el=document.getElementById('results-list');
    if(!list||!list.length){el.innerHTML='<div class="panel-empty">No companies found. Try a different city/query, or click the map to add manually.</div>';return;}
    el.innerHTML=list.map(function(co){
        var dot=co.status==='ready'?'<span style="color:#059669">&#9679;</span> ':co.status==='searching'?'<span class="spinner"></span> ':co.status==='no-email'?'<span style="color:#9ca3af">&#9679;</span> ':'';
        return('<div class="result-item" data-flyto="'+co.id+'">'
            +'<div class="ri-name">'+dot+escHtml(co.name)+'</div>'
            +(co.address?'<div class="ri-addr">'+escHtml(co.address)+'</div>':'')
            +'<div class="ri-actions">'
            +(co.status==='ready'&&co.draft
                ?'<button class="btn btn-success btn-sm" data-action="queuedraft" data-coid="'+co.id+'">Queue</button>'
                :'<button class="btn btn-secondary btn-sm" data-action="flytofindemail" data-coid="'+co.id+'">Find email</button>')
            +'</div></div>');
    }).join('');
    el.querySelectorAll('[data-flyto]').forEach(function(item){
        item.addEventListener('click',function(e){ if(e.target.closest('[data-action]'))return; flyTo(item.getAttribute('data-flyto')); });
    });
    el.querySelectorAll('[data-action="queuedraft"]').forEach(function(btn){ btn.addEventListener('click',function(e){e.stopPropagation();queueDraft(btn.getAttribute('data-coid'));}); });
    el.querySelectorAll('[data-action="flytofindemail"]').forEach(function(btn){ btn.addEventListener('click',function(e){e.stopPropagation();flyToAndFind(btn.getAttribute('data-coid'));}); });
}

function flyTo(id){ var co=companyData[id]; if(!co)return; map.flyTo([co.lat,co.lon],15,{duration:0.8}); var m=markers.find(function(mk){return mk.coId===id;}); if(m)m.openPopup(); }
function flyToAndFind(id){ var co=companyData[id]; if(!co)return; map.flyTo([co.lat,co.lon],15,{duration:0.6}); var m=markers.find(function(mk){return mk.coId===id;}); if(m){m.openPopup();setTimeout(function(){findEmailPopup(id);},700);} }
function setMsg(text,type){ var cls=type==='error'?'map-msg-error':type==='ok'?'map-msg-ok':'map-msg-info'; document.getElementById('map-msg').innerHTML='<div class="map-msg '+cls+'">'+text+'</div>'; }
function setBtnLoading(id,on){ var btn=document.getElementById(id); if(!btn)return; btn.disabled=on; if(on){btn._orig=btn.innerHTML;btn.innerHTML='<span class="spinner"></span>';}else{btn.innerHTML=btn._orig||btn.innerHTML;} }
""".strip()
    body = (
        '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>'
        '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>'
        '<div class="map-wrap">'
        '<div class="map-panel">'
        '<div class="map-panel-top">'
        '<div style="font-size:15px;font-weight:700;margin-bottom:10px">&#128506; Map Search</div>'
        '<div class="form-hint" style="margin-bottom:10px;background:#fef3c7;color:#92400e;padding:8px 10px;border-radius:6px;font-size:11px">'
        '&#9888; Data from OpenStreetMap \u2014 not all companies listed. Click the map to add manually.'
        '</div>'
        '<div class="form-group">'
        '<label class="form-label">City</label>'
        '<div style="display:flex;gap:6px">'
        '<input id="city-input" type="text" placeholder="e.g. Waterloo, ON" style="flex:1" onkeydown="if(event.key===\'Enter\')geocodeCity()">'
        '<button class="btn btn-primary btn-sm" onclick="geocodeCity()" id="geo-btn">Go</button>'
        '</div></div>'
        '<div class="form-group">'
        '<label class="form-label">Find companies</label>'
        '<div style="display:flex;gap:6px">'
        '<input id="query-input" type="text" placeholder="e.g. power electronics" style="flex:1" onkeydown="if(event.key===\'Enter\')searchCompanies()">'
        '<button class="btn btn-primary btn-sm" onclick="searchCompanies()" id="find-btn">&#128269;</button>'
        '</div>'
        '<div class="form-hint" style="margin-top:4px">Leave blank to scan all offices near the city.</div>'
        '</div>'
        '<div id="map-msg"></div>'
        '</div>'
        '<div class="map-panel-scroll">'
        '<div class="panel-section-title">Companies found</div>'
        '<div id="results-list"><div class="panel-empty">Search a city to see companies here.</div></div>'
        '<div class="panel-section-title" style="margin-top:16px">'
        'Send queue <span id="queue-count" style="background:#dcfce7;color:#166534;border-radius:20px;padding:1px 8px;font-size:10px;margin-left:6px;font-weight:700">0</span>'
        '</div>'
        '<div id="draft-queue"><div class="panel-empty">No emails queued yet.</div></div>'
        '<div id="queue-actions" style="display:none;margin-top:10px">'
        '<button class="btn btn-success" style="width:100%;margin-bottom:6px" onclick="sendAllQueued()">&#9993; Send all queued</button>'
        '<button class="btn btn-secondary" style="width:100%" onclick="addAllToBulkDrafts()">&#128203; Move to bulk preview</button>'
        '</div>'
        '</div>'
        '</div>'
        '<div id="leaflet-map"></div>'
        '</div>'
        f'<script>{map_js}</script>'
    )
    return _layout("Map Search", "map", body, cfg)


# ── API: geocode ───────────────────────────────────────────────────────────────
@app.route("/api/geocode", methods=["POST"])
def api_geocode():
    city = (request.json or {}).get("city","").strip()
    if not city: return jsonify({"error":"No city provided."})
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
            params={"q":city,"format":"json","limit":1,"addressdetails":1},
            headers={"User-Agent":"AutoSend-EE-Outreach/2.0"}, timeout=10)
        data = r.json()
        if not data: return jsonify({"error":f'City "{city}" not found.'})
        top = data[0]
        return jsonify({"lat":float(top["lat"]),"lon":float(top["lon"]),
                        "display_name":top.get("display_name",city).split(",")[0]})
    except Exception as e:
        return jsonify({"error":"Geocoding error: "+str(e)})


# ── API: company search ────────────────────────────────────────────────────────
@app.route("/api/company_search", methods=["POST"])
def api_company_search():
    data  = request.json or {}
    query = data.get("query","").strip()
    lat   = float(data.get("lat",0))
    lon   = float(data.get("lon",0))
    companies: List[Dict] = []
    seen_names: set = set()

    def _add(c):
        key = c["name"].lower().strip()
        if key and key not in seen_names and len(companies) < 80:
            seen_names.add(key); companies.append(c)

    kw_name = ""
    if query:
        safe_q  = re.sub(r"[^a-zA-Z0-9 ]","",query)
        kw_name = '["name"~"'+safe_q.replace(" ","|")+'",i]'

    overpass_q = (
        "[out:json][timeout:28];\n(\n"
        '  nwr["office"]' + kw_name + "(around:25000,"+str(lat)+","+str(lon)+");\n"
        '  nwr["name"~"electric|power|hardware|semiconductor|robotics|engineering|tech|circuit|energy|systems|embedded|firmware|pcb|motor|control|automation|sensor|photonics|optic|quantum|aerospace|defence|defense|software",i]'
        '["amenity"!="restaurant"]["amenity"!="cafe"]["amenity"!="bar"]["amenity"!="fast_food"]'
        "(around:20000,"+str(lat)+","+str(lon)+");\n"
        ");\nout center tags;\n"
    )
    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data={"data":overpass_q}, timeout=30)
        for el in r.json().get("elements",[]):
            tags   = el.get("tags",{})
            name   = tags.get("name","").strip()
            if not name or len(name) < 2: continue
            center = el if el["type"]=="node" else el.get("center",{})
            c_lat  = center.get("lat"); c_lon = center.get("lon")
            if not c_lat or not c_lon: continue
            website = tags.get("website") or tags.get("url") or tags.get("contact:website") or ""
            addr    = ", ".join(filter(None,[tags.get("addr:street",""),tags.get("addr:city","")]))
            _add({"name":name,"lat":float(c_lat),"lon":float(c_lon),"website":website,"address":addr})
    except Exception:
        pass
    return jsonify({"companies":companies})


# ── API: quick draft ───────────────────────────────────────────────────────────
@app.route("/api/quick_draft", methods=["POST"])
def api_quick_draft():
    data = request.json or {}
    name = data.get("name","").strip()
    url  = data.get("url","").strip()
    if not url: return jsonify({"error":"No URL provided."})
    if not url.startswith("http"): url = "https://"+url
    emails = find_emails(url)
    pick   = best_email(emails)
    if not pick: return jsonify({"error":"No email address found on that site."})
    cfg     = load_cfg()
    subject = f"Electrical Engineering Co-op - {cfg['internship_term']} | {name[:60]}"
    body    = make_body(name, _role_hint("hardware"), cfg)
    return jsonify({"draft":{"company":name,"email":pick,"subject":subject,"body":body,"url":url}})


# ── API: send single ───────────────────────────────────────────────────────────
@app.route("/api/send_single", methods=["POST"])
def api_send_single():
    data = request.json or {}
    cfg  = load_cfg()
    ok   = send_gmail(data.get("email",""), data.get("subject",""), data.get("body",""))
    append_log([{"date":datetime.date.today().isoformat(),"term":cfg["internship_term"],
                 "company":data.get("name",""),"email":data.get("email",""),
                 "subject":data.get("subject",""),"status":"SENT" if ok else "FAILED",
                 "source_url":data.get("url","")}])
    return jsonify({"ok":ok,"error":"" if ok else "SMTP failed - check credentials."})


# ── API: add to session drafts ─────────────────────────────────────────────────
@app.route("/api/add_to_session_drafts", methods=["POST"])
def api_add_to_session_drafts():
    incoming = (request.json or {}).get("drafts",[])
    existing = session.get("drafts",[])
    seen = {(d["company"].lower(),d["email"].lower()) for d in existing}
    for item in incoming:
        d   = item.get("draft") or item
        key = (d.get("company","").lower(), d.get("email","").lower())
        if key not in seen:
            existing.append(d); seen.add(key)
    session["drafts"] = existing
    return jsonify({"ok":True,"total":len(existing)})


# ── API: add to paste list ─────────────────────────────────────────────────────
@app.route("/api/add_to_paste_list", methods=["POST"])
def api_add_to_paste_list():
    line    = (request.json or {}).get("line","").strip()
    pending = session.get("pending_companies","")
    if line and line not in pending:
        session["pending_companies"] = (pending+"\n"+line).strip()
    return jsonify({"ok":True})


if __name__ == "__main__":
    port  = int(os.environ.get("PORT","5000"))
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(host="0.0.0.0", port=port, debug=debug)
