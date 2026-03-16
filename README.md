## AutoSend

AutoSend is a small local Python/Flask web app that helps you semi‑automate outreach for electrical engineering internships.

It uses SerpAPI to discover companies (with a focus on hardware/PCB, power/renewables, robotics/controls, and semiconductor/VLSI roles in Waterloo, Toronto, and San Francisco), scrapes their sites for contact emails, and then lets you:

- Review and **edit** each email (subject + body) in the browser.
- **Delete** individual drafts you don’t want to contact.
- Select which drafts to send (up to a daily cap).
- Send via Gmail (SMTP with an app password) with your resume attached.
- Log all sent attempts to a CSV file so duplicates to the same company/email are avoided on future runs.

### Setup

1. Create and activate a virtual environment, then install dependencies:

```bash
python -m venv .venv
.\.venv\bin\Activate.ps1   # PowerShell
pip install -r requirements.txt
```

2. Create a `.env` file alongside `app.py`:

```bash
FLASK_SECRET_KEY=your_random_secret
SERPAPI_API_KEY=your_serpapi_key
GMAIL_USER=your_gmail_address@gmail.com
GMAIL_APP_PASSWORD=your_gmail_app_password
RESUME_PATH=Resume - Jeyakumar Kaviyan.pdf
DAILY_EMAIL_LIMIT=20
EMAIL_LOG_PATH=email_log.csv
```

3. Run the app:

```bash
python app.py
```

Then open `http://127.0.0.1:5000` in your browser.

