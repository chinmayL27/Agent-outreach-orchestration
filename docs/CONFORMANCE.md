# MVP conformance: PRD v1.0 / TDD v1.0

What is implemented, where, and how it was verified — including the five items
that are **not** fully met.

Verified on the bundled offline campaign (`outreach run campaign.demo.yaml`)
and a 94-test suite (`pytest -q`, ~9s, no network, no model, no mail).

---

## 1. PRD §5 — MVP success criteria

| Criterion | Status | Evidence |
|---|---|---|
| Process at least 20 leads | ✅ | `DISCOVERY: 20 ok` — `test_one_command_processes_twenty_leads_into_the_review_queue` |
| Complete packages for ≥5 qualified leads | ✅ | 5 premium leads with demo + video + email; same test |
| All generation stages through `REVIEW_REQUIRED` in one command | ✅ | `outreach run campaign.demo.yaml` |
| Resume/retry without duplicate leads or sends | ✅ | `test_pipeline_stages_are_idempotent`, `test_approved_lead_is_sent_once_and_never_resent` |
| One lead's failure does not end the batch | ✅ | per-lead try/except in every stage; `test_leads_without_a_website_are_partial_not_invented` |
| No email sent in generation runs or tests | ✅ | `run` has no send stage; `test_send_refuses_leads_that_were_never_approved`; tests use recording providers |

Day-2 milestone (one clinic → enrichment → demo → MP4 → email): ✅ — two
complete packages with 60.1s playable mp4s in the verification run above.

## 2. PRD §3 — workflow stages

| Stage | Where | Verified by |
|---|---|---|
| Find clinics/providers | `extraction/nppes.py` (NPPES, fixture), `extraction/csv_source.py` (bring-your-own list) | `test_extraction.py`, `test_nppes_client.py`, `test_csv_import.py` |
| Normalize and deduplicate | `extraction/normalize.py` | `test_dedupe_key_*`, `test_merge_raw_leads_*` |
| Discover + enrich from public sites | `extraction/website.py`, `enrichment/` | `test_integration.py`, `test_crawler_safety.py` |
| Score with rules | `scoring/lead_score.py` | `test_email_and_scoring.py` |
| Personalized sales angle | `personalization/` | `test_packet_generates_grounded_personalization` |
| Customized demo | `demo/` | `test_demo_config_and_page_are_built_from_the_lead` |
| ~60-second video | `video/` | `test_demo_page_records_to_a_video_artifact` |
| Outreach email | `outreach/email_generator.py` | `test_email_rendering.py` |
| Human approval | `outreach/approval.py`, `outreach review` | `test_editing_after_approval_invalidates_it` |
| Send + record result | `outreach/sender.py`, `SendRecord` | `test_uncertain_send_is_distinct_and_never_auto_resent` |

## 3. TDD section-by-section

| § | Requirement | Status | Notes |
|---|---|---|---|
| 2 | Deterministic orchestration of typed workers, SQLite state | ✅ | `orchestration/pipeline.py`; no agent framework, no queue |
| 3 | Python/Pydantic/SQLAlchemy/httpx/BeautifulSoup/Playwright/Jinja2/Typer/pytest/FFmpeg | ✅ | TDD says 3.12+; package declares `>=3.11`, verified on 3.12 |
| 4 | Repository structure | ✅ | plus `outreach/publisher.py`, `enrichment/enricher.py`, `llm/factory.py`, `observability/`, `util/` |
| 5 | Campaign YAML + env config, no committed credentials | ✅ | `campaign.yaml`, `.env.example`, `.env` git-ignored |
| 6 | Lead / Evidence / Enrichment / Personalization / Campaign / StageExecution / Artifact / Outreach / Suppression | ✅ | `Lead` is the ORM entity; Pydantic is used at the LLM boundary and for config. Evidence carries a supporting excerpt; artifacts carry hash + version |
| 7 | State machine, idempotency, package versioning, no exactly-once claim | ✅ | `orchestration/state.py`; approval binds to `package_version`; uncertain sends never auto-retried |
| 8 | `LeadSource` protocol, NPPES adapter, bounded pagination | ✅ | Google Places / Hunter remain future adapters, as the PRD intends |
| 9 | Bounded crawl, robots, destination restrictions, redirect revalidation, escaping | ✅ | `util/http.py`; 16 tests in `test_crawler_safety.py` |
| 10 | Configurable weights, unknown ≠ false, stored signal contributions | ✅ | `score_breakdown` on every lead |
| 11 | Evidence-grounded packets, validated claim ids | ✅ | `personalization/lead_packet.py`, `check_grounding` |
| 12 | Provider abstraction, retry-once, caching, subprocess isolation | ✅ | 4 providers; `LLMCacheEntry`; no shell, scrubbed env, throwaway cwd, tools disabled |
| 13 | Reusable JSON-configured demo, labelled, synthetic, non-diagnostic | ✅ | `demo/`; clinical questions deflect to the practice |
| 14 | Playwright capture → FFmpeg mp4, scene timing, duration inspected | ✅ | 60.1s verified; `duration_in_range` and `playable` persisted; captions instead of TTS |
| 15 | 50–120 word email, sender identity, postal address, opt-out, review actions | ✅ | rendering fails without postal address / opt-out |
| 16 | Artifact publishing boundary, validate links before sending | ⚠️ | adapter + pre-send gate implemented; the check is populated-and-public-shaped, **not** a live HTTP fetch |
| 17 | Approval/suppression re-check, transactional reservation, uncertain ≠ failed | ⚠️ | all but cross-process reservation: single SQLite writer, no distributed lock |
| 18 | Suppression table, reasons, send-time enforcement, survives re-import | ✅ | `outreach/suppression.py` |
| 19 | Structured stage events, attempt, bounded errors, no credentials | ✅ | `StageEvent` with `attempt` + `input_fingerprint`; `outreach stats` |
| 20 | Crawler retry once; LLM retry once; video failure preserves demo; no blind resend | ✅ | `test_crawler_safety.py`, `test_provider_retries_once_then_succeeds` |
| 21 | CLI contract | ✅ | all commands present; `outreach review` is a group (`list`/`next`/`approve`/`reject`) |
| 22 | Unit / integration / e2e test plan | ✅ | see the table in README §2 |
| 23 | Security and privacy | ✅ | see README §9 |
| 24 | Prompt-injection defense with tool denial as the real boundary | ✅ | envelope + marker stripping + tool-less subprocess |
| 26 | Definition of done | ✅ | verification run above |

## 4. Not met — read before relying on these

1. **Live link-accessibility check (TDD §16).** Before sending, the pipeline
   verifies that demo/video URLs exist and are publicly *shaped*
   (`is_publicly_reachable`), and blocks real providers otherwise. It does not
   fetch them to confirm HTTP 200. A publisher that returns a URL for a file it
   failed to upload would not be caught.
2. **Cross-process send reservation (TDD §17).** Duplicate sending is prevented
   by a prior-attempt check inside the send transaction, which is sufficient
   for one SQLite writer. Two `outreach send` processes racing on the same
   database are not covered; don't run concurrent senders.
3. **JS-rendered enrichment (TDD §9).** Crawling is httpx + BeautifulSoup only.
   Playwright is used for video recording, not for rendering
   client-side-rendered clinic sites, so a React-only site enriches poorly.
4. **Website discovery confidence (TDD §8).** Websites come from a campaign
   `website_map`, a CSV import (a `website` column, or the domain of a supplied
   work email), or an email found later. There is no search-based discovery and
   therefore no domain-match confidence score or ambiguous-match review queue.
   Leads without a website stay `ENRICHMENT_PARTIAL`. A name derived from a
   domain during CSV import is a placeholder, replaced at enrichment by the name
   the site gives itself; until then it is visibly machine-made, and the human
   review gate is what catches it.
5. **Automated opt-out ingestion (PRD §4.8, TDD §18).** Opt-outs are recorded by
   an operator via `outreach suppress add`. Reply/bounce mailbox ingestion is
   future work — which is what the PRD scopes for the MVP, but it means
   compliance depends on the operator acting within 10 business days.

## 5. Deviations worth noting (deliberate)

* **A fourth LLM provider, `template`, is the default.** Deterministic
  assembly from the lead packet — no model, no network. It makes the pipeline
  demoable and CI hermetic before an LLM is configured; `LLM_PROVIDER` switches
  to `claude_cli`/`ollama`/`anthropic` with no other change.
* **`file://` crawling** exists so the bundled fixture campaign runs fully
  offline. It is off by default and gated behind `ALLOW_FILE_URLS`.
* **Lead is an ORM entity, not a Pydantic model.** The TDD sketches it as
  Pydantic; persistence, mutation tracking and query ergonomics argued for
  SQLAlchemy, with Pydantic kept where validation matters most (LLM I/O,
  campaign config, demo config, video script).
