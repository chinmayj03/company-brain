"""
PII regex patterns and dictionary for the PII detector.

ADR-0064 M1: conservative regexes for common PII kinds.
Luhn validator for credit card numbers.
Personal name dictionary (top-1000 common first names as proxy).
"""
from __future__ import annotations

import re
from typing import Pattern


# ── Compiled regexes ─────────────────────────────────────────────────────────
# Each pattern yields a match group 0 == the entire PII value.

EMAIL_RE: Pattern = re.compile(
    r"""(?<![.\w@])                         # not preceded by word / @
        [a-zA-Z0-9._%+\-]+                  # local part
        @
        [a-zA-Z0-9.\-]+                     # domain
        \.[a-zA-Z]{2,}                      # TLD
        (?!\w)                              # not followed by word char
    """,
    re.VERBOSE,
)

# E.164 and common US/EU formats
PHONE_RE: Pattern = re.compile(
    r"""
    (?<!\d)                          # not a digit before
    (?:
        (?:\+?1[\s\-.]?)?            # optional US country code
        [\(\[]?\d{3}[\)\]]?          # area code (any 3 digits)
        [\s\-.]                      # separator required (prevents pure digit sequences)
        \d{3}                        # first 3 digits
        [\s\-.]                      # separator
        \d{4}                        # last 4 digits
    |
        \+[1-9]\d{6,14}              # E.164 international
    )
    (?!\d)                           # not a digit after
    """,
    re.VERBOSE,
)

# US Social Security Number  xxx-xx-xxxx or xxxxxxxxx
SSN_RE: Pattern = re.compile(
    r"""
    (?<!\d)
    (?!000|666|9\d\d)               # invalid SSN prefixes
    [0-9]{3}
    [\s\-]?
    (?!00)[0-9]{2}
    [\s\-]?
    (?!0000)[0-9]{4}
    (?!\d)
    """,
    re.VERBOSE,
)

# Credit card: 13-19 digit sequences that pass Luhn validation
# We capture candidate sequences first, then Luhn-filter them.
CREDIT_CARD_CANDIDATE_RE: Pattern = re.compile(
    r"""
    (?<!\d)
    (?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))  # major issuers
    [\s\-]?
    \d{4}
    [\s\-]?
    \d{4}
    [\s\-]?
    \d{4}
    (?:\d{3})?                      # Amex 15 vs. 16 digits
    (?!\d)
    """,
    re.VERBOSE,
)

# IPv4
IPV4_RE: Pattern = re.compile(
    r"""
    (?<![.\d])
    (?:25[0-5]|2[0-4]\d|[01]?\d\d?)
    \.
    (?:25[0-5]|2[0-4]\d|[01]?\d\d?)
    \.
    (?:25[0-5]|2[0-4]\d|[01]?\d\d?)
    \.
    (?:25[0-5]|2[0-4]\d|[01]?\d\d?)
    (?![\d.])
    """,
    re.VERBOSE,
)

# IPv6 (simplified — matches the canonical colon-separated form)
IPV6_RE: Pattern = re.compile(
    r"""
    (?<![:\w])
    (?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}
    |
    (?:[0-9a-fA-F]{1,4}:){1,7}:
    |
    :(?::[0-9a-fA-F]{1,4}){1,7}
    |
    ::
    (?![\w:])
    """,
    re.VERBOSE,
)

# API keys — high-entropy tokens for common providers
# Patterns are conservative; entropy check defers false positives.
API_KEY_RE: Pattern = re.compile(
    r"""
    (?:
        sk-ant-api\d{2}-[A-Za-z0-9\-_]{16,}   # Anthropic
    |   sk-[A-Za-z0-9]{32,}                    # OpenAI
    |   AKIA[0-9A-Z]{16}                        # AWS access key
    |   (?:github|gh)_(?:pat_)?[A-Za-z0-9_]{20,}  # GitHub PAT
    |   sk-proj-[A-Za-z0-9\-_]{20,}            # OpenAI project key
    |   glpat-[A-Za-z0-9\-_]{20,}              # GitLab PAT
    |   xoxb-[0-9]+-[A-Za-z0-9\-]+             # Slack bot token
    |   xoxp-[0-9]+-[A-Za-z0-9\-]+             # Slack user token
    |   [A-Za-z0-9]{32,}(?=["'\s]|$)           # generic high-entropy token
    )
    """,
    re.VERBOSE,
)

# ── Compiled dict keyed by kind ───────────────────────────────────────────────

REGEX_PATTERNS: dict[str, Pattern] = {
    "email": EMAIL_RE,
    "phone": PHONE_RE,
    "ssn": SSN_RE,
    "credit_card": CREDIT_CARD_CANDIDATE_RE,
    "ip_address": IPV4_RE,
    "api_key": API_KEY_RE,
}


# ── Luhn validator ────────────────────────────────────────────────────────────

def luhn_valid(number: str) -> bool:
    """Return True if `number` (digits only) passes the Luhn check."""
    digits = [int(c) for c in number if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# ── Personal name dictionary (top-1000 common US first names) ─────────────────
# Sourced from US Census Bureau SSA data; used for dictionary-based detection.

FIRST_NAMES: frozenset[str] = frozenset({
    # Top ~300 most common US first names (both genders)
    "James", "John", "Robert", "Michael", "William", "David", "Richard", "Joseph",
    "Thomas", "Charles", "Christopher", "Daniel", "Matthew", "Anthony", "Mark",
    "Donald", "Steven", "Paul", "Andrew", "Joshua", "Kenneth", "Kevin", "Brian",
    "George", "Timothy", "Ronald", "Edward", "Jason", "Jeffrey", "Ryan",
    "Jacob", "Gary", "Nicholas", "Eric", "Jonathan", "Stephen", "Larry",
    "Justin", "Scott", "Brandon", "Benjamin", "Samuel", "Raymond", "Gregory",
    "Frank", "Alexander", "Patrick", "Jack", "Dennis", "Jerry",
    # Female names
    "Mary", "Patricia", "Jennifer", "Linda", "Barbara", "Elizabeth", "Susan",
    "Jessica", "Sarah", "Karen", "Lisa", "Nancy", "Betty", "Margaret", "Sandra",
    "Ashley", "Dorothy", "Kimberly", "Emily", "Donna", "Michelle", "Carol",
    "Amanda", "Melissa", "Deborah", "Stephanie", "Rebecca", "Sharon", "Laura",
    "Cynthia", "Kathleen", "Amy", "Angela", "Shirley", "Anna", "Brenda",
    "Pamela", "Emma", "Nicole", "Helen", "Samantha", "Katherine", "Christine",
    "Debra", "Rachel", "Carolyn", "Janet", "Catherine", "Maria", "Heather",
    "Diane", "Julie", "Joyce", "Victoria", "Ruth", "Virginia", "Lauren",
    "Kelly", "Christina", "Joan", "Evelyn", "Judith", "Megan", "Cheryl",
    "Andrea", "Hannah", "Martha", "Jacqueline", "Frances", "Gloria", "Teresa",
    "Kathryn", "Sara", "Janice", "Jean", "Alice", "Madison", "Doris", "Abigail",
    "Julia", "Judy", "Grace", "Denise", "Amber", "Marilyn", "Beverly",
    "Danielle", "Theresa", "Diana", "Brittany", "Natalie", "Sophia", "Rose",
    "Isabella", "Alexis", "Kayla", "Charlotte", "Olivia", "Ava", "Mia",
    # Additional common names
    "Tyler", "Aaron", "Jose", "Adam", "Nathan", "Henry", "Zachary", "Douglas",
    "Peter", "Kyle", "Noah", "Ethan", "Jeremy", "Walter", "Keith", "Terry",
    "Austin", "Sean", "Gerald", "Carl", "Harold", "Dylan", "Arthur", "Lawrence",
    "Jordan", "Jesse", "Bryan", "Billy", "Bruce", "Gabriel", "Joe", "Logan",
    "Alan", "Juan", "Albert", "Willie", "Elijah", "Wayne", "Randy", "Mason",
    "Vincent", "Liam", "Roy", "Bobby", "Caleb", "Bradley", "Russell",
    "Lucas", "Hunter", "Phillip", "Steve", "Jimmy", "Antonio", "Stanley",
    "Roger", "Carlos", "Eugene", "Louis", "Craig", "Sean", "Alan", "Glen",
})

LAST_NAMES: frozenset[str] = frozenset({
    # Top ~200 most common US last names (SSA / Census data)
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
    "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson",
    "Walker", "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen",
    "Hill", "Flores", "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera",
    "Campbell", "Mitchell", "Carter", "Roberts", "Turner", "Phillips", "Evans",
    "Collins", "Edwards", "Stewart", "Morris", "Murphy", "Cook", "Rogers",
    "Morgan", "Peterson", "Cooper", "Reed", "Bailey", "Bell", "Gomez", "Kelly",
    "Howard", "Ward", "Cox", "Diaz", "Richardson", "Wood", "Watson", "Brooks",
    "Bennett", "Gray", "James", "Reyes", "Cruz", "Hughes", "Price", "Myers",
    "Long", "Foster", "Sanders", "Ross", "Morales", "Powell", "Sullivan",
    "Russell", "Ortiz", "Jenkins", "Gutierrez", "Perry", "Butler", "Barnes",
    "Fisher", "Henderson", "Coleman", "Simmons", "Patterson", "Jordan", "Reynolds",
    "Hamilton", "Graham", "Kim", "Gonzales", "Alexander", "Ramos", "Wallace",
    "Griffin", "West", "Cole", "Hayes", "Chavez", "Gibson", "Bryant", "Ellis",
    "Stevens", "Murray", "Ford", "Marshall", "Owens", "Mcdonald", "Harrison",
    "Ruiz", "Kennedy", "Wells", "Alvarez", "Woods", "Mendoza", "Castillo",
    "Olson", "Webb", "Washington", "Tucker", "Freeman", "Burns", "Henry",
    "Vasquez", "Snyder", "Simpson", "Crawford", "Jimenez", "Porter", "Mason",
    "Shaw", "Gordon", "Wagner", "Hunter", "Romero", "Hicks", "Dixon",
})
