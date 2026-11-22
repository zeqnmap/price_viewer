import re
import unicodedata
from unidecode import unidecode

def normalize(value):
    """Apply the same Unicode and transliteration rules to every query and value."""
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return " ".join(re.findall(r"[a-z0-9]+", unidecode(value)))
