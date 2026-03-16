import os
import csv
import datetime
import re
import smtplib
import random
from email.message import EmailMessage
from typing import List, Dict, Any

from flask import Flask, request, redirect, url_for, render_template_string, session, flash
import requests
from bs4 import BeautifulSoup

from dotenv import load_dotenv


load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-change-me")

# Config / environment
GMAIL_USER = os.environ.get("GMAIL_USER", "kaviyan.n.jeyakumar@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
RESUME_PATH = os.environ.get("RESUME_PATH", "Resume - Jeyakumar Kaviyan.pdf")
DAILY_LIMIT = int(os.environ.get("DAILY_EMAIL_LIMIT", "100"))
LOG_PATH = os.environ.get("EMAIL_LOG_PATH", "email_log.csv")


def parse_manual_company_lines(lines: str) -> List[Dict[str, Any]]:
    """
    Parse user-supplied lines in the format:
    Company Name | https://company-website-url.com | City, Region
    into a list of {title, link, location, query} dicts.
    """
    results: List[Dict[str, Any]] = []
    for raw in lines.splitlines():
        line = raw.strip()
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        name = parts[0]
        url = parts[1]
        loc = parts[2] if len(parts) > 2 else ""
        if not name or not url:
            continue
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
        results.append(
            {
                "title": name,
                "link": url,
                "location": loc,
                "query": "manual_company_list",
            }
        )
    return results


EMAIL_REGEX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def extract_emails_from_url(url: str) -> List[str]:
    """
    Fetch a URL and try to extract relevant email addresses from the page.
    """
    try:
        resp = requests.get(url, timeout=20)
    except Exception:
        return []
    if resp.status_code >= 400:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # 1) Emails in visible text
    text = soup.get_text(" ", strip=True)
    candidates = set(re.findall(EMAIL_REGEX, text))

    # 2) Emails in mailto: links
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("mailto:"):
            addr = href.split("mailto:", 1)[1].split("?", 1)[0]
            if EMAIL_REGEX.fullmatch(addr):
                candidates.add(addr)

    filtered = []
    filtered_primary: List[str] = []
    filtered_fallback: List[str] = []

    banned_locals = [
        "noreply",
        "no-reply",
        "donotreply",
        "do-not-reply",
        "sales",
        "bizdev",
        "business",
        "media",
        "press",
        "investor",
        "ir@",
        "support",
        "help",
        "billing",
        "accounting",
        "marketing",
        "newsletter",
        "advertising",
    ]
    preferred_locals = [
        "careers",
        "career",
        "jobs",
        "job",
        "hr",
        "recruit",
        "talent",
        "intern",
        "internship",
        "students",
        "university",
        "campus",
        "info",
        "contact",
        "engineering",
    ]

    for email in candidates:
        local_part = email.split("@", 1)[0].lower()
        # Ignore obvious non-contact emails entirely
        if any(b in local_part for b in banned_locals):
            continue
        # Strongly prefer "good" inboxes (careers/info/etc.)
        if any(p in local_part for p in preferred_locals):
            filtered_primary.append(email)
        else:
            filtered_fallback.append(email)

    # If we found any clearly relevant inboxes, use only those.
    if filtered_primary:
        return filtered_primary
    # Otherwise fall back to any non-banned emails we saw.
    return filtered_fallback


def extract_emails_with_contact_variants(url: str) -> List[str]:
    """
    Try the main URL first, then common 'contact'/'careers' style URLs
    for the same host to improve chances of finding an email.
    """
    seen: set[str] = set()
    emails: List[str] = []

    def _add_from(u: str):
        nonlocal emails
        found = extract_emails_from_url(u)
        for e in found:
            if e.lower() not in seen:
                seen.add(e.lower())
                emails.append(e)

    try:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        base = ""

    # 1) Main page
    _add_from(url)

    # 2) A few common subpaths for company contact/careers
    if base:
        for path in ["/contact", "/contact-us", "/careers", "/jobs", "/about", "/team"]:
            _add_from(base + path)

        # 3) Heuristically follow a handful of internal links that look like contact/careers pages.
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code < 400:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(resp.text, "html.parser")
                keyword_paths = []
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    text = (a.get_text(strip=True) or "").lower()

                    # Normalize to absolute URLs on the same host
                    full = urlparse(href)
                    if not full.netloc:
                        full_url = base.rstrip("/") + "/" + href.lstrip("/")
                    elif full.netloc == parsed.netloc:
                        full_url = href
                    else:
                        continue

                    if any(
                        kw in (href.lower() + " " + text)
                        for kw in ["contact", "career", "jobs", "team", "people", "about"]
                    ):
                        keyword_paths.append(full_url)

                # Limit to a few to avoid crawling the whole site
                for link in keyword_paths[:5]:
                    _add_from(link)
        except Exception:
            pass

    return emails


def read_today_sent_count() -> int:
    """
    Count how many emails were logged as sent today.
    """
    if not os.path.exists(LOG_PATH):
        return 0
    today = datetime.date.today().isoformat()
    count = 0
    with open(LOG_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("date") == today and row.get("status") == "SENT":
                count += 1
    # Clamp to DAILY_LIMIT so remaining never goes negative if the log
    # somehow contains more than the configured daily limit for today.
    return min(count, DAILY_LIMIT)


def append_log(rows: List[Dict[str, Any]]):
    """
    Append a list of log rows to the CSV file.
    """
    file_exists = os.path.exists(LOG_PATH)
    fieldnames = ["date", "company", "email", "subject", "status", "source_url"]
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_already_contacted() -> set[tuple[str, str]]:
    """
    Read the CSV log and return a set of (company, email) pairs
    that have already been successfully SENT.
    """
    contacted: set[tuple[str, str]] = set()
    if not os.path.exists(LOG_PATH):
        return contacted

    with open(LOG_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            status = (row.get("status") or "").upper()
            if status in ("SENT", "REJECTED"):
                company = (row.get("company") or "").strip()
                email = (row.get("email") or "").strip().lower()
                if company and email:
                    contacted.add((company, email))
    return contacted


def choose_preferred_email(emails: List[str]) -> str | None:
    """
    From a list of candidate emails, pick a single 'best' one, preferring
    careers/info/hr-style inboxes over everything else.
    """
    if not emails:
        return None

    priority_order = [
        "careers",
        "career",
        "jobs",
        "job",
        "intern",
        "internship",
        "hr",
        "recruit",
        "talent",
        "students",
        "university",
        "campus",
        "engineering",
        "info",
        "contact",
    ]

    def score(email: str) -> int:
        local = email.split("@", 1)[0].lower()
        for idx, token in enumerate(priority_order):
            if token in local:
                # Higher priority → larger score
                return len(priority_order) - idx
        return 0

    # Sort descending by score, then by email to be deterministic
    best = sorted(emails, key=lambda e: (score(e), e.lower()), reverse=True)[0]
    return best


def send_email_via_gmail(to_email: str, subject: str, body: str) -> bool:
    """
    Send a single email with the attached resume through Gmail SMTP.
    Returns True on success, False otherwise.
    """
    if not GMAIL_APP_PASSWORD:
        print("GMAIL_APP_PASSWORD is not set in the environment.")
        return False

    if not os.path.exists(RESUME_PATH):
        print(f"Resume file not found at path: {RESUME_PATH}")
        return False

    try:
        msg = EmailMessage()
        msg["From"] = GMAIL_USER
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(body)

        with open(RESUME_PATH, "rb") as f:
            data = f.read()
        msg.add_attachment(
            data,
            maintype="application",
            subtype="pdf",
            filename=os.path.basename(RESUME_PATH),
        )

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.send_message(msg)

        return True
    except Exception as e:
        print(f"Error sending email via SMTP: {e}")
        return False


def make_personalized_body(company_name: str, role_hint: str, url: str) -> str:
    """
    Build a personalized email body text based on your resume themes.
    """
    return f"""Hi there,

My name is Kaviyan Jeyakumar, an Electrical Engineering student at the University of Waterloo seeking a paid Summer 2026 (May - August) internship in hardware-focused electrical engineering.

I’m particularly interested in opportunities related to {role_hint} at {company_name}. I have hands-on experience with hardware system design, PCB layout, and embedded systems, including:
- Electrical validation and CAN/J1939-based product development at Electrans Technology, including power architecture optimization and PCB test fixture design in Altium and LTSpice.
- Hardware power electronics work on a solar power PCB and buck converter digital twin with the Orbital Electrical Team (LTSpice, Altium, lab testing).
- Practical lab skills such as soldering, PCB debug, and use of oscilloscopes and logic analyzers.

I’ve attached my resume, which provides more detail about my background with tools like LTSpice, Altium, and experience across power electronics, PCB design, and embedded systems.

If there are any upcoming or potential Summer 2026 internships that fits me well, I would greatly appreciate the chance to discuss them or learn more about your hiring process.

Thank you for humoring my cold email.

Sincerely,      
Kaviyan Jeyakumar
Waterloo, Ontario
Portfolio: https://kaviyanj.github.io/KaviyanJeyakumarPortfolio.github.io/
"""


def infer_role_hint(query: str) -> str:
    q = query.lower()
    if "power" in q or "renewable" in q:
        return "power systems and renewables"
    if "pcb" in q or "hardware" in q:
        return "hardware design and PCB development"
    if "robotics" in q or "controls" in q:
        return "robotics, automation, and controls"
    if "semiconductor" in q or "vlsi" in q:
        return "semiconductor and VLSI hardware"
    return "hardware-focused electrical engineering"


@app.route("/", methods=["GET"])
def index():
    today_sent = read_today_sent_count()
    remaining = max(0, DAILY_LIMIT - today_sent)
    drafts = session.get("drafts", [])
    default_max_drafts = session.get("max_drafts", 100)
    return render_template_string(
        """
        <!doctype html>
        <html>
        <head>
            <title>EE Internship Outreach</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 2rem; }
                .status { margin-bottom: 1rem; }
                .btn { padding: 0.5rem 1rem; background:#2563eb; color:white;
                       border:none; border-radius:4px; cursor:pointer; }
                .btn[disabled] { background:#9ca3af; cursor:not-allowed; }
            </style>
        </head>
        <body>
            <h2>EE Internship Outreach Dashboard</h2>
            <div class="status">
                <p><strong>Daily limit:</strong> {{ daily_limit }} | <strong>Sent today:</strong> {{ today_sent }} | <strong>Remaining:</strong> {{ remaining }}</p>
            </div>
            {% with messages = get_flashed_messages() %}
                {% if messages %}
                    <ul>
                        {% for msg in messages %}
                            <li>{{ msg }}</li>
                        {% endfor %}
                    </ul>
                {% endif %}
            {% endwith %}
            <form method="post" action="{{ url_for('run_search') }}">
                <p>Paste company lines in the format: <code>Company Name | https://company-website-url.com | City, Region</code>. The app will find emails, avoid duplicates, and build editable drafts.</p>
                <label>
                    <strong>Max drafts to create this run (1–100)</strong>
                    <input type="number" name="max_drafts" min="1" max="100" value="{{ default_max_drafts }}">
                </label>
                <br><br>
                <label>
                    <strong>Company list (one per line)</strong><br>
                    <textarea name="company_lines" rows="8" style="width:100%;" placeholder="Example Power Systems | https://example.com | Waterloo, ON"></textarea>
                </label>
                <br><br>
                <button class="btn" type="submit">Build drafts from list</button>
            </form>
            {% if drafts %}
                <hr>
                <p>You currently have {{ drafts|length }} drafts ready.</p>
                <a href="{{ url_for('preview') }}">Review &amp; send drafts</a>
            {% endif %}
        </body>
        </html>
        """,
        daily_limit=DAILY_LIMIT,
        today_sent=today_sent,
        remaining=remaining,
        drafts=session.get("drafts", []),
        default_max_drafts=default_max_drafts,
    )


@app.route("/run_search", methods=["POST"])
def run_search():
    today_sent = read_today_sent_count()
    if today_sent >= DAILY_LIMIT:
        flash("Daily email limit already reached. Try again tomorrow.")
        return redirect(url_for("index"))

    # Read desired max drafts from form and remember in session
    max_drafts_raw = request.form.get("max_drafts", "").strip()
    try:
        max_drafts = int(max_drafts_raw)
    except ValueError:
        max_drafts = 10
    if max_drafts < 1:
        max_drafts = 1
    if max_drafts > 100:
        max_drafts = 100
    session["max_drafts"] = max_drafts

    # Parse user-supplied company lines.
    lines_raw = request.form.get("company_lines", "")
    search_results: List[Dict[str, Any]] = []
    if lines_raw.strip():
        search_results = parse_manual_company_lines(lines_raw)
    already_contacted = read_already_contacted()
    drafts: List[Dict[str, Any]] = []
    processed_companies: set[str] = set()
    total_results = len(search_results)
    total_with_emails = 0

    for item in search_results:
        url = item["link"]
        title = item["title"]
        company_name = title.split(" - ")[0][:80]

        # Avoid processing the same company multiple times (across duplicate URLs)
        company_key = company_name.strip().lower()
        if company_key in processed_companies:
            continue

        emails = extract_emails_with_contact_variants(url)
        if not emails:
            continue
        total_with_emails += 1

        role_hint = infer_role_hint(item["query"])
        subject = f"Summer 2026 Electrical Engineering Internship – {company_name}"
        body = make_personalized_body(company_name, role_hint, url)

        # Choose a single preferred email per company so we don't spam multiple inboxes.
        preferred = choose_preferred_email(emails)
        if not preferred:
            continue
        key = (company_name.strip(), preferred.strip().lower())
        if key in already_contacted:
            # Skip duplicates so we don't email the same company + email twice
            continue

        drafts.append(
            {
                "company": company_name,
                "email": preferred,
                "subject": subject,
                "body": body,
                "url": url,
            }
        )
        processed_companies.add(company_key)

    # Shuffle to mix Waterloo, Toronto, and SF results before truncating
    random.shuffle(drafts)
    if len(drafts) > max_drafts:
        drafts = drafts[:max_drafts]

    # Store drafts in session (semi-automatic, same browser)
    session["drafts"] = drafts
    if not drafts:
        flash(
            f"Found {total_results} company results but no usable email addresses "
            f"(after skipping sent/rejected). Try again later or adjust filters."
        )
    else:
        flash(
            f"Found {total_results} company results, {total_with_emails} with emails. "
            f"Built {len(drafts)} unique drafts. Review them before sending."
        )
    return redirect(url_for("preview"))


@app.route("/preview", methods=["GET", "POST"])
def preview():
    drafts: List[Dict[str, Any]] = session.get("drafts", [])
    today_sent = read_today_sent_count()
    remaining = max(0, DAILY_LIMIT - today_sent)

    if request.method == "POST":
        action = request.form.get("action")

        # Update in-memory drafts with any edited subjects/bodies
        updated_drafts: List[Dict[str, Any]] = []
        for idx, d in enumerate(drafts):
            subj_key = f"subject_{idx}"
            body_key = f"body_{idx}"
            new_subject = request.form.get(subj_key, d.get("subject", "")).strip()
            new_body = request.form.get(body_key, d.get("body", ""))
            d["subject"] = new_subject
            d["body"] = new_body
            updated_drafts.append(d)
        drafts = updated_drafts

        selected_indices = request.form.getlist("selected")
        selected_indices_int = [int(i) for i in selected_indices]

        if action == "delete":
            # Mark selected drafts as REJECTED in the log and remove them from the list
            rejected_logs = []
            keep_indices = set(range(len(drafts))) - set(selected_indices_int)
            remaining_drafts: List[Dict[str, Any]] = []
            for idx, d in enumerate(drafts):
                if idx in selected_indices_int:
                    rejected_logs.append(
                        {
                            "date": datetime.date.today().isoformat(),
                            "company": d["company"],
                            "email": d["email"],
                            "subject": d["subject"],
                            "status": "REJECTED",
                            "source_url": d["url"],
                        }
                    )
                else:
                    remaining_drafts.append(d)

            if rejected_logs:
                append_log(rejected_logs)

            session["drafts"] = remaining_drafts
            flash(f"Rejected and removed {len(rejected_logs)} draft(s). They will not reappear in future runs.")
            return redirect(url_for("preview"))

        # Default action: send selected
        if not selected_indices:
            flash("No drafts selected.")
            return redirect(url_for("preview"))

        # Enforce daily limit
        if len(selected_indices_int) > remaining:
            flash(
                f"Selected {len(selected_indices_int)} emails but only {remaining} remain for today. "
                f"Only the first {remaining} will be sent."
            )
            selected_indices_int = selected_indices_int[:remaining]

        sent_logs = []
        for idx in selected_indices_int:
            if idx < 0 or idx >= len(drafts):
                continue
            draft = drafts[idx]
            success = send_email_via_gmail(
                draft["email"], draft["subject"], draft["body"]
            )
            status = "SENT" if success else "FAILED"
            sent_logs.append(
                {
                    "date": datetime.date.today().isoformat(),
                    "company": draft["company"],
                    "email": draft["email"],
                    "subject": draft["subject"],
                    "status": status,
                    "source_url": draft["url"],
                }
            )

        if sent_logs:
            append_log(sent_logs)

        flash(f"Attempted to send {len(sent_logs)} emails. Check log for details.")
        # Clear drafts after sending
        session.pop("drafts", None)
        return redirect(url_for("index"))

    return render_template_string(
        """
        <!doctype html>
        <html>
        <head>
            <title>Preview Draft Emails</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 2rem; }
                table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
                th, td { border: 1px solid #ddd; padding: 0.5rem; vertical-align: top; }
                th { background:#f3f4f6; }
                .btn { padding: 0.5rem 1rem; background:#16a34a; color:white;
                       border:none; border-radius:4px; cursor:pointer; margin-top:1rem; }
            </style>
            <script>
                function toggleSelectAll(source) {
                    const checkboxes = document.querySelectorAll('input[name="selected"]');
                    checkboxes.forEach(cb => { cb.checked = source.checked; });
                }
            </script>
        </head>
        <body>
            <h2>Preview &amp; Select Draft Emails</h2>
            <p><a href="{{ url_for('index') }}">Back to dashboard</a></p>
            <p><strong>Daily limit:</strong> {{ daily_limit }} | <strong>Sent today:</strong> {{ today_sent }} | <strong>Remaining:</strong> {{ remaining }}</p>
            {% with messages = get_flashed_messages() %}
                {% if messages %}
                    <ul>
                        {% for msg in messages %}
                            <li>{{ msg }}</li>
                        {% endfor %}
                    </ul>
                {% endif %}
            {% endwith %}
            {% if not drafts %}
                <p>No drafts available. Run a search from the dashboard.</p>
            {% else %}
            <form method="post">
                <table>
                    <thead>
                        <tr>
                            <th>
                                <input type="checkbox" onclick="toggleSelectAll(this)">
                                Select
                            </th>
                            <th>Company</th>
                            <th>Email</th>
                            <th>Subject (editable)</th>
                            <th>Body (editable)</th>
                            <th>Source URL</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for d in drafts %}
                        <tr>
                            <td>
                                <input type="checkbox" name="selected" value="{{ loop.index0 }}">
                            </td>
                            <td>{{ d.company }}</td>
                            <td>{{ d.email }}</td>
                            <td>
                                <input type="text" name="subject_{{
                                    loop.index0 }}" value="{{ d.subject }}" style="width:100%;">
                            </td>
                            <td>
                                <textarea name="body_{{ loop.index0 }}" style="width:100%; height: 160px;">{{ d.body }}</textarea>
                            </td>
                            <td><a href="{{ d.url }}" target="_blank">Link</a></td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
                <button class="btn" type="submit" name="action" value="send">Send selected (up to {{ remaining }} today)</button>
                <button class="btn" type="submit" name="action" value="delete" style="background:#dc2626; margin-left:0.5rem;">Delete selected</button>
            </form>
            {% endif %}
        </body>
        </html>
        """,
        drafts=drafts,
        daily_limit=DAILY_LIMIT,
        today_sent=today_sent,
        remaining=remaining,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=True)

