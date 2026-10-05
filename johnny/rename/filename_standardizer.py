import argparse
import json
import os
import re

from johnny.core.utils import generate_safe_filename, safe_rename


def _load_hints(kind):
    """Personal name/place hints: local_hints.json (git-ignored) or JOHNNY_HINTS, else local_hints.example.json."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    paths = [os.environ.get("JOHNNY_HINTS"), os.path.join(root, "local_hints.json"), os.path.join(root, "local_hints.example.json")]
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                return {str(key).lower(): str(value) for key, value in json.load(handle).get(kind, {}).items()}
        except (OSError, ValueError, AttributeError):
            return {}
    return {}


WEAK_BASE_PATTERNS = [
    r"^\d+$",
    r"^[a-f0-9-]{8,}$",
    r"^filename(?:[_ -]?\d+)?(?: \(\d+\))?$",
    r"^downloaded(?:[_ -].*)?$",
    r"^null(?:[_ -]?\d+)?$",
    r"^unknown(?:[_ -].*)?$",
    r"^document(?:[_ -]?\d+)?$",
    r"^file(?:[_ -]?\d+)?$",
    r"^new[_ -]?document.*$",
    r"^viewpdfservlet.*$",
]

GENERIC_STOPWORDS = {
    "a", "an", "and", "copy", "document", "documents", "downloaded", "file", "final",
    "for", "from", "image", "images", "new", "of", "on", "or", "scan", "temp", "the",
    "to", "untitled", "with", "aadhaar", "adhar", "aadhar", "card", "certificate",
    "marksheet", "salary", "appointment", "letter", "migration", "voter", "id", "dob",
    "degree", "domicile", "panjikarn", "www", "com", "flv2mp3"
}

TYPE_PATTERNS = [
    ("MigrationCertificate", [r"\bmigration\s+certificate\b", r"\bcertificate\s+of\s+migration\b"]),
    ("ProvisionalCertificate", [r"\bprovisional\s+certificate\b"]),
    ("DegreeCertificate", [r"\bbachelors?\s+degree\b", r"\bdegree\b"]),
    ("BirthCertificate", [r"\bdob\b", r"\bdate\s+of\s+birth\b", r"\bbirth\s+certificate\b"]),
    ("DomicileCertificate", [r"\bdomicile\b"]),
    ("AadhaarCard", [r"\baadhaar\b", r"\badhar\b", r"\baadhar\b", r"\badharcard\b"]),
    ("PANCard", [r"\bpan\b"]),
    ("DrivingLicence", [r"\bdriving\s+licen[cs]e\b", r"\bdl\b"]),
    ("VoterID", [r"\bvoter\s+id\b", r"\belection\s+card\b"]),
    ("GoldenCard", [r"\bgolden\s+card\b"]),
    ("PhotoID", [r"\bphoto\s*id\b"]),
    ("Marksheet", [r"\bmarksheet\b", r"\bmark\s*sheet\b"]),
    ("SalarySlip", [r"\bsalary\b", r"\bsalary\s+slip\b"]),
    ("BankStatement", [r"\bbank\s+statement\b"]),
    ("ChallanReceipt", [r"\bchallan\s+receipt\b"]),
    ("ChallanStatement", [r"\bchallan\s+statement\b"]),
    ("TaxAcknowledgement", [r"\back(?:nowledgement)?\b", r"\btax\s+ack(?:nowledgement)?\b"]),
    ("ITRForm", [r"\bitr\s*form\b", r"\bform\s+itr\b", r"\bform_pdf\b"]),
    ("ITRV", [r"\bitrv\b", r"\bitr-v\b", r"\bverification\b"]),
    ("Invoice", [r"\binvoice\b"]),
    ("Receipt", [r"\breceipt\b"]),
    ("ElectricityBill", [r"\belectricity\s+bill\b"]),
    ("WaterBill", [r"\bwater\s+bill\b", r"\bpeyjal\b"]),
    ("Bill", [r"\bbill\b"]),
    ("Policy", [r"\bpolicy\b"]),
    ("AppointmentLetter", [r"\bappointment\s+letter\b"]),
    ("Application", [r"\bapplication\b"]),
    ("Agreement", [r"\bagreement\b"]),
    ("AdmitCard", [r"\badmit\s+card\b"]),
    ("QuestionPaper", [r"\bquestion\s+paper\b"]),
    ("AnswerKey", [r"\banswer\s+key\b"]),
    ("SubmittedAnswers", [r"\bsubmitted\s+answers?\b", r"\bsubmitedanswer\b"]),
    ("Resume", [r"\bresume\b"]),
    ("CV", [r"\bcv\b"]),
    ("ResearchProposal", [r"\bresearch\s+proposal\b"]),
    ("Haemogram", [r"\bhaemogram\b", r"\bcbc\b"]),
    ("Cytology", [r"\bcytology\b", r"\bbal\b"]),
    ("Report", [r"\breport\b"]),
    ("Notes", [r"\bnotes?\b"]),
    ("Lecture", [r"\blecture\b", r"\blec\d*\b"]),
    ("Module", [r"\bmodule\b", r"\bmod\d*\b"]),
    ("Checklist", [r"\bchecklist\b"]),
    ("Index", [r"\bindex\b"]),
    ("Certificate", [r"\bcertificate\b"]),
    ("EmploymentRegistration", [r"\bpanjikarn\b", r"\bemployment\s+registration\b"]),
    ("Guide", [r"\bguide\b"]),
    ("Book", [r"\bbook\b"]),
    ("Data", [r"\bdata\b"]),
    ("Screenshot", [r"\bscreenshot\b"]),
    ("Photo", [r"\bphoto\b"]),
]

ISSUER_PATTERNS = [
    ("UIDAI", [r"\buidai\b", r"\baadhaar\b", r"\badhar\b", r"\baadhar\b"]),
    ("SBI", [r"\bsbi\b", r"\bstate\s+bank\b"]),
    ("UPCL", [r"\bupcl\b"]),
    ("GATE", [r"\bgate\b"]),
    ("UKPSC", [r"\bukpsc\b"]),
    ("ICICI", [r"\bicici\b"]),
    ("IFMS", [r"\bifms\b"]),
    ("ISKCON", [r"\biskcon\b"]),
    ("Portronics", [r"\bportronic?s\b"]),
    ("Autodesk", [r"\bautodesk\b"]),
    ("Revit", [r"\brevit\b"]),
    ("UIDAI", [r"\baadhaar\s+card\b", r"\badhar\s+card\b"]),
]

STATUS_PATTERNS = [
    ("Draft", [r"\bdraft\b"]),
    ("Final", [r"\bfinal\b"]),
    ("Ocr", [r"\bocr(?:ed)?\b"]),
    ("Recovered", [r"\brecovered\b"]),
    ("Prefilled", [r"\bprefilled\b"]),
]

VERSION_PATTERNS = [
    r"\brev(?:ision)?[_ -]?(\d+)\b",
    r"\bv(?:er(?:sion)?)?[_ -]?(\d+)\b",
    r"\bpart[_ -]?(\d+)\b",
    r"\bmodule[_ -]?(\d+)\b",
    r"\bmod[_ -]?(\d+)\b",
    r"\blec(?:ture)?[_ -]?(\d+)\b",
]

PERSON_HINTS = _load_hints("person")

MONTH_NAMES = {
    name: f"{number:02d}"
    for number, names in enumerate(
        [("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
         ("july", "jul"), ("august", "aug"), ("september", "sept", "sep"), ("october", "oct"), ("november", "nov"), ("december", "dec")], 1)
    for name in names
}

LOCATION_HINTS = _load_hints("location")

RAW_RENAME_PATTERNS = [
    r"\badhar\b",
    r"\baadhar\b",
    r"\blicience\b",
    r"\blicence\b",
    r"\bform[_ -]?pdf\b",
    r"\bviewpdfservlet\b",
    r"\bscan(?:ned)?\b",
    r"\bwhatsapp\s+image\b",
    r"\bimg[-_ ]?\d+\b",
]

GENERIC_FALLBACK_TYPES = {"Document", "MediaFile", "Data", "Photo"}

SOURCE_NOISE_PATTERNS = [
    re.compile(r"\bwww\b", re.I),
    re.compile(r"\bflv2mp3\b", re.I),
    re.compile(r"\bupload[_ -]?\w+\b", re.I),
]

BENIGN_SUFFIX_TOKENS = {"copy", "scanned", "scan", "final"}
STRUCTURAL_PHRASES = [
    ("bank", "statement"),
    ("annual", "report"),
    ("floor", "plan"),
    ("flight", "dynamics"),
    ("migration", "certificate"),
    ("provisional", "certificate"),
    ("birth", "certificate"),
    ("question", "paper"),
    ("answer", "key"),
    ("legal", "notice"),
    ("court", "order"),
    ("code", "book"),
    ("steel", "design"),
    ("data", "pipeline"),
    ("leave", "policy"),
    ("stock", "market"),
    ("sponsorship", "policy"),
    ("service", "bulletin"),
    ("missile", "guidance"),
    ("earnings", "call"),
]


TITLE_LIKE_TYPES = {"Report", "Book", "Guide", "Notes", "Lecture", "Module"}

PATTERN_CLASS_CONFIG = {
    "identity_personal": {
        "types": {
            "AadhaarCard",
            "PANCard",
            "DrivingLicence",
            "VoterID",
            "GoldenCard",
            "PhotoID",
            "BirthCertificate",
            "DomicileCertificate",
        },
        "order": ["name", "type", "issuer", "date", "version", "status"],
        "required": ["type"],
    },
    "financial_tax": {
        "types": {
            "BankStatement",
            "ChallanReceipt",
            "ChallanStatement",
            "TaxAcknowledgement",
            "ITRForm",
            "ITRV",
            "Invoice",
            "Receipt",
            "ElectricityBill",
            "WaterBill",
            "Bill",
            "Policy",
        },
        "order": ["name", "type", "issuer", "id", "date", "version", "status"],
        "required": ["type"],
    },
    "academic_certificate": {
        "types": {
            "Marksheet",
            "Certificate",
            "ProvisionalCertificate",
            "MigrationCertificate",
            "DegreeCertificate",
            "AdmitCard",
            "QuestionPaper",
            "AnswerKey",
            "SubmittedAnswers",
            "ResearchProposal",
            "Application",
            "Checklist",
            "Index",
        },
        "order": ["name_or_topic", "type", "issuer", "date", "version", "status"],
        "required": ["type"],
    },
    "study_reference": {
        "types": {"Notes", "Lecture", "Module", "Guide", "Book"},
        "order": ["topic", "type", "issuer", "date", "version", "status"],
        "required": ["topic", "type"],
    },
    "technical_project": {
        "types": {"Agreement", "Report"},
        "extensions": {".dwg", ".dxf", ".rvt", ".rfa", ".bak", ".pcp", ".skp"},
        "order": ["project", "type", "subject", "version", "date", "status"],
        "required": ["project"],
    },
    "media_title": {
        "extensions": {".mp3", ".wav", ".flac", ".m4a", ".mp4", ".mkv", ".avi", ".htm", ".html"},
        "order": ["artist", "title", "part", "date"],
        "required": ["title"],
    },
}


def split_words(raw):
    raw = raw.replace("%20", " ")
    raw = raw.replace("&", " and ")
    raw = raw.replace("(", " ").replace(")", " ")
    raw = raw.replace("[", " ").replace("]", " ")
    parts = re.sub(r"[_\-.]+", " ", raw)
    return [p for p in re.findall(r"[A-Za-z0-9]+", parts) if p]


def filter_noise_tokens(tokens):
    cleaned = []
    for token in tokens:
        if not token:
            continue
        if token.lower() in GENERIC_STOPWORDS:
            continue
        if any(pattern.search(token) for pattern in SOURCE_NOISE_PATTERNS):
            continue
        cleaned.append(token)
    return cleaned


def meaningful_tokens(tokens):
    return [
        token
        for token in tokens
        if token
        and token.lower() not in GENERIC_STOPWORDS
        and not any(pattern.search(token) for pattern in SOURCE_NOISE_PATTERNS)
    ]


def to_compact_pascal(tokens):
    parts = []
    for token in tokens:
        if not token:
            continue
        if re.search(r"[A-Z]", token) and re.search(r"[a-z]", token):
            parts.append(token)
            continue
        if token.isupper() and len(token) <= 6:
            parts.append(token)
        elif token.isdigit():
            parts.append(token)
        else:
            parts.append(token[:1].upper() + token[1:].lower())
    return "".join(parts)


def normalize_component(value):
    if not value:
        return None
    tokens = split_words(value)
    return to_compact_pascal(tokens) if tokens else None


def compact_token(value):
    return re.sub(r"[\W_]+", "", (value or "")).lower()


def strip_benign_suffixes(base):
    cleaned = base
    patterns = [
        r"\s*\(\d+\)\s*$",
        r"\bcopy\b\s*$",
        r"\bscanned\b\s*$",
        r"\bscan(?:ned)?\b\s*$",
        r"\bfinal\b\s*$",
    ]
    changed = True
    while changed:
        changed = False
        for pattern in patterns:
            updated = re.sub(pattern, "", cleaned, flags=re.I).strip(" _-.")
            if updated != cleaned:
                cleaned = updated
                changed = True
    return cleaned


def is_alnum_code(token):
    return bool(re.fullmatch(r"[A-Za-z]{1,5}\d{1,6}[A-Za-z0-9]*", token or ""))


def clean_display_token(token):
    if not token:
        return None
    if token.isupper() and len(token) <= 8:
        return token
    if token.isdigit():
        return token
    return token[:1].upper() + token[1:]


def normalize_sequence_token(token):
    if not token:
        return None
    if "_" in token:
        return to_compact_pascal(token.split("_"))
    if token.isupper() and len(token) <= 8:
        return token
    if token.isdigit():
        return token
    if re.fullmatch(r"[A-Za-z]+\d+", token):
        return token[:1].upper() + token[1:]
    return token[:1].upper() + token[1:]


class FilenameStandardizer:
    def __init__(self):
        self.type_patterns = [(label, [re.compile(p, re.I) for p in patterns]) for label, patterns in TYPE_PATTERNS]
        self.issuer_patterns = [(label, [re.compile(p, re.I) for p in patterns]) for label, patterns in ISSUER_PATTERNS]
        self.status_patterns = [(label, [re.compile(p, re.I) for p in patterns]) for label, patterns in STATUS_PATTERNS]

    def is_weak_name(self, file_name):
        base = os.path.splitext(os.path.basename(file_name))[0].strip().lower()
        return any(re.match(pattern, base) for pattern in WEAK_BASE_PATTERNS)

    def is_already_standardized(self, file_name):
        base = os.path.splitext(os.path.basename(file_name))[0].strip()
        if " " in base or "(" in base or ")" in base or "%" in base:
            return False
        components = [c for c in base.split("_") if c]
        if len(components) < 2:
            return False
        for comp in components:
            if re.fullmatch(r"\d{4}(?:-\d{2})?(?:-\d{2})?", comp):
                continue
            if re.fullmatch(r"\d{4}_\d{2}", comp):
                continue
            if re.fullmatch(r"\d{4}_\d{2}_\d{2}", comp):
                continue
            if re.fullmatch(r"(?:Rev|V|Part|Module|Lec)\d+", comp):
                continue
            if re.fullmatch(r"[A-Z]{2,8}", comp):
                continue
            if re.fullmatch(r"[A-Z]{2,}[A-Za-z0-9]*", comp):
                continue
            if re.fullmatch(r"[A-Z][A-Za-z0-9]+", comp):
                continue
            return False
        return True

    def matches_known_template(self, file_name):
        base = strip_benign_suffixes(os.path.splitext(os.path.basename(file_name))[0].strip())
        components = [c for c in base.split("_") if c]
        if not 2 <= len(components) <= 6:
            return False

        has_type = any(component in {label for label, _ in TYPE_PATTERNS} for component in components)
        has_date = any(re.fullmatch(r"\d{4}(?:-\d{2})?(?:-\d{2})?", component) for component in components)
        has_issuer = any(component in {label for label, _ in ISSUER_PATTERNS} for component in components)
        has_strong_lead = bool(components and re.fullmatch(r"[A-Z][A-Za-z0-9]+", components[0]))
        return has_strong_lead and (has_type or has_date or has_issuer)

    def should_hard_skip(self, file_name):
        base = os.path.splitext(os.path.basename(file_name))[0].strip()
        ext = os.path.splitext(file_name)[1]
        if ext.lower() in {".properties", ".ini", ".cfg", ".yaml", ".yml", ".toml"}:
            return True
        lowered = base.lower()
        if self.is_weak_name(file_name):
            return True
        if self.is_already_standardized(file_name) or self.matches_known_template(file_name):
            return True
        cleaned = strip_benign_suffixes(base)
        if cleaned != base:
            cleaned_name = f"{cleaned}{ext}"
            if self.is_already_standardized(cleaned_name) or self.matches_known_template(cleaned_name):
                return True
            cleaned_words = split_words(cleaned)
            meaningful = meaningful_tokens(cleaned_words)
            if len(meaningful) <= 1:
                return True
            matched_type = self.infer_type(" ".join(cleaned_words), ext)
            canonical = self.canonical_chunks(cleaned_words)
            structure = self.detect_structure(canonical, matched_type, ext)
            effective_type = None if matched_type in GENERIC_FALLBACK_TYPES else matched_type
            if len(meaningful) >= 3 and not effective_type and structure in {"artifact", "title", "record"} and not any("_" in chunk for chunk in canonical):
                return True
            if len(meaningful) >= 3 and matched_type in TITLE_LIKE_TYPES.union({"Policy", "Application"}) and structure in {"title", "record"}:
                return True
            has_short_alpha = any(token.isalpha() and len(token) <= 3 for token in meaningful)
            if len(meaningful) == 2 and structure == "record" and has_short_alpha:
                if structure in {"title", "record"}:
                    return True
        words = split_words(base)
        matched_type = self.infer_type(" ".join(words), ext)
        has_strong_signal = any(is_alnum_code(word) or re.search(r"\d", word) for word in words)
        if matched_type in TITLE_LIKE_TYPES and len(meaningful_tokens(words)) <= 3 and not has_strong_signal:
            return True
        if self.looks_like_title(words, None, ext) and len(meaningful_tokens(words)) >= 5:
            return True
        if not any(pattern.search(lowered) for pattern in SOURCE_NOISE_PATTERNS) and len(meaningful_tokens(split_words(base))) >= 3:
            if " " not in base and "__" not in base and "--" not in base and ".." not in base:
                return True
        return False

    def detect_phrase_chunks(self, words):
        chunks = []
        lowered = [w.lower() for w in words]
        idx = 0
        while idx < len(words):
            matched = False
            for phrase in STRUCTURAL_PHRASES:
                size = len(phrase)
                if idx + size <= len(words) and tuple(lowered[idx : idx + size]) == phrase:
                    chunks.append("_".join(words[idx : idx + size]))
                    idx += size
                    matched = True
                    break
            if matched:
                continue
            chunks.append(words[idx])
            idx += 1
        return chunks

    def canonical_chunks(self, words):
        merged = []
        idx = 0
        while idx < len(words):
            current = words[idx]
            current_lower = current.lower()
            next_word = words[idx + 1] if idx + 1 < len(words) else None
            if next_word:
                next_lower = next_word.lower()
                if current.isalpha() and len(current) <= 4 and next_word.isdigit():
                    merged.append(current.upper() + next_word)
                    idx += 2
                    continue
                if current_lower in {"module", "part", "level", "rev", "revision", "q", "quarter", "lec", "lecture"} and next_word.isdigit():
                    merged.append(to_compact_pascal([current_lower, next_word]))
                    idx += 2
                    continue
                if current_lower == "q" and re.fullmatch(r"\d+", next_word):
                    merged.append(f"Q{next_word}")
                    idx += 2
                    continue
                if current_lower.startswith("q") and len(current_lower) == 2 and current_lower[1:].isdigit():
                    merged.append(current.upper())
                    idx += 1
                    continue
                if current_lower == "vs":
                    merged.append("vs")
                    idx += 1
                    continue
            merged.append(current)
            idx += 1
        return self.detect_phrase_chunks(merged)

    def score_salience(self, chunks, matched_type):
        roles = []
        matched_type_compact = compact_token(matched_type) if matched_type else None
        for chunk in chunks:
            raw = chunk.replace("_", " ")
            lowered = raw.lower()
            compact = compact_token(raw)
            if lowered in BENIGN_SUFFIX_TOKENS or any(pattern.search(raw) for pattern in SOURCE_NOISE_PATTERNS):
                role = "noise"
            elif re.fullmatch(r"\d{4}(?:-\d{2})?(?:-\d{2})?", raw) or raw.lower().startswith(("rev", "v", "part", "module", "lec")):
                role = "qualifier"
            elif compact and matched_type_compact and (compact in matched_type_compact or matched_type_compact in compact):
                role = "anchor"
            elif is_alnum_code(raw) or raw.isupper():
                role = "anchor"
            elif "_" in chunk:
                role = "anchor"
            elif lowered in PERSON_HINTS or lowered in LOCATION_HINTS:
                role = "qualifier"
            else:
                role = "qualifier"
            roles.append({"text": chunk, "role": role})
        return roles

    def detect_structure(self, chunks, matched_type, ext):
        ext_lower = ext.lower()
        lowered_chunks = [chunk.replace("_", " ").lower() for chunk in chunks]
        if ext_lower in PATTERN_CLASS_CONFIG["media_title"]["extensions"]:
            return "title"
        if matched_type:
            return "record"
        if ext_lower in PATTERN_CLASS_CONFIG["technical_project"]["extensions"] or any(is_alnum_code(chunk.replace("_", "")) for chunk in chunks):
            return "artifact"
        if any(re.search(r"\b(report|notes|book|policy|analysis|design)\b", chunk) for chunk in lowered_chunks):
            return "title"
        return "weak"

    def format_chunk(self, chunk):
        if not chunk:
            return None
        if chunk == "vs":
            return "vs"
        return normalize_sequence_token(chunk)

    def should_attempt_standardization(self, file_name):
        base = os.path.splitext(os.path.basename(file_name))[0].strip()
        lowered = base.lower()
        if self.should_hard_skip(file_name):
            return False

        obvious_noise = any(ch in base for ch in [" ", "(", ")", "[", "]", "%"])
        separator_noise = "__" in base or "--" in base or ".." in base
        raw_pattern_hit = any(re.search(pattern, lowered, re.I) for pattern in RAW_RENAME_PATTERNS)

        if obvious_noise or separator_noise or raw_pattern_hit:
            return True

        components = [c for c in base.split("_") if c]
        if 2 <= len(components) <= 5:
            return False

        return False

    def extract_date(self, text):
        raw = text.replace("%20", " ")
        months = "|".join(MONTH_NAMES)  # a "Jan-Mar 2023" range is ambiguous, so it is not read as a single month

        def ymd(year, month, day):
            return f"{year}-{month}-{day}" if 1 <= int(month) <= 12 and 1 <= int(day) <= 31 else None

        def ym(year, month):
            is_range = int(month) == (int(year[2:]) + 1) % 100  # academic/fiscal year, e.g. 2021-22
            return f"{year}-{month}" if is_range or 1 <= int(month) <= 12 else None

        patterns = [
            (r"\b(20\d{2})[-_](\d{2})[-_](\d{2})\b", lambda m: ymd(m.group(1), m.group(2), m.group(3))),
            (r"(?<!\d)(\d{2})[-_. ](\d{2})[-_. ](20\d{2})(?!\d)", lambda m: ymd(m.group(3), m.group(2), m.group(1))),
            (r"\b(20\d{2})[-_](\d{2})\b", lambda m: ym(m.group(1), m.group(2))),
            (r"\b(\d{2})(\d{2})(20\d{2})\b", lambda m: ymd(m.group(3), m.group(2), m.group(1))),
            (r"\b(\d{2})(\d{2})(\d{2})\b", lambda m: ymd(f"20{m.group(3)}", m.group(2), m.group(1))),
            (rf"\b({months})[ _.-]*(20\d{{2}})\b", lambda m: None if re.search(rf"\b({months})[ _.-]+(?:to[ _.-]+)?({months})[ _.-]*20\d{{2}}", raw, re.I) else f"{m.group(2)}-{MONTH_NAMES[m.group(1).lower()]}"),
            (r"\b(20\d{2})\b", lambda m: m.group(1)),
            (r"\b(\d{4})[-_](\d{2})\b", lambda m: ym(m.group(1), m.group(2))),
        ]
        for pattern, formatter in patterns:
            match = re.search(pattern, raw, re.I)
            if match:
                value = formatter(match)
                if value:
                    return value
        return None

    def extract_version(self, text):
        for pattern in VERSION_PATTERNS:
            match = re.search(pattern, text, re.I)
            if match:
                number = match.group(1)
                key = pattern.split("\\b")[1]
                if "rev" in pattern:
                    return f"Rev{number}"
                if "lec" in pattern:
                    return f"Lec{number}"
                if "module" in pattern or "mod" in pattern:
                    return f"Module{number}"
                if "part" in pattern:
                    return f"Part{number}"
                return f"V{number}"
        copy_match = re.search(r"\((\d+)\)$", text)
        if copy_match:
            return copy_match.group(1)
        return None

    def match_label(self, text, compiled_patterns):
        for label, patterns in compiled_patterns:
            for pattern in patterns:
                if pattern.search(text):
                    return label
        return None

    def infer_primary_identifier(self, words, matched_type, issuer, date_value, version):
        lowered = [w.lower() for w in words]
        for token in lowered:
            if token in PERSON_HINTS:
                return PERSON_HINTS[token]

        candidates = []
        for word in words:
            lw = word.lower()
            if lw in GENERIC_STOPWORDS:
                continue
            if lw in PERSON_HINTS or lw in LOCATION_HINTS:
                continue
            if issuer and lw == issuer.lower():
                continue
            if matched_type and lw in matched_type.lower():
                continue
            if date_value and lw in date_value.replace("-", ""):
                continue
            if version and lw.lower() == version.lower():
                continue
            if lw.isdigit() and len(lw) >= 6:
                continue
            candidates.append(word)

        if not candidates:
            return None

        primary = to_compact_pascal(candidates[:3])
        return primary if primary else None

    def infer_secondary_identifier(self, words, matched_type, primary, issuer, date_value, version):
        for word in words:
            lw = word.lower()
            if lw in LOCATION_HINTS:
                return LOCATION_HINTS[lw]

        for word in words:
            lw = word.lower()
            if lw in PERSON_HINTS:
                continue
            if primary and lw in primary.lower():
                continue
            if issuer and lw == issuer.lower():
                continue
            if matched_type and lw in matched_type.lower():
                continue
            if date_value and lw in date_value.replace("-", ""):
                continue
            if version and lw.lower() == version.lower():
                continue
            if re.fullmatch(r"[A-Za-z]{2,}\d+[A-Za-z0-9]*", word):
                return word.upper() if len(word) <= 8 else to_compact_pascal([word])
        return None

    def infer_type(self, text, ext):
        matched = self.match_label(text, self.type_patterns)
        if matched:
            return matched

        ext_lower = ext.lower()
        if ext_lower in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}:
            return "Photo" if re.search(r"\b(site|inspection|photo|portrait|selfie)\b", text, re.I) else "MediaFile"
        if ext_lower in {".txt", ".csv", ".json", ".xml", ".py", ".js", ".ts"}:
            return "Data"
        if ext_lower in {".doc", ".docx", ".pdf"}:
            return "Document"
        return None

    def detect_pattern_class(self, matched_type, ext, words):
        ext_lower = ext.lower()
        lowered_text = " ".join(words).lower()
        for class_name, config in PATTERN_CLASS_CONFIG.items():
            if matched_type and matched_type in config.get("types", set()):
                return class_name
            if ext_lower in config.get("extensions", set()):
                if class_name != "media_title" or any(token in lowered_text for token in ["song", "album", "music", "audio", "geet", "part", "episode", "report", "landmarks", "from", "earthquake"]):
                    return class_name
                return class_name
        return None

    def looks_like_title(self, words, matched_type, ext):
        ext_lower = ext.lower()
        lower_words = [w.lower() for w in words]
        phrase_markers = {"of", "from", "on", "with", "part", "song", "report", "earthquake", "landmarks"}
        if ext_lower in PATTERN_CLASS_CONFIG["media_title"]["extensions"]:
            return True
        if matched_type in TITLE_LIKE_TYPES and any(word in phrase_markers for word in lower_words):
            return True
        return False

    def extract_known_name(self, words, matched_type):
        if matched_type not in {
            "AadhaarCard",
            "PANCard",
            "DrivingLicence",
            "VoterID",
            "GoldenCard",
            "PhotoID",
            "BirthCertificate",
            "DomicileCertificate",
            "Marksheet",
            "Certificate",
            "ProvisionalCertificate",
            "MigrationCertificate",
            "DegreeCertificate",
            "TaxAcknowledgement",
            "ITRForm",
            "ITRV",
            "AppointmentLetter",
        } | PATTERN_CLASS_CONFIG["financial_tax"]["types"]:
            return None

        found = []
        for word in words:
            mapped = PERSON_HINTS.get(word.lower())
            if mapped and mapped not in found:
                found.append(mapped)
        if not found:
            return None
        if len(found) > 1 and found[0] != found[1]:
            return None
        return found[0]

    def extract_identifier(self, words, text):
        match = re.search(r"\b([A-Za-z]{0,4}\d{4,}[A-Za-z0-9]*)\b", text)
        if match:
            raw = match.group(1)
            return raw.upper() if len(raw) <= 12 else to_compact_pascal([raw])
        return None

    def extract_media_roles(self, base):
        if "-" not in base:
            return None, None
        left, right = base.split("-", 1)
        artist_tokens = meaningful_tokens(split_words(left))
        title_tokens = meaningful_tokens(split_words(right))
        artist = "_".join(
            token.upper() if token.isupper() and len(token) <= 6 else token[:1].upper() + token[1:]
            for token in artist_tokens[:3]
        ) if artist_tokens else None
        title = "_".join(
            token.upper() if token.isupper() and len(token) <= 6 else token[:1].upper() + token[1:]
            for token in title_tokens[:6]
        ) if title_tokens else None
        return artist or None, title or None

    def infer_topic(self, words, matched_type, issuer, date_value, version, preserve_order=False):
        filtered = []
        chunks = self.canonical_chunks(words)
        version_compact = compact_token(version) if version else None
        matched_type_compact = compact_token(matched_type) if matched_type else None
        issuer_compact = compact_token(issuer) if issuer else None
        date_compact = compact_token(date_value) if date_value else None
        for chunk in chunks:
            lw = chunk.lower()
            if lw in GENERIC_STOPWORDS or lw in PERSON_HINTS or lw in LOCATION_HINTS:
                continue
            chunk_compact = compact_token(chunk)
            if issuer_compact and chunk_compact == issuer_compact:
                continue
            if matched_type_compact and chunk_compact == matched_type_compact:
                continue
            if date_compact and chunk_compact == date_compact:
                continue
            if version_compact and chunk_compact == version_compact:
                continue
            if matched_type in {"Module", "Lecture"} and re.fullmatch(rf"{matched_type}\d+", self.format_chunk(chunk) or "", re.I):
                continue
            filtered.append(chunk)
        filtered = [
            chunk for chunk in filtered
            if chunk
            and compact_token(chunk) not in {matched_type_compact, issuer_compact, date_compact, version_compact}
        ]
        if not filtered:
            return None
        selected = filtered[:5] if preserve_order else filtered[:3]
        return "_".join(self.format_chunk(token) for token in selected if self.format_chunk(token))

    def extract_technical_artifact(self, words):
        lowered = [w.lower() for w in words]
        if "floor" in lowered and "plan" in lowered:
            if "level" in lowered:
                idx = lowered.index("level")
                if idx + 1 < len(words) and words[idx + 1].isdigit():
                    return f"FloorPlan_Level{words[idx + 1]}"
            return "FloorPlan"
        if "model" in lowered:
            return "Model"
        if "layout" in lowered:
            return "Layout"
        if "design" in lowered and "plot" in lowered:
            return "PlotDesign"
        if "report" in lowered:
            return "Report"
        return None

    def build_candidate_components(self, pattern_class, matched_type, ext, words, text, issuer, status, date_value, version):
        name = self.extract_known_name(words, matched_type)
        topic = self.infer_topic(words, matched_type, issuer, date_value, version, preserve_order=self.looks_like_title(words, matched_type, ext))
        project = None
        for word in words:
            lw = word.lower()
            if lw in LOCATION_HINTS:
                project = LOCATION_HINTS[lw]
                break
        if not project and ext.lower() in PATTERN_CLASS_CONFIG["technical_project"].get("extensions", set()):
            project = topic
        if not project and pattern_class == "technical_project":
            project = topic
        identifier = self.extract_identifier(words, text)
        if identifier and date_value and compact_token(identifier) in compact_token(date_value):
            identifier = None  # the date already carries it (e.g. the year)
        artist, media_title = self.extract_media_roles(text)
        technical_artifact = self.extract_technical_artifact(words)
        normalized_type = normalize_component(matched_type) if matched_type else None
        if matched_type in {"Module", "Lecture", "Part"} and version and version.lower().startswith(matched_type.lower()):
            normalized_type = None

        roles = {
            "name": name,
            "type": normalized_type,
            "issuer": normalize_component(issuer) if issuer else None,
            "date": date_value,
            "version": version,
            "status": normalize_component(status) if status else None,
            "topic": topic,
            "project": project,
            "subject": technical_artifact or (topic if topic != project else None),
            "artist": artist,
            "title": media_title or topic,
            "part": version if version and version.startswith("Part") else None,
            "id": identifier,
            "name_or_topic": name or topic,
        }

        if pattern_class is None:
            return None
        config = PATTERN_CLASS_CONFIG[pattern_class]
        for required in config.get("required", []):
            if not roles.get(required):
                return None

        components = []
        for role in config.get("order", []):
            value = roles.get(role)
            if value and value not in components:
                components.append(value)
        return components

    def build_minimal_cleanup_candidate(self, base, ext, words, matched_type, version):
        cleaned = strip_benign_suffixes(base)
        cleaned_words = split_words(cleaned)
        chunks = self.canonical_chunks(cleaned_words)
        if cleaned == base and chunks == words:
            return None
        if cleaned == base:
            has_strong_signal = any(
                is_alnum_code(chunk.replace("_", "")) or re.search(r"\d", chunk)
                for chunk in chunks
            )
            if not has_strong_signal:
                return None
        structure = self.detect_structure(chunks, matched_type, ext)
        if structure == "weak":
            return None
        components = []
        for chunk in chunks:
            formatted = self.format_chunk(chunk)
            if not formatted:
                continue
            if version and compact_token(formatted) == compact_token(version):
                if formatted not in components:
                    components.append(formatted)
                continue
            if matched_type and compact_token(formatted) == compact_token(matched_type):
                if any("_" in original for original in chunks):
                    continue
            if formatted not in components:
                components.append(formatted)
        if len(components) < 1:
            return None
        if len(components) > 5:
            return None
        candidate = "_".join(components).strip("_")
        if not candidate:
            return None
        return candidate

    def candidate_preserves_meaning(self, base, candidate, words, matched_type, pattern_class):
        original = set(token.lower() for token in meaningful_tokens(words))
        new_tokens = set(token.lower() for token in split_words(candidate))
        if not new_tokens:
            return False
        candidate_parts = [compact_token(token) for token in split_words(candidate)]
        for word in words:
            mapped = PERSON_HINTS.get(word.lower())
            if mapped:
                keys = (compact_token(word), compact_token(mapped))
                if not any(part == keys[1] or part.startswith(keys[0]) for part in candidate_parts):
                    return False
        original_chunks = self.detect_phrase_chunks(words)
        original_roles = self.score_salience(original_chunks, matched_type)
        candidate_chunks = self.detect_phrase_chunks(split_words(candidate))
        candidate_compact = [compact_token(chunk) for chunk in candidate_chunks]

        anchor_chunks = [compact_token(item["text"]) for item in original_roles if item["role"] == "anchor"]
        for anchor in anchor_chunks:
            if anchor and not any(anchor in cand or cand in anchor for cand in candidate_compact):
                return False

        if original:
            original_compact = [compact_token(token) for token in original if compact_token(token)]
            candidate_parts = [compact_token(token) for token in split_words(candidate) if compact_token(token)]
            compact_coverage = 0.0
            if original_compact:
                covered = sum(
                    1 for token in original_compact
                    if any(token in part or part in token for part in candidate_parts)
                )
                compact_coverage = covered / len(original_compact)
            if compact_coverage < 0.45 and pattern_class not in {"identity_personal", "financial_tax", "academic_certificate"}:
                return False
            if compact_coverage == 0.0 and pattern_class in {"identity_personal", "financial_tax", "academic_certificate"}:
                return False

        if matched_type:
            type_tokens = set(split_words(matched_type))
            if type_tokens and not type_tokens & new_tokens:
                type_compact = compact_token(matched_type)
                if not any(type_compact in compact_token(token) or compact_token(token) in type_compact for token in split_words(candidate)):
                    return False

        if self.looks_like_title(words, matched_type, os.path.splitext(base)[1]):
            original_sequence = [compact_token(token) for token in meaningful_tokens(words)[:3] if compact_token(token)]
            candidate_sequence = [compact_token(token) for token in split_words(candidate)[: max(3, len(original_sequence))] if compact_token(token)]
            if original_sequence and not all(
                any(token in cand or cand in token for cand in candidate_sequence)
                for token in original_sequence[:2]
            ):
                return False
        return True

    def standardize_name(self, file_name):
        ext = os.path.splitext(file_name)[1]
        base = os.path.splitext(os.path.basename(file_name))[0].strip()
        if not base:
            return None

        if not self.should_attempt_standardization(file_name):
            return None

        words = split_words(base)
        text = " ".join(words)
        if not words:
            return None

        matched_type = self.infer_type(text, ext)
        pattern_class = self.detect_pattern_class(matched_type, ext, words)
        issuer = self.match_label(text, self.issuer_patterns)
        status = self.match_label(text, self.status_patterns)
        date_value = self.extract_date(base)
        version = self.extract_version(base)

        minimal_candidate = self.build_minimal_cleanup_candidate(base, ext, words, matched_type, version)
        effective_matched_type = None if matched_type in GENERIC_FALLBACK_TYPES else matched_type

        if minimal_candidate:
            if self.candidate_preserves_meaning(file_name, minimal_candidate, words, effective_matched_type, pattern_class):
                return minimal_candidate[:170].strip("_")

        if matched_type in GENERIC_FALLBACK_TYPES or pattern_class is None:
            return None

        components = self.build_candidate_components(
            pattern_class,
            matched_type,
            ext,
            words,
            text,
            issuer,
            status,
            date_value,
            version,
        )
        if not components:
            return None

        deduped = []
        seen = set()
        for component in components:
            key = component.lower()
            if key in seen:
                continue
            deduped.append(component)
            seen.add(key)

        if len(deduped) < 2:
            return None
        candidate = "_".join(deduped[:5])

        if not candidate or candidate == base:
            return None

        if not self.candidate_preserves_meaning(file_name, candidate, words, effective_matched_type, pattern_class):
            return None

        return candidate[:170].strip("_")

    def rename_file(self, file_path, dry_run=False, reserved=None):
        if not os.path.isfile(file_path):
            return {"path": file_path, "status": "skipped", "reason": "not_a_file"}

        original_name = os.path.basename(file_path)
        new_base = self.standardize_name(original_name)
        if not new_base:
            return {"path": file_path, "status": "skipped", "reason": "insufficient_filename_signal"}

        ext = os.path.splitext(file_path)[1]
        root = os.path.dirname(file_path)
        new_path = generate_safe_filename(root, new_base, ext, max_length=170, reserved=reserved)
        if os.path.normcase(new_path) == os.path.normcase(file_path):
            return {"path": file_path, "status": "skipped", "reason": "same_name"}

        result = {
            "path": file_path,
            "status": "renamed" if not dry_run else "would_rename",
            "old_name": original_name,
            "new_name": os.path.basename(new_path),
        }
        if not dry_run:
            safe_rename(file_path, new_path)
            result["new_path"] = new_path
        return result

    def rename_target(self, target_path, dry_run=False):
        results = []
        if os.path.isfile(target_path):
            return [self.rename_file(target_path, dry_run=dry_run)]

        reserved = set()
        for root, dirs, files in os.walk(target_path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for file_name in files:
                if file_name.startswith("."):
                    continue
                file_path = os.path.join(root, file_name)
                results.append(self.rename_file(file_path, dry_run=dry_run, reserved=reserved))
        return results


def main():
    parser = argparse.ArgumentParser(description="Fast filename-only standardization first pass.")
    parser.add_argument("target_path", type=str, help="Path to a file, folder, or drive root.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without renaming.")
    args = parser.parse_args()

    if not os.path.exists(args.target_path):
        print(f"Error: Path does not exist: {args.target_path}")
        raise SystemExit(1)

    standardizer = FilenameStandardizer()
    results = standardizer.rename_target(args.target_path, dry_run=args.dry_run)

    renamed = 0
    skipped = 0
    for result in results:
        if result["status"] in {"renamed", "would_rename"}:
            renamed += 1
            print(f'{result["status"].upper()}: {result["old_name"]} -> {result["new_name"]}')
        else:
            skipped += 1
    print(f"Completed. renamed={renamed} skipped={skipped}")


if __name__ == "__main__":
    main()
