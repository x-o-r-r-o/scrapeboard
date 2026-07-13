# Telegram user guide — Scrapeboard

How to buy a plan, upload inputs, pick a scraper, run jobs, and get support from Telegram.

Send **`/help`** on the bot anytime for the command list and this guide as an attachment. Your subscription plan controls which scrapers you can use.

---

## Quick start

1. Open the bot → `/start` (creates your account if needed).
2. **Buy** on the menu (or `/buy`) → pick a package → pay → `/paid <txid>` when required.
3. Upload input files (see [Uploads](#uploads)).
4. **Scrapers** — see sources allowed for you (or `/scrapers`).
5. **Run** — queue the job (`/run source=…`; default `source=gmaps` if omitted).
6. **Status** — progress · **Stop** — cancel (results may be sent here as a ZIP when delivery is enabled).

Menu (subscribers): **Run · Status · Stop · Scrapers · Plan · Upgrade · Help** (+ **Support** when enabled).

---

## Support tickets

Need help from an admin?

1. Make sure Support is available (menu button **Support**, or type `/support`).
2. Send a message with your issue, for example:
   ```text
   /support I paid but my subscription did not activate. TxID: abc123…
   ```
3. A ticket is opened (or your message is added to an open ticket).
4. Admins reply **in this same Telegram chat** — you get notified here.
5. When the issue is resolved, the ticket may be closed. Send `/support …` again to open a new one.

Tips:
- Include useful details (job id, error text, TxID, screenshots as photos if needed).
- One open ticket at a time — further `/support` messages append to it.
- If you see “Support is not enabled”, contact the site admin outside the bot.

---

## Commands (subscribers / users)

| Command | Purpose |
|---------|---------|
| `/start` | Welcome + account + menu |
| `/help` | Commands + this guide (attached file) · menu: **Help** |
| `/scrapers` | Sources you can run (`source=` values) · menu: **Scrapers** |
| `/whoami` | Your Telegram id |
| `/buy` `/paid` `/subscription` | Billing · menu: **Buy** / **Upgrade** / **Plan** |
| `/run [key=value …]` | Queue a job from uploaded files · menu: **Run** |
| `/status` | Your job progress · menu: **Status** |
| `/stop` | Stop your active job · menu: **Stop** |
| `/support <message>` | Open or update a support ticket · menu: **Support** |

### Useful `/run` options

| Option | Example | Notes |
|--------|---------|--------|
| `source=` | `source=google_search` | Scraper id (see below). Default: `gmaps` |
| `name=` | `name=NYC dentists` | Display name in `/status` |
| `engine=` | `engine=chrome` | `chrome`, `brave`, `camoufox`, … |
| `threads=` | `threads=2` | Browsers for this job (≤ plan) |
| `max_results=` | `max_results=50` | Cap per search (0 = default/unlimited where supported) |
| `scrape_websites=` | `scrape_websites=no` | **Google Maps only** — visit business websites for email/socials (`yes` default) |
| `use_dork=` | `use_dork=yes` | **Google Search only** — keywords = full dork queries |
| `validate_after=` | `validate_after=yes` | **Email harvest** — MX/syntax after harvest |
| `smtp_probe=` | `smtp_probe=yes` | **Email validate** — optional SMTP check |
| `channels=` | `channels=google_search` | **Email harvest** (only `google_search` today) |

Examples:

```text
/run source=gmaps threads=2 name=Dentists-TX
/run source=gmaps scrape_websites=no
/run source=google_search use_dork=yes
/run source=email_validate
/run source=email_harvest validate_after=yes
/run source=tiktok_shop engine=chrome threads=1
/run source=youtube max_results=30
```

---

## Uploads

Send a **document** (`.txt` or `.csv`, UTF-8). Set the **caption** (or filename) so the bot knows the type:

| Caption / filename contains | Stored as | Used for |
|----------------------------|-----------|----------|
| `keywords` or `dork` | keywords | Most scrapers |
| `emails` / `email` | keywords | `email_validate` |
| `locations` or `region` | locations | Most scrapers (keyword × location) |

- One entry per line; blank lines and `#` comments ignored.
- Locations: prefer `city,state,country` (e.g. `Austin,Texas,USA`).
- CSV may use header columns `keyword` / `query`, `location`, or `email`.
- **`email_validate`**: upload emails only (caption `emails`). No locations file.
- **`google_search` + `use_dork=yes`**: keywords hold full Google queries; locations optional (omit or use `-`).

Invalid/empty/wrong-type files are rejected **before** a job is queued.

---

## Available scrapers (`source=`)

Availability depends on your plan and a live subscription. Use `/scrapers` for *your* list.

### Maps & places

| `source=` | Inputs | What it does |
|-----------|--------|----------------|
| `gmaps` *(default)* | keywords × locations | Google Maps businesses (name, phone, website). Optional `scrape_websites=yes` visits sites for email/socials |

### Commerce

| `source=` | Inputs | What it does |
|-----------|--------|----------------|
| `tiktok_shop` | niches/keywords × regions | TikTok Shop creator discovery (public SERP + profiles; no Affiliate API) |

### Search & email

| `source=` | Inputs | What it does |
|-----------|--------|----------------|
| `google_search` | keywords × locations **or** dork lines | Organic SERP: title, URL, snippet. `use_dork=yes` for operators (`site:`, `filetype:`, …) |
| `email_harvest` | keywords × locations | Google Search → visit pages → extract emails. Optional `validate_after=yes` |
| `email_validate` | email list only | Syntax, disposable domains, MX; optional `smtp_probe=yes` |

### Social

| `source=` | Inputs | Notes |
|-----------|--------|--------|
| `instagram` | keywords × locations | Name / email / phone from public bios when visible |
| `tiktok` | keywords × locations | Same from public profile bios |
| `youtube` | keywords × locations | Channel/video pages — contact fields when public |
| `reddit` | keywords × locations | Posts + public page contacts |
| `pinterest` | keywords × locations | Pins/profiles + public contacts |
| `linkedin` | keywords × locations | Often login-walled; thin public data |
| `twitter` | keywords × locations | Name / email / phone from public bios when visible |

### Meta / Facebook

| `source=` | Inputs | Notes |
|-----------|--------|--------|
| `facebook_pages` | keywords × locations | Name / email / phone when public on page |
| `facebook_groups` | keywords × locations | Same when public |
| `facebook_posts` | keywords × locations | Post + profile contact enrichment |
| `facebook_comments` | keywords × locations | Limited without login |

CSV columns always include **`name`**, **`email`**, and **`phone`** (blank when not publicly available). Login walls and captchas often hide contacts — residential proxies help.

---

## Tips

- One job runs at a time per account; extras stay queued.
- `/status` anytime for %.
- `/stop` cancels and may deliver a partial ZIP here.
- Full guide again? **Help** / `/help` · sources? **Scrapers** · stuck? **Support** / `/support …`.
