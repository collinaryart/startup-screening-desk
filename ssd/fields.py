"""The fields the desk tries to fill for every start-up, in one place.

`question` is what the reviewer should ask in a first meeting when the field could not be found.
`core` fields count toward the evidence-coverage figure.
"""
from __future__ import annotations

FIELDS: list[dict] = [
    # --- company
    {"key": "hq_location", "label": "Headquarters", "group": "Company", "kind": "text", "core": False,
     "question": "Where is the company headquartered, and where does the team sit?"},
    {"key": "singapore_registered", "label": "Singapore-registered Pte Ltd", "group": "Company", "kind": "bool", "core": True,
     "question": "Is the company a Singapore-registered private limited company (ACRA number)?"},
    {"key": "year_incorporated", "label": "Year incorporated", "group": "Company", "kind": "year", "core": True,
     "question": "When was the company incorporated?"},
    {"key": "innovation_in_singapore", "label": "Innovation developed or held in Singapore", "group": "Company", "kind": "bool", "core": True,
     "question": "Is the R&D done in Singapore, and is the IP held by the Singapore entity?"},
    {"key": "headcount", "label": "Headcount", "group": "Company", "kind": "number", "core": True,
     "question": "How many people work at the company today, and how many are in Singapore?"},
    # --- product and IP
    {"key": "research_based_ip", "label": "Owns research-based IP", "group": "Product and IP", "kind": "bool", "core": True,
     "question": "What research-based IP does the company own (patents, know-how, trade secrets), and what makes it hard to copy?"},
    {"key": "patents", "label": "Patents", "group": "Product and IP", "kind": "text", "core": False,
     "question": "Which patents are filed or granted, in which countries, and who is the owner on record?"},
    {"key": "excluded_activity", "label": "Excluded activity", "group": "Product and IP", "kind": "bool", "core": False,
     "question": ""},
    # --- stage and funding
    {"key": "stage", "label": "Stage", "group": "Stage and funding", "kind": "text", "core": True,
     "question": "What stage is the company at, and what was the last round?"},
    {"key": "funding_to_date", "label": "Raised to date", "group": "Stage and funding", "kind": "money", "core": True,
     "question": "How much has the company raised to date, and from whom?"},
    {"key": "round_open", "label": "A round is open now", "group": "Stage and funding", "kind": "bool", "core": False,
     "question": "Is the company raising now, and when does the round close?"},
    {"key": "round_size", "label": "This round", "group": "Stage and funding", "kind": "money", "core": True,
     "question": "How much is the company raising in this round, and on what instrument?"},
    {"key": "pre_money_valuation", "label": "Pre-money valuation", "group": "Stage and funding", "kind": "money", "core": False,
     "question": "What valuation is the round priced at, and who set it?"},
    {"key": "lead_investor", "label": "Lead or co-investor", "group": "Stage and funding", "kind": "text", "core": True,
     "question": "Who is the independent lead or co-investor in this round, and how far is their diligence?"},
    {"key": "coinvestor_commitment", "label": "Co-investor commitment", "group": "Stage and funding", "kind": "money", "core": True,
     "question": "How much has an independent co-investor committed in writing?"},
    {"key": "first_institutional_round", "label": "First institutional round", "group": "Stage and funding", "kind": "bool", "core": False,
     "question": "Is this the company's first institutional round?"},
    {"key": "prior_government_equity", "label": "Earlier government equity", "group": "Stage and funding", "kind": "money", "core": False,
     "question": "Has any Startup SG Equity or other government equity gone into the company before?"},
    # --- traction
    {"key": "revenue", "label": "Revenue", "group": "Traction", "kind": "money", "core": True,
     "question": "What revenue did the company book in its latest year, and what is the run-rate now?"},
    {"key": "burn_monthly", "label": "Monthly burn", "group": "Traction", "kind": "money", "core": False,
     "question": "What is the monthly burn?"},
    {"key": "runway_months", "label": "Runway (months)", "group": "Traction", "kind": "number", "core": False,
     "question": "How many months of runway does the company have without this round?"},
    # --- market
    {"key": "tam", "label": "Market size", "group": "Market", "kind": "money", "core": True,
     "question": "How big is the market the product sells into, from a source other than the company?"},
    {"key": "market_growth", "label": "Market growth", "group": "Market", "kind": "percent", "core": False,
     "question": "How fast is that market growing?"},
]

LISTS: list[dict] = [
    {"key": "team", "label": "Management team", "core": True,
     "question": "Who are the founders and key managers, and what have they built before?"},
    {"key": "products", "label": "Products", "core": True,
     "question": "What exactly does the company sell today, and what is still in development?"},
    {"key": "customers", "label": "Customers", "core": True,
     "question": "Who are the pilot and paying customers, and can we speak to two of them?"},
    {"key": "share_of_wallet", "label": "Share of wallet", "core": True,
     "question": "For its best customer: what does the customer spend on this category a year, and how much of that goes to the company?"},
    {"key": "competitors", "label": "Competitors", "core": True,
     "question": "Who does the company lose deals to, and why does it win when it wins?"},
]

FIELD_KEYS = [f["key"] for f in FIELDS]
FIELD_BY_KEY = {f["key"]: f for f in FIELDS}
LIST_BY_KEY = {f["key"]: f for f in LISTS}

STAGES = ["Pre-seed", "Seed", "Series A", "Series B", "Series C+", "Growth", "Unknown"]
PRODUCT_STATUS = ["concept", "prototype", "pilot", "commercial", "unknown"]
CUSTOMER_RELATION = ["paying", "pilot", "letter of intent", "partner", "unknown"]
