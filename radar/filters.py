"""Cheap filters that run before anything costs money."""
from __future__ import annotations

import re

from .models import Job
from .text import age_days

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}
US_NAMES = {"us", "usa", "u.s.", "u.s.a.", "united states", "united states of america", "america"}
US_CITIES = [
    "new york", "nyc", "brooklyn", "manhattan", "san francisco", "bay area", "silicon valley",
    "palo alto", "mountain view", "sunnyvale", "santa clara", "san jose", "menlo park",
    "redwood city", "san mateo", "foster city", "oakland", "berkeley", "cupertino", "los angeles",
    "santa monica", "irvine", "san diego", "seattle", "bellevue", "redmond", "kirkland", "boston",
    "somerville", "waltham", "austin", "dallas", "houston", "plano", "denver", "boulder", "chicago",
    "atlanta", "miami", "philadelphia", "pittsburgh", "baltimore", "raleigh", "durham", "charlotte",
    "nashville", "minneapolis", "detroit", "ann arbor", "columbus", "salt lake city", "phoenix",
    "las vegas", "st. louis", "kansas city", "indianapolis", "tampa", "orlando", "jersey city",
    "hoboken", "stamford", "new haven", "providence", "richmond", "mclean", "reston", "herndon",
]
FOREIGN = [
    "canada", "ontario", "quebec", "british columbia", "alberta", "toronto", "montreal", "mexico",
    "united kingdom", "uk", "gb", "great britain", "england", "scotland", "wales", "london", "ireland", "germany", "berlin", "munich",
    "france", "paris", "netherlands", "amsterdam", "spain", "madrid", "barcelona", "portugal",
    "lisbon", "italy", "milan", "poland", "warsaw", "krakow", "switzerland", "zurich", "sweden",
    "stockholm", "denmark", "copenhagen", "norway", "finland", "belgium", "austria", "czech",
    "prague", "romania", "hungary", "serbia", "ukraine", "greece", "turkey", "israel", "tel aviv",
    "india", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai", "delhi", "gurgaon",
    "gurugram", "noida", "chennai", "china", "shanghai", "beijing", "shenzhen", "hong kong",
    "taiwan", "taipei", "japan", "tokyo", "korea", "seoul", "singapore", "malaysia", "indonesia",
    "philippines", "manila", "vietnam", "thailand", "australia", "sydney", "melbourne",
    "new zealand", "brazil", "são paulo", "sao paulo", "argentina", "buenos aires", "colombia",
    "chile", "costa rica", "uae", "dubai", "egypt", "nigeria", "kenya", "south africa", "emea",
    "apac", "latam", "europe",
]
_SPLIT_PARTS = re.compile(r";|\||\bor\b|\n")
_SPLIT_TOKENS = re.compile(r"[,\-–/()•·]+")
_FOREIGN_RE = re.compile(r"\b(" + "|".join(re.escape(x) for x in FOREIGN) + r")\b", re.I)
_CITY_RE = re.compile(r"\b(" + "|".join(re.escape(x) for x in US_CITIES) + r")\b", re.I)
_STATE_NAMES = {v.lower() for v in US_STATES.values()}
_UNKNOWN_LOC = re.compile(r"^\s*(\d+\s+locations?|multiple locations|various|tbd)\s*$", re.I)
_REMOTE = re.compile(r"\b(remote|anywhere|distributed|hybrid|virtual)\b", re.I)


def _part_is_us(part: str) -> bool | None:
    tokens = [t.strip() for t in _SPLIT_TOKENS.split(part) if t.strip()]
    has_us = any(t in US_STATES or t.lower().rstrip(".") in US_NAMES or t.lower() in _STATE_NAMES for t in tokens)
    has_us = has_us or bool(_CITY_RE.search(part))
    foreign = bool(_FOREIGN_RE.search(part))
    if has_us and not foreign:
        return True
    if foreign:
        return False
    if _REMOTE.search(part):
        return None  # remote with no country named
    return False


def _country_is_us(country) -> bool | None:
    if not country:
        return None
    c = str(country).strip().lower()
    return c in US_NAMES or c == "united states of america"


def us_location(job: Job, allow_unspecified_remote: bool = True) -> bool | None:
    """True = US, False = not US, None = can't tell yet (keep it and check the details)."""
    country = _country_is_us(job.extra.get("country"))
    loc = (job.location or "").strip()
    if not loc or _UNKNOWN_LOC.match(loc):
        return country
    verdicts = [_part_is_us(p) for p in _SPLIT_PARTS.split(loc) if p.strip()]
    if True in verdicts or country:
        return True
    if None in verdicts:
        return True if allow_unspecified_remote else None
    return False


_BAD_EMPLOYMENT = re.compile(r"intern|part|contract|temp|seasonal|freelance|fixed term", re.I)


def employment_ok(job: Job) -> bool:
    for value in (job.extra.get("commitment"), job.extra.get("employment_type")):
        if value and _BAD_EMPLOYMENT.search(str(value)):
            return False
    return True


class TitleFilter:
    def __init__(self, include: list[str], exclude: list[str]):
        self.include = [re.compile(p, re.I) for p in include]
        self.exclude = [re.compile(p, re.I) for p in exclude]

    def __call__(self, title: str) -> bool:
        t = title or ""
        return any(p.search(t) for p in self.include) and not any(p.search(t) for p in self.exclude)


def company_ok(job: Job, skip: list[str]) -> bool:
    name = (job.company or "").lower()
    return not any(s.lower() in name for s in skip)


def recent_enough(job: Job, max_age_days: float) -> bool:
    d = age_days(job.posted_at)
    return d is None or d <= max_age_days


# Work authorization. Aatmaj is on F-1 OPT (STEM OPT eligible) and needs H-1B sponsorship later,
# so postings that rule out sponsorship or require citizenship / clearance are skipped.
# Vague lines like "must be currently authorized to work in the United States" are fine on OPT and don't count.
# One sentence: periods in "U.S." and "U.S.C." don't end it.
_SENTENCE = r"(?:[^.!?;]|\.(?=\S)|(?<=\b[a-z])\.)"
# "sponsorship (such as TN, O-1, H-1B, etc.) for this role is not available"
_ASIDE = r"(?:\s*\([^)]{0,80}\))?(?:\s+for\s+(?:this|the)\s+(?:role|position|job|opportunity))?"
_NO_SPONSOR = re.compile(rf"""
  (?:will\s+not|won['’]?t|cannot|can['’]?t|unable\s+to|do(?:es)?\s+not|(?:are\s+)?not\s+able\s+to)\s+
      (?:currently\s+)?(?:provide|offer|support)?\s*(?:visa\s+|employment\s+|immigration\s+|h-?1b\s+)?sponsor
| (?:are|is)\s+not\s+(?:currently\s+)?(?:offering\s+|providing\s+)?sponsor(?:ing|ship)\b
| sponsorship{_ASIDE}\s+(?:(?:is|are|will)\s+)?(?:not|un)\s*(?:be\s+)?(?:available|provided|offered|possible|supported)
| not\s+(?:be\s+)?eligible\s+for\s+(?:(?:visa|immigration|employment|h-?1b)\s+){{0,2}}sponsorship
| \bno\s+(?:(?:visa|immigration|employment|h-?1b)\s+){{0,2}}sponsorship
| without\s+(?:the\s+)?(?:need\s+for\s+)?(?:current\s+or\s+future\s+|future\s+|employer\s+|company\s+)?
      (?:visa\s+|employment\s+|immigration\s+)?sponsorship
| sponsor\w*{_SENTENCE}{{0,80}}?\bnow\s+or\s+in\s+the\s+future\b(?!{_SENTENCE}*\?)
| \bnow\s+or\s+in\s+the\s+future\b{_SENTENCE}{{0,80}}?\bsponsor\w*(?!{_SENTENCE}*\?)
| authori[sz]ed\s+to\s+(?:be\s+employed|work)\s+(?:in\s+the\s+(?:u\.?\s?s\.?|united\s+states)\s+)?
      (?:for|by)\s+any\s+(?:u\.?\s?s\.?\s+)?employer
| permanent(?:ly)?\s+(?:u\.?\s?s\.?\s+)?(?:work\s+authori[sz]ation|authori[sz]ed\s+to\s+work)
""", re.I | re.X)
_CITIZEN = re.compile(r"""
  (?:u\.?\s?s\.?|united\s+states)\s+citizen(?:ship)?\s+(?:is\s+)?(?:required|only)
| must\s+(?:be\s+)?(?:a\s+)?(?:u\.?\s?s\.?|united\s+states)\s+citizens?
| (?:u\.?\s?s\.?|united\s+states)\s+citizens?\s+only
| (?:must\s+be|requires?\s+(?:being\s+)?)\s*an?\s+(?:u\.?\s?s\.?|united\s+states)\s+person
| (?:active|current)\s+(?:\w+\s+){0,3}(?:security\s+)?clearance
| (?:obtain|maintain)\s+(?:and\s+maintain\s+)?(?:a\s+|an\s+)?(?:\w+\s+){0,2}(?:security\s+)?clearance
| \bts\s*/\s*sci\b | top\s+secret | polygraph
| green\s+card\s+holders?\s+only | permanent\s+residents?\s+only
| must\s+be\s+(?:a\s+)?\(i\)\s*(?:u\.?\s?s\.?|united\s+states)\s+citizen
| \bitar\b[\s\S]{0,240}(?:u\.?\s?s\.?\s+persons?|citizens?\b)
""", re.I | re.X)
# ITAR style lists ("U.S. citizen or national ... refugee ... asylee") only count after requirement wording in the
# same sentence, because equal opportunity notices list the same groups.
_ITAR_LIST = re.compile(
    r"(?:must\s+be|applicants?\s+must|candidates?\s+must|\bitar\b|export\s+(?:control\s+)?regulations?)"
    rf"(?:{_SENTENCE}{{0,120}}?(?:u\.?\s?s\.?|united\s+states)\s+citizens?\s+or\s+(?:u\.?\s?s\.?\s+)?nationals?"
    rf"|{_SENTENCE}{{0,240}}?refugees?{_SENTENCE}{{0,160}}?asylees?)",
    re.I,
)


def work_auth_blocker(job: Job, skip_no_sponsorship: bool = True, skip_citizenship: bool = True) -> str | None:
    """Returns why a posting can't work for Aatmaj, or None if it's fine."""
    tag = str(job.extra.get("sponsorship") or "")
    if skip_citizenship and re.search(r"citizen", tag, re.I):
        return "US citizenship required"
    if skip_no_sponsorship and re.search(r"no\s+sponsor|not\s+offer\s+sponsor", tag, re.I):
        return "no visa sponsorship"
    text = f"{job.title}\n{job.description or ''}"
    if skip_citizenship and (_CITIZEN.search(text) or _ITAR_LIST.search(text)):
        return "citizenship or clearance required"
    if skip_no_sponsorship and _NO_SPONSOR.search(text):
        return "no visa sponsorship"
    return None
