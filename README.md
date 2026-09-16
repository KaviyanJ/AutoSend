# AutoSend

A local Flask app for semi-automating electrical engineering co-op outreach.

You supply a list of target companies, AutoSend scrapes each site for a usable
contact address, drafts a personalised email, and lets you review and edit every
draft before anything is sent. Nothing goes out without you selecting it.

## Files

```
app.py                  Flask routes and page rendering
core.py                 config, log, blocklist, scraping, SMTP, email template
static/style.css        stylesheet (was inlined in app.py)
static/map.js           map page script (was inlined in app.py)
autosend_config.json    term, name, location, portfolio, daily limit
email_log.csv           every send / rejection, appended forever
.autosend_data/         blocklist, scrape cache, saved lists, draft store
```

`.autosend_data/` is created on first run. It is gitignored.

## Setup

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # PowerShell
pip install -r requirements.txt
```

Create `.env` next to `app.py` — see `.env.example`. The Gmail app password is a
16-character password generated at myaccount.google.com/apppasswords, not your
normal account password.

```bash
python app.py
```

Then open http://127.0.0.1:5000.

## Pages

**Campaign** — paste or upload your company list, then build drafts.

```
Company Name | https://url.com | City, Region | focus
```

Location and focus are optional. `focus` sets the opening line of the email and
accepts `power`, `pcb`, `hardware`, `robotics`, `control`, `semiconductor`,
`vlsi`, `embedded`, or `test`. Anything unrecognised falls back to the default
focus in Settings.

**Preview** — edit any subject or body, then send, reject, or reject-and-block.
Sending goes over a single SMTP connection for the whole batch.

**Blocklist** — domains that are never contacted, in any term. Matching covers
subdomains, so `gridgear.ca` also blocks `careers.gridgear.ca` and
`hr@mail.gridgear.ca`. Blocked companies are dropped before any scraping runs.
Seeded with `gridgear.ca`; add Electrans' domain yourself.

**History** — the full log, filterable, exportable.

**Settings** — term, name, location, portfolio, default focus, daily cap, and a
button to clear the scrape cache.

**Map Search** — OpenStreetMap-based company discovery. Coverage is thin; the
paste list is the main path.

## Behaviour worth knowing

- **Term gating.** Companies contacted in a previous term become eligible again
  when you change the term. The blocklist ignores terms entirely.
- **One email per domain per term.** Deduplication is by email domain, not by
  company name, so the same inbox can't be hit twice because a company appears
  under two names.
- **Scrape cache.** Discovered addresses are cached per domain for 30 days.
  Clear it from Settings if a company changes its contact page.
- **Daily cap.** Enforced at send time and shown in the header. It lives in
  `autosend_config.json`; `DAILY_EMAIL_LIMIT` in `.env` is only a fallback for
  the first run.
- **Log migration.** On startup, a log missing the `term` or `domain` columns is
  rewritten in place with a dated `.bak-` backup alongside it.

## Address filtering

Scraped addresses are scored, not just filtered. Same-domain addresses are
strongly preferred, `careers@`/`jobs@`/`intern@`/`hr@` rank above `info@` and
`contact@`, and `sales@`, `investor@`, `legal@`, `press@`, `support@`, and
placeholder domains like `example.com` are rejected outright. If nothing scores
above zero the company is skipped rather than emailed at a bad address.
