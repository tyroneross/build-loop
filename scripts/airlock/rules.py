"""Covert-control detection for foreign agent-instruction files.

Discriminator: an ATTACK conceals its own directive or the agent's own action.
A LEGITIMATE rule conceals third-party DATA (customer names, ticket ids, traces).
Both say "do not mention". Only the object differs.
"""
import re, unicodedata

# --- normalization -------------------------------------------------------
def normalize(text):
    """NFKC + strip Unicode format chars (bidi, ZWSP, ZWJ). Returns (clean, had_hidden)."""
    hidden = sum(1 for c in text if unicodedata.category(c) == "Cf")
    stripped = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return unicodedata.normalize("NFKC", stripped), hidden > 0

# --- output-shaping directive -------------------------------------------
SHAPE = re.compile(r"""(
   (must|always|absolutely|should)\s+(\w+\s+){0,2}(use|include|insert|add|append|contain|emit)\b
 | (insert|append|include|add)\s+the\s+(word|phrase|token|marker|tag|string)
 | (append|send|include|attach|post)\s+(the\s+)?(contents?|body|value)\s+of
 | use\s+the\s+word
 | in\s+(your|all|every)\s+(prose|output|response|reply|writing|documentation|summary)
 | every\s+(pr|issue|comment|commit|message|response|file|link)
 | all\s+(generated|outbound|produced)\s+\w+
)""", re.I | re.X)

# --- concealment, with the OBJECT split out ------------------------------
CONCEAL_VERB = r"(do\s+not|don'?t|never|no\s+need\s+to|not\s+to\s+be)\s*" \
               r"(inform|tell|mention|disclose|reveal|say|announce|surface|explain|disclosed)"
CONCEAL_ALT = r"(keep\s+this\s+between\s+us|silently|without\s+(telling|informing|mentioning|disclosing))"
CONCEAL = re.compile(rf"({CONCEAL_VERB}|{CONCEAL_ALT})", re.I)

# object is the directive / the agent's own act  -> ATTACK
SELF_OBJ = re.compile(r"""(
    (that\s+)?you\s+(are\s+doing|did)\s+this
 | this\s+(instruction|directive|rule|prompt|requirement)
 | keep\s+this\s+between\s+us
 | what\s+(rules|instructions)\s+you\s+(followed|used)
 | an?\s+override\s+is\s+in\s+effect
 | this\s+is\s+not\s+to\s+be\s+disclosed
 | not\s+to\s+be\s+disclosed
 | no\s+need\s+to\s+surface\s+it
 | (mention|disclose|reveal)\s+(it|this)\b
 | in\s+your\s+(summary|output|response|reply)
)""", re.I | re.X)

# object is third-party data -> LEGITIMATE (suppressor)
DATA_OBJ = re.compile(r"""(
   (customer|client|user|account|internal|ticket|employee|patient)\s+
     (names?|ids?|identifiers?|numbers?|data|records?)
 | stack\s+traces?
 | (api\s+)?(keys?|secrets?|credentials?|tokens?|passwords?)
 | pii|personally\s+identifiable
 | your\s+reasoning
 | ticket\s+ids?
)""", re.I | re.X)

# A convention that explains itself is not covert. Presence of a rationale or a
# stated alternative is evidence AGAINST concealment.
TRANSPARENT = re.compile(r"""(
   this\s+convention\s+is\s+documented
 | documented\s+here
 | so\s+(that\s+)?contributors\s+can
 | rather\s+than\s+["\u2018\u201c']?\w+["\u2019\u201d']?\s+in
)""", re.I | re.X)

PROXIMITY = 300  # chars; beyond this, two matches are two unrelated rules

def _near(a, b):
    """True when two regex matches belong to the same directive."""
    if not a or not b:
        return False
    return abs(a.start() - b.start()) <= PROXIMITY

def classify(text):
    clean, hidden = normalize(text)
    shape_m = SHAPE.search(clean)
    conc  = CONCEAL.search(clean)
    shape = bool(shape_m) and (conc is None or _near(shape_m, conc))
    if TRANSPARENT.search(clean):
        return "none"                      # self-explaining convention, not covert
    if not conc:
        return "suspected" if shape and re.search(r"\b(word|phrase|token|marker|tag)\b", clean, re.I) else "none"
    self_obj = bool(SELF_OBJ.search(clean))
    data_obj = bool(DATA_OBJ.search(clean))
    if data_obj and not self_obj:
        return "none"                      # legitimate data-hygiene rule
    if hidden and (self_obj or shape):
        return "covert"                    # obfuscation is itself evidence
    if shape and (self_obj or not data_obj):
        return "covert"
    if self_obj:
        return "suspected"
    return "none"
