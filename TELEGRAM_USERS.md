# Telegram user guide — Scrapeboard

How to buy a plan, upload inputs, pick a scraper, and run jobs **with buttons** (almost no typing).

Send **❓ Help** or `/help` anytime for the command list and this guide as an attachment.

---

## Quick start (buttons)

1. Open the bot → `/start` (creates your account).
2. Tap **🛒 Buy** → pick a package → pick network → pay → send `/paid <txid>` when required.
3. Upload input files as **documents** (see [Uploads](#uploads)). Caption them correctly.
4. Tap **🚀 Run**.
5. Tap a **category** → a **scraper** → toggle **options** → **Continue**.
6. Follow upload prompts if needed → tap **🚀 Start job**.
7. **📊 Status** for progress · **⏹ Stop** to cancel (partial ZIP may be sent here).

Menu (subscribers): **🚀 Run · 📊 Status · ⏹ Stop · 📋 Plan · ⬆️ Upgrade · ❓ Help** (+ **💬 Support** when enabled).

No need to type `/run source=…` unless you want advanced options.

---

## Support tickets

1. Tap **💬 Support** (or `/support`).
2. Send: `/support Your message and details…`
3. Admins reply **in this same chat**.

---

## Commands

| Command | Purpose |
|---------|---------|
| `/start` | Welcome + menu |
| `/help` | Commands + this guide · menu: **❓ Help** |
| `/whoami` | Your Telegram id |
| `/buy` `/paid` `/subscription` | Billing · **🛒 Buy** / **⬆️ Upgrade** / **📋 Plan** |
| `/run` | **Button wizard** to pick a scraper and start · with args: advanced typed run |
| `/scrapers` | Alias of `/run` · `/scrapers list` for text catalog |
| `/status` | Job progress · **📊 Status** |
| `/stop` | Stop active job · **⏹ Stop** |
| `/support <message>` | Support ticket · **💬 Support** |

### Advanced `/run` (optional)

Power users can still type options:

```text
/run source=gmaps threads=2 scrape_websites=no
/run source=google_search use_dork=yes
/run source=email_validate smtp_probe=yes
```

| Option | Example | Notes |
|--------|---------|--------|
| `source=` | `source=google_search` | Scraper id. Default: `gmaps` |
| `name=` | `name=NYC dentists` | Display name in Status |
| `engine=` | `engine=chrome` | chrome, brave, camoufox, … |
| `threads=` | `threads=2` | ≤ plan |
| `max_results=` | `max_results=50` | Cap (0 = default) |
| `scrape_websites=` | `scrape_websites=no` | Maps only |
| `use_dork=` | `use_dork=yes` | Google Search only |
| `validate_after=` | `validate_after=yes` | Email harvest |
| `smtp_probe=` | `smtp_probe=yes` | Email validate |

---

## Uploads

Send a **document** (`.txt` or `.csv`, UTF-8). Set the **caption** (or filename):

| Caption / filename contains | Stored as | Used for |
|----------------------------|-----------|----------|
| `keywords` or `dork` | keywords | Most scrapers |
| `emails` / `email` | keywords | `email_validate` |
| `locations` or `region` | locations | Most scrapers |

- One entry per line; blank lines and `#` comments ignored.
- Locations: prefer `city,state,country`.
- **`email_validate`**: emails only (caption `emails`).
- **Google dork mode**: keywords hold full queries; locations optional (bot offers Skip).

The **Run** wizard tells you when each file is needed.

---

## Available scrapers (`source=`)

Availability depends on your plan. Tap **🚀 Run** for *your* list.

Includes: Google Maps, TikTok Shop, Google Search, Email Harvest / Validate, Facebook (pages/groups/posts/comments), Instagram, TikTok, YouTube, Reddit, Pinterest, LinkedIn, X (Twitter).

CSV outputs include **name**, **email**, and **phone** when publicly available.

---

## Tips

- Prefer **buttons** for buy + run; type only for TxID and support notes.
- One job at a time; extras queue.
- `/scrapers list` if you want a text catalog.
- Stuck? **💬 Support** or `/support …`.
