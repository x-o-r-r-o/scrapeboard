# Google Maps Business Scraper

Multi-threaded Google Maps scraper. Every keyword is searched against every
location, results stream to a CSV row-by-row (nothing is lost on interruption),
and you can drive it with **Chrome** (Playwright Chromium), **Brave**, or
**Camoufox** (stealth Firefox). Runs on **macOS, Linux and Windows**, and
headless on a bare Ubuntu VPS with no desktop.

## What it collects

For each business: **name, address, phone, email, website**, and social-media
URLs — **Facebook, Instagram, Twitter/X, LinkedIn, YouTube, TikTok, Pinterest,
WhatsApp, Telegram**. Email and socials are pulled by visiting the business
website and its likely `/contact` and `/about` pages through the same proxy.

## Feature summary

- **Cross-platform:** macOS, Linux, Windows — the OS is auto-detected and the
  browser is launched accordingly (launch args and Brave paths auto-tuned).
- **Fully automated setup on first run:** installs missing Python packages **and
  whichever browser the chosen engine needs** — bundled Chromium, real Google
  Chrome, Microsoft Edge, Brave, or Camoufox. Not limited to one browser.
- Five engines: `--engine chrome` (bundled Chromium), `google-chrome`, `edge`,
  `brave`, or `camoufox`.
- **Loads every business:** the results feed is scrolled to the real end of the
  list so nothing is skipped (`--max-results 0` = unlimited, the default).
- **Optional website scraping:** `--scrape-websites yes|no` chooses whether to
  visit each business's site for email + social links.
- **CAPTCHA auto-solve:** optional 2captcha or CaptchaAI integration to clear
  Google's reCAPTCHA / "unusual traffic" walls automatically.
- **Low resource footprint:** blocks images/media/fonts by default and applies
  memory-lean browser flags, so it runs on modest VPSs. Default `--threads 2`
  (each thread = one full browser).
- **One CSV per location:** each city/state/country gets its own timestamped file
  containing every keyword searched there, saved instantly as results are found.
- **Resumable:** progress is checkpointed, so an interrupted run (crash, reboot,
  Ctrl+C, bad proxies) auto-resumes from where it stopped on the next launch —
  no redoing finished work. `--fresh` starts over.
- **Scales to hundreds of millions of jobs:** jobs are generated lazily and
  processed in bounded chunks, so memory and CPU stay flat no matter how many
  keywords × locations you have.
- **Clean shutdown:** Ctrl+C, `kill`, or closing the terminal kills all threads,
  all browsers (headless or headed) and every child process — nothing is left
  running in the background.
- **Telegram bot control with roles:** run it as a service and start/stop/monitor
  jobs from Telegram — **admins** manage users/permissions, **users** get only
  what they're granted. Results are delivered as a ZIP. Users are fully isolated
  (private DMs).
- **Distributed / multi-server:** connect many machines to one Telegram bot as a
  **coordinator + workers** cluster. A job is split into chunks and spread across
  workers (**each chunk runs on exactly one worker — never duplicated**), then
  results are merged and sent back as a ZIP. Any machine can be standalone,
  coordinator, coordinator+worker, or worker — chosen by a first-run wizard.
- **Sell it as a subscription service:** optional billing with **USDT (TRC-20)
  auto-verified on-chain** and **manual** payment methods. Multiple packages with
  their own price, duration, **thread allowance**, and **upload-size limit**; expiry
  blocks access until renewal; **upgrade-only** (no downgrade while active). Each
  package's limits are enforced. **One job runs at a time per user**; that job's
  threads must be ≤ plan allowance. Extra jobs stay queued until the running one
  finishes (or is stopped/failed).
- **Proxies work with or without authentication** on every engine (including
  Camoufox) — `host:port` or `host:port:user:pass` are both fine.
- **Anti-bot / stealth measures** (see below) so Google is far less likely to
  flag it as automation.
- **Randomised human-like pauses** everywhere, plus **periodic long cool-off
  pauses** that protect proxies from bans/rate-limiting.
- **Browser cache + cookies + storage flushed on every keyword/city change** so
  no session state leaks between searches.
- **Instant CSV writing** — every row is written and `fsync`-flushed the moment
  it is found, behind a lock, deduplicated by (name, address).
- **Runs headless on a VPS with no desktop environment.** A desktop is not
  required; an optional "headed" mode via `xvfb` is documented below.
- **`--selftest`** verifies the browser launches, stealth is applied, and the
  cache flush works — on your machine, without touching Google.

## Install

**You usually don't need to install anything manually.** On first run the
script detects your OS and auto-installs whatever is missing for the chosen
engine — the Python packages *and* the browser:

```bash
python gmaps_scraper.py --engine chrome          # installs playwright + bundled Chromium
python gmaps_scraper.py --engine google-chrome   # installs real Google Chrome
python gmaps_scraper.py --engine edge            # installs Microsoft Edge
python gmaps_scraper.py --engine brave           # installs the Brave browser (per-OS)
python gmaps_scraper.py --engine camoufox        # installs camoufox + its browser
```

Each engine pulls exactly the browser it needs:

| Engine | Browser installed | How |
|--------|-------------------|-----|
| `chrome` | Playwright's bundled Chromium | `playwright install chromium` |
| `google-chrome` | real Google Chrome (stable) | `playwright install chrome` |
| `edge` | Microsoft Edge (stable) | `playwright install msedge` |
| `brave` | Brave | brew / Brave install-script / winget (per OS) |
| `camoufox` | Camoufox (stealth Firefox) | `camoufox fetch` |

(Only `python3` and `pip` need to already exist.) A one-time setup is tracked
per engine, per user, in `~/.gmaps_scraper/` so it isn't repeated on later runs.
`--browser-path` skips the download and uses a binary you already have.

### Manual install (optional)

If you prefer to control it yourself, or want to disable auto-setup with
`--skip-setup`:

```bash
pip install -r requirements.txt
python -m playwright install chromium            # chrome engine (bundled)
python -m playwright install chrome              # google-chrome engine (real Chrome)
python -m playwright install msedge              # edge engine
python -m playwright install-deps                # Linux only: system libs on a bare VPS
python -m camoufox fetch                         # camoufox engine
# Brave engine: install Brave from brave.com — it is auto-detected/auto-installed.
```

No X server / desktop is required for headless mode. On **macOS** and
**Windows** the pip packages plus the engine download above are all that's used.

### Setup flags

| Flag | Meaning |
|------|---------|
| *(default)* | auto-install missing deps + browser on first run |
| `--skip-setup` | never auto-install; use the environment as-is |
| `--force-setup` | re-run the dependency/browser setup even if already done |

### Quick test on macOS (one click)

Double-click **`mac_setup_and_test.command`** in Finder (or run
`bash mac_setup_and_test.command`). It creates a virtualenv, installs
everything, finds Brave, runs `--selftest`, then does a real 1-keyword Brave
scrape of "coffee shop in Austin, Texas" into `test_results.csv` — no proxies
needed. (First run may prompt Gatekeeper: right-click → Open, or
`System Settings → Privacy & Security → Open Anyway`.)

The `--no-proxy` flag used there runs a direct connection for quick local
testing. Use real proxies for anything larger to avoid your own IP being rate-
limited.

### macOS notes

Everything runs natively on macOS (Intel and Apple Silicon). `--no-sandbox` and
Linux-only flags are automatically skipped. For the Brave engine the scraper
looks for `"/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"`.

### Brave engine

Brave is Chromium-based, so it is driven through Playwright with Brave's own
binary. Auto-detected locations:

| OS | Path searched |
|----|---------------|
| macOS | `/Applications/Brave Browser.app/Contents/MacOS/Brave Browser` |
| Linux | `/usr/bin/brave-browser`, `/usr/bin/brave`, `/snap/bin/brave`, `/opt/brave.com/brave/brave-browser` |
| Windows | `C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe` (and Program Files (x86) / LocalAppData) |

If Brave lives somewhere else, point to it explicitly with `--browser-path`.
The same flag lets you drive any Chromium-based browser (e.g. an installed
Google Chrome).

**Auto-install:** if you select `--engine brave` and Brave isn't found, the
scraper installs it automatically using the platform's package manager:

| OS | Method used |
|----|-------------|
| macOS | `brew install --cask brave-browser` (needs Homebrew) |
| Linux | official script: `curl -fsS https://dl.brave.com/install.sh \| sh` |
| Windows | `winget install --id Brave.Brave` (falls back to `choco install brave`) |

If the package manager is missing it prints the manual download link. This may
prompt for admin/sudo. Use `--skip-setup` to disable all auto-installation.

## Input files

Three plain-text files (defaults shown; override with flags). `#` comments and
blank lines are ignored in all three.

- **`proxies.txt`** — one proxy per line (formats below).
- **`locations.txt`** — one `city,state,country` per line.
- **`keywords.txt`** — one keyword per line.

### Supported proxy formats

| Format | Example | Auth |
|--------|---------|------|
| `host:port` | `198.51.100.10:8080` | no |
| `host:port:user:pass` | `198.51.100.10:8080:john:secret` | yes |
| `user:pass@host:port` | `john:secret@198.51.100.10:8080` | yes |
| `scheme://host:port` | `https://198.51.100.10:8443` | no |
| `scheme://user:pass@host:port` | `socks5://john:secret@10.0.0.1:1080` | yes |

- `scheme` may be `http` (default if omitted), `https`, or `socks5`.
- Authentication is **optional on all engines**, including Camoufox — use plain
  `host:port` or an authenticated format, whichever your provider gives you.
- Proxies are assigned to jobs **round-robin**, so keep `--threads` at or below
  your proxy count to give each browser a distinct IP.

## Run

```bash
# Chrome, 4 threads, headless (default) — ideal for a VPS
python gmaps_scraper.py --engine chrome --threads 4 --output-dir results

# Brave (auto-detected on macOS/Linux/Windows)
python gmaps_scraper.py --engine brave --threads 3

# Brave/Chrome at a custom path
python gmaps_scraper.py --engine brave --browser-path "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"

# Camoufox (proxies optional; auth or no-auth both fine)
python gmaps_scraper.py --engine camoufox --threads 2

# Load ALL businesses (default), but skip visiting their websites
python gmaps_scraper.py --engine brave --scrape-websites no

# Auto-solve captchas with 2captcha (or captchaai)
python gmaps_scraper.py --engine chrome --captcha-provider 2captcha --captcha-key YOUR_KEY

# Test whether your proxies can actually reach Google (do this first!)
python gmaps_scraper.py --check-proxies

# Deep-diagnose ONE proxy in a real browser when jobs time out (definitive)
python gmaps_scraper.py --diagnose
python gmaps_scraper.py --diagnose --proxy-index 7 --engine chrome

# Verify your environment first (no Google traffic) — works per engine/OS
python gmaps_scraper.py --selftest --engine brave

# Gentler pacing to protect proxies
python gmaps_scraper.py --min-delay 4 --max-delay 9 --cooldown-every 15
```

### Options

| Flag | Default | Meaning |
|------|---------|---------|
| `--engine` | `chrome` | `chrome`, `google-chrome`, `edge`, `brave`, or `camoufox` |
| `--browser-path` | auto | explicit path to a Chromium-based binary (Brave/Chrome) |
| `--proxies` | `proxies.txt` | proxy file |
| `--locations` | `locations.txt` | location file |
| `--keywords` | `keywords.txt` | keyword file |
| `--output-dir` | `results` | folder for the per-location CSV files |
| `--output` | — | (deprecated) old single-file path; its folder is used as `--output-dir` |
| `--threads` | `2` | concurrent workers; each = one full browser (~300-600MB RAM) |
| `--chunk-size` | `500` | process jobs in bounded chunks (scales to millions of jobs) |
| `--block-resources` | `media` | abort heavy sub-resources: `none`/`images`/`media`/`all` |
| `--max-results` | `0` | max listings per keyword+location; `0` = unlimited (all) |
| `--nav-timeout` | `45` | seconds to wait for a page to start loading |
| `--proxy-attempts` | `3` | proxies to try per job (dead proxy AND page-load failures) |
| `--preflight-timeout` | `12` | seconds for the proxy reachability check |
| `--no-preflight` | off | skip the pre-launch proxy reachability check |
| `--check-proxies` | off | test every proxy against Google (requests), print a report, exit |
| `--diagnose` | off | deep-test ONE proxy in a real browser (neutral → Maps), exit |
| `--proxy-index` | `0` | which proxy line `--diagnose` uses |
| `--geoip` | off | Camoufox only: enable exit-IP geolocation spoofing |
| `--scrape-websites` | `yes` | `yes`/`no` — visit business sites for email + socials |
| `--captcha-provider` | `none` | `none`, `2captcha`, or `captchaai` |
| `--captcha-key` | — | API key for the captcha provider |
| `--captcha-host` | auto | override the provider API host (2captcha-compatible) |
| `--captcha-retries` | `2` | extra re-solves if the captcha isn't accepted |
| `--headless` / `--headed` | headless | headless (VPS) vs. visible window (needs display/xvfb) |
| `--min-delay` / `--max-delay` | `2` / `5` | random seconds between listings |
| `--cooldown-every` | `25` | force a long pause after N requests (`0` disables) |
| `--cooldown-min` / `--cooldown-max` | `25` / `60` | length of the cool-off pause |
| `--no-stealth` | off | disable the anti-detection injection |
| `--no-enrich` | off | alias for `--scrape-websites no` |
| `--no-proxy` | off | run direct (no proxies) — quick local testing |
| `--selftest` | off | launch engine, check stealth + cache flush, exit |
| `--setup` | off | run the interactive first-run wizard and save `config.json` |
| `--config` | `config.json` | path to the config file (role, telegram, cluster, users, scrape) |
| `--role` | (from config) | `standalone`, `coordinator`, `coordinator+worker`, or `worker` |
| `--bot` | off | single-machine Telegram bot (alias for coordinator+worker on this host) |
| `--bot-config` | `bot_config.json` | (legacy) old single-file bot config; prefer `--config` |
| `--telegram-token` | — | bot token (overrides config) |
| `--telegram-users` | — | comma-separated admin user ids (overrides config) |
| `--fresh` | off | ignore saved session; start over (default: auto-resume) |
| `--skip-setup` | off | don't auto-install deps/browser/Brave on startup |
| `--force-setup` | off | re-run dependency/browser setup |
| `--debug` | off | full tracebacks |

## Command cookbook (every flag, many combinations)

On **Windows** use `python`; on **macOS/Linux** use `python3`. Long commands use
`\` line-continuation (macOS/Linux). On Windows, put it all on one line or use
`^` for continuation.

### 1. First run, setup & diagnostics

```bash
# Show all flags
python gmaps_scraper.py --help

# Verify the browser/stealth/cache stack for each engine (no Google traffic)
python gmaps_scraper.py --selftest --engine chrome
python gmaps_scraper.py --selftest --engine google-chrome
python gmaps_scraper.py --selftest --engine edge
python gmaps_scraper.py --selftest --engine brave
python gmaps_scraper.py --selftest --engine camoufox

# Force a fresh dependency + browser (+ Brave) install
python gmaps_scraper.py --engine brave --force-setup --selftest

# Use an environment you manage yourself; never auto-install
python gmaps_scraper.py --engine chrome --skip-setup --selftest

# Test whether your proxies can actually reach Google, then exit
python gmaps_scraper.py --check-proxies
python gmaps_scraper.py --check-proxies --proxies my_proxies.txt --preflight-timeout 20
```

### 2. Simplest real runs (one engine each)

```bash
# Chrome, default everything (unlimited results, websites scraped, headless)
python gmaps_scraper.py --engine chrome

# Real Google Chrome (auto-installed)
python gmaps_scraper.py --engine google-chrome

# Microsoft Edge (auto-installed)
python gmaps_scraper.py --engine edge

# Brave (auto-detected / auto-installed)
python gmaps_scraper.py --engine brave

# Camoufox (proxies optional; auth or no-auth both fine)
python gmaps_scraper.py --engine camoufox

# Chrome/Brave at an explicit binary path
python gmaps_scraper.py --engine brave --browser-path "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
python gmaps_scraper.py --browser-path "C:\Program Files\Google\Chrome\Application\chrome.exe"
```

### 3. Custom input files + output folder

Output is always **one CSV per location** inside `--output-dir`.

```bash
# Point at your own proxy / location / keyword files; write files into leads/
python gmaps_scraper.py --engine chrome \
  --proxies proxies.txt \
  --locations locations.txt \
  --keywords keywords.txt \
  --output-dir leads

# Different campaign, its own folder
python gmaps_scraper.py --engine brave \
  --keywords dentists.txt --locations texas_cities.txt --output-dir tx_dentists
```

### 4. Quick local test with no proxies (direct connection)

```bash
# One keyword/city, capped, direct connection — fastest way to see it work
python gmaps_scraper.py --engine brave --no-proxy --threads 1 --max-results 5 \
  --keywords test_keywords.txt --locations test_locations.txt --output-dir test_out

# Direct connection, Google Maps fields only (no website visits)
python gmaps_scraper.py --engine chrome --no-proxy --scrape-websites no --max-results 10
```

### 5. Results volume & website scraping

```bash
# Load EVERY business per keyword+city (default), visit each website
python gmaps_scraper.py --engine brave --max-results 0 --scrape-websites yes

# Cap at 50 businesses per search
python gmaps_scraper.py --engine chrome --max-results 50

# Google Maps data only (no email/socials) — faster; two equivalent ways
python gmaps_scraper.py --engine brave --scrape-websites no
python gmaps_scraper.py --engine brave --no-enrich
```

### 6. Proxy reliability tuning (for flaky / datacenter proxies)

```bash
# Try up to 6 proxies per job, generous timeouts (slow proxies)
python gmaps_scraper.py --engine brave \
  --proxy-attempts 6 --preflight-timeout 20 --nav-timeout 60

# Trust proxies without pre-checking them (skip preflight)
python gmaps_scraper.py --engine chrome --no-preflight --nav-timeout 40

# Aggressive fast-fail for large proxy pools
python gmaps_scraper.py --engine brave \
  --proxy-attempts 3 --preflight-timeout 8 --nav-timeout 30 --threads 8
```

### 7. Pacing / anti-ban tuning

```bash
# Gentle: long random pauses + frequent cool-offs (protect scarce proxies)
python gmaps_scraper.py --engine brave \
  --min-delay 5 --max-delay 12 --cooldown-every 10 --cooldown-min 45 --cooldown-max 120

# Fast: short pauses, no cool-off (only with many good proxies + lots of RAM)
python gmaps_scraper.py --engine chrome \
  --min-delay 1 --max-delay 3 --cooldown-every 0 --threads 10

# Low-footprint for a small VPS (1 browser, block everything heavy incl. CSS)
python gmaps_scraper.py --engine chrome --threads 1 --block-resources all

# Load images too (heavier; rarely needed for scraping)
python gmaps_scraper.py --engine chrome --block-resources none

# Disable stealth injection (debugging only)
python gmaps_scraper.py --engine chrome --no-stealth --debug
```

### 8. CAPTCHA auto-solving

```bash
# 2captcha
python gmaps_scraper.py --engine chrome --captcha-provider 2captcha --captcha-key YOUR_KEY

# CaptchaAI
python gmaps_scraper.py --engine brave --captcha-provider captchaai --captcha-key YOUR_KEY

# Custom 2captcha-compatible endpoint
python gmaps_scraper.py --engine chrome \
  --captcha-provider 2captcha --captcha-key YOUR_KEY --captcha-host https://api.myprovider.com
```

### 9. Headless vs. headed

```bash
# Headless (default) — ideal for a VPS
python gmaps_scraper.py --engine chrome --headless

# Headed on macOS/Windows (a real window)
python gmaps_scraper.py --engine brave --headed --threads 1

# Headed on a Linux VPS with no desktop, via virtual framebuffer
xvfb-run -a python3 gmaps_scraper.py --engine chrome --headed --threads 2
```

### 10. Full production runs (many flags combined)

```bash
# Brave, datacenter-proxy-hardened, unlimited results, websites + captcha, gentle pacing
python gmaps_scraper.py \
  --engine brave \
  --proxies proxies.txt --locations locations.txt --keywords keywords.txt \
  --output-dir results \
  --threads 4 --max-results 0 \
  --scrape-websites yes \
  --proxy-attempts 5 --preflight-timeout 15 --nav-timeout 50 \
  --min-delay 4 --max-delay 9 --cooldown-every 15 --cooldown-min 30 --cooldown-max 90 \
  --captcha-provider 2captcha --captcha-key YOUR_KEY \
  --headless

# Chrome, high throughput with a large residential pool, no website scraping
python gmaps_scraper.py \
  --engine chrome \
  --proxies residential.txt --output-dir maps_data \
  --threads 12 --max-results 0 --scrape-websites no \
  --proxy-attempts 2 --preflight-timeout 8 --nav-timeout 30 \
  --min-delay 1.5 --max-delay 4 --cooldown-every 40

# Camoufox, maximum stealth, capped results, captcha via CaptchaAI
python gmaps_scraper.py \
  --engine camoufox \
  --proxies proxies.txt --output-dir stealth_run \
  --threads 2 --max-results 100 \
  --min-delay 6 --max-delay 14 --cooldown-every 8 --cooldown-min 60 --cooldown-max 150 \
  --captcha-provider captchaai --captcha-key YOUR_KEY \
  --nav-timeout 60 --preflight-timeout 20
```

### 11. The "kitchen sink" (every non-exclusive flag at once)

```bash
python gmaps_scraper.py \
  --engine brave \
  --browser-path "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" \
  --proxies proxies.txt --locations locations.txt --keywords keywords.txt \
  --output-dir results \
  --threads 2 --max-results 0 --block-resources media \
  --nav-timeout 50 --proxy-attempts 5 --preflight-timeout 15 \
  --min-delay 3 --max-delay 8 \
  --cooldown-every 20 --cooldown-min 30 --cooldown-max 90 \
  --captcha-provider 2captcha --captcha-key YOUR_KEY --captcha-host https://2captcha.com \
  --scrape-websites yes \
  --headless \
  --force-setup \
  --debug
```

> **Mutually exclusive / special flags:** `--headless`/`--headed`,
> `--scrape-websites no`/`--no-enrich` (same effect), `--skip-setup` vs
> `--force-setup` (don't combine), `--no-proxy` (ignores your proxy file), and
> `--output` (deprecated → use `--output-dir`). `--geoip` applies to Camoufox
> only. `--selftest`, `--check-proxies`, and `--diagnose` each run and then exit
> without scraping.

## Anti-bot / stealth measures

Applied to every page before any site script runs (Chrome engine; Camoufox adds
its own stronger fingerprint spoofing on top):

- `navigator.webdriver` forced to `undefined`, and the `--enable-automation`
  switch + `AutomationControlled` feature are stripped at launch.
- `navigator.plugins`, `languages`, `hardwareConcurrency`, `deviceMemory` and
  `platform` set to realistic human values.
- `window.chrome` runtime object restored; `permissions.query` patched.
- WebGL `UNMASKED_VENDOR`/`UNMASKED_RENDERER` spoofed to real GPU strings.
- **Random user-agent** chosen per browser session from a modern desktop pool,
  with a randomised **timezone** and `en-US` locale.
- Camoufox additionally rotates the fingerprint OS and (with `geoip`) matches
  locale/timezone to the proxy's IP, and humanises cursor movement.

Disable with `--no-stealth` for debugging.

## Proxy-ban protection (pacing)

- Random pause between every listing (`--min-delay`..`--max-delay`).
- Randomised scroll timing while loading results.
- A longer random pause between jobs.
- A **cool-off**: every `--cooldown-every` requests the worker sleeps
  `--cooldown-min`..`--cooldown-max` seconds. Increase these and lower
  `--threads` if you see CAPTCHAs.

## Loading all businesses (exhaustive scroll)

The results feed is scrolled to the very bottom repeatedly, re-collecting place
links after each lazy-load batch, until Google shows the real *"You've reached
the end of the list"* marker (or the count stops growing after many patient
retries — tolerant of slow proxies). Links are de-duplicated in order, so no
business is skipped or double-counted. `--max-results 0` (the default) means
unlimited; set a positive number to cap per search.

## Website scraping (email + socials)

Controlled by `--scrape-websites yes|no` (default `yes`). When on, each business
website found on Google Maps is fetched — plus its likely `/contact` and
`/about` pages, through the same proxy — to extract an email and social links.
Set `no` (or `--no-enrich`) to collect Google Maps fields only and run faster.

## CAPTCHA auto-solving (2captcha / CaptchaAI)

If Google shows a reCAPTCHA or "unusual traffic" wall, the scraper can solve it
automatically:

```bash
python gmaps_scraper.py --captcha-provider 2captcha  --captcha-key YOUR_KEY
python gmaps_scraper.py --captcha-provider captchaai --captcha-key YOUR_KEY
```

Both services share the same `in.php`/`res.php` API (CaptchaAI host:
`https://ocr.captchaai.com`). The scraper detects the reCAPTCHA site key on the
page, submits it to your provider, polls for the solved token, injects it, and
continues. `--captcha-host` overrides the API host if your account uses a
different endpoint. Without a provider configured, a captcha wall is logged and
that job is skipped (results already saved are kept).

**Key is validated at startup.** When you pass `--captcha-provider`, the scraper
checks your key/balance before scraping and **aborts with a clear message if the
key is rejected** (e.g. `ERROR_WRONG_USER_KEY`), so you don't waste a run. Get
your key from your provider dashboard (CaptchaAI: https://captchaai.com/config).

**Retries if a solve is rejected.** After injecting the token the scraper
reloads and **verifies the captcha actually cleared**. If it didn't — a
wrong/expired token, or Google served a fresh challenge — it re-solves, up to
`--captcha-retries` extra times (default 2). If it still can't clear it, that one
search is skipped (everything already saved is kept) and the run continues.

## Cache flushing

Because each `(keyword, location)` job runs in a **fresh browser context**, and
because `clear_browser_data()` is called at the start of every job, the browser
**cache, cookies, localStorage and sessionStorage are wiped whenever the keyword
or city changes**. On Chrome this also issues the CDP
`Network.clearBrowserCache` / `Network.clearBrowserCookies` commands.

## Running on an Ubuntu VPS with no desktop

Headless is the default and needs **no X server / desktop**:

```bash
python gmaps_scraper.py --engine chrome --threads 4
```

If you specifically want "headed" mode (some sites behave differently), you do
**not** need a real desktop — run under a virtual framebuffer:

```bash
sudo apt-get install -y xvfb
xvfb-run -a python gmaps_scraper.py --engine chrome --headed --threads 2
```

Verify the whole browser/stealth/cache stack on the VPS before a real run:

```bash
python gmaps_scraper.py --selftest --engine chrome
# expect: navigator.webdriver = None, plugins > 0, window.chrome = True, flush OK, RESULT: PASS
```

## Output: one CSV per location

Results are saved into `--output-dir` (default `results/`) as **one CSV per
city/state/country**, so each location gets its own file containing **all
keywords** searched for that location. Files are named:

```
<City_State_Country>_<YYYY-MM-DD_HH-MM-SS>.csv
```

For example a run over Austin + Miami with keywords `dentist, plumber,
coffee shop` produces:

```
results/Austin_Texas_USA_2026-07-11_14-05-30.csv     # dentist + plumber + coffee shop in Austin
results/Miami_Florida_USA_2026-07-11_14-05-30.csv    # dentist + plumber + coffee shop in Miami
```

Each file is created the moment that location's first result is found, written
**row-by-row and fsync-flushed instantly** (nothing is lost if interrupted), and
**de-duplicated within that location** (a business found under two keywords is
saved once). All files in a run share the same start timestamp so they sort
together. The run's end summary prints the path of every file written.

### Columns (in every file)

`keyword, query_location, name, address, phone, email, website,
review_count, category, latitude, longitude, opening_hours, facebook,
instagram, twitter, linkedin, youtube, tiktok, pinterest, whatsapp,
telegram, maps_url`

| Column | Source |
|--------|--------|
| `review_count` | Place panel rating row (`aria-label` / `(N)` text), else JSON-LD `aggregateRating.reviewCount` |
| `category` | Category chip under the title (`button[jsaction*="category"]` / `button.DkEaL`), else JSON-LD `@type` |
| `latitude` / `longitude` | Place URL `!3d…!4d…` (preferred), else `@lat,lng`, else JSON-LD `geo` |
| `opening_hours` | Hours control (`button[data-item-id="oh"]`) — expanded Mon–Sun table when available, else collapsed summary; else JSON-LD `openingHours` / `openingHoursSpecification` |

Empty when Google does not show the field (new listings, hidden category/hours, etc.).

## Watching progress

The program prints progress the whole way through — useful with very large
keyword/location lists where startup itself takes a moment.

**Loading phase** (before scraping) shows each step so it never looks frozen:

```
Loading inputs...
[load] reading proxies from proxies.txt ...
[load] proxies loaded: 50
[load] reading locations from locations.txt ...
[load] locations loaded: 1200
[load] reading keywords from keywords.txt ...
[load] keywords loaded: 40
[load] job space: 40 keywords x 1200 locations = 48,000 jobs (streamed in chunks; not held in RAM)
[load] checking for a resumable session ...
```

**During scraping**, a live line prints as jobs finish (how many of the total
are done, percent, rows saved so far, elapsed and ETA), plus a `[chunk]`
checkpoint line after each chunk:

```
[progress] 12040/48000 jobs (25.1%) | rows 83156 | elapsed 2h14m | ETA 6h39m | last: dentist | Austin, Texas, USA
[chunk] checkpoint 12000/48000 jobs (25.00%)
```

Alongside it, each job logs its own steps (`start`, cache flush, `found N
listings`, `saved: <business>`, `done`). At the end you get a summary with the
completed count and every output file path.

**Save the log to a file** (recommended for long runs) so you can watch it live
and keep a record:

```bash
# Windows PowerShell
python gmaps_scraper.py --engine chrome --output-dir leads *>&1 | Tee-Object run.log

# macOS / Linux
python3 gmaps_scraper.py --engine chrome --output-dir leads 2>&1 | tee run.log
```

Then watch it in another terminal with `Get-Content run.log -Wait` (Windows) or
`tail -f run.log` (macOS/Linux). Add `--debug` for full tracebacks on errors.

## Modes & first-run setup

The same program runs in one of four **modes**, chosen once and saved to
`config.json`:

| Mode | What it does |
|------|--------------|
| **standalone** | Just scrape on this machine (no Telegram, no cluster) — the classic CLI |
| **coordinator** | Runs the Telegram bot + splits/dispatches jobs to workers; does **not** scrape itself |
| **coordinator+worker** | Telegram bot **and** scrapes on this machine too |
| **worker** | No Telegram; connects to a coordinator and runs the chunks it's given |

**First run:** launch the program with no arguments. If there's no `config.json`
it starts an **interactive wizard** that asks which mode to use and configures
it. On later launches it reads `config.json` and just runs. Re-run the wizard any
time with `--setup`, or edit `config.json` by hand.

```bash
python gmaps_scraper.py            # first run → wizard; later runs → per config
python gmaps_scraper.py --setup    # re-run the wizard
python gmaps_scraper.py --role worker   # override the role for this launch
```

> If you pass explicit scrape flags (e.g. `--engine chrome --keywords ...`), the
> program runs **standalone** immediately and never shows the wizard. The wizard
> only appears on a truly bare first launch.

## Telegram control (single machine)

The simplest setup: one machine that is both bot and scraper.

**Step 1 — create the bot.** In Telegram, message **@BotFather**, send `/newbot`,
follow the prompts, and copy the **token**.

**Step 2 — get your user id.** Start your program (below), then in Telegram send
your bot **`/whoami`** — it replies with your numeric id. (Works before you're
authorized, so you can bootstrap.)

**Step 3 — run the wizard** and choose **coordinator+worker**:

```bash
python gmaps_scraper.py --setup
# choose 3, paste the token, paste your user id (becomes ADMIN), accept defaults
python gmaps_scraper.py         # now runs the bot per config.json
```

That's it — you're the admin, and you drive everything from Telegram. Keep the
process running (a VPS, `tmux`/`screen`, or a service).

## Distributed setup (many servers, one Telegram bot)

Connect several machines so one job is split across them and finishes faster —
**no machine ever runs the same keyword+location as another**.

### How it works

- **One coordinator** owns the Telegram bot (Telegram allows only one poller per
  token) and the **scheduler**. It splits each job's `keyword × location` space
  into chunks and **leases each chunk to exactly one worker**. Dead workers'
  chunks are re-assigned. When all chunks finish (or you `/stop`), results from
  all workers are **merged, de-duplicated, zipped, and sent to you** in Telegram.
- **Workers** connect to the coordinator over HTTP, report their **live CPU/RAM**,
  pull chunks (idlest workers get more), scrape them, and upload results. Workers
  only ever talk to their configured coordinator (shared secret) — they accept no
  other commands and expose no inbound ports.

### Networking (recommended: Tailscale — free)

Workers need to reach the coordinator's IP:port. The easy, secure, cross-platform
way is **[Tailscale](https://tailscale.com)** (free tier): install it on every
machine, and each gets a stable private `100.x.y.z` address with no open public
ports. Alternatively, open the coordinator's port on your firewall/router and use
its public IP (protected by the shared secret).

### Step-by-step

**On the coordinator machine:**

1. Install Python 3, then get the program files (`gmaps_scraper.py`).
2. (Optional) install Tailscale and note this machine's `100.x.y.z` address.
3. Run the wizard and pick mode **2 (coordinator)** or **3 (coordinator+worker)**:

   ```bash
   python gmaps_scraper.py --setup
   ```

   Enter:
   - **Telegram token** (from BotFather)
   - **Your Telegram user id** (becomes admin — send `/whoami` to the bot to get it)
   - **Port** workers connect to (default `8787`)
   - **Cluster secret** (press Enter to auto-generate — **copy it**, workers need it)

4. Start it and leave it running:

   ```bash
   python gmaps_scraper.py
   ```

   Note the printed `coordinator_url = http://THIS_HOST:8787` and the secret.

**On each worker machine:**

1. Install Python 3 and get the program files.
2. (Optional) install Tailscale (same tailnet as the coordinator).
3. Run the wizard and pick mode **4 (worker)**:

   ```bash
   python gmaps_scraper.py --setup
   ```

   Enter:
   - **Coordinator URL** — e.g. `http://100.x.y.z:8787` (Tailscale IP) or the
     coordinator's public `http://IP:8787`
   - **Cluster secret** — the exact secret from the coordinator
   - **Worker name** — anything (defaults to the hostname)

4. Start it:

   ```bash
   python gmaps_scraper.py
   ```

   It registers with the coordinator and waits for work. Add as many workers as
   you like (any mix of Windows/macOS/Linux) — repeat these steps on each.

**Verify:** in Telegram, send your bot `/servers` (admin) — you should see every
connected worker with its live CPU/RAM.

**Each worker needs its own** `proxies.txt` (workers use their local proxies),
and a browser gets auto-installed on first job per the engine in your config.

### Config file reference (`config.json`)

You can edit this directly instead of (or after) the wizard:

```json
{
  "role": "coordinator+worker",
  "telegram": { "token": "123:ABC", "notify_interval_sec": 300 },
  "cluster": {
    "secret": "shared-secret-string",
    "bind_host": "0.0.0.0", "bind_port": 8787,   // coordinator
    "coordinator_url": "http://100.x.y.z:8787",  // worker
    "worker_name": "server-1",                    // worker
    "lease_timeout_sec": 120
  },
  "chunk_size": 500,
  "users": {
    "123456789": { "role": "admin", "perms": {} },
    "222222222": { "role": "user",  "perms": { "can_run": true, "can_stop": false, "max_threads": 2, "allowed_engines": ["chrome"] } }
  },
  "scrape": { "engine": "chrome", "threads": 2, "keywords": "keywords.txt",
              "locations": "locations.txt", "proxies": "proxies.txt", "output_dir": "results" }
}
```

### Roles & permissions

- **Admin** — full control: add/remove users, set roles and per-user permissions,
  run/stop, view servers.
- **User** — only what admin grants. Permission keys: `can_run`, `can_stop`,
  `can_configure`, `can_upload_inputs`, `max_threads` (caps their threads),
  `allowed_engines` (`"all"` or a list). Users can't see or affect each other —
  every chat is a private DM, and user-management commands are admin-only.

### Telegram commands

**Everyone:** `/whoami` (your id), `/help`.

**Users (if permitted):**

| Command | Action |
|---------|--------|
| `/run [key=value …]` | Start a job with your uploaded inputs + config defaults; inline overrides e.g. `/run engine=brave threads=2` |
| `/status` | Live progress: searches done/total, %, chunks, businesses, workers + their load, queue |
| `/stop` | Stop **your** running job; partial results are still merged and sent |
| *(upload a `.txt`)* | Send a document with caption **`keywords`** or **`locations`** to set your inputs |

**Admins also:**

| Command | Action |
|---------|--------|
| `/servers` | List connected workers with live CPU/RAM |
| `/users` | List users and roles |
| `/adduser <id>` | Add a user |
| `/removeuser <id>` | Remove a user |
| `/setrole <id> admin\|user` | Change a user's role |
| `/setperm <id> can_run=1 max_threads=4 …` | Set a user's permissions |

### Results delivery

When a job completes **or** you `/stop` it, the coordinator merges every worker's
CSVs (de-duplicated, one file per location), zips them, and sends the ZIP(s) to
the requesting user. Telegram caps bot uploads at 50 MB, so larger results are
**automatically split into multiple `_partN.zip` files**.

## Subscriptions & payments (sell access)

Turn the bot into a paid service: users buy a subscription package before they can
run jobs, and each package sets their limits. Billing is **off by default** — turn
it on in `config.json` under `"billing"`.

### How it works

- **Packages**: you define any number of plans, each with `price_usdt`,
  `duration_days`, `threads` (their max threads), `max_upload_mb` (their upload
  limit), and a `tier` number (higher = better).
- **Payment methods** (show only what you enable):
  - **USDT (TRC-20)** — the user sends USDT to **your** receiving address, then
    submits the transaction id; the bot verifies it **on-chain** (correct wallet,
    USDT token, amount ≥ price, confirmed, and the TxID never used before) via the
    free TronScan API, and activates the subscription automatically.
  - **Manual** — you list bank/other details; the user pays and you `/approve` them.
- **Expiry**: when a subscription ends, that user id can't run jobs until they
  **renew or buy** again (`/packages`).
- **Upgrade-only**: while a subscription is active, a user can buy the **same or a
  higher tier**, never a lower one. After expiry they can pick anything.
- **Enforcement**: their `threads` and upload-size limits come from their package
  (e.g. a 5-thread plan is capped at 5 no matter what they pass to `/run`), and
  uploads are checked against your allowed extensions and their size limit.
- **Non-custodial & safe**: the program stores only your **public** wallet
  address — never private keys. TxIDs are single-use (replay-proof), the cluster
  secret is compared in constant time, payment verification is rate-limited, and
  all admin actions are admin-only.

### Configure (`config.json` → `billing`)

```json
"billing": {
  "enabled": true,
  "usdt": {
    "enabled": true,
    "network": "trc20",
    "wallet_address": "YOUR-TRON-USDT-RECEIVING-ADDRESS",
    "contract": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
    "api_base": "https://apilist.tronscanapi.com",
    "api_key": ""
  },
  "manual": {
    "enabled": true,
    "methods": [ { "name": "Bank Transfer", "details": "Bank X, IBAN YY, Ref: your Telegram id" } ]
  },
  "upload": { "allowed_extensions": [".txt", ".csv"], "max_upload_mb": 5 },
  "packages": [
    { "id": "basic", "name": "Basic", "tier": 1, "price_usdt": 10, "duration_days": 30, "threads": 2,  "max_upload_mb": 2 },
    { "id": "pro",   "name": "Pro",   "tier": 2, "price_usdt": 25, "duration_days": 30, "threads": 5,  "max_upload_mb": 10 },
    { "id": "max",   "name": "Max",   "tier": 3, "price_usdt": 60, "duration_days": 30, "threads": 12, "max_upload_mb": 50 }
  ]
}
```

- Set `usdt.enabled` / `manual.enabled` independently — a disabled method is
  **hidden** from users. If neither is enabled, buying is disabled.
- `allowed_extensions` and the global `max_upload_mb` gate uploads; each package's
  `max_upload_mb` overrides the size limit for that user.
- To require a fresh public API key for reliability, paste it into `usdt.api_key`.

### Buyer flow (Telegram)

| Command | Action |
|---------|--------|
| `/packages` | See plans, prices, limits, and available payment methods |
| `/buy <id>` | Order a plan → get payment instructions (wallet address and/or manual details) |
| `/paid <txid>` | (USDT) Submit your transaction id → auto-verified on-chain → subscription activated |
| `/subscription` | Your current plan, expiry, and days left |
| `/renew <id>` | Same as `/buy` (same/higher tier) |

`/packages`, `/buy`, `/paid`, and `/subscription` work **before** a user is
authorized, so anyone can buy access. Everything else still requires an active
subscription (admins bypass billing).

### Admin billing commands

| Command | Action |
|---------|--------|
| `/pending` | List users with an open order |
| `/approve <user_id> <package_id>` | Grant/activate a plan (used for manual payments) |

### Get your USDT receiving address

Use any TRON wallet (e.g. TronLink, or an exchange's TRC-20 USDT deposit address).
Paste the **public address** (starts with `T…`) into `usdt.wallet_address`. The
bot only ever **reads** the chain to confirm deposits to that address.

## Security notes

Built-in protections:

- **No private keys** anywhere — only your public receiving address is stored.
- **Payment replay protection** — each USDT TxID can be used once.
- **On-chain checks** — recipient wallet, USDT token contract, amount ≥ price, and
  confirmation status are all verified before activating a subscription.
- **Constant-time secret comparison** for the cluster HTTP secret (timing-safe);
  workers expose **no inbound ports** (they only make outbound requests).
- **Rate limiting** — per-user command limit, and a stricter limit on `/paid`
  verification attempts.
- **Admin-only gating** on all user/permission/billing-admin commands; users are
  isolated in private DMs and can't see or affect each other.
- **Input/file validation** — allowed extensions + size caps on uploads, typed
  coercion of settings (no code execution), numeric-id checks.

Your responsibilities (important):

- Keep `config.json` private: it holds your bot token and cluster secret. On
  Linux/macOS run `chmod 600 config.json`.
- Put the coordinator behind **Tailscale** (or a firewall); don't expose the
  worker API port to the public internet unprotected.
- Use a **strong cluster secret** (the wizard auto-generates one).
- No software is "unhackable." This is a hardened, sensible setup — not a
  guarantee. Keep Python and dependencies updated, and run the coordinator on a
  machine you trust.

## Handling very large job lists (millions of keyword × location)

`Total jobs = keywords × locations`, which can be enormous (e.g. 10k × 10k =
100,000,000). The scraper is built so this **does not** blow up your RAM/CPU:

- Jobs are **generated lazily** (streamed) — the full list is never built in
  memory. Startup shows the count computed by multiplication, instantly.
- Work runs in **bounded chunks** (`--chunk-size`, default 500). Only that many
  jobs are ever queued at once, so memory and CPU stay flat whether you have
  1,000 or 1,000,000,000 jobs.
- Progress is **checkpointed after every chunk** using a tiny cursor (a single
  number), so the session file stays a few bytes even at massive scale.

```bash
# 100M+ jobs, smooth on modest hardware
python gmaps_scraper.py --engine chrome --output-dir leads --threads 2 --chunk-size 500
```

Tuning: `--chunk-size` doesn't change memory much (chunks are small either way);
it just controls how often progress is checkpointed. Keep `--threads` sized to
your RAM (each thread is one browser). If a run still stresses the machine, lower
`--threads` and use `--block-resources all`.

## Clean shutdown (kills all browsers)

Stopping the program **kills everything it started** — all worker threads, every
browser (headless *and* headed), and all their child processes — so nothing is
left running in the background. This is triggered by:

- **Ctrl+C** (SIGINT)
- **`kill <pid>`** (SIGTERM)
- **closing the terminal / SSH session** (SIGHUP on macOS/Linux)
- **Windows console break** (SIGBREAK) and normal exit (atexit backstop)

On shutdown it hard-kills the child process tree (using `psutil`, auto-installed)
so no orphaned Chromium/Firefox survives, then stops the thread pool without
waiting. Progress is checkpointed first, so you can relaunch to resume. You'll
see:

```
[shutdown] received SIGINT — killing browsers and all child processes...
[interrupted] stopping. Progress is saved — relaunch the same command to resume from where it stopped.
```

Note: a hard `kill -9` (SIGKILL) can't be trapped by any program, so run a normal
`kill`/Ctrl+C to get the clean teardown. On Windows, clicking the console's X
button is handled on a best-effort basis; Ctrl+C is the most reliable stop.

## Pause & resume (crash-safe)

Stop anytime and pick up where you left off. Progress is checkpointed after each
chunk to a tiny file `<output-dir>/.gmaps_session.json` (just a cursor + your
input fingerprint).

- **To pause:** stop the program (Ctrl+C, close the terminal, or let the VPS
  reboot). Everything already scraped is on disk.
- **To resume:** run **the exact same command again**. It auto-detects the saved
  session, **skips the jobs already processed**, reuses the same timestamp so it
  **appends to the same per-location CSVs** (de-duplicating against what's already
  there), and continues from the cursor.
- **Nothing is lost:** jobs that failed (proxy couldn't load Maps, captcha not
  cleared, crash) are written to `failed_jobs_<timestamp>.txt` in the output
  folder (`keyword <tab> location <tab> reason`) so you can review or re-run them.
- **Start over:** add `--fresh` to ignore the saved session and begin anew.

```bash
# First run (interrupted partway)
python gmaps_scraper.py --engine chrome --output-dir leads

# ...later: same command resumes from the checkpoint automatically
python gmaps_scraper.py --engine chrome --output-dir leads

# Ignore the checkpoint and restart from scratch
python gmaps_scraper.py --engine chrome --output-dir leads --fresh
```

The session is keyed to your keywords + locations + engine. Change any of those
and it safely starts fresh (a resume wouldn't line up). When all jobs finish, the
checkpoint file is deleted automatically. Resume granularity is one chunk, so at
most `--chunk-size` jobs are re-done after an interruption.

## Troubleshooting

### Every job fails with `navigation timed out` and 0 rows

The browser can't load Google Maps through the proxy. **Important:** a proxy can
pass `--check-proxies` (a trivial `requests` call to a Google endpoint that is
never bot-checked) yet still fail to load Maps in a real browser, because Google
blocks the IP at the *Maps application layer*. To find out which is happening,
run the deep diagnosis on one proxy:

```bash
python gmaps_scraper.py --diagnose                 # tests proxy #0 in a real browser
python gmaps_scraper.py --diagnose --proxy-index 3 # test a specific proxy line
```

It runs three steps — `requests` → Google, browser → a neutral site, browser →
Google Maps — and prints a verdict:

- **"Browser reaches the internet but Google MAPS times out"** → the IP is
  blocked by Google at the Maps layer. This is the usual result for
  **datacenter proxies** (ColoCrossing ranges like `107.174.x`, `107.175.x`,
  `104.168.x`). No code change fixes it — **use residential or mobile proxies.**
- **"The BROWSER can't use the proxy"** (but `requests` can) → a proxy-auth /
  config issue. Keep `--engine chrome`, verify credentials, keep `--disable-quic`
  (now on by default).
- **"Maps loaded but served a CAPTCHA"** → the IP is flagged; use residential
  proxies or set `--captcha-provider`/`--captcha-key` to auto-solve.
- **"Everything works!"** → a normal run will scrape.

What the scraper already does to help: preflights each proxy and **retries with
a fresh proxy on any failure** — a dead proxy *and* a live proxy that can't load
the page or clear a captcha — up to `--proxy-attempts` times per job (default 3).
It **fast-fails** navigation (`--nav-timeout`, default 45s) instead of hanging,
and **disables QUIC** so Chromium doesn't stall on Google domains behind an HTTP
proxy. Tune `--preflight-timeout`/`--nav-timeout` up for slow-but-valid proxies,
raise `--proxy-attempts` if you have many proxies, and keep `--threads` ≤ your
count of *working* proxies. Jobs that fail every attempt are written to
`failed_jobs_<timestamp>.txt` in the output folder.

### The VPS/terminal froze or became very slow

Each thread runs a **separate full browser** (~300–600 MB RAM + CPU). Running
several at once — especially while they retry on slow/blocked proxies — can
exhaust a small VPS and make everything crawl. Fixes now built in and how to
tune:

- **Default `--threads` is 2** (was 4). Raise it only if you have the RAM: roughly
  `RAM_in_GB × 1.5` threads is a safe ceiling. On a 2 GB VPS use `--threads 1`.
- **Heavy resources are blocked by default** (`--block-resources media` — images,
  media, fonts). This cuts memory and bandwidth a lot and speeds up loads.
  Business data is text, so nothing useful is lost. Use `--block-resources all`
  to also drop CSS (leanest), or `none` to load everything.
- **Memory-lean browser flags** are always applied (disabled extensions,
  background networking, sync, audio, capped JS heap, etc.).

```bash
# Lightest footprint for a small VPS
python gmaps_scraper.py --engine chrome --threads 1 --block-resources all

# Balanced (default-ish) on a modest box
python gmaps_scraper.py --engine chrome --threads 2 --block-resources media
```

Tip: bad proxies make this worse — every job holds a browser open for the full
`--nav-timeout` while it retries. Run `--check-proxies` / `--diagnose` first so
you're not launching browsers that are doomed to time out.

### Camoufox: "Failed to get IP address" / "Sync API inside the asyncio loop"

Fixed. These came from Camoufox's `geoip` exit-IP lookup running inside worker
threads. `geoip` is now **off by default**; enable it only if your proxies allow
an IP lookup:

```bash
python gmaps_scraper.py --engine camoufox --geoip
```

### It loads but finds 0 businesses / empty fields

Google occasionally changes the Maps DOM. The selectors live in `scrape_place`
and `scroll_results` (`div[role="feed"]`, `data-item-id="address"`, etc.). Run
with `--headed` (via xvfb on Linux) and `--debug` to watch what happens.

## Testing status (important)

All non-browser logic is unit-tested and passing, including a whole-program
integration test that runs the full `run_job` pipeline against a simulated
browser. Verified: proxy parsing across every supported format (auth optional on
all engines), randomised pacing + cool-off, stealth-script contents, OS-consistent
`navigator.platform`, Brave binary detection **and per-OS auto-install**
(brew/apt-script/winget, mocked), `--browser-path` validation, platform-aware
launch args (macOS/Linux/Windows), auto-install bootstrap (pip + browser),
**exhaustive scroll loading every business with no skips/duplicates**,
`--scrape-websites` toggle, **2captcha/CaptchaAI solver** (submit→poll→token,
mocked) and reCAPTCHA site-key detection, **proxy credential URL-encoding for
special characters, proxy preflight, per-job proxy rotation, fast-fail
navigation, and the `--check-proxies` reporter**, instant/threaded/deduplicated
CSV writing (rows confirmed on disk before close), cache-flush wiring, CLI
parsing of every flag, and the **cluster layer** — first-run wizard for every
role, roles/permissions enforcement, the setup config, **exactly-once chunk
leasing (no duplicates) in-process AND over the real HTTP API with two workers**,
lease reassignment on worker death, coordinator command handling + admin-only
gating, document (keyword/location) upload, and the **merge → de-duplicate → ZIP
(with 50 MB split) → deliver** finalize flow. **Billing** is also fully unit-tested:
subscription activation/expiry/admin-bypass, per-package thread + upload-size caps,
**upgrade-only (no downgrade while active)**, **TRC-20 verification parsing**
(valid / insufficient / wrong-wallet / unconfirmed / not-found), **TxID replay
protection**, rate limiting, the buy→/paid→activate flow, `/approve`, and
hiding of unconfigured payment methods.

The live distributed run (real Telegram + multiple machines + real browsers)
must be exercised in your environment — the build sandbox has no browsers, no
Telegram reachability, and one host. All the coordination logic it depends on is
unit-tested as above.

The end-to-end browser run against live Google Maps must be executed in **your**
environment (it needs a downloaded browser binary and network access to Google,
both of which are blocked in the build sandbox). Use `--selftest` first — it
confirms the engine launches, stealth is applied, and the cache flush works, all
without contacting Google.

## Notes

- Keep `--threads` ≤ proxy count.
- Selectors target Google Maps' current DOM (`role="feed"`,
  `data-item-id="address"`, `data-item-id^="phone:tel:"`,
  `a[data-item-id="authority"]`, rating-row review counts, category chip,
  `data-item-id="oh"` hours). Coordinates come from the settled place URL
  (`!3d`/`!4d`). Google changes these periodically; if fields come back empty,
  adjust them in `scrape_place` / `scroll_results`.
- Respect Google's Terms of Service and applicable law (including how you use
  scraped emails, e.g. GDPR / CAN-SPAM).
