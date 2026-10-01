"""Indian ceremonies as event types (§13.14). Labels in the three
relationship-name languages; the type names are stable ids."""

PERSON = {
    "namakaranam": {"en": "Barasala / Namakaranam (naming)", "te": "బారసాల / నామకరణం", "hi": "नामकरण"},
    "annaprasana": {"en": "Annaprasana (first rice)", "te": "అన్నప్రాశన", "hi": "अन्नप्राशन"},
    "aksharabhyasam": {"en": "Aksharabhyasam (first letters)", "te": "అక్షరాభ్యాసం", "hi": "अक्षरारंभ / विद्यारंभ"},
    "upanayanam": {"en": "Upanayanam (sacred thread ceremony)", "te": "ఉపనయనం", "hi": "उपनयन"},
    "seemantham": {"en": "Seemantham (baby shower)", "te": "సీమంతం", "hi": "सीमंतोन्नयन / गोदभराई"},
    "shashtipoorthi": {"en": "Shashtipoorthi (60th birthday)", "te": "షష్టిపూర్తి", "hi": "षष्टिपूर्ति"},
    "sahasra_chandra": {"en": "Sahasra Chandra Darshanam (1000 full moons)", "te": "సహస్ర చంద్ర దర్శనం",
                        "hi": "सहस्र चंद्र दर्शन"},
    "ceremony": {"en": "Other ceremony", "te": "ఇతర వేడుక", "hi": "अन्य संस्कार"},
}
FAMILY = {
    "nischitartham": {"en": "Nischitartham (engagement ceremony)", "te": "నిశ్చితార్థం", "hi": "सगाई"},
    "gruhapravesham": {"en": "Gruhapravesham (housewarming)", "te": "గృహప్రవేశం", "hi": "गृह प्रवेश"},
    "ceremony": PERSON["ceremony"],
}
ALL = {**PERSON, **FAMILY}


def label(etype: str, lang: str = "en") -> str | None:
    lab = ALL.get(etype)
    return (lab.get(lang) or lab["en"]) if lab else None
