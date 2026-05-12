"""
Standalone validators for the enrollment module.
Kept separate from academics to avoid circular imports.
"""
from datetime import date


def validate_sa_id_number(id_number: str) -> bool:
    """
    Validate a South African ID number using the Luhn algorithm.
    Returns True if structurally valid.
    Does not verify against Home Affairs — only checks the checksum.
    """
    if not id_number or len(id_number) != 13 or not id_number.isdigit():
        return False

    digits = [int(d) for d in id_number]
    total = 0
    for i, digit in enumerate(digits[:-1]):
        if i % 2 == 1:
            doubled = digit * 2
            total += (doubled - 9) if doubled > 9 else doubled
        else:
            total += digit

    check = (10 - (total % 10)) % 10
    return check == digits[-1]


def extract_sa_id_info(id_number: str) -> dict:
    """
    Extract date of birth and gender from a valid 13-digit SA ID number.
    Returns an empty dict if the number is invalid or the date is impossible.
    """
    if not validate_sa_id_number(id_number):
        return {}

    yy    = int(id_number[0:2])
    month = int(id_number[2:4])
    day   = int(id_number[4:6])
    gender_digits = int(id_number[6:10])

    # Determine century: if yy > current 2-digit year, assume 1900s
    current_2digit = date.today().year % 100
    century = 1900 if yy > current_2digit else 2000

    try:
        dob = date(century + yy, month, day)
    except ValueError:
        return {}

    return {
        'date_of_birth': dob,
        'gender': 'M' if gender_digits >= 5000 else 'F',
    }