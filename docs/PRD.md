# AI Healthcare Outreach Orchestrator — Product Requirements Document

Version: 1.0  
Date: September 18, 2026  
Companion document: [TDD.md](TDD.md)

This document organizes the product framework, constraints, examples, and delivery plan from the preceding conversation. The companion TDD contains the development architecture and technical specification. TDD means Technical Design Document in this project.

## 1. Problem and objective

The founder sells a healthcare assistance chatbot and wants to automate finding potential clinic customers, researching their public information, preparing tailored demonstrations, producing approximately one-minute videos, and drafting and sending personalized outreach.

Build a local-first outreach pipeline that turns public clinic/provider information into evidence-grounded sales packages. Deliver an MVP in three development days, developing one working component at a time.

### Constraints

- Tight budget and minimal external API expenditure.
- Claude Max and Claude Code are available; ordinary Anthropic API credits are not.
- Initial execution should run on the founder's laptop.
- Each component must be replaceable independently.
- Use public professional/business information and synthetic demo conversations; do not collect or process patient PHI.
- Human approval precedes email sending in the MVP.
- Clinic-specific claims require captured evidence.

## 2. Users and target market

Primary operator: the founder or sales operator who configures a campaign, reviews lead information and generated outreach, approves or edits packages, and tracks sending outcomes.

Prospects: small and midsize clinics and medical practices whose patient-support workflows could benefit from FAQs, service navigation, and appointment routing.

Initial ideal customer profile:

```yaml
specialties:
  - family medicine
  - dermatology
  - pediatrics
  - dental
  - urgent care
locations:
  - San Francisco
  - San Jose
  - Oakland
practice_size:
  min_providers: 1
  max_providers: 30
exclude:
  - hospitals
  - universities
  - government
```

## 3. Product framework

Use deterministic orchestration with specialized workers. Each worker accepts structured input and returns structured output. One bounded LLM personalization step produces most of the creative material per qualified lead.

The workflow is:

1. Find clinics/providers.
2. Normalize and deduplicate leads.
3. Discover and enrich from public websites.
4. Score prospects using rules.
5. Generate a personalized sales angle.
6. Configure a customized chatbot demo.
7. Produce a roughly 60-second video.
8. Draft personalized outreach email.
9. Present the package for human approval.
10. Send approved outreach and record the result.

The orchestrator calls `extract_leads()`, `enrich_lead()`, `score_lead()`, `personalize_lead()`, `create_demo()`, `create_video()`, `create_email()`, `approve()`, and `send()`.

## 4. Requirements by stage

### 4.1 Lead extraction

Start from explicit geography, specialty, and practice-size criteria. Use NPPES/NPI Registry as the initial low-cost US provider source. Extract provider/organization name, specialty, practice address, phone, and NPI. Then discover the organization's website and crawl selected public pages.

Example public clinic record:

```json
{
  "clinic_name": "ABC Family Medicine",
  "website": "https://example.com",
  "specialties": ["Family Medicine"],
  "providers": ["Dr. Jane Smith"],
  "phone": "public business phone",
  "emails": ["info@example.com"],
  "appointment_url": "https://example.com/appointments",
  "services": [],
  "locations": [],
  "has_chatbot": false,
  "has_online_booking": true
}
```

Prefer publicly displayed clinic contact addresses. Optional future enrichment may use Hunter or another professional-contact provider. Store the source URL beside every extracted claim. NPI registration is not proof of current licensure or credentialing.

### 4.2 Enrichment

Turn a basic lead into a factual profile: services, provider count, location count, appointment links, observed booking technology, FAQ content, and detected chat features.

Example analysis: a dermatology practice with three locations, medical and cosmetic dermatology, online booking, and extensive FAQs may be a candidate for appointment questions, procedure-preparation navigation, and service routing. Describe potential use cases as analysis rather than established business problems.

Keep evidence-backed facts separate from inferred sales opportunities. If a crawler does not detect a chatbot, record the observation without claiming certainty that no chatbot exists.

### 4.3 Scoring and qualification

Use configurable rules instead of an LLM to score leads.

| Signal | Points |
| --- | ---: |
| Target specialty | 25 |
| 2–20 providers | 15 |
| Multiple locations | 10 |
| Online booking present | 10 |
| No chatbot detected in inspected pages | 20 |
| At least five FAQ items | 10 |
| Public contact email | 10 |

| Score | Action |
| --- | --- |
| 80–100 | Personalized email, demo, and video |
| 60–79 | Basic email outreach package |
| Below 60 | Backlog; no automatic personalization |

Unknown signals do not receive points. Future versions may adjust weights using conversion results.

### 4.4 Personalization

Provide the LLM a structured packet containing clinic identity, specialties, services, providers, website findings, product capabilities, and evidence. Generate the sales angle, potential use cases, demo scenario/questions, email subject/body, video introduction/outro, and references to the claims used in one structured generation.

Do not invent a service, provider, location, technology, operational problem, or business metric. Omit claims with insufficient evidence. Do not claim ROI, staffing pressure, patient volume, or costs without evidence.

### 4.5 Customized demo

Use a reusable demo UI configured by JSON at `/demo/{lead_id}`. Personalization is configuration rather than new application code per customer. Prefer integration with the existing chatbot product; otherwise build a small server-rendered demo for the MVP.

Configuration includes clinic name, optional logo, specialty, evidenced services, and sample questions. Example questions:

- How do I schedule a skin screening?
- Which location offers cosmetic treatments?
- Do I need a referral?

Questions are examples; answers must not invent clinic policies. Clearly label the page as a demonstration. Use synthetic interactions and avoid diagnostic or treatment recommendations.

### 4.6 One-minute video

Automate opening the demo, showing branding, entering two questions, displaying responses, showing appointment/service routing, and presenting a CTA. Capture the browser session with Playwright, then use FFmpeg to produce an MP4 with introduction, captions, and outro. Local/offline narration is optional; captions suffice for the MVP.

| Time | Scene |
| --- | --- |
| 0–7 seconds | Personalized introduction |
| 7–15 seconds | Branded assistant |
| 15–35 seconds | First question and response |
| 35–48 seconds | Second question and response |
| 48–55 seconds | Appointment/service routing or evidenced product benefit |
| 55–60 seconds | Call to action |

Acceptable MVP duration: 45–65 seconds.

Example intro: “Hi ABC Dermatology — I created a quick example of how our assistant could work on your site.”

Example outro: “Happy to build this against your actual workflow — reply if you'd like to see more.”

### 4.7 Email drafting and review

Target 50–120 words containing a factual observation, a specific demo explanation, video/demo URL, and one CTA. Avoid generic AI marketing language, fabricated ROI, fake familiarity, or fake prior interactions.

Example, usable only when the names, services, and locations are supported:

> Hi Jane,
>
> I was looking at ABC Dermatology and noticed you offer medical and cosmetic dermatology across your San Jose locations.
>
> I put together a 60-second example showing how an AI assistant could handle common patient questions and route patients toward the appropriate service.
>
> [Video/demo link]
>
> Would you be open to a quick conversation about it?

Alternative CTA: “Would it be worth spending 15 minutes looking at how this could fit your patient-support workflow?”

Review displays clinic, recipient, lead score, evidence, generated email, demo URL, and video, with approve, reject, and edit actions. A CLI review screen is sufficient.

### 4.8 Sending and suppression

Only explicitly approved packages may be sent. Use authenticated business email, Gmail OAuth, or SMTP. Store recipient, campaign, timestamp, provider message ID, and outcome. Check suppression before every send. Preserve unsubscribed, bounced, invalid, and manually blocked contacts across imports and campaigns. Do not automatically resend when delivery status is uncertain.

US commercial outreach requirements described in the prior discussion include accurate sender/header information, non-deceptive subjects, a valid postal address, opt-out capability, and honoring opt-outs within 10 business days. Include these in email templates and operator configuration.

## 5. MVP success criteria

With geography, specialty, and optional size criteria configured, the system supports discovery, deduplication, enrichment, scoring, personalization, demo generation, video production, email drafting, review, approved sending, and outcome/opt-out recording.

Target demonstration:

- Process at least 20 leads.
- Produce complete outreach packages for at least five qualified leads when the source data supports that qualification.
- Run all generation stages through `REVIEW_REQUIRED` using one command.
- Resume/retry stages without creating duplicate leads or sends.
- Keep one lead's failure from terminating the entire batch.
- Send no email in generation runs or automated tests.

The most important intermediate milestone is one clinic going through enrichment → personalized demo → MP4 → personalized email by the end of Day 2.

## 6. Non-goals

- Autonomous diagnosis or treatment advice.
- Processing PHI or private patient information.
- Full CRM functionality.
- Autonomous high-volume emailing.
- Free-form multi-agent conversations.
- Distributed infrastructure, Kubernetes, Kafka, Redis, Celery, Temporal, Airflow, or agent-framework dependencies.
- Machine-learning conversion optimization.
- Custom source-code generation for every clinic.
- Browser automation of the Claude website.

## 7. Budget and LLM strategy

Keep the pipeline local and deterministic wherever practical. Use one LLM call per qualified lead; cache validated outputs. Encapsulate model calls behind a provider interface so Claude CLI, Ollama/local models, and a future Anthropic API provider are interchangeable.

The preceding conversation distinguished Claude Max/Claude Code subscription usage from ordinary Anthropic API credits, and discussed non-interactive Claude CLI/Agent SDK subscription availability. That billing guidance is time-sensitive and was not newly verified for this document. Confirm current account entitlement before selecting the runtime provider; the architecture supports local generation if CLI entitlement is unavailable.

Do not require paid lead enrichment, paid TTS, or paid model APIs for the initial MVP. Website discovery and externally accessible demo/video hosting remain explicit integrations; local URLs are sufficient for operator review, while recipients require reachable links.

## 8. Three-day execution plan

| Day | Work | Exit milestone |
| --- | --- | --- |
| 1 | SQLite models; NPPES discovery; normalization/deduplication; website discovery/crawling; public contact extraction; evidence; rule scoring | 20–50 clean leads with evidence where discovery permits |
| 2 | Lead packets; LLM provider; validated personalization; reusable demo JSON/page; Playwright capture; FFmpeg video | One command converts one lead into email + demo + MP4 |
| 3 | Resumable state machine; review CLI; authenticated sender; suppression/opt-out process; logs; retries; batch operation | Process 20 leads, generate top-five packages, review and send approved outreach |

Suggested Day 1 milestone command: `python main.py discover --city "San Jose" --specialty dermatology`. The final CLI uses the command contract in the TDD.

Develop one complete stage at a time. Validate each phase end-to-end before moving to later phases. Batch processing follows a working single-lead path.

## 9. Risks and implementation dependencies

- Provider records may not map one-to-one to clinics. Preserve provider and organization identities and avoid merging solely on addresses.
- A public provider directory may lack websites or emails. Support seed URLs and imported leads as fallback inputs.
- Website feature detection is incomplete. Distinguish unknown, detected, and not detected.
- Schema validation and evidence IDs alone do not prove factual grounding. Review generated statements against the cited material.
- Arbitrary website text is untrusted. Prompt delimiters are only one layer; deny model access to tools, credentials, and sending.
- Browser timing and variable answers can disrupt video duration. Use controlled demo scenarios and explicit scene timing.
- Local artifacts need a reachable publishing destination before emailed links are useful.
- Mail-provider setup and sender configuration are required before live sending.

## 10. Definition of done

`outreach run campaign.yaml` discovers and processes the configured campaign, creates eligible personalized emails, demos, and approximately one-minute MP4s, and leaves complete packages in the review queue. Approved sending is a separate operator action. Evidence and suppression records persist, failures remain inspectable, and retries do not duplicate outreach.

## 11. References retained from the conversation

These links preserve the prior discussion's references rather than representing a new verification:

- [NPI Registry search/API entry point](https://npiregistry.cms.hhs.gov/search/generateAdvancedSearch.do): public provider directory, API/bulk-source considerations.
- [NPI Registry provider entry cited previously](https://npiregistry.cms.hhs.gov/provider-view/1831432343): prior discussion's licensure/credentialing caveat.
- [Hunter pricing](https://hunter.io/pricing): optional professional-email discovery/verification; the prior discussion mentioned 50 free monthly credits, subject to current pricing.
- [Claude subscription versus API billing](https://support.claude.com/en/articles/9876003-i-have-a-paid-claude-subscription-pro-max-team-or-enterprise-plans-why-do-i-have-to-pay-separately-to-use-the-claude-api-and-console).
- [Claude Agent SDK plan guidance](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan): time-sensitive CLI/SDK entitlement guidance.
- [FTC CAN-SPAM compliance guide](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business).
