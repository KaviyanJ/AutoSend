import os
import csv
import datetime
import re
import smtplib
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
SERPAPI_API_KEY = os.environ.get("SERPAPI_API_KEY", "")
GMAIL_USER = os.environ.get("GMAIL_USER", "kaviyan.n.jeyakumar@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
RESUME_PATH = os.environ.get("RESUME_PATH", "Resume - Jeyakumar Kaviyan.pdf")
DAILY_LIMIT = int(os.environ.get("DAILY_EMAIL_LIMIT", "20"))
LOG_PATH = os.environ.get("EMAIL_LOG_PATH", "email_log.csv")

# Hard-coded focus and search phrases based on your preferences
FOCUS_QUERIES = [
    # Hardware / PCB
    "electrical engineering internship hardware design pcb startup small team",
    # Power / renewables
    "electrical engineering intern power systems renewable energy startup",
    # Robotics / controls
    "robotics controls electrical engineering internship startup early-stage",
    # Semiconductor / VLSI
    "semiconductor vlsi electrical engineering internship startup",
]

LOCATIONS = [
    "Waterloo, Ontario, Canada",
    "Toronto, Ontario, Canada",
    "San Francisco, California, USA",
]

EXCLUDED_TERMS = ["oil and gas", "oil & gas", "petroleum"]


def serpapi_search() -> List[Dict[str, Any]]:
    """
    Use SerpAPI to search for potential internship employers.
    Returns a list of search result dicts with 'title' and 'link'.
    """
    if not SERPAPI_API_KEY:
        raise RuntimeError(
            "SERPAPI_API_KEY is not set. Please set it in your .env file."
        )

    results: List[Dict[str, Any]] = []
    # Optional cap on number of raw search results we keep before
    # filtering down to the user-chosen draft limit.
    max_results_per_query = 15
    for loc in LOCATIONS:
        for q in FOCUS_QUERIES:
            params = {
                "engine": "google",
                "q": f"{q} {loc} -\"oil & gas\" -\"oil and gas\" -\"recruiting agency\"",
                "api_key": SERPAPI_API_KEY,
                "num": max_results_per_query,
            }
            resp = requests.get("https://serpapi.com/search", params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            organic_results = data.get("organic_results", [])
            for item in organic_results:
                link = item.get("link")
                title = item.get("title")
                if not link or not title:
                    continue
                # Basic exclusion filter
                lowered = (title or "").lower()
                if any(term in lowered for term in EXCLUDED_TERMS):
                    continue
                results.append(
                    {
                        "title": title,
                        "link": link,
                        "location": loc,
                        "query": q,
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
    text = soup.get_text(" ", strip=True)
    candidates = set(re.findall(EMAIL_REGEX, text))

    filtered = []
    for email in candidates:
        # Ignore obvious non-contact emails
        if any(
            s in email.lower()
            for s in ["noreply@", "no-reply@", "donotreply@", "do-not-reply@"]
        ):
            continue
        filtered.append(email)
    return filtered


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
    return count


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
            if row.get("status") == "SENT":
                company = (row.get("company") or "").strip()
                email = (row.get("email") or "").strip().lower()
                if company and email:
                    contacted.add((company, email))
    return contacted


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
    default_max_drafts = session.get("max_drafts", 20)
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
                <p>This will use SerpAPI to search for EE-focused startup companies (small teams) near Waterloo, Toronto, and San Francisco, then try to discover contact emails.</p>
                <label>
                    <strong>Max drafts to create this run</strong>
                    <input type="number" name="max_drafts" min="1" max="200" value="{{ default_max_drafts }}">
                </label>
                <br><br>
                <button class="btn" type="submit">Run search &amp; build drafts</button>
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
        max_drafts = 20
    if max_drafts < 1:
        max_drafts = 1
    if max_drafts > 200:
        max_drafts = 200
    session["max_drafts"] = max_drafts

    search_results = serpapi_search()
    already_contacted = read_already_contacted()
    drafts: List[Dict[str, Any]] = []

    for item in search_results:
        url = item["link"]
        title = item["title"]
        emails = extract_emails_from_url(url)
        if not emails:
            continue

        company_name = title.split(" - ")[0][:80]
        role_hint = infer_role_hint(item["query"])
        subject = f"Summer 2026 Electrical Engineering Internship – {company_name}"
        body = make_personalized_body(company_name, role_hint, url)

        for email in emails:
            key = (company_name.strip(), email.strip().lower())
            if key in already_contacted:
                # Skip duplicates so we don't email the same company + email twice
                continue
            drafts.append(
                {
                    "company": company_name,
                    "email": email,
                    "subject": subject,
                    "body": body,
                    "url": url,
                }
            )

            # Stop once we have reached the requested number of drafts
            if len(drafts) >= max_drafts:
                break
        if len(drafts) >= max_drafts:
            break

    # Store drafts in session (semi-automatic, same browser)
    session["drafts"] = drafts
    flash(f"Built {len(drafts)} email drafts. Review them before sending.")
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
            # Remove selected drafts from the list (no emails sent)
            remaining_drafts: List[Dict[str, Any]] = []
            for idx, d in enumerate(drafts):
                if idx in selected_indices_int:
                    continue
                remaining_drafts.append(d)
            session["drafts"] = remaining_drafts
            flash(f"Deleted {len(drafts) - len(remaining_drafts)} draft(s).")
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
                            <th>Select</th>
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

