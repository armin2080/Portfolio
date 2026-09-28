# Armin Maddah Asl - Portfolio Website

Welcome to my [portfolio website](https://armin2080.de/)! This site showcases my skills, projects, and experiences as a Junior Data Scientist and Developer.

![image](https://github.com/user-attachments/assets/8fef47f9-71f8-4ef5-840f-8443a20e507f)


## Table of Contents
- [About Me](#about-me)
- [Features](#features)
- [Technologies Used](#technologies-used)
- [Setup Instructions](#setup-instructions)
- [How to Use](#how-to-use)
- [Contact Information](#contact-information)

## About Me
I am a dedicated Data Scientist and Developer with expertise in transforming complex data into actionable insights. I enjoy solving challenging problems and continuously expanding my knowledge in machine learning and software development.

## Features
- **Skills Section**: Displays key skills with automatically calculated years of experience, split into technical and soft skills.
- **Projects Section**: Showcases various projects I've worked on, filterable by category.
- **Contact Page**: Allows users to get in touch via a contact form (protected by reCAPTCHA, a honeypot field, and rate limiting).
- **Downloadable Resume**: Users can download my resume in both English and German formats.

## Technologies Used
This portfolio was built using the following technologies:
- Python
- Django
- HTML & CSS (with Tailwind CSS)
- JavaScript
- SQLite (for database management)

## Setup Instructions
To set up this portfolio website locally, follow these steps:

Clone the repository:
   ```bash
   git clone git@github.com:armin2080/Portfolio.git
   ```

Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   source .venv/bin/activate   # optional, so plain `python` works
   ```

> **Note:** do **not** copy `.env.example` for local development. It is a
> production template (`DEBUG=False`, `SECURE_SSL_REDIRECT=True`, database in
> `/var/lib/portfolio`), which would make `runserver` redirect you to HTTPS on
> `http://127.0.0.1:8000`. Locally you can simply skip `.env` — the defaults
> are development-friendly (debug on, database at `db.sqlite3`).

Apply migrations and start the development server:
   ```bash
   .venv/bin/python manage.py migrate
   .venv/bin/python manage.py runserver
   ```

Then open <http://127.0.0.1:8000/>. To add content, create a superuser with
`.venv/bin/python manage.py createsuperuser` and use `/admin/`.

### Frontend assets (Tailwind CSS)

The CSS bundle (`static/css/tailwind.css`) is built from
`static/css/tailwind.src.css` using the Tailwind standalone CLI — no Node.js
required. The compiled file is committed, so a normal deploy needs no build
step. Rebuild it after changing templates or styles:

   ```bash
   scripts/build-css.sh          # one-off minified build
   scripts/build-css.sh watch    # rebuild on change during development
   ```

### Running tests

   ```bash
   .venv/bin/python manage.py test
   ```

GitHub Actions runs the tests, `manage.py check --deploy`, and verifies that the
committed CSS bundle matches the templates (see `.github/workflows/ci.yml`).

### Contact form

Submitted messages are stored in the database *and* emailed, so a message is
never lost when the mail server is unavailable. Stored messages appear under
**Contact messages** in `/admin/`.

reCAPTCHA is skipped while `DEBUG` is on, so you can submit the form locally.
With `DEBUG=False` the keys are required, and `manage.py check` fails loudly if
they are missing or still set to the `.env.example` placeholders.

The rate limit (3 messages per hour) is applied per **visitor**, using the real
client IP from `CF-Connecting-IP` rather than `REMOTE_ADDR` — behind Cloudflare
the latter is the proxy's address, which would otherwise make the limit apply to
the whole site at once. See `TRUST_PROXY_HEADERS` in `.env.example`.

### Visitor statistics

A private dashboard at `/dashboard/` shows page views, unique visitors, top
pages, countries, devices, browsers and traffic sources. Only signed-in staff
can open it, and your own visits are not counted while you are logged in to the
admin.

It is deliberately built to avoid needing a cookie consent banner:

- **No cookies, no local storage, no device identifiers or fingerprinting.**
- **No IP addresses are stored.** The IP is combined with a secret and the
  current date and immediately reduced to a pseudonym, which changes every day —
  so daily uniques can be counted without following anyone across days.
- **Country comes from Cloudflare** (`CF-IPCountry`), so there is no GeoIP
  database to install or keep updated.
- **Bots and crawlers** are detected and excluded from the figures.
- **No data leaves the server.** There is no third-party analytics service.
- **Retention:** page views older than `ANALYTICS_RETENTION_DAYS` (default 180)
  are deleted by a daily systemd timer, or manually:

  ```bash
  .venv/bin/python manage.py purge_pageviews --dry-run   # see what would go
  .venv/bin/python manage.py purge_pageviews             # delete
  ```

Set `ANALYTICS_ENABLED=False` to turn collection off entirely. A privacy notice
is published at `/privacy/`, which is required because the site processes
personal data (contact messages) and records these statistics.

### Raspberry Pi deployment

For a lightweight production setup, use the Gunicorn systemd deployment in
[SYSTEMD_DEPLOY.md](SYSTEMD_DEPLOY.md). The service is enabled at boot and
automatically restarts after application failures.

### How to Use

Once the website is running, you can navigate through different sections using the menu bar at the top of each page:

**Home Page:** Overview of who I am.

**Resume Page:** Showcasing my resume, with downloadable files.

**Skills Page:** Detailed information about my skills.

**Projects Page:** Showcase of projects I've completed.

**Contact Page:** Fill out the form to get in touch or download my resume.
