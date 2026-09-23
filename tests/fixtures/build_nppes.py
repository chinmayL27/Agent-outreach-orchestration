"""Generate the offline NPPES fixture: 20 discoverable leads.

Shaped like real registry output - organizations, individual providers that
belong to those organizations, excluded facility types, and practices with no
known website - so the offline campaign exercises deduplication, exclusion and
partial enrichment, not just the happy path.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent


def org(npi, name, street, city, state, zipcode, phone, taxonomies, official=None):
    basic = {"organization_name": name}
    if official:
        basic["authorized_official_first_name"], basic["authorized_official_last_name"] = official
    return {
        "enumeration_type": "NPI-2", "number": npi, "basic": basic,
        "addresses": [
            {"address_purpose": "MAILING", "address_1": "PO Box 9", "city": city,
             "state": state, "postal_code": zipcode},
            {"address_purpose": "LOCATION", "address_1": street, "city": city.upper(),
             "state": state, "postal_code": zipcode + "1234", "telephone_number": phone},
        ],
        "taxonomies": [{"desc": desc, "primary": index == 0}
                       for index, desc in enumerate(taxonomies)],
    }


def individual(npi, first, last, org_name, street, city, state, zipcode, phone, taxonomy):
    return {
        "enumeration_type": "NPI-1", "number": npi,
        "basic": {"first_name": first, "last_name": last},
        "other_names": [{"organization_name": org_name}],
        "addresses": [
            {"address_purpose": "LOCATION", "address_1": street, "city": city,
             "state": state, "postal_code": zipcode, "telephone_number": phone},
        ],
        "taxonomies": [{"desc": taxonomy, "primary": True}],
    }


RESULTS = [
    # --- five clinics with crawlable sites (the qualifying set) -------------
    org("1234567893", "ABC DERMATOLOGY, P.C.", "1200 Meridian Ave", "San Jose", "CA", "95125",
        "408-555-0142", ["Dermatology", "Dermatology, Procedural Dermatology"], ("Jane", "Smith")),
    individual("1093847561", "ROBERT", "CHEN", "ABC Dermatology PC", "1200 Meridian Ave",
               "San Jose", "CA", "95125", "408-555-0142", "Dermatology"),
    org("1245319599", "WILLOW GLEN PEDIATRICS", "1500 Lincoln Ave", "San Jose", "CA", "95125",
        "408-555-0210", ["Pediatrics"], ("Maria", "Lopez")),
    org("1356420688", "BAY AREA DENTAL CARE", "300 Grand Ave", "Oakland", "CA", "94612",
        "510-555-0320", ["Dentist", "Dentist, General Practice"], ("Steven", "Alvarez")),
    org("1467531777", "MISSION URGENT CARE", "2600 Mission St", "San Francisco", "CA", "94110",
        "415-555-0455", ["Clinic/Center, Urgent Care"], ("Omar", "Haddad")),
    org("1578642866", "GOLDEN GATE FAMILY MEDICINE", "2300 Sutter St", "San Francisco", "CA",
        "94115", "415-555-0588", ["Family Medicine"], ("Helen", "Okafor")),
    individual("1689753955", "MARCUS", "WEBB", "Golden Gate Family Medicine", "2300 Sutter St",
               "San Francisco", "CA", "94115", "415-555-0588", "Family Medicine"),

    # --- a clinic that already runs a chat widget (scores to backlog) -------
    org("1598765432", "BAYVIEW FAMILY MEDICINE", "1500 Broadway", "Oakland", "CA", "94612",
        "510-555-0110", ["Family Medicine"]),

    # --- excluded facility types -------------------------------------------
    org("1700112233", "VALLEY REGIONAL HOSPITAL", "900 Hospital Way", "San Jose", "CA", "95128",
        "408-555-0900", ["General Acute Care Hospital"]),
    org("1811223344", "STATE UNIVERSITY STUDENT HEALTH CENTER", "1 Campus Dr", "San Jose", "CA",
        "95192", "408-555-0901", ["Clinic/Center, Student Health"]),
    org("1922334455", "COUNTY DEPARTMENT OF PUBLIC HEALTH", "70 Civic Plaza", "Oakland", "CA",
        "94612", "510-555-0902", ["Clinic/Center, Public Health, State or Local"]),

    # --- practices with no website we can resolve (stay ENRICHMENT_PARTIAL) -
    org("2033445566", "SUNNYVALE SKIN CLINIC", "800 W El Camino Real", "Sunnyvale", "CA", "94087",
        "408-555-0777", ["Dermatology"]),
    org("2144556677", "EASTSIDE PEDIATRIC ASSOCIATES", "455 Alum Rock Ave", "San Jose", "CA",
        "95116", "408-555-0778", ["Pediatrics"]),
    org("2255667788", "HARBOR DENTAL GROUP", "77 Jack London Sq", "Oakland", "CA", "94607",
        "510-555-0779", ["Dentist"]),
    org("2366778899", "PRESIDIO URGENT CARE", "3300 Lombard St", "San Francisco", "CA", "94123",
        "415-555-0780", ["Clinic/Center, Urgent Care"]),
    org("2477889900", "NOE VALLEY FAMILY PRACTICE", "4000 24th St", "San Francisco", "CA", "94114",
        "415-555-0781", ["Family Medicine"]),
    org("2588990011", "ALAMEDA DERMATOLOGY PARTNERS", "2200 Central Ave", "Alameda", "CA", "94501",
        "510-555-0782", ["Dermatology"]),
    org("2699001122", "BERKELEY PEDIATRIC CARE", "1800 Shattuck Ave", "Berkeley", "CA", "94709",
        "510-555-0783", ["Pediatrics"]),
    org("2700112233", "SOUTH BAY DENTAL ARTS", "1250 Blossom Hill Rd", "San Jose", "CA", "95118",
        "408-555-0784", ["Dentist"]),
    org("2811223344", "MARINA URGENT CARE CENTER", "2100 Chestnut St", "San Francisco", "CA",
        "94123", "415-555-0785", ["Clinic/Center, Urgent Care"]),
    org("2922334455", "TWIN PEAKS FAMILY HEALTH", "500 Portola Dr", "San Francisco", "CA", "94131",
        "415-555-0786", ["Family Medicine"]),
    org("3033445566", "LOS GATOS SKIN INSTITUTE", "15 N Santa Cruz Ave", "Los Gatos", "CA", "95030",
        "408-555-0787", ["Dermatology"]),
    org("3144556677", "RICHMOND DISTRICT PEDIATRICS", "700 Clement St", "San Francisco", "CA",
        "94118", "415-555-0788", ["Pediatrics"]),
    org("3255667788", "TEMESCAL DENTAL STUDIO", "4200 Telegraph Ave", "Oakland", "CA", "94609",
        "510-555-0789", ["Dentist"]),
    org("3366778899", "CAMBRIAN FAMILY HEALTH", "2900 Union Ave", "San Jose", "CA", "95124",
        "408-555-0790", ["Family Medicine"]),
    org("3477889900", "POTRERO HILL URGENT CARE", "1300 18th St", "San Francisco", "CA", "94107",
        "415-555-0791", ["Clinic/Center, Urgent Care"]),
]

WEBSITE_HINTS = {
    "1234567893": "sites/abc-dermatology/index.html",
    "1245319599": "sites/willow-glen-pediatrics/index.html",
    "1356420688": "sites/bay-area-dental-care/index.html",
    "1467531777": "sites/mission-urgent-care/index.html",
    "1578642866": "sites/golden-gate-family-medicine/index.html",
    "1598765432": "sites/bayview-family-medicine/index.html",
}

if __name__ == "__main__":
    payload = {"result_count": len(RESULTS), "website_hints": WEBSITE_HINTS, "results": RESULTS}
    (HERE / "nppes_sample.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(RESULTS)} registry records, {len(WEBSITE_HINTS)} with websites")
