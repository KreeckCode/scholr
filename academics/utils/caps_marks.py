"""
CAPS (Curriculum and Assessment Policy Statement) mark utilities.

Achievement levels and symbols as defined by the DBE.
Used in report card generation - computed in the service layer,
not in templates.
"""

ACHIEVEMENT_LEVELS = [
    (7, 80, 100, "Outstanding Achievement"),
    (6, 70, 79, "Meritorious Achievement"),
    (5, 60, 69, "Substantial Achievement"),
    (4, 50, 59, "Adequate Achievement"),
    (3, 40, 49, "Moderate Achievement"),
    (2, 30, 39, "Elementary Achievement"),
    (1, 0,  29, "Not Achieved"),
]


def get_symbol(percentage) -> int | None:
    """Return the CAPS achievement symbol (1-7) for a percentage."""
    if percentage is None:
        return None
    pct = float(percentage)
    for symbol, lower, upper, _ in ACHIEVEMENT_LEVELS:
        if lower <= pct <= upper:
            return symbol
    return 1


def get_description(symbol: int) -> str:
    """Return the achievement level description for a symbol."""
    for s, _, _, desc in ACHIEVEMENT_LEVELS:
        if s == symbol:
            return desc
    return ""


def get_symbol_and_description(percentage) -> tuple:
    """Return (symbol, description) for a percentage. Both None if percentage is None."""
    symbol = get_symbol(percentage)
    if symbol is None:
        return None, ""
    return symbol, get_description(symbol)