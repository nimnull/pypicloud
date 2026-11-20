"""Tests for password hashing"""

import unittest

from pypicloud.access.base import IAccessBackend
from pypicloud.hashing import get_pwd_context, is_legacy_hash

# sha512_crypt hash of 'asdf', as written by pypicloud 1.x
LEGACY_HASH = (
    "$6$rounds=1000$Q5rf4IcKLTpMw.dN$xl/4AxlYZkSx9w78BWXBA1BwbexhvN5EN800Rh47"
    "HllK1zEamYNpjIYC4rHDImKYJEBvp72qoD0wkYYCR7Cvy."
)


class StoredHashBackend(IAccessBackend):
    """Access backend that serves one stored password hash"""

    def __init__(self, password_hash):
        super().__init__(pwd_context=get_pwd_context())
        self.password_hash = password_hash

    def _get_password_hash(self, username):
        return self.password_hash


class TestHashing(unittest.TestCase):
    """Tests for the password hasher"""

    def test_default_scheme(self):
        """Passwords hash with argon2id by default"""
        self.assertTrue(get_pwd_context().hash("secret").startswith("$argon2id$"))

    def test_bcrypt_scheme(self):
        """The bcrypt scheme is available"""
        self.assertTrue(get_pwd_context("bcrypt").hash("secret").startswith("$2b$"))

    def test_unknown_scheme_falls_back(self):
        """A scheme this version doesn't know falls back to argon2id"""
        context = get_pwd_context("sha512_crypt")
        self.assertTrue(context.hash("secret").startswith("$argon2id$"))

    def test_unknown_scheme_warns(self):
        """A scheme this version doesn't know is named in the log"""
        with self.assertLogs("pypicloud.hashing", level="WARNING") as logs:
            get_pwd_context("sha512_crypt")
        self.assertIn("sha512_crypt", logs.output[0])

    def test_known_scheme_is_quiet(self):
        """A supported scheme logs nothing"""
        with self.assertNoLogs("pypicloud.hashing", level="WARNING"):
            get_pwd_context("bcrypt")
            get_pwd_context("argon2")
            get_pwd_context()

    def test_verifies_both_schemes(self):
        """Either scheme verifies a hash written by the other"""
        argon2, bcrypt = get_pwd_context(), get_pwd_context("bcrypt")
        self.assertTrue(argon2.verify("secret", bcrypt.hash("secret")))
        self.assertTrue(bcrypt.verify("secret", argon2.hash("secret")))

    def test_rejects_wrong_password(self):
        """A wrong password does not verify"""
        context = get_pwd_context()
        self.assertFalse(context.verify("nope", context.hash("secret")))

    def test_rounds(self):
        """The rounds setting reaches bcrypt's cost factor"""
        self.assertTrue(
            get_pwd_context("bcrypt", 5).hash("secret").startswith("$2b$05")
        )

    def test_detects_legacy_hashes(self):
        """Hashes written by pypicloud 1.x are recognized"""
        self.assertTrue(is_legacy_hash(LEGACY_HASH))
        self.assertTrue(is_legacy_hash("$5$rounds=1000$abc$def"))
        self.assertTrue(is_legacy_hash("$pbkdf2-sha256$1000$abc$def"))
        self.assertFalse(is_legacy_hash(get_pwd_context().hash("secret")))
        self.assertFalse(is_legacy_hash(get_pwd_context("bcrypt").hash("secret")))


class TestVerifyUser(unittest.TestCase):
    """Tests for verifying a stored password hash"""

    def test_verifies_current_hash(self):
        """A user with a current hash can log in"""
        backend = StoredHashBackend(get_pwd_context().hash("asdf"))
        self.assertTrue(backend.verify_user("user", "asdf"))
        self.assertFalse(backend.verify_user("user", "wrong"))

    def test_rejects_legacy_hash(self):
        """A user with a 1.x hash is refused instead of erroring"""
        backend = StoredHashBackend(LEGACY_HASH)
        with self.assertLogs("pypicloud.access.base", level="WARNING") as logs:
            self.assertFalse(backend.verify_user("user", "asdf"))
        self.assertIn("no longer supports", logs.output[0])

    def test_rejects_unreadable_hash(self):
        """An unreadable hash is refused instead of erroring"""
        backend = StoredHashBackend("not-a-hash")
        with self.assertLogs("pypicloud.access.base", level="WARNING"):
            self.assertFalse(backend.verify_user("user", "asdf"))

    def test_rejects_missing_hash(self):
        """A user with no stored hash is refused"""
        self.assertFalse(StoredHashBackend("").verify_user("user", "asdf"))
