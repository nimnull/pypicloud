"""Password hashing"""

import logging

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher

LOG = logging.getLogger(__name__)

# Hashes written by pypicloud 1.x, which this version cannot verify
LEGACY_PREFIXES = ("$5$", "$6$", "$pbkdf2-sha256$", "$pbkdf2-sha512$", "$1$", "$md5$")
SCHEMES = ["argon2", "bcrypt"]
DEFAULT_SCHEME = "argon2"


def get_pwd_context(scheme: str | None = None, rounds: int | None = None):
    """Build the password hasher.

    ``scheme`` is 'argon2' or 'bcrypt'; any other value, including the scheme names
    pypicloud 1.x accepted, falls back to argon2id. ``rounds`` sets bcrypt's cost
    factor, or argon2's time cost.

    The returned object hashes with the chosen scheme and verifies hashes written by
    either scheme. Hashes from pypicloud 1.x do not verify; those users need a new
    password.
    """
    if scheme == "bcrypt":
        bcrypt = BcryptHasher(rounds=rounds) if rounds else BcryptHasher()
        return PasswordHash((bcrypt, Argon2Hasher()))
    argon2 = Argon2Hasher(time_cost=rounds) if rounds else Argon2Hasher()
    return PasswordHash((argon2, BcryptHasher()))


def is_legacy_hash(password_hash: str) -> bool:
    """True if the hash was written by a pypicloud version before 2.0"""
    return password_hash.startswith(LEGACY_PREFIXES)
