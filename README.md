# AI Healthcare Outreach Orchestrator

Local-first pipeline that finds healthcare practices, enriches them from public
web data, scores them deterministically, writes evidence-grounded outreach,
builds a customized chatbot demo, records a ~60-second demo video, and parks
everything in a **human approval queue** before anything is sent.

```
campaign.yaml
     │
     ▼
 discover ──► enrich ──► score ──► personalize ──► demo ──► video ──► email
 (NPPES /     (crawl     (rules,   (1 LLM call     (JSON   (Play-    (copy +
  CSV /        + evidence  no LLM)  per lead)       config  wright   CAN-SPAM
  fixture)     capture)                             + HTML) + ffmpeg) footer)
                                                                        │
                                                                        ▼
                                              review (human) ──► send ──► track
```

It is a **pipeline of typed workers**, not a swarm of agents talking to each
other: every stage consumes structured data and produces structured data, so
each one is debuggable, replayable and cheap. The only LLM call in the whole
run is one structured generation per qualified lead.

---

## 1. Quick start — test run it in 5 minutes

Nothing below touches the network, an API key, an LLM, or a real mailbox. It
uses the bundled fixture registry payload and two sample clinic websites in
`tests/fixtures/`.

```bash
# 1. Install (Python 3.11+)
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,video,web]"

# 2. Config
cp .env.example .env          # defaults are safe: template LLM + console mailer

# 3. Run the whole pipeline offline, end to end
outreach run campaign.demo.yaml
```

Expected output:

```
campaign: Offline demo campaign  |  LLM provider: template
DISCOVERY: 2 ok, 0 failed, 0 skipped (of 2)
ENRICHMENT: 2 ok, 0 failed, 0 skipped (of 2)
SCORING: 2 ok, 0 failed, 0 skipped (of 2)
PERSONALIZATION: 1 ok, 0 failed, 0 skipped (of 1)
DEMO: 1 ok, 0 failed, 0 skipped (of 1)
VIDEO: 1 ok, 0 failed, 0 skipped (of 1)
EMAIL: 1 ok, 0 failed, 0 skipped (of 1)

Nothing has been sent. Review with `outreach review next`.
```

The video stage takes about a minute of real time (it records a real browser
session at real speed). To skip it or speed it up:

```bash
outreach run campaign.demo.yaml --skip-video      # no recording
outreach run campaign.demo.yaml --speed 6         # ~10s recording instead of 60s
```

### What just happened

```bash
outreach leads          # the funnel so far
outreach stats          # status counts + per-stage event counts
outreach show <lead_id> # one lead: fields, score breakdown, evidence + sources
```

```
SCORE  STATUS               NAME                     EMAIL                        WEBSITE
  100  REVIEW_REQUIRED      ABC Dermatology, P.C.    info@abcdermatology.example  file:///...
   35  BACKLOG              Bayview Family Medicine  hello@bayviewfamily.example  file:///...
```

Two clinics were discovered from the fixture registry (a hospital in the same
payload was excluded by the campaign rules, and two NPI records for the same
practice were merged into one lead). The dermatology practice scored 100 — target
specialty, 4 providers, 2 locations, online booking, **no chat widget**, 6 FAQ
questions, a public contact address — so it got the full treatment. Bayview
already runs an Intercom widget and published no FAQ, so it scored 35 and went
to the backlog instead of being contacted.

Artifacts:

| Path | What it is |
|---|---|
| `artifacts/demos/<lead_id>.json` | demo configuration (no per-lead code) |
| `artifacts/demos/<lead_id>.html` | the standalone personalized demo page |
| `artifacts/videos/<lead_id>.mp4` | ~60-second recording (`.webm` if ffmpeg is missing) |
| `artifacts/outbox/*.eml` | messages the console sender "sent" |
| `outreach.db` | leads, evidence, drafts, sends, suppressions, stage events |

Open the demo page in a browser (`open artifacts/demos/<lead_id>.html`) and try
the chat — it answers from the clinic's own published services and locations.

### Review, approve, send

```bash
outreach review list           # everything waiting
outreach review next           # walk the queue: [A]pprove / [R]eject / [E]dit / [S]kip
outreach send                  # sends APPROVED only; console provider by default
ls artifacts/outbox/           # the .eml that would have gone out, video attached
```

`outreach run` never sends. `outreach send` only touches leads a human moved to
`APPROVED`, checks the suppression list immediately before delivery, and refuses
to send twice to the same lead.

Try the guard rails:

```bash
outreach suppress add info@abcdermatology.example --reason UNSUBSCRIBED
outreach send                  # the lead is skipped and marked SUPPRESSED
```

### Run the tests

```bash
pytest -q                      # 64 tests, ~6 seconds
```

The suite covers registry parsing and dedupe, email extraction and ranking,
scoring, state transitions, prompt-injection defense, JSON/schema validation
with a retry, website→evidence enrichment, packet→personalization grounding,
demo rendering and escaping, a real (fast) video recording, and the full
fixture-to-review-queue run including approval, suppression and send failures.
The video test skips itself automatically if Playwright's Chromium is missing.

---

## 2. Running it against real data

### Discovery: the NPI Registry

`campaign.yaml` is configured for the live CMS **NPPES / NPI Registry** — a free
public directory of providers, specialties and practice addresses. It needs
outbound HTTPS to `npiregistry.cms.hhs.gov`.

```bash
outreach discover campaign.yaml --city "San Jose" --state CA --specialty Dermatology --limit 20
outreach enrich && outreach score && outreach leads
```

Two things to know about NPPES:

* **It has no website field.** Enrichment is what produces everything valuable,
  and it needs a URL. Supply one via `website_map` in `campaign.yaml`, or import
  your own list (below). A lead with no known website is parked as
  `ENRICHMENT_PARTIAL` rather than enriched against a guessed domain.
* **NPI registration is not proof of current licensure or credentialing.** CMS
  says so explicitly; don't present it as such.

CMS rate-limits the Registry API. For bulk work, use their downloadable data
file and feed it in as CSV.

### Discovery: bring your own leads

```yaml
# campaign.yaml
campaign:
  source: csv
  csv_path: leads.csv
```

```csv
organization_name,website,city,state,phone,specialty,email
ABC Dermatology,https://abcdermatology.com,San Jose,CA,408-555-0142,Dermatology;Cosmetic,
```

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
address, a working opt-out, honored within 10 business days. The footer and the
`List-Unsubscribe` header are added by the system, never by the model. When
someone opts out, record it immediately:

```bash
outreach suppress add someone@clinic.example --reason UNSUBSCRIBED
```

Suppressed addresses are never re-imported into an active campaign.

---

## 3. LLM providers

The application never knows which provider is active; it asks for a Pydantic
model and gets one, or the stage fails.

| `LLM_PROVIDER` | What it does | Cost |
|---|---|---|
| `template` (default) | Deterministic assembly from the lead packet. No model, no network. | free |
| `claude_cli` | Shells out to the local `claude` binary (`claude -p … --output-format json`), which draws on a Claude Pro/Max subscription. | subscription |
| `ollama` | Local model at `OLLAMA_HOST`. | free |
| `anthropic` | Anthropic API (`ANTHROPIC_API_KEY`). Separate from a Claude subscription. | per token |

```bash
outreach personalize campaign.yaml --provider claude_cli
```

Start on `template` to get the plumbing working, then switch one environment
variable. The prompt, the schema, the retry and the grounding checks are
identical across providers.

**Output is validated, not trusted.** Invalid JSON is retried once and then the
stage is marked `FAILED` rather than guessed at. Copy that cites evidence ids
that do not exist, invents a business metric, drifts out of the length band, or
uses hype language is rejected, regenerated once with the specific complaints,
and then failed.

---

## 4. Video

```bash
outreach video campaign.demo.yaml --limit 5
```

Playwright drives the personalized demo page through a scripted sequence
(intro card → branded assistant → patient question → answer → second question →
answer → benefit → CTA card), Chromium records it, and ffmpeg converts it to
mp4.

* **Browser:** `playwright install chromium`. If a mismatched browser build is
  already on the machine, point at it with `PLAYWRIGHT_CHROMIUM_EXECUTABLE`;
  the recorder also auto-detects common install paths.
* **ffmpeg:** optional. Without an H.264-capable ffmpeg the pipeline keeps the
  `.webm` Chromium produced, records the reason on the artifact, and the lead
  still reaches review. (Playwright bundles an ffmpeg that only encodes VP8 —
  the recorder detects that and does not try to use it for mp4.)
* **Length:** the script targets 60 seconds. Chromium's screencast stretches
  timestamps slightly while the page animates, so the final file is re-timed
  during transcode to land on target (~60s).
* **Speed:** `--speed 6` shortens every beat for a quick smoke test.

If recording fails, the lead becomes `VIDEO_FAILED`, keeps its demo, and still
moves to the review queue — the email simply goes out without an attachment.

---

## 5. Demo app

Each lead gets configuration, not code: `artifacts/demos/<lead_id>.json` drives
a generic demo page. Serve the live version for links in email:

```bash
outreach serve                    # http://127.0.0.1:8000/demo/<lead_id>
```

Demo answers are assembled from the clinic's published services, locations,
phone and booking flow. Clinical questions are deflected to the practice — the
demo never gives medical advice — and every page carries a "demonstration only,
no patient data" banner.

---

## 6. Command reference

```
outreach init-db                      create the SQLite schema
outreach discover [campaign.yaml]     find practices (--city --state --specialty --limit --source)
outreach enrich                       crawl websites, store facts + source URLs
outreach score                        deterministic score, qualify or backlog
outreach personalize                  one structured LLM call per qualified lead
outreach demo                         demo config + standalone demo page
outreach video                        record the ~60s walkthrough (--speed, --limit)
outreach email                        render the email, move to the review queue
outreach run [campaign.yaml]          everything above, stopping at REVIEW_REQUIRED
outreach review list|next|approve|reject
outreach send                         APPROVED only (--provider, --attach-video, --yes)
outreach suppress add|list
outreach leads | show <id> | stats
outreach serve                        demo app at /demo/{lead_id}
```

Every stage takes `--force` to re-run work that is already done; without it,
stages are idempotent and skip leads that have moved past them. `-v` prints one
structured JSON event per stage execution.

---

## 7. How it works

### Evidence grounding

Every fact extracted from a website is stored with the page it came from:

```
service       | Mohs Surgery                 | https://clinic.example/services
faq           | Do I need a referral?        | https://clinic.example/faq
no_chatbot    | No chat widget detected      | https://clinic.example/
```

Personalization is given a **lead packet** — that evidence, the clinic summary
and the product capabilities — and nothing else. The prompt forbids inventing
services, providers, technologies, patient volumes, revenue or business metrics,
and the result is checked against the packet before it is stored. This is why
generated copy can say "you list cosmetic dermatology across two locations" and
cite where it learned that.

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

`≥80` premium (demo + video), `60–79` basic (email only), `<60` backlog. Weights
and thresholds live in `campaign.yaml`, not in code. `has_chatbot: null` means
"could not tell" and earns nothing — only a confirmed absence scores.

### State machine

```
DISCOVERED → ENRICHED → SCORED → QUALIFIED → PERSONALIZED → DEMO_READY
          → VIDEO_READY → REVIEW_REQUIRED → APPROVED → SENT
```

with `ENRICHMENT_PARTIAL`, `BACKLOG`, `VIDEO_FAILED`, `SEND_FAILED`, `REJECTED`,
`FAILED`, `SUPPRESSED` and `UNSUBSCRIBED` as documented alternates. Transitions
are enforced (`REVIEW_REQUIRED → SENT` is impossible; approval is the only way
through), and a failure on one lead never stops the batch.

### Layout

```
app/
  extraction/   registry sources, website discovery, email, normalization
  enrichment/   crawler, service/provider/location extraction, tech detection
  scoring/      deterministic lead score
  llm/          provider interface + template/claude_cli/ollama/anthropic, prompts
  personalization/  lead packet + generation + grounding checks
  demo/         config generator, Jinja renderer, FastAPI server
  video/        script builder, Playwright recorder, ffmpeg
  outreach/     email rendering, senders, suppression, approval
  orchestration/  pipeline stages + state machine
  observability/  stage events
```

---

## 8. Safety and compliance

* **No PHI.** Only public professional/business information is collected. Demo
  conversations are synthetic. Nothing patient-identifying is stored.
* **Website text is untrusted input.** Page content is cleaned, size-capped,
  stripped of anything resembling a system turn or an envelope marker, and
  wrapped in an explicit data envelope the model is told never to follow
  instructions from. The crawler-facing path has no filesystem, shell, email or
  credential tools. Site text is HTML-escaped everywhere it is rendered.
* **Human approval is mandatory** in this version. `run` cannot send.
* **Suppression is checked immediately before delivery**, not just at drafting.
* **Credentials** come from the environment. `.env` is git-ignored.
* **Crawling** stays on-domain, obeys `robots.txt` by default, caps pages per
  site (`MAX_PAGES_PER_SITE`) and rate-limits itself (`CRAWL_DELAY`).

## 9. Deliberately not built

Autonomous multi-agent conversations, autonomous high-volume sending, a CRM,
conversion ML, per-customer forks of the product, and any distributed
infrastructure. The MVP runs on a laptop with SQLite and a browser.

Known gaps worth knowing about before you scale this up: website discovery for
NPPES leads is manual (map or CSV), service extraction is heuristic and will
need tuning per vertical, the video has no narration track (captions/TTS is the
obvious next step), and there is no reply/bounce ingestion yet — suppression is
recorded by hand.
