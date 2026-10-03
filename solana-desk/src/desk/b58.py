"""Base58 (Bitcoin alphabet): enough to validate Solana addresses and build fixture ones."""

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {char: value for value, char in enumerate(ALPHABET)}


def b58encode(data: bytes) -> str:
    number = int.from_bytes(data, "big")
    digits = []
    while number:
        number, remainder = divmod(number, 58)
        digits.append(ALPHABET[remainder])
    leading_zeros = len(data) - len(data.lstrip(b"\0"))
    return "1" * leading_zeros + "".join(reversed(digits))


def b58decode(text: str) -> bytes:
    number = 0
    for char in text:
        number = number * 58 + _INDEX[char]
    leading_ones = len(text) - len(text.lstrip("1"))
    return b"\0" * leading_ones + number.to_bytes((number.bit_length() + 7) // 8, "big")


def is_address(value: object) -> bool:
    """True for a base58 string that decodes to the 32 bytes of a Solana public key."""
    if not isinstance(value, str) or not 32 <= len(value) <= 44:
        return False
    try:
        return len(b58decode(value)) == 32
    except KeyError:
        return False
