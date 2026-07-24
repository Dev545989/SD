import re


def clean_text(value) -> str:
    """Normalize any value to a clean display string: collapses all unicode
    whitespace (regular spaces, \\xa0 non-breaking spaces, tabs, etc.) into a
    single space and strips the ends. Falls back to 'Unknown' for empty/None."""
    if value is None:
        return "Unknown"
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or "Unknown"


def sanitize_filename(value) -> str:
    """clean_text() plus stripping characters that aren't safe in a filename/R2 key."""
    text = clean_text(value)
    text = re.sub(r'[\\/:*?"<>|]', "-", text)
    return text or "Unknown"