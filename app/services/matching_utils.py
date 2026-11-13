"""
Profile-driven matching utilities.

Nothing in this module is specific to any company or industry: every term that
counts towards a match comes from the company's own extracted profile
(capabilities, offerings, technologies, domains, interest tags) and every
exclusion comes from the profile's own `excluded_capabilities` /
`preferred_procurement_types`.

This module provides the cheap, deterministic stage of matching:
  1. `hard_gate`          - profile-driven yes/no checkpoint (procurement type, exclusions)
  2. `quick_match_score`  - capped keyword/vector pre-score used for ranking candidates
                            before the LLM relevance judgment, and as a fallback when
                            no judgment exists yet.
  3. `check_eligibility`  - turnover / experience criteria vs the profile.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

# A tender that has not been verified by the LLM relevance judgment can never
# score above this value, so unverified results cannot look like 90%+ matches.
QUICK_SCORE_CAP = 0.6

# Words that carry no information about *what* is being bought. They are
# ignored when comparing profile phrases with tender text, so that e.g. the
# word "software" or "government" alone can never produce a match.
GENERIC_WORDS: Set[str] = {
    "a", "an", "and", "the", "of", "for", "to", "in", "on", "at", "by", "with", "from", "into", "or", "as",
    "is", "are", "be", "this", "that", "its", "their", "our", "all", "any", "other", "etc", "via",
    "service", "services", "solution", "solutions", "system", "systems", "software", "technology",
    "technologies", "tech", "it", "digital", "platform", "platforms", "product", "products",
    "supply", "supplies", "provision", "providing", "provider", "management", "support", "maintenance",
    "development", "implementation", "work", "works", "project", "projects", "operation", "operations",
    "government", "govt", "public", "sector", "department", "ministry", "organisation", "organization",
    "enterprise", "business", "industry", "industrial", "general", "various", "related", "based",
    "including", "include", "item", "items", "goods", "equipment", "contract", "annual", "comprehensive",
    "new", "custom", "end", "complete", "integrated", "advanced", "smart", "india", "indian", "national",
}

# Canonical procurement types produced by the tender/company extraction prompts.
PROCUREMENT_TYPES = {"goods", "services", "works", "software", "consulting", "mixed"}

# A company that lists the key in preferred_procurement_types accepts these tender types.
PROCUREMENT_COMPATIBILITY: Dict[str, Set[str]] = {
    "goods": {"goods"},
    "works": {"works"},
    "services": {"services", "software", "consulting"},
    "software": {"software", "services"},
    "consulting": {"consulting", "services"},
}


def normalize_text(text: Any) -> str:
    """Lowercase, collapse separators and strip punctuation."""
    if not text:
        return ""
    text = str(text).lower().strip()
    text = re.sub(r"[\s\-_/]+", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _stem(word: str) -> str:
    """Very light stemming so 'medicines' == 'medicine', 'analytics' stays useful."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def significant_tokens(text: Any) -> Set[str]:
    """Meaningful, stemmed tokens of a text (generic words removed)."""
    tokens = set()
    for word in normalize_text(text).split():
        if word in GENERIC_WORDS or len(word) < 2 or word.isdigit():
            continue
        tokens.add(_stem(word))
    return tokens


_FILLER_WORDS = {"a", "an", "and", "the", "of", "for", "to", "in", "on", "at", "by", "with", "from", "or", "as", "etc"}


def all_tokens(text: Any) -> Set[str]:
    """All stemmed tokens except grammatical filler (keeps words like 'management', 'supply')."""
    return {_stem(w) for w in normalize_text(text).split() if w not in _FILLER_WORDS and len(w) >= 2}


def _as_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                out.extend(str(v) for v in item.values() if isinstance(v, str))
        return out
    return [str(value)]


def tender_core_text(tender_meta: Dict[str, Any], scraped_info: Optional[Dict[str, Any]] = None) -> str:
    """Text describing *what is actually being purchased* (not boilerplate)."""
    parts: List[str] = [
        tender_meta.get("title") or "",
        tender_meta.get("what_is_being_bought") or "",
    ]
    parts += _as_list(tender_meta.get("required_products_or_services"))
    parts += _as_list(tender_meta.get("scope_items"))
    for category in tender_meta.get("scope_categories") or []:
        if isinstance(category, dict):
            parts.append(str(category.get("name") or ""))
            parts += _as_list(category.get("items"))
    if scraped_info:
        parts.append(scraped_info.get("items") or "")
    return " ".join(p for p in parts if p)


def tender_full_text(tender_meta: Dict[str, Any], scraped_info: Optional[Dict[str, Any]] = None) -> str:
    parts = [tender_core_text(tender_meta, scraped_info), tender_meta.get("summary") or ""]
    parts += _as_list(tender_meta.get("required_technologies"))
    parts += _as_list(tender_meta.get("domains"))
    return " ".join(p for p in parts if p)


def profile_offering_phrases(profile_meta: Dict[str, Any], interest_tags: Optional[List[str]] = None) -> List[str]:
    """All phrases describing what the company offers, from its own profile."""
    phrases: List[str] = []
    for key in ("core_capabilities", "offerings", "capabilities", "technologies", "domains"):
        phrases += _as_list(profile_meta.get(key))
    phrases += _as_list(interest_tags)
    seen: Set[str] = set()
    unique: List[str] = []
    for phrase in phrases:
        norm = normalize_text(phrase)
        if norm and norm not in seen:
            seen.add(norm)
            unique.append(phrase)
    return unique


def _phrase_hits(phrase: str, tender_tokens: Set[str], min_coverage: float) -> bool:
    tokens = significant_tokens(phrase)
    if not tokens:
        return False
    covered = len(tokens & tender_tokens) / len(tokens)
    return covered >= min_coverage


def normalize_procurement_type(value: Any) -> Optional[str]:
    norm = normalize_text(value)
    if not norm:
        return None
    if norm in PROCUREMENT_TYPES:
        return norm
    aliases = {
        "product": "goods", "products": "goods", "supply": "goods", "material": "goods",
        "service": "services", "it services": "services", "professional services": "services",
        "technology services": "services", "consultancy": "consulting", "advisory": "consulting",
        "civil works": "works", "construction": "works", "work": "works",
        "software development": "software", "it": "software",
    }
    if norm in aliases:
        return aliases[norm]
    for key, mapped in aliases.items():
        if key in norm:
            return mapped
    return None


def hard_gate(tender_meta: Dict[str, Any], profile_meta: Dict[str, Any], scraped_info: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """
    Profile-driven yes/no checkpoint. Returns a human-readable rejection reason,
    or None if the tender may proceed to scoring.
    """
    # 1. Procurement type must be one the company actually takes on.
    tender_type = normalize_procurement_type(tender_meta.get("procurement_type"))
    preferred = {
        t for t in (normalize_procurement_type(v) for v in _as_list(profile_meta.get("preferred_procurement_types"))) if t
    }
    if tender_type and tender_type != "mixed" and preferred:
        accepted: Set[str] = set()
        for pref in preferred:
            accepted |= PROCUREMENT_COMPATIBILITY.get(pref, {pref})
        if tender_type not in accepted:
            return (
                f"Procurement type is '{tender_type}', but the company profile takes on "
                f"{', '.join(sorted(preferred))} only"
            )

    # 2. What is being bought must not be something the company excludes.
    #    Every word of the exclusion phrase (generic words included) must appear, otherwise
    #    "Event management" would shrink to "event" and reject e.g. "event-driven analytics".
    core_tokens = all_tokens(tender_core_text(tender_meta, scraped_info))
    if core_tokens:
        for excluded in _as_list(profile_meta.get("excluded_capabilities")):
            phrase_tokens = all_tokens(excluded)
            if phrase_tokens and phrase_tokens <= core_tokens:
                return f"Tender requires '{excluded}', which the company profile excludes"
    return None


def normalize_vector_similarity(value: float) -> float:
    """Map raw cosine similarity of sentence embeddings (~0.3 unrelated .. ~0.8 close) to 0..1."""
    return max(0.0, min(1.0, (float(value or 0.0) - 0.3) / 0.5))


def quick_match_score(
    tender_meta: Dict[str, Any],
    profile_meta: Dict[str, Any],
    vector_similarity: float,
    interest_tags: Optional[List[str]] = None,
    scraped_info: Optional[Dict[str, Any]] = None,
) -> Tuple[float, List[str], Optional[str]]:
    """
    Cheap pre-score. Returns (score, reasons, rejection_reason).
    Score is capped at QUICK_SCORE_CAP because it is not a business judgment.
    """
    rejection = hard_gate(tender_meta, profile_meta, scraped_info)
    if rejection:
        return 0.0, [rejection], rejection

    tender_tokens = significant_tokens(tender_full_text(tender_meta, scraped_info))
    hits = [p for p in profile_offering_phrases(profile_meta, interest_tags) if _phrase_hits(p, tender_tokens, 0.6)]

    keyword_part = min(len(hits) / 3.0, 1.0)
    vector_part = normalize_vector_similarity(vector_similarity)
    score = min(0.6 * keyword_part + 0.4 * vector_part, QUICK_SCORE_CAP)

    reasons: List[str] = []
    if hits:
        reasons.append(f"Profile terms found in tender: {', '.join(hits[:4])}")
    return round(score, 4), reasons, None


# Backwards-compatible name used by search_service.
def calculate_enhanced_match_score(
    tender_meta: Dict[str, Any],
    profile_meta: Dict[str, Any],
    vector_similarity: float,
) -> Tuple[float, List[str]]:
    score, reasons, _ = quick_match_score(tender_meta, profile_meta, vector_similarity)
    return score, reasons


# ---------------------------------------------------------------------------
# Eligibility (turnover / experience) - deterministic, from extracted numbers.
# ---------------------------------------------------------------------------

_UNIT_MULTIPLIERS = [
    (r"(crores?|cr)\b", 1e7),
    (r"(lakhs?|lacs?|lac|l)\b", 1e5),
    (r"(millions?|mn|m)\b", 1e6),
    (r"(billions?|bn)\b", 1e9),
    (r"(thousands?|k)\b", 1e3),
]


def parse_inr(value: Any) -> Optional[float]:
    """Parse amounts like '₹4.93 Crore', '50 Lakh', '₹ 1,84,93,108', 49300000 into rupees."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    text = str(value).lower().replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*([a-z]*)", text.replace("₹", " ").replace("rs.", " ").replace("inr", " "))
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2) or ""
    for pattern, multiplier in _UNIT_MULTIPLIERS:
        if unit and re.fullmatch(pattern.replace(r"\b", ""), unit):
            return number * multiplier
    return number if number > 0 else None


def format_inr(amount: Optional[float]) -> str:
    if not amount:
        return "unknown"
    if amount >= 1e7:
        return f"₹{amount / 1e7:.2f} Cr"
    if amount >= 1e5:
        return f"₹{amount / 1e5:.2f} L"
    return f"₹{amount:,.0f}"


def _first_number(*values: Any) -> Optional[float]:
    for value in values:
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            return float(value)
        match = re.search(r"\d+(?:\.\d+)?", str(value))
        if match:
            return float(match.group(0))
    return None


_STARTUP_HINTS = ("dpiit", "startup india", "start-up india", "recognised startup", "recognized startup")
_MSE_HINTS = ("msme", "udyam", "micro and small", "micro or small", "small enterprise", "nsic", "mse")


def company_relaxation_status(profile_meta: Dict[str, Any]) -> Dict[str, bool]:
    """Is the company a DPIIT startup / registered MSE? Explicit flags first, then its registrations."""
    registrations = " ".join(_as_list(profile_meta.get("registrations"))).lower()

    def flag(key: str, hints: tuple) -> bool:
        value = profile_meta.get(key)
        if isinstance(value, bool):
            return value
        return any(hint in registrations for hint in hints)

    return {"startup": flag("is_dpiit_startup", _STARTUP_HINTS), "mse": flag("is_mse", _MSE_HINTS)}


def _relaxed(tender_meta: Dict[str, Any], company: Dict[str, bool], criterion: str) -> Optional[str]:
    """Which relaxation (if any) waives `criterion` ("experience" / "turnover") for this company."""
    if company["startup"] and (tender_meta.get("startup_relaxation") or {}).get(criterion):
        return "Startup relaxation"
    if company["mse"] and (tender_meta.get("mse_relaxation") or {}).get(criterion):
        return "MSE relaxation"
    return None


def check_eligibility(
    tender_meta: Dict[str, Any],
    profile_meta: Dict[str, Any],
    profile_experience_years: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Compare the tender's bidder criteria with the company profile, honouring the GeM
    Startup / MSE relaxations stated in the bid.
    Returns {"fit": "MET" | "NOT_MET" | "UNKNOWN", "score": 0..1, "checks": [...]}.
    Check status is "met", "not_met", "unknown" or "exempt" (waived by a relaxation).
    """
    criteria = tender_meta.get("eligibility_criteria") or {}
    company = company_relaxation_status(profile_meta)
    checks: List[Dict[str, Any]] = []

    def add_check(label: str, criterion: str, required: str, company_value: str, status: str) -> None:
        relaxation = _relaxed(tender_meta, company, criterion) if status != "met" else None
        checks.append(
            {
                "label": label,
                "required": required,
                "company": f"{company_value} ({relaxation})" if relaxation else company_value,
                "status": "exempt" if relaxation else status,
            }
        )

    required_turnover = parse_inr(tender_meta.get("min_avg_turnover") or criteria.get("min_turnover"))
    company_turnover = parse_inr(profile_meta.get("annual_turnover_inr") or profile_meta.get("annual_turnover"))
    if required_turnover:
        status = "unknown"
        if company_turnover:
            status = "met" if company_turnover >= required_turnover else "not_met"
        add_check(
            "Minimum average annual turnover", "turnover",
            format_inr(required_turnover), format_inr(company_turnover), status,
        )

    required_years = _first_number(
        tender_meta.get("min_experience_years"),
        tender_meta.get("required_experience_years"),
        criteria.get("min_experience_years"),
    )
    company_years = _first_number(profile_experience_years, profile_meta.get("years_in_business"))
    if required_years:
        status = "unknown"
        if company_years is not None:
            status = "met" if company_years >= required_years else "not_met"
        add_check(
            "Past experience", "experience", f"{required_years:g} years",
            f"{company_years:g} years" if company_years is not None else "unknown", status,
        )

    statuses = [c["status"] for c in checks]
    if "not_met" in statuses:
        fit, score = "NOT_MET", 0.0
    elif statuses and all(s in ("met", "exempt") for s in statuses):
        fit, score = "MET", 1.0
    else:
        fit, score = "UNKNOWN", 0.5
    return {"fit": fit, "score": score, "checks": checks}
