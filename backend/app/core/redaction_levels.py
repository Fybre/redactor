"""
Defines which Presidio entity types are redacted at each level.
Each level is a superset of the previous.
"""

MINIMAL_ENTITIES = [
    "CREDIT_CARD",
    "CVV",
    "CRYPTO",
    "IBAN_CODE",
    "US_SSN",
    "US_BANK_NUMBER",
    "US_PASSPORT",
    "UK_NHS",
    "IN_AADHAAR",
    "IN_PAN",
    "SG_NRIC_FIN",
    "AU_TFN",
    "AU_MEDICARE",
]

STANDARD_ENTITIES = MINIMAL_ENTITIES + [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "US_DRIVER_LICENSE",
    "IP_ADDRESS",
    "MEDICAL_LICENSE",
    "US_ITIN",
]

AGGRESSIVE_ENTITIES = STANDARD_ENTITIES + [
    "LOCATION",
    "DATE_TIME",
    "NRP",
    "URL",
    "AU_ABN",
    "AU_ACN",
    "IN_VEHICLE_REGISTRATION",
]

MAXIMUM_ENTITIES = AGGRESSIVE_ENTITIES + [
    "ORGANIZATION",
    "AGE",
    "MONEY",
    "FACILITY",
]

ENTITY_LEVELS = {
    "minimal": MINIMAL_ENTITIES,
    "standard": STANDARD_ENTITIES,
    "aggressive": AGGRESSIVE_ENTITIES,
    "maximum": MAXIMUM_ENTITIES,
}

# Human-readable descriptions for the UI
ENTITY_DESCRIPTIONS = {
    "CREDIT_CARD": "Credit card numbers",
    "CVV": "Card security code — 3-4 digit number appearing immediately after a label like 'CVV:', 'CVC:', 'CVV2:', or 'Security Code:', typically found near a credit card number and expiry date (use with Credit Card — LLM only)",
    "CRYPTO": "Cryptocurrency wallet addresses",
    "IBAN_CODE": "IBAN bank account numbers",
    "US_SSN": "US Social Security Numbers",
    "US_BANK_NUMBER": "US bank account numbers",
    "US_PASSPORT": "US passport numbers",
    "UK_NHS": "UK NHS numbers",
    "IN_AADHAAR": "Indian Aadhaar numbers",
    "IN_PAN": "Indian PAN card numbers",
    "SG_NRIC_FIN": "Singapore NRIC/FIN numbers",
    "AU_TFN": "Australian Tax File Numbers",
    "AU_MEDICARE": "Australian Medicare numbers",
    "PERSON": "Person names",
    "EMAIL_ADDRESS": "Email addresses",
    "PHONE_NUMBER": "Phone numbers",
    "US_DRIVER_LICENSE": "US driver's license numbers",
    "IP_ADDRESS": "IP addresses",
    "MEDICAL_LICENSE": "Medical license numbers",
    "US_ITIN": "US Individual Taxpayer Identification Numbers",
    "LOCATION": "Locations and addresses",
    "DATE_TIME": "Dates and times",
    "NRP": "Nationalities, religions, political groups",
    "URL": "URLs and web addresses",
    "AU_ABN": "Australian Business Numbers",
    "AU_ACN": "Australian Company Numbers",
    "IN_VEHICLE_REGISTRATION": "Indian vehicle registration numbers",
    "ORGANIZATION": "Organization and company names",
    "AGE": "Ages",
    "MONEY": "Monetary values",
    "FACILITY": "Facility names (hospitals, schools, etc.)",
}

LEVEL_DESCRIPTIONS = {
    "minimal": "Redacts only high-confidence financial and government IDs (SSN, credit cards, passports).",
    "standard": "Redacts names, emails, phone numbers, and IDs. Recommended for most use cases.",
    "aggressive": "Redacts dates, locations, URLs, and regional identifiers in addition to standard.",
    "maximum": "Redacts all detected entities including organizations, monetary values, and ages.",
    "custom": "Select specific entity types to redact.",
}


def get_entities_for_level(level: str, custom_entities: list = None) -> list:
    if level == "custom":
        return custom_entities or []
    return ENTITY_LEVELS.get(level, STANDARD_ENTITIES)
