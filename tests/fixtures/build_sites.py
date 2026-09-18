"""Generate the fixture clinic websites used by the offline demo campaign."""
from pathlib import Path

SITES = Path(__file__).parent / "sites"

PAGE = """<!doctype html><html><head><title>{title}</title>{head}</head><body>
<header><h1>{name}</h1><nav>{nav}</nav></header>
<main>{body}</main></body></html>
"""

NAV = ('<a href="index.html">Home</a> <a href="services.html">Services</a> '
       '<a href="providers.html">Our Providers</a> <a href="faq.html">Patient FAQ</a> '
       '<a href="contact.html">Contact</a> <a href="appointments.html">Request an Appointment</a>')


def write(slug: str, filename: str, title: str, name: str, body: str, head: str = "", nav: str = NAV):
    directory = SITES / slug
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(
        PAGE.format(title=title, name=name, body=body, head=head, nav=nav), encoding="utf-8"
    )


def build_clinic(slug, name, city, state, zipcode, street, phone, email, services, providers,
                 faqs, second_location=None, chat_script=None, booking=True):
    """A full multi-page clinic site."""
    locations = f"<p>{street}, {city}, {state} {zipcode}. Phone: {phone}</p>"
    if second_location:
        locations += f"<p>{second_location}</p>"
    head = f'<script src="{chat_script}"></script>' if chat_script else ""

    write(slug, "index.html", f"{name} | {city}, {state}", name, f"""
<h2>{services[0]} and more in {city}, {state}</h2>
<p>{name} serves patients in {city}, {state}.</p>
{locations}
<p>Email us at <a href="mailto:{email}">{email}</a>.</p>
""", head=head)

    write(slug, "services.html", f"Services | {name}", name,
          "<h1>Our Services</h1><ul>" + "".join(f"<li>{s}</li>" for s in services) + "</ul>"
          + ('<p><a href="appointments.html">Request an appointment</a></p>' if booking else ""))

    write(slug, "providers.html", f"Our Providers | {name}", name,
          "<h1>Our Providers</h1>" + "".join(f"<h2>{p}</h2><p>Clinician.</p>" for p in providers))

    write(slug, "faq.html", f"Patient FAQ | {name}", name,
          "<h1>Patient FAQ</h1>" + "".join(f"<h3>{q}</h3><p>Please contact the office.</p>" for q in faqs))

    write(slug, "contact.html", f"Contact | {name}", name, f"""
<h1>Contact Us</h1>{locations}
<p>Email: <a href="mailto:{email}">{email}</a></p>
<p>Billing: <a href="mailto:billing@{email.split('@')[1]}">billing@{email.split('@')[1]}</a></p>
""")

    if booking:
        write(slug, "appointments.html", f"Request an Appointment | {name}", name,
              "<h1>Request an Appointment</h1><p>Book online through our patient portal.</p>"
              "<form><input name='name'/><button>Request appointment</button></form>")


CLINICS = [
    dict(slug="abc-dermatology", name="ABC Dermatology, P.C.", city="San Jose", state="CA",
         zipcode="95125", street="1200 Meridian Ave", phone="(408) 555-0142",
         email="info@abcdermatology.example",
         services=["Skin Cancer Screening", "Acne Treatment", "Eczema and Psoriasis Care",
                   "Mohs Surgery", "Cosmetic Dermatology", "Pediatric Dermatology"],
         providers=["Jane Smith, MD", "Robert Chen, MD", "Priya Nair, DO", "Alex Romero, PA-C"],
         faqs=["Do I need a referral to be seen?", "How do I schedule a skin cancer screening?",
               "Which location offers cosmetic treatments?", "Do you accept new patients?",
               "What should I bring to my first visit?", "How do I request my medical records?"],
         second_location="480 E Hamilton Ave, Campbell, CA 95008. Phone: (408) 555-0187"),
    dict(slug="willow-glen-pediatrics", name="Willow Glen Pediatrics", city="San Jose", state="CA",
         zipcode="95125", street="1500 Lincoln Ave", phone="(408) 555-0210",
         email="info@willowglenpeds.example",
         services=["Well-Child Visits", "Immunizations", "Newborn Care", "Sports Physicals",
                   "Asthma Management", "Developmental Screening"],
         providers=["Maria Lopez, MD", "Daniel Park, MD", "Aisha Khan, NP"],
         faqs=["Are you accepting new patients?", "Do you offer same-day sick visits?",
               "Which insurance plans do you accept?", "How do I get a school form signed?",
               "Do you offer weekend hours?", "How do I request a refill?"],
         second_location="2100 Camden Ave, San Jose, CA 95124. Phone: (408) 555-0211"),
    dict(slug="bay-area-dental-care", name="Bay Area Dental Care", city="Oakland", state="CA",
         zipcode="94612", street="300 Grand Ave", phone="(510) 555-0320",
         email="hello@bayareadental.example",
         services=["Preventive Cleanings", "Dental Implants", "Invisalign", "Root Canal Therapy",
                   "Teeth Whitening", "Emergency Dental Care"],
         providers=["Steven Alvarez, DDS", "Nina Petrov, DMD", "Grace Lee, DDS"],
         faqs=["Do you take walk-in emergencies?", "How much does an implant consultation cost?",
               "Do you offer payment plans?", "Are you accepting new patients?",
               "Do you treat children?", "How often should I have a cleaning?"],
         second_location="55 Broadway, Oakland, CA 94607. Phone: (510) 555-0321"),
    dict(slug="mission-urgent-care", name="Mission Urgent Care", city="San Francisco", state="CA",
         zipcode="94110", street="2600 Mission St", phone="(415) 555-0455",
         email="frontdesk@missionurgent.example",
         services=["Walk-In Urgent Care", "X-Ray Imaging", "Occupational Health", "Flu and COVID Testing",
                   "Minor Injury Treatment", "Travel Vaccinations"],
         providers=["Omar Haddad, MD", "Rebecca Stone, PA-C", "Luis Moreno, NP"],
         faqs=["What are your walk-in hours?", "Do I need an appointment?",
               "Do you take my insurance?", "How long is the typical wait?",
               "Do you treat children?", "Can you provide a work note?"],
         second_location="1200 Valencia St, San Francisco, CA 94110. Phone: (415) 555-0456"),
    dict(slug="golden-gate-family-medicine", name="Golden Gate Family Medicine", city="San Francisco",
         state="CA", zipcode="94115", street="2300 Sutter St", phone="(415) 555-0588",
         email="contact@ggfamilymed.example",
         services=["Annual Physicals", "Chronic Disease Management", "Womens Health",
                   "Preventive Screening", "Telehealth Visits", "Minor Procedures"],
         providers=["Helen Okafor, MD", "Marcus Webb, MD", "Sofia Duarte, NP"],
         faqs=["Are you accepting new patients?", "Do you offer telehealth?",
               "How do I transfer my records?", "Which insurance do you accept?",
               "How do I book a physical?", "Do you offer evening appointments?"],
         second_location="1600 Divisadero St, San Francisco, CA 94115. Phone: (415) 555-0589"),
    # Already runs a chat widget and publishes no FAQ -> scores into the backlog.
    dict(slug="bayview-family-medicine", name="Bayview Family Medicine", city="Oakland", state="CA",
         zipcode="94612", street="1500 Broadway", phone="(510) 555-0110",
         email="hello@bayviewfamily.example",
         services=["Annual Physicals", "Chronic Disease Management", "Immunizations", "Womens Health"],
         providers=["Karen Bell, MD"], faqs=[],
         chat_script="https://widget.intercom.io/widget/abc123", booking=False),
]

if __name__ == "__main__":
    for clinic in CLINICS:
        build_clinic(**clinic)
    print(f"built {len(CLINICS)} fixture sites")
