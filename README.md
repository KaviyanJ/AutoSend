## AutoSend

AutoSend is a small local Python/Flask web app that helps you semi‑automate outreach for electrical engineering internships.

You provide a list of target companies (with a focus on hardware/PCB, power/renewables, robotics/controls, and semiconductor/VLSI roles in Waterloo, Toronto, and San Francisco), the app scrapes their sites for contact emails, and then lets you:

- Paste companies in the format `Company Name | https://company-website-url.com | City, Region`.
- Automatically discover likely **careers/info/engineering** inboxes while skipping sales/investor/media/support addresses.
- Ensure **only one email per company** and skip any company+email that has already been sent to or explicitly rejected.
- Review and **edit** each email (subject + body) in the browser.
- **Select all** or individually choose which drafts to send or delete.
- Enforce a daily sending cap (configurable via `DAILY_EMAIL_LIMIT`).
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

Then open `http://127.0.0.1:5000` in your browser. From the dashboard you can set the **max drafts per run**, paste your company list, build drafts, and then review/edit/send them from the preview page.

