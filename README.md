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
- **Projects Section**: Showcases various projects I've worked on, filterable by category. New public GitHub repositories are imported automatically once a day.
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
> `http://127.0.0.1:8000` and make reCAPTCHA keys mandatory. Locally you can
> simply skip `.env` — the defaults are development-friendly (debug on, database
> at `db.sqlite3`).
>
> The one exception is the GitHub project sync, which needs a username. A
> minimal local `.env` is enough — no production values:
>
> ```
> DEBUG=True
> GITHUB_USERNAME=your-github-user
> ```

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

### Themes

Colours and fonts are editable from the admin under **Themes** — no code change,
no CSS rebuild and no restart. Activating a theme applies it to the live site
immediately.

Each theme defines:

- a **light palette** and a **dark palette**, so dark mode is a genuine second
  palette rather than a set of overrides;
- a **heading font** and a **body font**.

Eleven colour roles are available: *primary, secondary, accent, emphasis, page,
surface, inverse, heading, ink, muted, border*. Templates use them as ordinary
Tailwind utilities (`bg-primary`, `text-heading`, `border-border`, …), and the
admin renders each as native colour picker with a live preview.

Colours are typed or picked as **hex codes**. The field accepts `#1D3557`,
`1d3557` and the shorthand `#abc`, and stores one canonical `#RRGGBB` form. The
picker sits next to the text field so either workflow works — pick a colour, or
paste a code — and the two stay in sync. A value that cannot be parsed is
flagged in the field and never reaches the stylesheet.

Under the hood the roles are CSS custom properties, so switching a theme only
changes variable values. The defaults live in `static/css/tailwind.src.css` and
match the original design, which means the site renders correctly even with no
theme configured.

**Why `heading` is separate from `primary`:** a single brand colour cannot be
both a dark surface (the navigation in dark mode) and readable heading text
(which must be light in dark mode). Keeping them apart is what allows one theme
to work in both modes — `primary` is for surfaces, `heading` for text.

Form-validation colours (`red-100` … `red-700`) are deliberately *not* themed, so
error states stay recognisably red in every palette.

Fonts are **self-hosted** (`static/fonts/`, three variable families, ~130 KB).
No request is made to Google, so there is no third-party transfer to disclose —
which is why the privacy notice no longer mentions Google Fonts.

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

### Projects from GitHub

A daily timer imports your public GitHub repositories as projects, so a new
repository appears on the site without any manual step. Run it by hand with:

   ```bash
   .venv/bin/python manage.py sync_github_projects --dry-run   # preview only
   .venv/bin/python manage.py sync_github_projects             # write
   ```

- Set `GITHUB_USERNAME` (a local `.env` is enough — see the note above).
  `GITHUB_TOKEN` is optional: without it the API allows 60 requests per hour,
  which is far more than one sync a day needs.
- **Nothing is ever deleted.** A repository that disappears from GitHub simply
  stops being updated; the project stays until you remove it.
- **Your edits always win.** Name, description, link and date are written only
  on first import, so changing them in the admin is never undone. Photos,
  categories, skills and the published flag are never touched — uploading a
  screenshot later is safe.
- Repositories are matched on GitHub's numeric id, which survives renames, and
  forks, archived and disabled repositories are skipped.
- Imported projects arrive published and show a placeholder graphic until you
  upload an image. Untick **is published** to hide one (deleting it would not
  help — the next run would import it again); the admin also has filters for
  "Imported from GitHub" and "Still needs an image".

The first run imports every public repository at once, so preview it with
`--dry-run` first.

### Skills from GitHub

The same sync also tags each imported project with the skills its repository
actually demonstrates, so the skills page reflects real work without hand-tagging
every new repository.

Detection is deterministic and never guessed — a tag can always be traced to
something in the repository:

- **Files and folders** (`manage.py` → Django, `*.ipynb` → Machine Learning,
  `Dockerfile` → Cloud & Deployment).
- **Dependency files** — `requirements.txt`, `pyproject.toml`, `Pipfile`,
  `package.json`, `environment.yml` (`scikit-learn` → Scikit-learn, `torch` →
  Machine Learning, `psycopg2` → PostgreSQL).
- **GitHub's reported language.**

GitHub's `language` field alone is not enough: it reports `Jupyter Notebook`,
`TeX` and `SCSS`, which say little, and never reports Django, pandas or Machine
Learning.

The rules are editable in the admin under **Skill signals**, so a technology can
be recognised without a deploy. A detected technology with no matching skill yet
is created **hidden**, which is how a newly noticed skill reaches the admin for
review instead of appearing on the site unannounced.

**Nothing is tagged automatically.** The sync records what it found as a
*Skill suggestion* — with the evidence that produced it, such as
`manage.py` or `pandas in a dependency file` — and you accept or dismiss each one
under **Skill suggestions** in the admin, individually or in bulk. So a tag like
IT Service Management, which no rule can prove, is never at risk, and the site
only ever shows tags you chose.

Two things worth knowing:

- Untick *"suggest skills from GitHub"* on a project to stop suggestions for it
  entirely.
- **Reading contents costs API requests.** After changing a rule, run
  `sync_github_projects --force-skills`; an unchanged repository is otherwise
  skipped, which keeps the steady-state cost near zero.

The skills themselves are a portfolio-level set rather than a technology list:
languages and disciplines (`Python`, `Machine Learning`, `Statistical Modelling`,
`AI & Language Models`), the platforms under them (`SQL & Databases`,
`Web Development`, `Cloud & Deployment`) and the foundations (`Linux`, `Git`).
Specific libraries — pandas, scikit-learn — belong in project descriptions and in
the evidence behind a suggestion, not as a skill of their own, because a reader
cannot tell what "Pandas" claims next to "Python".

### Skills page: main skills and sub-skills

The skills page is a two-level tree. A **main skill** gets its own card with an
image, a description, how long you have used it, and how many projects it
appears in. Its **sub-skills** are listed underneath as small labels.

This is where the specific technologies live. A flat list has to choose between
being readable and being specific — "Pandas" next to "Python" reads as a peer of
a language, but dropping it loses the detail that says what the work involved.
The tree keeps both: `Python` on the card, `Jupyter Notebook`, `FastAPI` and
`NumPy` underneath it.

- Add or rearrange the tree in the admin under **Skills**. Leave *parent* empty
  for a main skill; set it to make a sub-skill. A main skill can also be edited
  from its own page, where its sub-skills appear inline.
- **Images are for main skills.** Upload one under *Skills* → *Presentation*; a
  placeholder is used until you do. Sub-skills are labels, so they do not need
  one.
- **Display order** puts a skill where you want it on the page. Leave it at 0 to
  fall back to ordering by how long you have used the skill.
- **Project cards show the main skill behind each tag.** A project tagged with
  pandas or PyMC displays as *Data Analysis & Visualisation* and *Statistical
  Modelling*, so cards read at a consistent level while the skills page keeps the
  detail. The "used in N projects" count includes sub-skill work, so adding
  detail never makes a main skill look less used.
- A detection rule can name the main skill a newly found technology belongs under
  (`parent_skill_name`), so an unfamiliar package is created in the right place
  rather than at the top level.

> **A `GITHUB_TOKEN` is effectively required.** Reading contents needs one
> request per repository plus one per dependency file, and the unauthenticated
> limit is 60 requests per hour *per IP* — shared by every device on your
> network. The first full pass needs more than that, and without a token it only
> converges over several daily runs. A classic token with **no scopes** is
> enough, because all the repositories are public:

> ```
> GITHUB_TOKEN=ghp_your_token_here
> ```

### Images

Uploads are converted to **WebP** automatically, and shrunk so the longest edge
is at most 1600px. You can upload whatever comes off your phone or out of a
screenshot tool; there is no need to resize first.

This matters more than it sounds. The skill banners were originally uploaded as
2172x724 PNGs of about 1.6 MB each, so twelve of them was roughly 19 MB of images
on one page. After conversion they are about 45 KB each — the same page is well
under a megabyte.

Two things happen on upload:

- **Format.** WebP is around 30-50% smaller than JPEG at equal quality, and far
  smaller than PNG for anything photographic.
- **Size.** Anything larger than 1600px on its longest edge is scaled down. The
  widest this site displays an image is a 475px skill banner, so 1600px still
  covers a 3x screen with room to spare.

EXIF metadata is dropped, which also removes any GPS coordinates from photos
taken on a phone. Transparency is kept where the image has it.

Images uploaded before this existed can be converted with:

   ```bash
   .venv/bin/python manage.py convert_images_to_webp --dry-run   # preview + savings
   .venv/bin/python manage.py convert_images_to_webp             # convert
   ```

It walks `Profile.profile_picture`, `Skill.image` and `Project.image`, points the
database at the new file and deletes the original. Files already in WebP are
skipped, so re-running is harmless. `--keep-originals` leaves the old files on
disk. Take a backup of `media/` first: restoring it is the only undo.

Project and skill images are lazy-loaded, so a page with many cards only fetches
the images you actually scroll to.

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
