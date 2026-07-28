import phonenumbers
from phonenumbers.phonenumberutil import NumberParseException


class InvalidPhoneNumber(ValueError):
    pass


def normalize_phone(value: str, default_region: str = "CA") -> str:
    try:
        parsed = phonenumbers.parse(value, None if value.strip().startswith("+") else default_region)
    except NumberParseException as exc:
        raise InvalidPhoneNumber("Enter a valid phone number") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise InvalidPhoneNumber("Enter a valid phone number")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
