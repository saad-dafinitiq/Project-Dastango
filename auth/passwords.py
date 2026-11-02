"""Password rules and hashing.

Hashing is Argon2id, with argon2-cffi's defaults: OWASP's first recommendation,
and the library tracks the parameters so this file doesn't have to. Nothing here
is hand-rolled: hashing a password yourself is how passwords get lost.

The rules for a *new* password follow OWASP ASVS 4.0.3 section 2.1 (DST-24):
12 to 128 characters, no composition rules, nothing truncated, and nothing on a
list of passwords exposed in breaches. The list is a file in this package, so no
password is ever sent anywhere to be checked.
"""
from __future__ import annotations

import functools
import re
import unicodedata
from pathlib import Path
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Long enough that guessing is hard, short enough to be typed. Length beats
# composition rules, so there are no composition rules. A run of spaces counts
# as one character when the minimum is checked, so twelve spaces are not a
# password.
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128    # for a password being chosen; argon2 would take far more

# Signing in, and confirming the current password, still accept what accounts
# made before the rules tightened may have: up to the old limit. Refusing it
# here would lock those people out.
MAX_SIGNIN_LENGTH = 1024

# Built by scripts/build_common_passwords.py from a public breach list; where it
# came from, and its hash, are in common_passwords.meta.json.
COMMON_PASSWORDS_FILE = Path(__file__).with_name("common_passwords.txt")
# A file much smaller than this was cut short, and checking against it would
# quietly let weak passwords through.
_MIN_LIST_ENTRIES = 1000

_hasher = PasswordHasher()


class WeakPassword(ValueError):
    """The password doesn't meet the rules."""


def effective_length(password: str) -> int:
    """Characters in the password, with each run of whitespace counted as one."""
    return len(re.sub(r"\s+", " ", password))


def normalise(password: str) -> str:
    """The form a password is compared in: compatibility-folded and case-folded,
    so "PASSWORD123456" and a full-width spelling of it match the list too."""
    return unicodedata.normalize("NFKC", password).casefold()


@functools.lru_cache(maxsize=1)
def _common_passwords() -> frozenset:
    """The bundled list, read once. A missing or cut-short file is an error, not an
    empty list: an empty one would pass every password."""
    try:
        text = COMMON_PASSWORDS_FILE.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"password list {COMMON_PASSWORDS_FILE.name} cannot be read: {e}") from e
    entries = frozenset(line for line in text.splitlines() if line)
    if len(entries) < _MIN_LIST_ENTRIES:
        raise RuntimeError(f"password list {COMMON_PASSWORDS_FILE.name} has only "
                           f"{len(entries)} entries; it looks truncated")
    return entries


def is_common(password: str) -> bool:
    """True when the password, as typed or with its spaces collapsed, is on the list."""
    common = _common_passwords()
    collapsed = re.sub(r"\s+", " ", password)
    return normalise(password) in common or normalise(collapsed) in common


def check_strength(password: str) -> None:
    """Raise WeakPassword, with a message a person can act on, if a password someone
    is *choosing* is not acceptable. Never put the password in the message."""
    if not password.strip():
        raise WeakPassword("password cannot be only whitespace")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise WeakPassword(
            f"password is too long: use at most {MAX_PASSWORD_LENGTH} characters")
    if effective_length(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(
            f"password must be at least {MIN_PASSWORD_LENGTH} characters "
            "(spaces in a row count as one)")
    if is_common(password):
        raise WeakPassword(
            "that password has appeared in data breaches or is very common; "
            "choose another, for example a few unrelated words")


def sign_in_notice(password: str) -> Optional[dict]:
    """After a *successful* sign-in: say so if this password would not be accepted
    as a new one because it is too short or on the breach list, else None.

    The checking side of requirement 2.1.7. It only warns: an account made under
    the older rules must keep working, and its owner meets the new rules at the
    next password change. A password over 128 characters is not flagged, since
    length is not weakness. It runs only after the right password was given, so it
    reveals nothing about anyone else's account, and the password is never logged.
    """
    if effective_length(password) < MIN_PASSWORD_LENGTH:
        return {"code": "too_short",
                "message": f"your password is shorter than the {MIN_PASSWORD_LENGTH} "
                           "characters now required; please change it"}
    if is_common(password):
        return {"code": "breached",
                "message": "your password has appeared in data breaches or is very "
                           "common; please change it"}
    return None


def hash_password(password: str) -> str:
    """Hash a password. Does not apply the rules above, on purpose.

    Signing in upgrades an old hash by hashing the password again, and an account
    made under the old rules (10 characters, or 1,000) must keep working. Whoever
    lets a person choose a password calls `check_strength` first (accounts.create
    and accounts.set_password do).
    """
    return _hasher.hash(password)


def verify(password_hash: str, password: str) -> bool:
    """Constant-time check. False for a wrong password or a corrupt hash."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash predates the current cost parameters."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def dummy_verify() -> None:
    """Burn the same time as a real check when the account doesn't exist.

    Without it, "no such user" returns measurably faster than "wrong password"
    and the login endpoint tells an attacker which addresses are registered.
    """
    try:
        _hasher.verify(_DUMMY_HASH, "not-the-password")
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        pass


# Hashed once at import so every failed login pays the same cost.
_DUMMY_HASH = _hasher.hash("a password that is never anyone's password")
