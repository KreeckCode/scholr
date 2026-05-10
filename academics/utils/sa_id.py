"""
South African Identity Number validation.

Format: YYMMDDGSSSCAZ  (13 digits, no spaces)
    YY   - Year of birth
    MM   - Month of birth (01-12)
    DD   - Day of birth (01-31)
    G    - Gender (0-4 = female, 5-9 = male)
    SSS  - Sequence number (000-999)
    C    - Citizenship (0 = SA citizen, 1 = permanent resident)
    A    - Legacy race field, deprecated - typically 8
    Z    - Luhn check digit
"""

from datetime import date


def _luhn_valid(id_number: str) -> bool:
    """
    Standard Luhn algorithm applied to the full 13-digit number.
    The check digit (last) makes the total divisible by 10.
    """
    digits = [int(d) for d in id_number]
    total = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2 == 1:
            doubled = digit * 2
            total += (doubled - 9) if doubled > 9 else doubled
        else:
            total += digit
    return total % 10 == 0


def _extract_dob(id_number: str) -> date:
    """
    Extract and return a date object from the first 6 digits.
    Raises ValueError if the date is invalid.
    """
    yy = int(id_number[0:2])
    mm = int(id_number[2:4])
    dd = int(id_number[4:6])

    # Century: if yy <= current 2-digit year, it's 2000s; otherwise 1900s.
    # A learner born in 2010 has yy=10; the current year suffix e.g. 25 > 10 so: 2010.
    current_suffix = date.today().year % 100
    full_year = 2000 + yy if yy <= current_suffix else 1900 + yy

    return date(full_year, mm, dd)  # raises ValueError on impossible dates


def validate_sa_id(id_number: str) -> tuple:
    """
    Validate a South African ID number end-to-end.

    Returns:
        (True, dict) on success  - dict contains date_of_birth, gender, citizenship
        (False, str) on failure  - str is a human-readable error message
    """
    if not id_number:
        return False, "ID number is required."

    cleaned = id_number.strip().replace(" ", "")

    if len(cleaned) != 13:
        return False, f"SA ID must be exactly 13 digits. Got {len(cleaned)}."

    if not cleaned.isdigit():
        return False, "SA ID must contain digits only."

    try:
        dob = _extract_dob(cleaned)
    except ValueError:
        return False, (
            f"The date encoded in this ID ({cleaned[0:6]}) is not a valid calendar date."
        )

    if not _luhn_valid(cleaned):
        return False, "ID number checksum is invalid. Check for a typing error."

    gender_digit = int(cleaned[6])
    citizenship_digit = int(cleaned[10])

    return True, {
        "date_of_birth": dob,
        "gender": "M" if gender_digit >= 5 else "F",
        "citizenship": "citizen" if citizenship_digit == 0 else "resident",
    }