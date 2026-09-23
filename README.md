# AI Healthcare Outreach Orchestrator

Local-first pipeline that discovers healthcare practices, enriches them from
public web data, scores them deterministically, writes evidence-grounded
outreach, builds a customized chatbot demo, records a ~60-second demo video,
and parks everything in a **human approval queue** before anything is sent.

**New here?** [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md) explains the whole
system in plain language, with a diagram — start there if you are not going to
read the code.

Implements [PRD.md](docs/PRD.md) and [TDD.md](docs/TDD.md); conformance and
known gaps are tracked in [CONFORMANCE.md](docs/CONFORMANCE.md).

```
campaign.yaml
     │
     ▼
 discover ──► enrich ──► score ──► personalize ──┬─► demo ──► video ──┐
 (NPPES /     (crawl     (rules,   (1 LLM call   │  (JSON    (Play-   │
  CSV /        + evidence  no LLM)  per lead,    │  config   wright   ├─► email ──► review
  fixture)     capture)             cached)      │  + HTML)  + ffmpeg)│    (copy +   (human)
                                                 │                    │   CAN-SPAM)    │
                                                 └─ basic tier ───────┘                ▼
                                                    (60-79: email only)        send ──► track
```

It is a **pipeline of typed workers**, not a swarm of agents talking to each
other: every stage consumes structured data and produces structured data, so
each one is debuggable, replayable and cheap. The only model call in a run is
one structured generation per qualified lead, cached by input fingerprint.

---

## 1. Quick start

Nothing below touches the network, an API key, an LLM, or a real mailbox. It
runs against a bundled registry payload and six sample clinic websites in
`tests/fixtures/`.

```bash
# 1. Install (Python 3.11+)
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,video,web]"
playwright install chromium        # only needed for the video stage

# 2. Config
cp .env.example .env               # defaults are safe: template LLM + console mailer

# 3. Run the whole pipeline offline, end to end
outreach run campaign.demo.yaml --limit 2
```

`--limit 2` caps how many leads get a personalized demo and video, so the run
takes about three minutes instead of ten. Expected output:

```
campaign: Offline demo campaign  |  LLM provider: template
DISCOVERY: 20 ok, 0 failed, 0 skipped (of 20)
ENRICHMENT: 6 ok, 0 failed, 14 skipped (of 20)
SCORING: 20 ok, 0 failed, 0 skipped (of 20)
PERSONALIZATION: 2 ok, 0 failed, 0 skipped (of 2)
DEMO: 2 ok, 0 failed, 0 skipped (of 2)
VIDEO: 2 ok, 0 failed, 0 skipped (of 2)
EMAIL: 2 ok, 0 failed, 0 skipped (of 2)

Nothing has been sent. Review with `outreach review next`.
```

Drop `--limit` (or pass `--limit 10`) to produce all six packages; add
`--skip-video` for a ~10 second run, or `--speed 6` to record 10-second videos
instead of 60-second ones.

### What just happened

```bash
outreach leads          # the funnel
outreach stats          # status counts, per-stage events, drafts and sends
outreach show <lead_id> # one lead: fields, score breakdown, evidence + sources
```

```
SCORE  STATUS               NAME                          EMAIL
  100  REVIEW_REQUIRED      ABC Dermatology, P.C.         info@abcdermatology.example
   90  REVIEW_REQUIRED      Bay Area Dental Care          hello@bayareadental.example
   90  REVIEW_REQUIRED      Golden Gate Family Medicine   contact@ggfamilymed.example
   90  REVIEW_REQUIRED      Mission Urgent Care           frontdesk@missionurgent.example
   90  REVIEW_REQUIRED      Willow Glen Pediatrics        info@willowglenpeds.example
   60  REVIEW_REQUIRED      Bayview Family Medicine       hello@bayviewfamily.example
   25  BACKLOG              Sunnyvale Skin Clinic         -
   ...
```

Twenty leads were discovered from the fixture registry. A hospital, a
university health center and a county health department were excluded by the
campaign rules; two individual NPI records were merged into their practices.
Fourteen leads have no resolvable website (exactly like the real registry) so
they stay `ENRICHMENT_PARTIAL`/backlog rather than being enriched against a
guessed domain.

Five practices scored 80+ and got the full treatment. Bayview scored 60: it
already runs an Intercom widget and publishes no FAQ, so it takes the
**basic-email branch** — no demo, no video, just a drafted email.

| Path | What it is |
|---|---|
| `artifacts/demos/<lead_id>.json` | demo configuration (no per-lead code) |
| `artifacts/demos/<lead_id>.html` | the standalone personalized demo page |
| `artifacts/videos/<lead_id>.mp4` | ~60-second captioned recording |
| `artifacts/outbox/*.eml` | messages the console sender "sent" |
| `outreach.db` | leads, evidence, drafts, sends, suppressions, events, cache |

Open a demo page in a browser and try the chat — it answers from the clinic's
own published services and locations.

### Review, approve, send

```bash
outreach review list           # everything waiting
outreach review next           # [A]pprove / [R]eject / [E]dit / [S]kip
outreach send                  # APPROVED only; console provider by default
ls artifacts/outbox/           # the .eml that would have gone out
```

`outreach run` never sends. `outreach send` only touches leads a human moved to
`APPROVED`, re-checks the suppression list immediately before delivery, refuses
a package whose approval is stale, and will not resend one that was already
attempted.

---

## 2. Test instructions

### Automated suite

```bash
pytest -q                      # 106 tests, ~9 seconds
pytest -q --durations=5        # see where the time goes
pytest tests/test_pipeline_e2e.py -q -v   # the MVP criteria, named individually
```

The video test records a real browser session (~5s at 20x speed) and skips
itself automatically when Playwright's Chromium is not installed. Every test
uses a temporary database and artifacts directory; **no test can send email** —
the send path is exercised through recording providers only.

| File | Covers |
|---|---|
| `test_extraction.py` | registry parsing, organization/provider identity, dedupe keys, exclusions, website resolution |
| `test_csv_import.py` | CSV header aliasing, email-only rows, list-not-refiltered, merge/dedupe, import → crawl → services |
| `test_nppes_client.py` | live-API query construction and pagination, against a mock transport |
| `test_email_and_scoring.py` | public email extraction/ranking, scoring signals, unknown-vs-false, configurable weights, suppression |
| `test_crawler_safety.py` | private/loopback/link-local/metadata refusal, DNS rebinding, redirect revalidation, retry-once, file:// opt-in |
| `test_state_and_llm.py` | state transitions, approval gate, prompt envelope, injection hygiene, JSON/schema validation and retry limit |
| `test_llm_isolation_and_cache.py` | subprocess isolation (no shell, scrubbed env, throwaway cwd, tools off), result caching by fingerprint |
| `test_integration.py` | website → sourced facts, packet → grounded personalization, personalization → demo config → escaped page |
| `test_video.py` | scene timing, 60-second shape, demo page → playable artifact |
| `test_email_rendering.py` | CAN-SPAM footer, unreachable-link suppression, console provider output |
| `test_pipeline_e2e.py` | the full campaign: 20 leads, ≥5 packages, basic branch, approval invalidation, suppression, uncertain sends, idempotency |

### Manual verification

Each of these is a claim worth checking yourself rather than taking on trust.

**1. One command reaches review and sends nothing.**

```bash
rm -f outreach.db && outreach run campaign.demo.yaml --skip-video
outreach stats | grep -A3 Outreach     # drafts: 6   sent: 0
```

**2. Generated copy is grounded in captured evidence.**

```bash
outreach show $(outreach leads | awk 'NR==2 {print $NF}' | head -1) | head -40
```

Every service, provider and FAQ line carries the page URL it came from, plus a
surrounding excerpt. Compare the drafted email against that list — the
grounding checks reject hype, invented metrics and unknown evidence ids, but a
human still reads the actual sentences.

**3. Nothing sends without approval.**

```bash
outreach send --yes            # "nothing approved to send"
```

**4. Approval binds to an exact package version.**

```bash
LEAD=$(sqlite3 outreach.db "select id from leads where status='REVIEW_REQUIRED' limit 1")
outreach review approve $LEAD
sqlite3 outreach.db "select package_version, approved_package_version from email_drafts where lead_id='$LEAD'"
# edit the copy, then watch the approval drop and the lead return to review
```

**5. Suppression is enforced at send time, not just at discovery.**

```bash
outreach suppress add info@abcdermatology.example --reason UNSUBSCRIBED
outreach send --yes            # the lead is skipped and marked SUPPRESSED
```

**6. The crawler refuses internal destinations.**

```bash
python -c "from app.util.http import assert_public_http_url as c; c('http://169.254.169.254/latest/meta-data/')"
# BlockedDestination: 169.254.169.254 resolves to non-public address
```

**7. Videos are real, captioned and in-band.**

```bash
outreach video campaign.demo.yaml --limit 1 --force
ffprobe -v error -show_entries format=duration -of csv=p=0 artifacts/videos/*.mp4
sqlite3 outreach.db "select container, round(duration_seconds,1), duration_in_range, playable from videos"
```

---

## 3. Running it against real data

### Discovery: the NPI Registry

`campaign.yaml` is configured for the live CMS **NPPES / NPI Registry** — a
free public directory of providers, specialties and practice addresses. It
needs outbound HTTPS to `npiregistry.cms.hhs.gov`.

```bash
outreach discover campaign.yaml --city "San Jose" --state CA --specialty Dermatology --limit 20
outreach enrich && outreach score && outreach leads
```

Two things to know:

* **NPPES has no website field.** Enrichment is what produces everything
  valuable, and it needs a URL. Supply one via `website_map` in
  `campaign.yaml`, or import your own list (below). A lead with no known
  website stays `ENRICHMENT_PARTIAL` instead of being enriched against a
  guessed domain.
* **NPI registration is not proof of current licensure or credentialing.** CMS
  says so explicitly; don't present it as such. For bulk work use the CMS
  downloadable data file and feed it in as CSV — the Registry API is
  rate-limited.

### Discovery: bring your own leads (CSV)

Already have a list - a conference export, a CRM dump, a purchased file? Import
it and the rest of the pipeline is unchanged: crawl each site, extract services
with their source URLs, score, personalize, review.

```bash
outreach import-csv leads.csv --dry-run   # show the column mapping, write nothing
outreach import-csv leads.csv             # import
outreach enrich && outreach score
```

Or point a campaign at the file and use the normal `discover` command:

```yaml
# campaign.yaml
campaign:
  source: csv
  csv_path: leads.csv
```

A row needs **either a website or a work email** - an email's domain is the site
that gets crawled, so a list of addresses alone is enough to start:

```csv
practice_name,email,website,city,state,phone,specialty,services
Lakeside Dermatology,info@lakesidederm.example,https://lakesidederm.example,Austin,TX,512-555-0100,Dermatology;Cosmetic,Mohs surgery;Acne care
,frontdesk@cedar-peds-clinic.example,,Austin,TX,,Pediatrics,
```

See `leads.example.csv`. What the importer does with a file:

* **Headers are matched loosely.** Case, spacing and punctuation are ignored, so
  `Email`, `E-mail Address` and `email_address` all land on the same field.
  Recognized: practice/company/organization name, website/url/domain, email,
  city, state, zip/postal code, address, phone, specialty, services, provider/
  contact name, NPI. Columns it does not recognize are listed, not silently
  dropped.
* **An email is enough to start.** `frontdesk@cedar-peds-clinic.example` yields
  the site `cedar-peds-clinic.example` and the provisional name "Cedar Peds
  Clinic"; enrichment replaces that placeholder with the name the site calls
  itself. Free mailbox domains (gmail, outlook, …) are never mistaken for a
  practice website - those leads import, but stay `ENRICHMENT_PARTIAL`.
* **Your list is not re-filtered.** Campaign `specialties` and `geography`
  narrow a *registry search*; they do not cut rows you chose by hand.
  `exclude_keywords` still applies, because that is a do-not-contact policy.
* **Cells may hold several values,** separated by `;` or `|`. Commas also split
  emails, specialties and NPIs, but never provider names ("Smith, Jane") or
  service descriptions.
* **Rows for the same practice merge** (matched on domain, else name + place),
  pooling their emails and services. Re-importing an updated file adds no
  duplicates and backfills newly supplied contact details.
* **Asserted facts are labelled as such.** Services or emails supplied in the
  file get evidence rows citing `csv:<filename>`, never a page URL, so a
  reviewer can tell what you asserted from what the crawler observed.

Every skipped row is counted with a reason:

```
DISCOVERY: 2 ok, 0 failed, 0 skipped (of 2)
    - 4 row(s) read, 2 lead(s) after merge, 2 with a crawlable website
    - 1 lead(s) named from their domain until enrichment reads the site's own name
    - 1 row(s) skipped: matched an exclude keyword
    - 1 row(s) skipped: blank row
    - ignored column(s): Notes
```

### Publishing artifacts (required before sending)

A clinic cannot open `file:///...` or `http://localhost:8000/...`. Until you
configure a publishing destination, demos and videos stay local, emails carry
no demo link, and **a real mail provider refuses to send the package**:

```
Bay Area Dental Care: demo/video links are not public (configure PUBLIC_ARTIFACT_BASE_URL)
```

```bash
# .env
PUBLIC_ARTIFACT_BASE_URL=https://demos.yourcompany.com
```

`app/outreach/publisher.py` holds the boundary: `DirectoryPublisher` copies
artifacts into a directory your web host serves and returns public URLs;
swap in S3/R2/Netlify by implementing the same `publish()` method. The console
sender is exempt (it never delivers anywhere), which is what keeps the offline
demo runnable.

### Sending for real

```bash
# .env
EMAIL_PROVIDER=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@yourcompany.com
SMTP_PASSWORD=<app password, not your account password>
SENDER_NAME=Your Name
SENDER_EMAIL=you@yourcompany.com
SENDER_POSTAL_ADDRESS=123 Example St, Suite 100, San Jose, CA 95113
UNSUBSCRIBE_MAILTO=unsubscribe@yourcompany.com
```

`SENDER_POSTAL_ADDRESS` and `UNSUBSCRIBE_MAILTO` are **required** — email
rendering fails without them. US commercial email, including B2B, is subject to
CAN-SPAM: accurate headers, a non-deceptive subject, a valid physical postal
address, a working opt-out honored within 10 business days. The footer and the
`List-Unsubscribe` header are added by the system, never by the model. Record
opt-outs immediately:

```bash
outreach suppress add someone@clinic.example --reason UNSUBSCRIBED
```

Suppressed addresses are never re-imported into an active campaign.

---

## 4. LLM providers

The application never knows which provider is active; it asks for a Pydantic
model and gets one, or the stage fails.

| `LLM_PROVIDER` | What it does | Cost |
|---|---|---|
| `template` (default) | Deterministic assembly from the lead packet. No model, no network. | free |
| `claude_cli` | Shells out to the local `claude` binary, which draws on a Claude Pro/Max subscription. | subscription |
| `ollama` | Local model at `OLLAMA_HOST`. | free |
| `anthropic` | Anthropic API (`ANTHROPIC_API_KEY`) — separate from a Claude subscription. | per token |

```bash
outreach personalize campaign.yaml --provider claude_cli
```

Start on `template` to get the plumbing working, then switch one environment
variable. Prompt, schema, retry, grounding checks and caching are identical
across providers. Confirm your own CLI entitlement before relying on
`claude_cli`; the subscription-vs-API billing rules change.

**Output is validated, not trusted.** Invalid JSON is retried once, then the
stage is marked `FAILED` rather than guessed at. Copy that cites evidence ids
that don't exist, invents a business metric, drifts out of the length band, or
uses hype language is rejected, regenerated once with the specific complaints,
then failed. Validated results are cached by packet + product + prompt version,
so a failure downstream never forces paid regeneration.

**The model gets no tools.** The CLI subprocess runs from an argument array
(never a shell string), in a throwaway working directory, with the app's
credentials stripped from its environment and tool execution disabled where the
installed CLI supports the flag. Prompt delimiters are a hygiene layer; the
enforceable boundary is that the model can't reach anything.

---

## 5. Video

```bash
outreach video campaign.demo.yaml --limit 5
```

Playwright drives the personalized demo page through a scripted sequence —
intro card, branded assistant, patient question, answer, second question,
answer, benefit, CTA — Chromium records it, and ffmpeg converts it to mp4 with
a caption strip on every scene (no narration or TTS required).

* **Browser:** `playwright install chromium`. If a mismatched browser build is
  already on the machine, point at it with `PLAYWRIGHT_CHROMIUM_EXECUTABLE`;
  the recorder also auto-detects common install paths.
* **ffmpeg:** optional. Without an H.264-capable ffmpeg the pipeline keeps the
  `.webm` Chromium produced, records why on the artifact, and the lead still
  reaches review. (Playwright bundles an ffmpeg that only encodes VP8 — the
  recorder detects that and won't try to use it for mp4.)
* **Length:** scenes follow the 0-7 / 7-15 / 15-35 / 35-48 / 48-55 / 55-60
  second plan. Chromium's screencast stretches timestamps slightly while the
  page animates, so the file is re-timed during transcode to land on target.
  Duration and playability are then *verified* and stored
  (`duration_in_range`, `playable`); an out-of-band result is flagged for
  review rather than silently shipped.
* **Speed:** `--speed 6` shortens every beat for a quick smoke test.

If recording fails, the lead becomes `VIDEO_FAILED`, keeps its demo, and still
reaches review — the email just goes out without the walkthrough.

---

## 6. Demo app

Each lead gets configuration, not code: `artifacts/demos/<lead_id>.json` drives
a generic demo page. Serve the live version for links in email:

```bash
outreach serve                    # http://127.0.0.1:8000/demo/<lead_id>
```

Answers are assembled from the clinic's published services, locations, phone
and booking flow. Clinical questions are deflected to the practice — the demo
never gives medical advice — and every page carries a "demonstration only, no
patient data" banner.

---

## 7. Command reference

```
outreach init-db                      create the SQLite schema
outreach discover [campaign.yaml]     find practices (--city --state --specialty --limit --source)
outreach import-csv leads.csv         import your own list (--dry-run, --limit)
outreach enrich                       crawl websites, store facts + source URLs
outreach score                        deterministic score, qualify or backlog
outreach personalize                  one structured LLM call per qualified lead
outreach demo                         demo config + standalone demo page (premium tier)
outreach video                        record the ~60s walkthrough (--speed, --limit)
outreach email                        render the email, move to the review queue
outreach run [campaign.yaml]          everything above, stopping at REVIEW_REQUIRED
outreach review list|next|approve|reject
outreach send                         APPROVED only (--provider, --attach-video, --yes)
outreach suppress add|list
outreach leads | show <id> | stats
outreach serve                        demo app at /demo/{lead_id}
```

Every stage takes `--force` to redo completed work; without it, stages are
idempotent and skip leads that have moved past them. `-v` prints one structured
JSON event per stage execution, with attempt number and input fingerprint.

---

## 8. How it works

### Evidence grounding

Every fact extracted from a website is stored with the page it came from and a
supporting excerpt:

```
service       | Mohs Surgery            | https://clinic.example/services  | "…Our Services Skin Cancer Screening Acne Treatment … Mohs Surgery…"
faq           | Do I need a referral?   | https://clinic.example/faq       | "…Patient FAQ Do I need a referral to be seen? Most PPO plans…"
no_chatbot    | No chat widget detected | https://clinic.example/          | …
```

Personalization sees that evidence, a clinic summary and the product
capabilities — nothing else. The prompt forbids inventing services, providers,
technologies, patient volumes, revenue or business metrics, and the result is
checked against the packet before it is stored.

Detection is three-valued: `has_chatbot` is `true` (found), `false` (not found
on the pages inspected) or `null` (never looked). Only a confirmed absence
scores points.

### Scoring (deterministic, no tokens)

| Signal | Points |
|---|---:|
| Target specialty | +25 |
| Providers within the configured range | +15 |
| Multiple locations | +10 |
| Online booking present | +10 |
| **No chat widget detected** | +20 |
| ≥5 published FAQ questions | +10 |
| Public contact email | +10 |

`≥80` premium (demo + video + email), `60–79` basic (email only), `<60`
backlog. Weights and thresholds live in `campaign.yaml`. Each signal's
contribution is stored so a score can explain itself in review.

### State machine

```
DISCOVERED → ENRICHED → SCORED → QUALIFIED → PERSONALIZED → DEMO_READY
          → VIDEO_READY → REVIEW_REQUIRED → APPROVED → SENT
                                ↑
          PERSONALIZED ─────────┘  (basic tier: no demo, no video)
```

with `ENRICHMENT_PARTIAL`, `BACKLOG`, `VIDEO_FAILED`, `SEND_FAILED`,
`SEND_UNCERTAIN`, `REJECTED`, `FAILED`, `SUPPRESSED` and `UNSUBSCRIBED` as
documented alternates. Transitions are enforced — `REVIEW_REQUIRED → SENT` is
impossible — and one lead's failure never stops the batch.

`SEND_UNCERTAIN` is deliberately distinct from `SEND_FAILED`: if the connection
drops after the server may have accepted the message, that is recorded as
uncertain and **never** retried automatically.

### Package versioning

An approval binds to an exact recipient and package version. Editing the copy,
or regenerating the package, bumps `package_version`, drops the approval and
returns the lead to review. `send` refuses anything whose approval is stale.

### Layout

```
app/
  extraction/       registry + CSV sources, website resolution, email, normalization
  enrichment/       crawler, service/provider/location extraction, tech detection
  scoring/          deterministic lead score
  llm/              provider interface + template/claude_cli/ollama/anthropic, prompts
  personalization/  lead packet + generation + grounding checks
  demo/             config generator, Jinja renderer, FastAPI server
  video/            script builder, Playwright recorder, ffmpeg
  outreach/         email rendering, senders, publisher, suppression, approval
  orchestration/    pipeline stages + state machine
  observability/    stage events
```

---

## 9. Safety and compliance

* **No PHI.** Only public professional/business information is collected. Demo
  conversations are synthetic. Nothing patient-identifying is stored.
* **Crawler destinations are restricted.** http(s) only, resolved addresses
  checked against private/loopback/link-local/reserved ranges (so cloud
  metadata endpoints and DNS-rebinding tricks are refused), every redirect hop
  re-validated, same-domain only, page budget and rate limit enforced, robots
  respected by default.
* **Website text is untrusted input.** It is cleaned, size-capped, stripped of
  anything resembling a system turn or envelope marker, wrapped in an explicit
  data envelope, and HTML-escaped wherever it is rendered.
* **The model has no tools, credentials or filesystem.** See §4.
* **Human approval is mandatory.** `run` cannot send; approvals expire when the
  package changes.
* **Suppression is checked immediately before delivery**, not just at drafting.
* **Credentials** come from the environment. `.env` is git-ignored.

## 10. Deliberately not built

Autonomous multi-agent conversations, autonomous high-volume sending, a CRM,
conversion ML, per-customer forks of the product, and any distributed
infrastructure. The MVP runs on a laptop with SQLite and a browser.

Known gaps worth knowing before scaling up: website discovery for registry
leads is manual (map or CSV import); service extraction is heuristic and will
need tuning per vertical; the video has captions but no narration track; and
there is no reply/bounce ingestion yet — opt-outs are recorded by hand, which
is what the PRD asks for in the MVP.
