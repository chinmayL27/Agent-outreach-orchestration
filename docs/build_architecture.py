"""Generate docs/architecture.svg - the stakeholder-facing pipeline diagram.

Run after editing:  python docs/build_architecture.py
Keeping the generator means the diagram can be corrected without hand-editing
SVG coordinates.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

W, H = 1240, 1000
FONT = "ui-sans-serif, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

INK, MUTED, LINE, BG, CARD = "#0f172a", "#55657a", "#cbd5e1", "#f7f9fc", "#ffffff"

KINDS = {
    "rules": ("#1d4ed8", "#eff6ff", "Rules-based code"),
    "ai": ("#7c3aed", "#f5f3ff", "AI writes once"),
    "browser": ("#0f766e", "#ecfdf5", "Browser automation"),
    "human": ("#b45309", "#fff7ed", "Human decision"),
    "stop": ("#64748b", "#f1f5f9", "Parked"),
}

out: list[str] = []


def add(markup: str) -> None:
    out.append(markup)


def text(x, y, content, size=13, weight=400, fill=INK, anchor="start", opacity=1.0):
    add(
        f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}" '
        f'opacity="{opacity}">{escape(content)}</text>'
    )


def box(x, y, w, h, kind, title, lines, number=None, title_size=15):
    accent, tint, _ = KINDS[kind]
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{CARD}" stroke="{LINE}" stroke-width="1"/>')
    add(f'<path d="M{x} {y+12} a12 12 0 0 1 12 -12 h0 v{h} h0 a12 12 0 0 1 -12 -12 z" fill="{accent}"/>')
    add(f'<rect x="{x+5}" y="{y}" width="{w-5}" height="{h}" rx="12" fill="{tint}" opacity="0.55"/>')
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="none" stroke="{LINE}"/>')

    text_x = x + 20
    if number is not None:
        add(f'<circle cx="{x+30}" cy="{y+26}" r="12" fill="{accent}"/>')
        text(x + 30, y + 31, str(number), size=12.5, weight=700, fill="#ffffff", anchor="middle")
        text_x = x + 50
    text(text_x, y + 31, title, size=title_size, weight=650)
    for index, line in enumerate(lines):
        text(x + 20, y + 54 + index * 17, line, size=12.5, fill=MUTED)


def arrow(x1, y1, x2, y2, color=None):
    color = color or "#94a3b8"
    add(f'<path d="M{x1} {y1} L{x2} {y2}" stroke="{color}" stroke-width="2" fill="none" marker-end="url(#tip)"/>')


def elbow(x1, y1, x2, y2, color=None):
    """Right-angle connector: down, across, then into the target."""
    color = color or "#94a3b8"
    mid = (y1 + y2) / 2
    add(
        f'<path d="M{x1} {y1} V{mid} H{x2} V{y2}" stroke="{color}" stroke-width="2" '
        f'fill="none" marker-end="url(#tip)"/>'
    )


# ---------------------------------------------------------------------------
add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" '
    f'aria-label="How the AI healthcare outreach orchestrator works, from campaign brief to approved outreach">')
add('<defs><marker id="tip" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
    'orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#94a3b8"/></marker></defs>')
add(f'<rect width="{W}" height="{H}" fill="{BG}"/>')

# -- header -----------------------------------------------------------------
text(40, 48, "How the outreach orchestrator works", size=27, weight=700)
text(40, 74, "One campaign brief in. Evidence-backed outreach packages out. A person approves every email before it is sent.",
     size=14.5, fill=MUTED)

legend_x = 40
for kind in ("rules", "ai", "browser", "human"):
    accent, tint, label = KINDS[kind]
    add(f'<rect x="{legend_x}" y="94" width="13" height="13" rx="4" fill="{accent}"/>')
    text(legend_x + 20, 105, label, size=12.5, fill=MUTED)
    legend_x += 24 + len(label) * 7

# -- row 1: find, research, score -------------------------------------------
ROW1_Y, BW, BH = 132, 250, 118
xs = [40, 330, 620, 910]
box(xs[0], ROW1_Y, BW, BH, "rules", "Campaign brief", [
    "Specialties, cities, practice size,", "who to exclude. One YAML file.",
], title_size=15)
box(xs[1], ROW1_Y, BW, BH, "rules", "Find practices", [
    "Public provider registry, your own", "CSV, or a CRM export. Duplicates", "merged, hospitals filtered out.",
], number=1)
box(xs[2], ROW1_Y, BW, BH, "browser", "Research the website", [
    "Reads the clinic's public pages:", "services, providers, locations,", "FAQs, booking, existing chat.",
], number=2)
box(xs[3], ROW1_Y, BW, BH, "rules", "Score the fit", [
    "Seven signals, fixed weights.", "No AI, no cost. Every point is", "traceable to a signal.",
], number=3)

for index in range(3):
    arrow(xs[index] + BW, ROW1_Y + BH / 2, xs[index + 1] - 6, ROW1_Y + BH / 2)

# -- fork bus ---------------------------------------------------------------
BUS_Y = 288
add(f'<path d="M{xs[3] + BW/2} {ROW1_Y + BH} V{BUS_Y}" stroke="#94a3b8" stroke-width="2" fill="none"/>')
add(f'<path d="M190 {BUS_Y} H{xs[3] + BW/2}" stroke="#94a3b8" stroke-width="2" fill="none"/>')

LANES = [
    (40, 560, "rules", "SCORE 80 +", "full package", 190),
    (640, 300, "rules", "SCORE 60 - 79", "email only", 790),
    (980, 180, "stop", "BELOW 60", "backlog", 1070),
]
LANE_TOP, LANE_BOTTOM = 316, 578
for x, w, kind, band, label, center in LANES:
    accent, tint, _ = KINDS[kind]
    height = LANE_BOTTOM - LANE_TOP if kind != "stop" else 150
    add(f'<rect x="{x}" y="{LANE_TOP}" width="{w}" height="{height}" rx="14" fill="{tint}" '
        f'stroke="{accent}" stroke-opacity="0.35" stroke-dasharray="6 5"/>')
    arrow(center, BUS_Y, center, LANE_TOP - 4)
    text(x + 18, LANE_TOP + 26, f"{band}  ·  {label}", size=12.5, weight=700, fill=accent)

# -- lane A: full package ---------------------------------------------------
SBW, SBH = 250, 96
lane_a = [
    (60, 344, "ai", 4, "Write the outreach", ["One AI call per clinic, using only", "the captured evidence."]),
    (330, 344, "rules", 5, "Build the demo", ["A branded chat demo, generated", "from configuration, not new code."]),
    (60, 456, "browser", 6, "Record the video", ["60 seconds of the demo answering", "real patient questions, captioned."]),
    (330, 456, "rules", 7, "Draft the email", ["Short, specific, with the legal", "footer and opt-out attached."]),
]
for x, y, kind, number, title, lines in lane_a:
    box(x, y, SBW, SBH, kind, title, lines, number=number, title_size=14)
arrow(60 + SBW, 344 + SBH / 2, 330 - 6, 344 + SBH / 2)   # 4 -> 5
elbow(455, 344 + SBH, 185, 456 - 6)                      # 5 -> 6, wrapping down
arrow(60 + SBW, 456 + SBH / 2, 330 - 6, 456 + SBH / 2)   # 6 -> 7

# -- lane B: email only -----------------------------------------------------
box(660, 344, SBW, SBH, "ai", "Write the outreach", ["Same single AI call, no demo", "and no video for this tier."], number=4, title_size=14)
box(660, 456, SBW, SBH, "rules", "Draft the email", ["Same copy checks and legal", "footer as the full package."], number=7, title_size=14)
arrow(785, 344 + SBH, 785, 456 - 6)

# -- lane C: backlog --------------------------------------------------------
box(1000, 344, 140, SBH, "stop", "Kept", ["Score and", "evidence saved.", "No outreach."], title_size=14)

# -- human review -----------------------------------------------------------
REVIEW_Y = 628
box(300, REVIEW_Y, 640, 116, "human", "A person reviews every package", [
    "Sees the clinic, the score breakdown, each claim with the web page it came from, the drafted",
    "email, the demo and the video. Approves, edits or rejects. Editing cancels a prior approval.",
], title_size=17)
elbow(185, LANE_BOTTOM, 480, REVIEW_Y - 6, KINDS["human"][0])
elbow(785, LANE_BOTTOM, 760, REVIEW_Y - 6, KINDS["human"][0])

# -- storage note -----------------------------------------------------------
box(40, REVIEW_Y, 230, 186, "rules", "One local file", [
    "Leads, every claim with its", "source link, drafts, approvals,", "sends and opt-outs all live in",
    "one SQLite file on the laptop.", "", "No cloud service required.",
], title_size=14)

# -- send row ---------------------------------------------------------------
SEND_Y = 800
box(300, SEND_Y, 250, 108, "human", "Send", ["Only approved packages.", "Suppression list re-checked", "at the moment of sending."], title_size=15)
box(590, SEND_Y, 250, 108, "rules", "Record what happened", ["Sent, failed, or uncertain.", "An uncertain send is never", "retried automatically."], title_size=15)
box(880, SEND_Y, 250, 108, "rules", "Honour opt-outs", ["Unsubscribes are suppressed", "for good, across every future", "campaign and import."], title_size=15)
elbow(620, REVIEW_Y + 116, 425, SEND_Y - 6, KINDS["human"][0])
arrow(550, SEND_Y + 54, 590 - 6, SEND_Y + 54)
arrow(840, SEND_Y + 54, 880 - 6, SEND_Y + 54)

# -- guardrail band ---------------------------------------------------------
BAND_Y = 934
add(f'<rect x="40" y="{BAND_Y}" width="1090" height="48" rx="12" fill="#0f172a"/>')
text(60, BAND_Y + 30, "Guardrails:", size=13, weight=700, fill="#ffffff")
guardrails = [
    "no patient data, ever",
    "no claim without a source link",
    "the AI gets no tools, files or mailbox",
    "nothing sends without a human",
]
gx = 150
for index, item in enumerate(guardrails):
    text(gx, BAND_Y + 30, item, size=12.5, fill="#cbd5e1")
    gx += len(item) * 6.9 + 22
    if index < len(guardrails) - 1:
        text(gx - 14, BAND_Y + 30, "·", size=13, fill="#64748b")

add("</svg>")

target = Path(__file__).parent / "architecture.svg"
target.write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"wrote {target} ({target.stat().st_size // 1024} KB)")
