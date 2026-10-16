# PDF Redactor - offline PDF redaction tool
# Copyright (C) 2026 Kiran
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program comes with ABSOLUTELY NO WARRANTY. See the LICENSE file.
"""
Offline PDF redaction engine.

Detection (all on-device):
  * Pattern rules  - SSN/ITIN/EIN (with or without dashes), phones (any format),
                     street / PO Box / city-state-ZIP addresses, emails, cards,
                     bank & account numbers, DOB, labelled IDs
  * Form labels    - values sitting next to / under labels like "Employee's name",
                     "Recipient's address", "SSN", "Dear", "Sincerely"
  * Name lists     - US Census first/last names (bundled in data/)
  * Local AI       - spaCy NER (also run on ALL-CAPS text, which it normally misses)
  * Propagation    - once a name is found, every other mention of it is found too
  * Barcodes / QR  - decoded locally with zxing-cpp, then removed

Redaction is real: text, vector graphics and image pixels under each box are
deleted, then the file is deep-cleaned of metadata and hidden content.
"""
import glob
import os
import re
import socket
import sys
from dataclasses import dataclass, field

import pymupdf as fitz

# Where bundled resources live: next to this file, or inside the packaged app
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Network kill-switch
# ---------------------------------------------------------------------------
def block_network():
    """After this runs, any network connection attempt from this process fails."""
    def _blocked(*a, **k):
        raise RuntimeError("Network access is disabled in PDF Redactor (offline mode).")

    socket.socket.connect = _blocked
    socket.socket.connect_ex = _blocked
    socket.socket.sendto = _blocked
    socket.create_connection = _blocked
    socket.getaddrinfo = _blocked


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
STATE_ABBR = ("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
              "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY "
              "PR VI GU AS MP AA AE AP").split()
STATE_NAMES = ["Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
               "Delaware", "District of Columbia", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois",
               "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland",
               "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana",
               "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
               "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
               "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah",
               "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming",
               "Puerto Rico"]

STREET_SUFFIXES = (
    "Street St Avenue Ave Av Road Rd Boulevard Blvd Drive Dr Lane Ln Court Ct Way Place Pl "
    "Circle Cir Parkway Pkwy Pky Terrace Ter Terr Trail Trl Highway Hwy Square Sq Loop Pike "
    "Crossing Xing Ridge Rdg Hollow Holw Cove Cv Commons Cmns Alley Aly Expressway Expy "
    "Freeway Fwy Route Rte Plaza Plz Heights Hts Glen Gln Bend Bnd Creek Crk Crest Estates "
    "Ests Gardens Gdns Meadows Mdws Meadow Mdw Station Sta Landing Lndg Manor Mnr Mews Oval "
    "Trace Trce Turnpike Tpke Vista Vis Harbor Hbr Junction Jct Orchard Orch Shores Shrs "
    "Springs Spgs Summit Smt Grove Grv Park Pt Point Path Run Row Walk Hill Hl Hills Hls "
    "Knoll Knl Valley Vly Woods Wds Center Ctr Village Vlg Green Grn Close Cres Crescent").split()
STREET_SUFFIX_SET = {s.upper() for s in STREET_SUFFIXES}
UNIT_WORDS = r"(?:Apt|Apartment|Unit|Suite|Ste|Bldg|Building|Fl|Floor|Rm|Room|Lot|Spc|Space|Dept|#)"

# Words that show up on forms / in prose and must never be treated as part of a name
FORM_WORDS = set("""
a an the and or of for to in on at by with from as is be are was this that these those your our
my his her their its name names first last middle initial suffix full legal print printed
address addresses street city town state zip code postal country county apt unit suite
number no num id identification ssn tin ein itin social security medicare wages wage tips tip
compensation federal income tax taxes withheld withholding employer employee employees
recipient recipients payer payers payee borrower lender taxpayer spouse dependent dependents
statement form copy box boxes total totals amount amounts date dates signature signed sign
page department treasury internal revenue service omb void corrected filed return returns
instructions see back front annual planning account accounts bank routing interest dividends
dividend distribution distributions gross net paid payment payments plan plans retirement
pension benefits benefit allocated nonqualified deferred elective statutory third party sick
pay local locality employment insurance mortgage principal premium premiums property points
schedule line lines part section year years month day phone telephone fax email mail
mailing home office business company corporation inc llc ltd corp co llp trust estate
dept notice letter dear sincerely regards thank thanks you please
contact call information info details detail report summary balance due credit debit card
check checking savings deposit direct electronic transfer wire online customer client member
policy loan loans claim claims invoice receipt reference ref transaction transactions
january february march april june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec rev
nw ne sw se north south east west n s e w mr mrs ms dr jr sr ii iii iv md phd esq
""".split())

# First names that are also everyday words - not trusted on their own
COMMON_WORD_FIRST_NAMES = set("""
WILL MAY JUNE APRIL AUGUST GRANT MARK BILL CHASE FRANK ROSE HOPE JOY FAITH PENNY ART DEAN
GENE SUE PAT DON RAY WARD MAJOR PRINCE KING SUMMER AUTUMN DAWN IVY HOLLY CRYSTAL AMBER RUBY
PEARL JEWEL BUD SKIP SONNY ROCKY BRANDY CANDY CHERRY DELL CLAY CLIFF DALE GLEN GLENN LANE
REED REID ROSS ROY SHELBY STERLING WALKER WARREN HUNTER CARTER PARKER MASON TAYLOR FISHER
BAKER COOPER CHRISTIAN NORMAN BERNARD MILES MOSES OPAL PAGE JAN ED AL BEN MAX GUY LEE ROD
TED TOM KIT VAL ROBIN SPARROW STORM RAIN SKY BLAIR BROOK BROOKS BRANDON CHANCE CHARITY
TRUE LOYAL ROYAL FREEMAN JOSE MAR FEDERAL STATE MASTER CLARK WOOD WOODS COLE MILLER
DESIRE LOVE LUCK MERRY PATIENCE PRUDENCE GRACE VIRGINIA GEORGIA CAROLINA DAKOTA ASIA
INDIA CHINA PARIS SAN SANTA SAINT ST JOHNNIE GAY TERRY MINOR MANY SO VAN VON DE LA DEL
""".split())

HONORIFICS = r"(?:Mr|Mrs|Ms|Mx|Miss|Dr|Prof|Rev|Hon|Sr|Sra|Srta)"
NAME_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z'\u2019\-]*\.?$")


def _load_names(fname):
    try:
        with open(os.path.join(HERE, "data", fname), encoding="utf-8") as fh:
            return {ln.strip().upper() for ln in fh if ln.strip()}
    except OSError:
        return set()


FIRST_NAMES = _load_names("first_names.txt")
LAST_NAMES = _load_names("last_names.txt")


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------
def _digits(s):
    return re.sub(r"\D", "", s)


def _luhn_ok(num):
    d = [int(c) for c in _digits(num)]
    if not 13 <= len(d) <= 19:
        return False
    tot = 0
    for i, x in enumerate(reversed(d)):
        if i % 2:
            x *= 2
            if x > 9:
                x -= 9
        tot += x
    return tot % 10 == 0


def _ssn_shape_ok(s):
    d = _digits(s)
    if len(d) != 9:
        return False
    area, group, serial = d[:3], d[3:5], d[5:]
    if d[0] == "9":                    # ITIN range
        return 50 <= int(group) <= 65 or 70 <= int(group) <= 99
    return area not in ("000", "666") and group != "00" and serial != "0000"


def _iban_ok(s):
    s = re.sub(r"\s", "", s).upper()
    if not 15 <= len(s) <= 34 or not s[:2].isalpha():
        return False
    try:
        return int("".join(str(int(c, 36)) for c in s[4:] + s[:4])) % 97 == 1
    except ValueError:
        return False


def _bank_name_ok(s):
    words = [w.strip(",.").lower() for w in s.split()]
    core = [w for w in words if w not in ("bank", "bancorp", "bancshares", "credit", "union",
                                          "savings", "trust", "company", "n.a", "na", "fcu", "of")]
    return bool(core) and not all(w in FORM_WORDS for w in core)


def _acct_ok(s):
    """Account-number value: 4+ digits, or masked like ****1234 / x1234, and not money."""
    if re.search(r"\$|\d,\d{3}|\.\d{2}$", s):
        return False
    return len(_digits(s)) >= 4 or bool(re.search(r"[X*\u2022x.]\d{3,}", s))


def _phone_digits_ok(s):
    return 7 <= len(_digits(s)) <= 15


def _has_digits(n):
    return lambda s: len(_digits(s)) >= n


def plausible_name(s, min_tokens=2):
    s = s.strip(" ,.;:-")
    toks = [t for t in re.split(r"[\s,]+", s) if t]
    toks_core = [t for t in toks if not re.fullmatch(HONORIFICS + r"\.?", t, re.I)]
    if not min_tokens <= len(toks_core) <= 5 or len(s) > 60:
        return False
    if any(not NAME_TOKEN_RE.match(t) for t in toks_core):
        return False
    if not any(len(t.strip(".")) > 1 for t in toks_core):
        return False
    low = [re.sub(r"['\u2019]s$", "", t.lower().strip(".")) for t in toks_core]
    if any(t in FORM_WORDS for t in low if len(t) > 1):
        return False
    if toks_core[-1].upper().strip(".") in STREET_SUFFIX_SET:
        return False
    if all(len(t.strip(".")) == 1 for t in toks_core):
        return False
    return True


# ---------------------------------------------------------------------------
# Pattern rules  (kind, regex, value-group, validator)
# Order = priority when two rules hit the same text.
# ---------------------------------------------------------------------------
_SUF = "|".join(sorted(STREET_SUFFIXES, key=len, reverse=True))
_STATES = "|".join(STATE_ABBR)
_STATE_FULL = "|".join(sorted(STATE_NAMES, key=len, reverse=True))
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_DATE = (r"(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2}|" + _MONTH +
         r"\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+" + _MONTH + r",?\s+\d{4})")

RULES = [
    ("Email", r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b", 0, None),
    ("SSN / ITIN", r"(?<![\w-])\d{3}[- ]\d{2}[- ]\d{4}(?![\w-])", 0, _ssn_shape_ok),
    ("SSN (masked)", r"(?i)(?<![\w])(?:[X*\u2022#]{3}[- ]?[X*\u2022#]{2}[- ]?|[X*\u2022#]{5})\d{4}(?!\d)", 0, None),
    ("EIN", r"(?<![\w-])\d{2}-\d{7}(?![\w-])", 0, None),
    ("Credit card", r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])", 0,
     lambda v: _luhn_ok(v) and not re.fullmatch(r"\d(?: \d)+", v)),
    ("Date of birth", r"(?i)\b(?:dob|d\.o\.b\.?|date\s+of\s+birth|birth\s*date|born(?:\s+on)?)\b[\s:.\-]*("
     + _DATE + r")", 1, None),
    # e-file signature PINs (Form 8879 etc.): "Taxpayer's PIN 48213", "Spouse’s PIN: 7 0 5 9 1",
    # Identity Protection PIN (6 digits). Text without digits may sit between label and number.
    ("PIN", r"(?i:\b(?:taxpayer|spouse|ero|practitioner|preparer|signer|signature|self[- ]select(?:ed)?|"
     r"identity\s+protection|ip)(?:[^\w\s]s)?\s+pin\b)[^\d\n]{0,80}?(\d(?:[ \t]?\d){4,5})(?![\d,.])", 1, None),
    ("PIN", r"\b(?:PIN|Pin)\b(?:\s*(?:#|no\.?|number|code))?[^\d\n]{0,40}?(\d(?:[ \t]?\d){4,5})(?![\d,.])",
     1, None),
    ("IBAN", r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?\b", 0, _iban_ok),
    ("SWIFT / BIC", r"(?i:\b(?:swift|bic)(?:\s*/\s*(?:swift|bic))?(?:\s+code)?\s*[:#.\-]?\s*)"
     r"([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b", 1, None),
    ("Bank routing #", r"[A\u2446|:](\d{9})[A\u2446|:]", 1, None),                     # check MICR line
    ("Bank account #", r"[A\u2446]\d{9}[A\u2446][ \t]*([\d\- ]{3,20}\d)[ \t]*[C\u2448]", 1, None),
    ("Bank routing #",
     r"(?i:\b(?:routing|aba|rtn|transit)\b(?:\s*/\s*transit)?\s*(?:\((?:aba|rtn)\))?\s*"
     r"(?:no\.?|number|num\.?|#)?\s*[:#.\-]?\s*)(\d(?:[ \-]?\d){8})(?!\d)", 1, None),
    ("Bank account #",
     r"(?i:\b(?:account|acct|a/c|checking|savings|share|draft|deposit\s+account|card)\b\.?\s*"
     r"(?:(?:no|num|number)\.?|#|id)?\s*(?:(?:is|ending|ends)\s+(?:in\s+)?|last\s+(?:4|four)"
     r"(?:\s+digits)?(?:\s+of)?\s*)?[:#.\-]?\s*)"
     r"((?:[X*\u2022.]{2,}[ \-]?)?\d(?:[\d\-]|[ ](?=\d)){2,30}|[xX]\d{3,6})(?![\d,])", 1, _acct_ok),
    ("Bank account #", r"(?i)\b(?:ending|ends)\s+in\s+(\d{3,6})\b", 1, None),
    ("Bank account #", r"(?<![\w*\u2022])(?:[X*\u2022]{4,}|\.{3,})[ \-]?\d{3,6}(?!\d)", 0, None),
    ("Bank name", r"\b(?:[A-Z][\w&'.\-]*[ \t]+){1,4}(?i:bank|bancorp|bancshares|credit[ \t]+union|"
     r"savings[ \t]+bank|trust[ \t]+company|fcu)\b(?:,?[ \t]+N\.?A\.?)?", 0, _bank_name_ok),
    ("Bank name", r"\b(?i:bank)[ \t]+of[ \t]+(?:[A-Z][\w&'.\-]*[ \t]?){1,3}", 0, None),
    ("Bank name", r"\b(?:Chase|JPMorgan Chase|JPMorgan|Wells Fargo|Citibank|Citi|Capital One|U\.S\. Bank|US Bank|PNC|Truist|TD Bank|Ally Bank|Ally Financial|USAA|Navy Federal|Fifth Third|KeyBank|Huntington Bank|Citizens Bank|M&T Bank|Regions Bank|BMO|Santander|HSBC|SoFi|Chime|Discover Bank|American Express|Amex|Synchrony|Goldman Sachs|Bank of America|BofA|Schwab Bank|Varo Bank|Revolut|CHASE|JPMORGAN CHASE|JPMORGAN|WELLS FARGO|CITIBANK|CITI|CAPITAL ONE|U\.S\. BANK|US BANK|PNC|TRUIST|TD BANK|ALLY BANK|ALLY FINANCIAL|USAA|NAVY FEDERAL|FIFTH THIRD|KEYBANK|HUNTINGTON BANK|CITIZENS BANK|M&T BANK|REGIONS BANK|BMO|SANTANDER|HSBC|SOFI|CHIME|DISCOVER BANK|AMERICAN EXPRESS|AMEX|SYNCHRONY|GOLDMAN SACHS|BANK OF AMERICA|BOFA|SCHWAB BANK|VARO BANK|REVOLUT)(?![\w&])", 0, None),
    ("Account / ID #",
     r"(?i:\b(?:account|acct|a/c|loan|policy|member|customer|client|reference|ref|parcel|pin|"
     r"license|licence|passport|certificate|contract|claim|case|invoice|routing|aba|iban|vin|"
     r"medicare|medicaid|insurance|subscriber|patient|student|employee|badge|driver'?s?\s+license|"
     r"control|confirmation)\s*(?:id|identification)?\s*(?:no\.?|number|num\.?|#|id)\s*[:.\-]?\s*)"
     r"([A-Z0-9][A-Z0-9\-./]*\d[A-Z0-9\-./]*(?:[ ]\d[\d\-]*)*)", 1, _has_digits(3)),
    ("Phone",
     r"(?<![\w+\-])(?:\+?1[ .\-]?)?(?:\(\s*[2-9]\d{2}\s*\)|[2-9]\d{2})[ .\-]?[2-9]\d{2}[ .\-]?\d{4}"
     r"(?:\s*(?:x|ext\.?|extension)\s*\d{1,5})?(?![\w\-])", 0, None),
    ("Phone", r"(?<![\w+])\+(?!1\b)\d{1,3}(?:[ .\-]?\(?\d{1,5}\)?){2,5}(?![\w\-])", 0, _phone_digits_ok),
    ("Phone", r"(?i)\b(?:phone|tel|telephone|cell(?:ular)?|mobile|fax|ph|contact\s+(?:no|number|#))\b"
     r"\.?\s*(?:#|no\.?|number)?\s*[:\-]?\s*([+\d(][\d()+ .\-]{5,20}\d)", 1, _phone_digits_ok),
    ("Long number (possible account)", r"(?<![\w.,$\-/])\d{10,19}(?![\w\-/]|[.,]\d)", 0, None),
    ("Street address",
     r"(?i)(?<![\w$.,])\d{1,6}[A-Z]?(?:-\d{1,4})?[ \t]+(?:(?:N|S|E|W|NE|NW|SE|SW|North|South|East|West)\.?[ \t]+)?"
     r"(?:[A-Z0-9][\w'.\-]*[ \t]+){0,4}(?:" + _SUF + r")\.?(?:[ \t]+(?:N|S|E|W|NE|NW|SE|SW)\b\.?)?"
     r"(?:,?[ \t]*" + UNIT_WORDS + r"\.?[ \t]*#?[ \t]*[A-Z0-9\-]{1,6})?(?![\w])", 0, None),
    ("Street address", r"(?i)\b(?:P\.?\s?O\.?\s?Box|Post\s+Office\s+Box|PMB|(?:RR|Rural\s+Route)\s*\d+\s+Box)"
     r"\s*#?\s*\d{1,7}\b", 0, None),
    ("City, State ZIP",
     r"(?<![\w])(?:[A-Z][A-Za-z.'\-]+[ \t]){0,2}[A-Z][A-Za-z.'\-]+,?[ \t]+(?:" + _STATES +
     r"|(?i:" + _STATE_FULL + r"))\.?,?[ \t]+\d{5}(?:-\d{4})?(?!\d)", 0, None),
    ("ZIP code", r"\b(?:" + _STATES + r")[ \t]+\d{5}(?:-\d{4})?\b", 0, None),
    ("ZIP code", r"(?<![\d-])\d{5}-\d{4}(?![\d-])", 0, None),
    ("Company", r"\b(?:[A-Z][\w&'.\-]*[ \t]){1,4}(?:LLC|L\.L\.C\.|Inc\.?|Corp\.?|Corporation|Co\.|Ltd\.?|"
     r"LLP|LP|PLLC|P\.?C\.)(?![\w])", 0, None),
    ("IP address", r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b", 0, None),
]
RULES = [(k, re.compile(p), g, v) for k, p, g, v in RULES]
KIND_PRIORITY = {}
for _k, *_ in RULES:
    KIND_PRIORITY.setdefault(_k, len(KIND_PRIORITY))

NINE_DIGITS = re.compile(r"(?<![\w.,$\-/])\d{9}(?![\w\-/]|[.,]\d)")
CTX_EIN = re.compile(r"(?i)\bein\b|employer\s+identification|employer'?s?\s+(?:fed(?:eral)?\s+)?id|\bfein\b|"
                     r"federal\s+(?:tax\s+)?id|payer'?s?\s+(?:federal\s+)?(?:tin|id)")
CTX_SSN = re.compile(r"(?i)\bssn\b|social\s+security\s+(?:number|no|#)|\bss\s*#|soc\.?\s*sec|\bitin\b|"
                     r"\btin\b|taxpayer\s+id|tax\s*id|identifying\s+number|identification\s+number|"
                     r"recipient'?s?\s+(?:tin|id)")
SPACED_DIGITS = re.compile(r"(?<![\d.,])\d(?:[ \t]{1,3}\d){6,19}(?![\d.,])")
PIN_LABEL = re.compile(r"(?i)\b(?:taxpayer|spouse|ero|practitioner|preparer|self[- ]select(?:ed)?|"
                       r"identity\s+protection|ip)(?:[^\w\s]s)?\s+pin\b")
PIN_VALUE = re.compile(r"(?<![\d$.,])\d(?:[ \t]?\d){4,5}(?![\d,.])")
ADDR_SHAPE = re.compile(
    r"(?i)^\s*\d{1,6}[A-Z]?\s+\S.*\b(?:" + "|".join(STREET_SUFFIXES) + r")\b\.?(?:[\s,#]+\S+){0,3}\s*$"
    r"|^\s*(?:P\.?\s?O\.?\s?Box|PMB)\b"
    r"|^\s*[A-Za-z.' \-]{2,40},?\s+(?:" + "|".join(STATE_ABBR) + r")\.?,?\s+\d{5}(?:-\d{4})?\s*$")
BANK_LABEL = re.compile(r"(?i)\b(account|acct|a/c|routing|aba|rtn|transit|iban|card)\b")
CTX_SKIP = re.compile(r"(?i)\bcusip\b|\bsedol\b|\bisin\b")
CTX_ROUTING = re.compile(r"(?i)routing|\baba\b|\brtn\b")
CTX_ACCOUNT = re.compile(r"(?i)\baccount\b|\bacct\b|\bloan\b|\bpolicy\b")

NAME_LABEL = re.compile(
    r"(?i)\bname\b|\b(?:dear|hi|hello|attn\.?|attention|c/o|care\s+of|prepared\s+(?:for|by)|"
    r"signed\s+by)(?=\s)|\b(?:sincerely|regards|respectfully|cordially|"
    r"signature|signed|taxpayer|spouse|employee|recipient|beneficiary|insured|"
    r"patient|cc|bcc|to|from|owner|borrower|applicant|client|customer|account\s+holder)\b\s*[:,]")
NAME_BELOW_LABEL = re.compile(
    r"(?i)\bname\b|\b(?:sincerely|regards|respectfully|cordially|signature|thank you|best)\b")
ADDR_LABEL = re.compile(r"(?i)\baddress\b|\bstreet\b|\bresidence\b|\bcity\b")
LABEL_WORDS = re.compile(
    r"(?i)\b(?:name|address|number|code|wages|tax|income|withheld|identification|social|"
    r"employer|employee|recipient|payer|compensation|box|form|copy|statement|total|date|"
    r"signature|city|state|zip|department|omb|account|telephone|phone|amount|interest|"
    r"dividends|instructions|page)\b")
INLINE_NAME = re.compile(
    r"^\s*[:,\-]?\s*((?:" + HONORIFICS + r"\.?\s+)?[A-Z][A-Za-z'\u2019\-]*\.?"
    r"(?:,?[ \t]+(?:[A-Z][A-Za-z'\u2019\-]*\.?|[A-Z]\.?(?![A-Za-z]))){0,4})")
HONORIFIC_NAME = re.compile(
    r"\b" + HONORIFICS + r"\.?[ \t]+([A-Z][A-Za-z'\u2019\-]+(?:[ \t]+[A-Z]\.?(?![A-Za-z]))?(?:[ \t]+[A-Z][A-Za-z'\u2019\-]+){0,2})")

AI_LABELS = {"PERSON": "Name", "ORG": "Organization", "GPE": "Place", "LOC": "Place", "FAC": "Facility"}


# ---------------------------------------------------------------------------
# Offline OCR (Tesseract)
# ---------------------------------------------------------------------------
def find_tessdata():
    cands = [os.path.join(HERE, "tessdata"), os.environ.get("TESSDATA_PREFIX", "")]
    try:
        cands.append(fitz.get_tessdata())
    except Exception:
        pass
    cands += ["/opt/homebrew/share/tessdata", "/usr/local/share/tessdata", "/usr/share/tessdata",
              r"C:\Program Files\Tesseract-OCR\tessdata", r"C:\Program Files (x86)\Tesseract-OCR\tessdata"]
    cands += sorted(glob.glob("/usr/share/tesseract-ocr/*/tessdata"), reverse=True)
    cands += sorted(glob.glob("/opt/homebrew/Cellar/tesseract/*/share/tessdata"), reverse=True)
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "eng.traineddata")):
            return c
    return None


def page_needs_ocr(page, mode):
    if mode == "off" or not page.get_images():
        return None
    if len(page.get_text().strip()) < 50:
        return "full"
    return "images" if mode == "all" else None


# ---------------------------------------------------------------------------
# Barcodes / QR codes (zxing-cpp, offline)
# ---------------------------------------------------------------------------
def find_barcodes(page, dpis=(150, 300)):
    """Return [(Rect in page coords, format, decoded_text)] or None if zxing-cpp is missing."""
    try:
        import numpy as np
        import zxingcpp
    except ImportError:
        return None
    found = []
    for dpi in dpis:
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
        for res in zxingcpp.read_barcodes(arr):
            pos = res.position
            pts = [pos.top_left, pos.top_right, pos.bottom_right, pos.bottom_left]
            xs, ys = [p.x for p in pts], [p.y for p in pts]
            r = fitz.Rect(min(xs), min(ys), max(xs) + 1, max(ys) + 1) / zoom
            r = r * page.derotation_matrix
            r = fitz.Rect(r.x0 - 6, r.y0 - 6, r.x1 + 6, r.y1 + 6)  # quiet-zone margin
            if not any((r & f[0]).get_area() > 0.5 * r.get_area() for f in found):
                found.append((r, str(res.format).split(".")[-1], res.text))
    return found


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class Line:
    text: str
    rect: fitz.Rect
    row: bool = False      # True = assembled from several pieces on one baseline


@dataclass
class Finding:
    page: int
    text: str
    kind: str
    source: str            # "rule", "label", "names", "ai", "barcode", "custom", "repeat"
    rects: list = field(default_factory=list)
    keep: bool = True      # True = will be redacted
    ocr: bool = False


def _merge_line_rects(rects):
    """Join word boxes that sit on the same line into one box."""
    out = []
    for r in rects:
        if out and abs(out[-1].y0 - r.y0) < 3 and abs(out[-1].y1 - r.y1) < 3 and r.x0 - out[-1].x1 < 25:
            out[-1] = out[-1] | r
        else:
            out.append(fitz.Rect(r))
    return out


def _norm(tok):
    return re.sub(r"[^0-9a-z@]+", "", tok.lower())


def _title_if_caps(line):
    letters = [c for c in line if c.isalpha()]
    if len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        return re.sub(r"[A-Za-z][A-Za-z'\u2019]*", lambda m: m.group(0).capitalize(), line)
    return line


# ---------------------------------------------------------------------------
# Redactor
# ---------------------------------------------------------------------------
class Redactor:
    def __init__(self, use_ai=True, ai_categories=("Name",), custom_terms=(),
                 ocr_mode="auto", ocr_dpi=300, barcodes=True):
        self.use_ai = use_ai
        self.ai_categories = set(ai_categories)
        self.custom_terms = [t.strip() for t in custom_terms if t.strip()]
        self.ocr_mode = ocr_mode
        self.ocr_dpi = ocr_dpi
        self.barcodes = barcodes
        self.tessdata = find_tessdata() if ocr_mode != "off" else None
        self.nlp = None
        self.model_name = None
        if use_ai:
            import spacy  # local models, loaded from disk
            for m in ("en_core_web_lg", "en_core_web_md", "en_core_web_sm"):
                try:
                    self.nlp = spacy.load(m, disable=["lemmatizer"])
                    self.model_name = m
                    break
                except OSError:
                    continue
            if self.nlp is None:
                raise RuntimeError("No spaCy English model found. Run: python -m spacy download en_core_web_sm")

    # ------------------------------------------------------------------
    @staticmethod
    def open_baked(pdf_path):
        """Open the PDF with form-field values and annotation appearances burned into
        the page, so detection sees them and redaction can remove them."""
        doc = fitz.open(pdf_path)
        if doc.needs_pass:
            raise RuntimeError("This PDF is password-protected. Open it in a PDF viewer, "
                               "save an unlocked copy, and redact that.")
        if not doc.is_pdf:
            doc = fitz.open("pdf", doc.convert_to_pdf())
        # Comment-type annotations (sticky notes, attachments, sounds) are deleted
        # outright; visible ones (typed text, stamps, ink) are burned into the page.
        for page in doc:
            for a in list(page.annots() or []):
                if a.type[0] in (fitz.PDF_ANNOT_TEXT, fitz.PDF_ANNOT_POPUP,
                                 fitz.PDF_ANNOT_FILE_ATTACHMENT, fitz.PDF_ANNOT_SOUND):
                    page.delete_annot(a)
        try:
            doc.bake(annots=True, widgets=True)
        except Exception:
            pass
        return doc

    def _textpage(self, page):
        need = page_needs_ocr(page, self.ocr_mode)
        if need and self.tessdata:
            return page.get_textpage_ocr(language="eng", dpi=self.ocr_dpi,
                                         full=(need == "full"), tessdata=self.tessdata), True
        return page.get_textpage(), False

    @staticmethod
    def _lines(page, tp):
        out = []
        d = page.get_text("dict", textpage=tp, sort=True)
        for b in d["blocks"]:
            if b.get("type") != 0:
                continue
            for ln in b["lines"]:
                t = "".join(s["text"] for s in ln["spans"])
                if t.strip():
                    out.append(Line(t, fitz.Rect(ln["bbox"])))
        return out

    # -- context helpers ------------------------------------------------
    @staticmethod
    def _neighbors(lines, i, above=32, left=170):
        """Text of lines just above (overlapping horizontally) or just left on the same row."""
        r = lines[i].rect
        res = []
        for j, o in enumerate(lines):
            if j == i:
                continue
            q = o.rect
            is_above = -2 <= r.y0 - q.y1 <= above and q.x0 < r.x1 + 10 and q.x1 > r.x0 - 10
            is_left = abs((q.y0 + q.y1) / 2 - (r.y0 + r.y1) / 2) < 4 and 0 <= r.x0 - q.x1 <= left
            if is_above or is_left:
                res.append(o.text)
        return res

    @staticmethod
    def _rows(lines, max_gap=30):
        """Join pieces that sit on the same baseline (digit-per-box fields, split cells)."""
        order = sorted(lines, key=lambda l: ((l.rect.y0 + l.rect.y1) / 2, l.rect.x0))
        rows, cur = [], []
        for ln in order:
            if cur:
                prev = cur[-1]
                same_row = abs((ln.rect.y0 + ln.rect.y1) / 2 - (prev.rect.y0 + prev.rect.y1) / 2) < 3
                if same_row and 0 <= ln.rect.x0 - prev.rect.x1 <= max_gap:
                    cur.append(ln)
                    continue
                if len(cur) > 1:
                    rows.append(cur)
            cur = [ln]
        if len(cur) > 1:
            rows.append(cur)
        out = []
        for grp in rows:
            r = fitz.Rect(grp[0].rect)
            for g in grp[1:]:
                r |= g.rect
            out.append(Line(" ".join(g.text.strip() for g in grp), r, row=True))
        return out

    @staticmethod
    def _right(lines, i, max_dist=260, max_items=2):
        r = lines[i].rect
        cy = (r.y0 + r.y1) / 2
        res = [o for j, o in enumerate(lines) if j != i and not o.row and abs((o.rect.y0 + o.rect.y1) / 2 - cy) < 4
               and 0 <= o.rect.x0 - r.x1 <= max_dist]
        return sorted(res, key=lambda o: o.rect.x0)[:max_items]

    @staticmethod
    def _below(lines, i, max_lines=3, gap=30):
        """Lines stacked directly below line i (value lines under a form label)."""
        r = lines[i].rect
        cands = [o for j, o in enumerate(lines) if j != i and o.rect.y0 >= r.y1 - 3
                 and o.rect.x0 < r.x1 and o.rect.x1 > r.x0 - 5 and o.rect.x0 >= r.x0 - 25]
        cands.sort(key=lambda o: o.rect.y0)
        res, last_y = [], r.y1
        for o in cands:
            if o.rect.y0 - last_y > gap or len(res) >= max_lines:
                break
            res.append(o)
            last_y = o.rect.y1
        return res

    # -- detection on one page ------------------------------------------
    def _detect(self, lines):
        hits = {}  # normalized value -> (value, kind, source)

        def add(val, kind, src):
            val = val.strip(" \t,;:")
            if re.search(r"\d\.$", val):
                val = val[:-1]
            if len(val) < 2:
                return
            key = _norm(val)
            if not key:
                return
            if key in hits and KIND_PRIORITY.get(kind, 99) >= KIND_PRIORITY.get(hits[key][1], 99):
                return
            hits[key] = (val, kind, src)

        lines = lines + self._rows(lines)
        for i, ln in enumerate(lines):
            t = ln.text
            # 1. pattern rules
            for kind, rx, grp, check in RULES:
                for m in rx.finditer(t):
                    val = m.group(grp).strip()
                    if val and (check is None or check(val)):
                        add(val, kind, "rule")

            # 2. bare 9-digit numbers: SSN / EIN / routing / account by nearby label
            for m in NINE_DIGITS.finditer(t):
                ctx = " ".join([t[:m.start()][-80:]] + self._neighbors(lines, i))
                if CTX_SKIP.search(t[:m.start()][-30:]):
                    continue
                if CTX_EIN.search(ctx):
                    kind = "EIN"
                elif CTX_SSN.search(ctx):
                    kind = "SSN / ITIN"
                elif CTX_ROUTING.search(ctx):
                    kind = "Bank routing #"
                elif CTX_ACCOUNT.search(ctx):
                    kind = "Account / ID #"
                else:
                    kind = "9-digit ID (possible SSN)"
                add(m.group(0), kind, "rule")

            # 2b. digit-per-box fields ("0 7 1 0 0 0 0 1 3") on forms like the 1040 refund section
            for m in SPACED_DIGITS.finditer(t):
                ctx = " ".join([t[:m.start()][-80:]] + self._neighbors(lines, i))
                nd = len(_digits(m.group(0)))
                if CTX_ROUTING.search(ctx) and nd == 9:
                    kind = "Bank routing #"
                elif nd == 9 and CTX_SSN.search(ctx):
                    kind = "SSN / ITIN"
                elif nd == 9 and CTX_EIN.search(ctx):
                    kind = "EIN"
                else:
                    kind = "Bank account #" if CTX_ACCOUNT.search(ctx) else "Account / ID #"
                add(m.group(0), kind, "rule")

            # 2b2. PIN labels whose number sits in a separate cell to the right or below
            pm = PIN_LABEL.search(t)
            if pm and not PIN_VALUE.search(t[pm.end():]):
                right = " ".join(o.text.strip() for o in self._right(lines, i, max_items=12))
                below = [o.text for o in self._below(lines, i, max_lines=2, gap=20)
                         if len(o.text.split()) <= 3 or re.fullmatch(r"[\d\s]+", o.text.strip())]
                for cand in [right] + below:
                    vm = PIN_VALUE.search(cand)
                    if vm:
                        add(vm.group(0), "PIN", "label")
                        break

            # 2c. bank labels with the value under them or to the right (tables, forms)
            bm = BANK_LABEL.search(t)
            if bm and len(t.split()) <= 8 and len(_digits(t[bm.end():])) < 4:
                kind = "Bank routing #" if re.search(r"(?i)routing|aba|rtn|transit", t) else "Bank account #"
                cands = self._below(lines, i, max_lines=15, gap=24) + self._right(lines, i)
                for o in cands:
                    m = re.search(r"(?:[X*\u2022.]{2,}[ \-]?)?\d(?:[\d\-]|[ ](?=\d)){3,30}", o.text)
                    if not m or not _acct_ok(m.group(0)) or re.search(r"[$]|\d\.\d\d\b", o.text[m.start():m.end() + 3]):
                        if o in cands[:1] or not re.search(r"\d", o.text):
                            break
                        continue
                    add(m.group(0), kind, "label")

            # 3. names after honorifics ("Mr. Kiran Patel")
            for m in HONORIFIC_NAME.finditer(t):
                if plausible_name(m.group(1), 1):
                    add(m.group(1), "Name", "label")

            # 4a. names inline after a label ("Dear Priya Natarajan,", "Taxpayer: RAMIREZ, JORDAN")
            for m in NAME_LABEL.finditer(t):
                im = INLINE_NAME.match(t[m.end():])
                if im and plausible_name(im.group(1), 1):
                    add(im.group(1), "Name", "label")
            # 4b. names on the line under a label ("e Employee's first name..." / "Sincerely,")
            if (NAME_BELOW_LABEL.search(t) and LABEL_WORDS.search(t)) or re.search(
                    r"(?i)^\s*(sincerely|regards|respectfully|cordially|best|thank you)\b", t):
                for o in self._below(lines, i, max_lines=2):
                    cand = o.text.strip()
                    if LABEL_WORDS.search(cand) and not re.search(r"\d", cand):
                        break
                    if plausible_name(cand, 1):
                        add(cand, "Name", "label")
                        break

            # 5. address lines under an address label
            am = ADDR_LABEL.search(t)
            if am and len(t.split()) <= 10 and not re.search(r"\d", t[am.end():]):
                for o in self._below(lines, i, max_lines=3):
                    cand = o.text.strip()
                    if (LABEL_WORDS.search(cand) and not re.search(r"\d", cand)) or len(cand.split()) > 8:
                        break
                    addr_like = (ADDR_SHAPE.search(cand) or
                                 (len(cand.split()) <= 5 and re.search(r"\d", cand)
                                  and not re.search(r"[.!?]\s|\b(?:dear|call|please|the|and|your)\b", cand, re.I)))
                    if not addr_like:
                        break
                    add(cand, "Address", "label")

            # 6. census name lists: "JORDAN A RAMIREZ", "Maria Gonzalez", "RAMIREZ, JORDAN"
            for val in self._gazetteer(t):
                add(val, "Name", "names")

        # 7. custom terms
        for term in self.custom_terms:
            add(term, "Custom term", "custom")

        # 8. local AI (spaCy), with ALL-CAPS lines title-cased so it can read them
        if self.nlp:
            orig = "\n".join(ln.text for ln in lines)
            mixed = "\n".join(_title_if_caps(ln.text) for ln in lines)
            known_vals = " | ".join(v[0].lower() for v in hits.values())
            for ent in self.nlp(mixed).ents:
                kind = AI_LABELS.get(ent.label_)
                if kind not in self.ai_categories:
                    continue
                for piece in orig[ent.start_char:ent.end_char].split("\n"):
                    piece = piece.strip(" ,.:;'\"()")
                    if not piece or piece.lower() in known_vals:
                        continue
                    if kind == "Name":
                        toks = piece.split()
                        known = any(x.upper().strip(".,") in FIRST_NAMES or
                                    x.upper().strip(".,") in LAST_NAMES for x in toks)
                        if not plausible_name(piece, 1) or (len(toks) == 1 and (
                                not known or piece.upper().strip(".") in COMMON_WORD_FIRST_NAMES)):
                            continue
                    elif (len(piece) < 3 or piece.lower() in FORM_WORDS or
                          any(w in FORM_WORDS for w in piece.lower().split())):
                        continue
                    add(piece, kind, "ai")
        return list(hits.values())

    @staticmethod
    def _gazetteer(text):
        out = []
        toks = [(m.group(0), m.start(), m.end())
                for m in re.finditer(r"[A-Za-z][A-Za-z'\u2019\-]*\.?|,", text)]
        words = [t[0] for t in toks]

        def is_cap(w):
            return w[:1].isupper()

        def first_ok(w):
            u = w.upper().strip(".")
            return (is_cap(w) and len(u) > 1 and u in FIRST_NAMES and u not in COMMON_WORD_FIRST_NAMES
                    and u.lower() not in FORM_WORDS)

        def last_ok(w):
            u = w.upper().strip(".")
            return (is_cap(w) and len(u) > 1 and u in LAST_NAMES and u.lower() not in FORM_WORDS
                    and u not in STREET_SUFFIX_SET)

        n = len(words)
        for i in range(n):
            if first_ok(words[i]):                               # First [M.] Last
                j = i + 1
                if j < n and len(words[j].strip(".")) == 1 and is_cap(words[j]):
                    j += 1
                if j < n and last_ok(words[j]):
                    out.append(text[toks[i][1]:toks[j][2]])
                    continue
            if i + 2 < n and last_ok(words[i]) and words[i + 1] == "," and first_ok(words[i + 2]):
                end = toks[i + 2][2]                             # Last, First [M.]
                if i + 3 < n and len(words[i + 3].strip(".")) == 1 and is_cap(words[i + 3]):
                    end = toks[i + 3][2]
                out.append(text[toks[i][1]:end])
        return out

    # -- locate values on the page (whole-word aware) --------------------
    @staticmethod
    def _locate(page, tp, words, value):
        vt = [x for x in (_norm(t) for t in value.split()) if x]
        if not vt:
            return []
        rects = []
        n, k = len(words), len(vt)
        i = 0
        while i <= n - k:
            if all(words[i + j][0] == vt[j] for j in range(k)):
                rects += _merge_line_rects([words[i + j][1] for j in range(k)])
                i += k
            else:
                i += 1
        if rects and len(_digits(value)) >= 6:
            for r in page.search_for(value, textpage=tp):
                if not any((r & q).get_area() > 0.5 * r.get_area() for q in rects):
                    rects.append(r)
        if rects:
            return rects
        # glued text like "SSN:123456789" - substring search, but only for values
        # that are safe to substring-match (contain digits / @, or are long)
        if re.search(r"\d|@", value) or len(value) >= 8:
            return page.search_for(value, textpage=tp)
        return []

    # -- full scan --------------------------------------------------------
    def scan(self, pdf_path, progress=None):
        doc = self.open_baked(pdf_path)
        findings, ocr_pages, unreadable, unlocated = [], [], [], []
        page_data = []
        barcode_missing = False
        for pno, page in enumerate(doc):
            if progress:
                progress(pno + 1, len(doc))
            need = page_needs_ocr(page, self.ocr_mode if self.ocr_mode != "off" else "auto")
            tp, used_ocr = self._textpage(page)
            if used_ocr:
                ocr_pages.append(pno + 1)
            elif need == "full":
                unreadable.append(pno + 1)
            lines = self._lines(page, tp)
            words = [(_norm(w[4]), fitz.Rect(w[:4])) for w in page.get_text("words", textpage=tp)]
            words = [w for w in words if w[0]]
            page_data.append((page, tp, words, used_ocr))

            for val, kind, src in self._detect(lines):
                rects = self._locate(page, tp, words, val)
                if rects:
                    findings.append(Finding(pno, val, kind, src, rects, ocr=used_ocr))
                else:
                    unlocated.append((pno + 1, kind, val))

            if self.barcodes:
                bcs = find_barcodes(page)
                if bcs is None:
                    barcode_missing = True
                else:
                    for r, fmt, txt in bcs:
                        label = f"{fmt}: {txt[:40]}{'...' if len(txt) > 40 else ''}" if txt else fmt
                        findings.append(Finding(pno, label, "QR / barcode", "barcode", [r]))

        # Propagate names: every other mention of a found name's parts gets redacted too
        tokens = set()
        for f in findings:
            if f.kind in ("Name", "Custom term"):
                for t in re.split(r"[\s,]+", f.text):
                    t = re.sub(r"['\u2019]s$", "", t.strip(".'"))
                    if (len(t) >= 3 and t.lower() not in FORM_WORDS
                            and not re.fullmatch(HONORIFICS, t, re.I) and not re.search(r"\d", t)):
                        tokens.add(t)
        for pno, (page, tp, words, used_ocr) in enumerate(page_data):
            for t in sorted(tokens):
                rects = self._locate(page, tp, words, t)
                if rects:
                    findings.append(Finding(pno, t, "Name", "repeat", rects, ocr=used_ocr))

        findings = self._dedupe(findings)
        doc.close()
        return findings, {"ocr_pages": ocr_pages, "unreadable_pages": unreadable,
                          "ocr_available": bool(self.tessdata), "unlocated": unlocated,
                          "barcode_lib_missing": barcode_missing, "model": self.model_name}

    @staticmethod
    def _dedupe(findings):
        """Drop boxes already covered by another (bigger) finding."""
        findings.sort(key=lambda f: (f.page, -sum(r.get_area() for r in f.rects)))
        kept = []
        for f in findings:
            others = [r for g in kept if g.page == f.page for r in g.rects]
            new_rects = [r for r in f.rects
                         if not any((o & r).get_area() > 0.8 * max(r.get_area(), 1e-6) for o in others)]
            if new_rects:
                f.rects = new_rects
                kept.append(f)
        kept.sort(key=lambda f: (f.page, KIND_PRIORITY.get(f.kind, 50), f.text))
        return kept

    # -- redaction ---------------------------------------------------------
    @staticmethod
    def apply(pdf_path, out_path, findings, label=True, flatten=False, flatten_dpi=200):
        doc = Redactor.open_baked(pdf_path)
        for f in findings:
            if not f.keep:
                continue
            page = doc[f.page]
            for r in f.rects:
                r = fitz.Rect(r)
                if f.kind != "QR / barcode":   # trim line-height padding so labels above/below survive
                    h = r.height
                    r = fitz.Rect(r.x0 - 1, r.y0 + h * 0.18, r.x1 + 1, r.y1 - h * 0.12)
                page.add_redact_annot(r, text=f"[{f.kind.upper()}]" if label else "",
                                      fontsize=5, fill=(0, 0, 0), text_color=(1, 1, 1))
        for page in doc:
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS,
                                  graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                                  text=fitz.PDF_REDACT_TEXT_REMOVE)
        deep_clean(doc)
        if flatten:
            flat = fitz.open()
            for page in doc:
                pix = page.get_pixmap(dpi=flatten_dpi, alpha=False)
                newp = flat.new_page(width=page.rect.width, height=page.rect.height)
                newp.insert_image(newp.rect, stream=pix.tobytes("jpeg", jpg_quality=85))
            doc.close()
            doc = flat
            deep_clean(doc)
        doc.save(out_path, garbage=4, deflate=True, clean=True, no_new_id=True)
        doc.close()

    # -- verification -------------------------------------------------------
    def verify(self, out_path, findings):
        """Re-read the saved file. Returns dict of problems (all empty = clean)."""
        doc = fitz.open(out_path)
        chunks, barcodes_left = [], []
        for pno, page in enumerate(doc):
            chunks.append(page.get_text())
            if self.tessdata and page.get_images():
                chunks.append(page.get_text(textpage=page.get_textpage_ocr(
                    language="eng", dpi=self.ocr_dpi, full=True, tessdata=self.tessdata)))
            if self.barcodes:
                for r, fmt, txt in find_barcodes(page) or []:
                    barcodes_left.append(f"page {pno + 1}: {fmt}")
        meta = metadata_report(doc)
        doc.close()
        text = re.sub(r"\[[A-Z0-9 /#(),.\-]+\]", " ", re.sub(r"\s+", " ", " ".join(chunks)))
        leaks = set()
        for f in findings:
            if not f.keep or f.kind == "QR / barcode":
                continue
            v = f.text.strip()
            if len(_digits(v)) >= 7:   # numbers: match with any separators
                pat = r"(?<!\d)" + r"[\s.\-()]?".join(_digits(v)) + r"(?!\d)"
            else:
                pat = r"(?<!\w)" + r"\s+".join(map(re.escape, v.split())) + r"(?!\w)"
            if re.search(pat, text, re.I):
                leaks.add(v)
        return {"leaks": sorted(leaks), "barcodes": barcodes_left, "metadata": meta}


# ---------------------------------------------------------------------------
# Deep metadata / hidden-content removal
# ---------------------------------------------------------------------------
_CATALOG_KEYS = ("PageMode", "Info", "Metadata", "PieceInfo", "StructTreeRoot", "MarkInfo", "Names", "OpenAction", "AA",
                 "AcroForm", "Outlines", "Threads", "Perms", "Legal", "SpiderInfo", "URI", "Lang",
                 "PageLabels", "Collection", "Dests", "AF")
_PAGE_KEYS = ("PieceInfo", "Metadata", "StructParents", "Thumb", "AA", "B", "LastModified",
              "Annots", "Tabs", "ID", "PZ", "SeparationInfo", "AF")


def _strip_jpeg(j):
    """Drop EXIF/XMP/IPTC/comment segments from a JPEG, keep the image itself."""
    if not j.startswith(b"\xff\xd8"):
        return None
    out, i = bytearray(b"\xff\xd8"), 2
    while i + 4 <= len(j):
        if j[i] != 0xFF:
            return None
        m = j[i + 1]
        if m == 0xDA:                               # start of scan: rest is image data
            out += j[i:]
            return bytes(out)
        seg_len = int.from_bytes(j[i + 2:i + 4], "big")
        drop = (0xE1 <= m <= 0xEF and m not in (0xE2, 0xEE)) or m == 0xFE
        if not drop:
            out += j[i:i + 2 + seg_len]
        i += 2 + seg_len
    return None


def deep_clean(doc):
    doc.set_metadata({})
    try:
        doc.del_xml_metadata()
    except Exception:
        pass
    try:
        doc.set_toc([])
    except Exception:
        pass
    for name in list(doc.embfile_names()):
        doc.embfile_del(name)
    for page in doc:
        for a in list(page.annots() or []):
            page.delete_annot(a)
        for w in list(page.widgets() or []):
            page.delete_widget(w)
    doc.scrub(attached_files=True, clean_pages=True, embedded_files=True, hidden_text=True,
              javascript=True, metadata=True, redactions=False, remove_links=True,
              reset_fields=True, reset_responses=True, thumbnails=True, xml_metadata=True)
    touched = set()

    def kill(xref, names):
        try:
            keys = doc.xref_get_keys(xref)
        except Exception:
            return
        for k in names:
            if k in keys:
                doc.xref_set_key(xref, k, "null")
                touched.add(xref)

    kill(doc.pdf_catalog(), _CATALOG_KEYS)
    for page in doc:
        kill(page.xref, _PAGE_KEYS)
    for xref in range(1, doc.xref_length()):
        kill(xref, ("Metadata", "PieceInfo", "LastModified", "StructParent", "StructParents", "JS", "AF"))
        if (doc.xref_get_key(xref, "Subtype")[1] == "/Image"
                and doc.xref_get_key(xref, "Filter")[1] == "/DCTDecode"):
            raw = doc.xref_stream_raw(xref)
            clean = _strip_jpeg(raw)
            if clean and clean != raw:
                doc.update_stream(xref, clean, compress=0)
                doc.xref_set_key(xref, "Filter", "/DCTDecode")
    for xref in touched:   # remove the "/Key null" leftovers entirely
        obj = doc.xref_object(xref, compressed=True)
        new = re.sub(r"/[A-Za-z0-9]+ ?null(?=[/>\s])", "", obj)
        if new != obj:
            doc.update_object(xref, new)


def metadata_report(doc):
    """List anything metadata-like still present in an opened document."""
    issues = []
    if any(v for k, v in doc.metadata.items() if k not in ("format", "encryption")):
        issues.append("document info fields")
    if doc.xref_xml_metadata():
        issues.append("XMP metadata")
    if doc.get_toc():
        issues.append("bookmarks")
    if doc.embfile_count():
        issues.append("attachments")
    for page in doc:
        if page.first_annot or page.first_widget:
            issues.append(f"annotations/form fields on page {page.number + 1}")
    for xref in range(1, doc.xref_length()):
        try:
            keys = doc.xref_get_keys(xref)
        except Exception:
            continue
        for k in ("Metadata", "PieceInfo", "JS", "JavaScript", "EmbeddedFile"):
            if k in keys:
                issues.append(f"/{k} in object {xref}")
        if doc.xref_get_key(xref, "Filter")[1] == "/DCTDecode":
            raw = doc.xref_stream_raw(xref)
            if b"Exif" in raw[:4000] or b"ns.adobe.com/xap" in raw[:8000]:
                issues.append(f"photo EXIF/XMP in image {xref}")
    return sorted(set(issues))


# ---------------------------------------------------------------------------
# Self-test: builds a fake document, redacts it, and checks the result.
# Run with:  python pdf_redactor.py --selftest   (or the app binary + --selftest)
# ---------------------------------------------------------------------------
def self_test():
    import tempfile
    secrets = ["JORDAN A RAMIREZ", "123456789", "4821 MAPLE GROVE DR", "SPRINGFIELD IL 62704",
               "(217)555-0142", "48213", "0 7 1 0 0 0 0 1 3", "Priya Natarajan", "SUNITA K VENKATARAMAN",
               "456789123"]
    keep = ["84250.00", "Wages, tips, other compensation", "Taxpayer"]
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((40, 50), "a Employee's social security number", fontsize=7)
    p.insert_text((40, 64), "123456789", fontsize=10)
    p.insert_text((300, 50), "1 Wages, tips, other compensation", fontsize=7)
    p.insert_text((300, 64), "84250.00", fontsize=10)
    p.insert_text((40, 90), "e Employee's first name and initial   Last name", fontsize=7)
    p.insert_text((40, 104), "JORDAN A RAMIREZ", fontsize=10)
    p.insert_text((40, 130), "f Employee's address and ZIP code", fontsize=7)
    p.insert_text((40, 144), "4821 MAPLE GROVE DR", fontsize=10)
    p.insert_text((40, 158), "SPRINGFIELD IL 62704", fontsize=10)
    p.insert_text((40, 190), "Dear Priya Natarajan, call (217)555-0142.  Taxpayer's PIN 48213", fontsize=10)
    p.insert_text((40, 215), "b Routing number", fontsize=8)
    for i, ch in enumerate("071000013"):
        p.insert_text((150 + i * 16, 215), ch, fontsize=10)
    barcode_ok = False
    try:
        import io
        import numpy as np
        import zxingcpp
        img = np.array(zxingcpp.create_barcode("JORDAN RAMIREZ 123456789",
                                               zxingcpp.BarcodeFormat.QRCode).to_image(scale=4))
        pix = fitz.Pixmap(fitz.csGRAY, img.shape[1], img.shape[0], img.tobytes(), False)
        p.insert_image(fitz.Rect(420, 120, 520, 220), pixmap=pix)
        barcode_ok = True
    except Exception as e:  # noqa
        print("  barcode test skipped:", e)
    # a scanned page (image only) to exercise OCR
    tmp = fitz.open()
    tp = tmp.new_page()
    tp.insert_text((50, 60), "RECIPIENT'S NAME\nSUNITA K VENKATARAMAN\nRECIPIENT'S TIN 456789123", fontsize=12)
    scan = doc.new_page()
    scan.insert_image(scan.rect, pixmap=tp.get_pixmap(dpi=200))
    doc.set_metadata({"author": "Jordan Ramirez"})

    d = tempfile.mkdtemp()
    src, out = os.path.join(d, "in.pdf"), os.path.join(d, "out.pdf")
    doc.save(src)
    r = Redactor()
    print(f"  AI model: {r.model_name}\n  OCR data: {r.tessdata or 'NOT FOUND'}")
    findings, info = r.scan(src)
    Redactor.apply(src, out, findings)
    rep = r.verify(out, findings)
    res = fitz.open(out)
    text = ""
    for pg in res:
        text += pg.get_text() + " "
        if r.tessdata and pg.get_images():
            text += pg.get_text(textpage=pg.get_textpage_ocr(dpi=300, full=True, tessdata=r.tessdata))
    text = re.sub(r"\s+", " ", text)
    left = [x for x in secrets if x in text]
    lost = [x for x in keep if x not in text]
    found_bc = any(f.kind == "QR / barcode" for f in findings)
    ok = (not left and not lost and not any(rep.values()) and r.tessdata is not None
          and (found_bc or not barcode_ok) and res.metadata.get("author", "") == "")
    print(f"  findings: {len(findings)}   OCR pages: {info['ocr_pages']}   barcode found: {found_bc}")
    print(f"  still readable: {left or 'none'}   wrongly removed: {lost or 'none'}")
    print(f"  verify: {rep}")
    print("SELF-TEST " + ("PASSED" if ok else "FAILED"))
    return ok
