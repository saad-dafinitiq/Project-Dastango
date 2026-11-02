"""DST-24: password rules at the OWASP ASVS 4.0.3 level (section 2.1).

Before this the minimum was 10, 129 and 1,024 characters were accepted, and
`1234567890`, `password12` and `iloveyou123` all passed (DST-21 audit, probe P5).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select

from auth import accounts, passwords
from shared import db
# The account helpers and the app fixture the M5 tests use.
from tests.unit.test_m5_auth import (GOOD_PASSWORD, OTHER_PASSWORD, client,  # noqa: F401
                                     register, sign_in, sign_out)

ROOT = Path(__file__).resolve().parents[2]
MIN, MAX = passwords.MIN_PASSWORD_LENGTH, passwords.MAX_PASSWORD_LENGTH

# Taken from the bundled list: each is a real, very common password that is long
# enough to pass the length rule, which is the case the list is for.
LISTED = ["password1234", "iloveyou1234", "q1w2e3r4t5y6", "1qaz2wsx3edc"]


@pytest.fixture(autouse=True)
def isolated(isolated_dirs, monkeypatch):
    """A throwaway database for every test, and no sign-up limit in the way of the
    tests that make many accounts (DST-23's limit is tested in its own file)."""
    monkeypatch.setenv("SIGNUP_IP_MAX_ATTEMPTS", "1000")


def refused(password: str) -> str:
    """The message for a password that check_strength refuses; fails if it accepts."""
    with pytest.raises(passwords.WeakPassword) as e:
        passwords.check_strength(password)
    return str(e.value)


# ---- what is accepted --------------------------------------------------------------

@pytest.mark.parametrize("password", [
    "tidal-meadow",                                   # exactly 12: the minimum
    ("tangerine-sparrow-" * 4)[:64],                  # 64 characters: must be allowed
    ("amber lighthouse seven " * 6)[:128],            # exactly 128: the maximum
    "qzxjwvkbnmpl",                                   # one case, no digit, no symbol
    "482915037261",                                   # digits only
    "correct horse battery staple",                   # a passphrase with spaces
    "пароль для сайта ёлка",                          # Unicode
    "Ünïcödé pässwörd 2026",
    "🌲" * 12,                                        # 12 characters, however many bytes
])
def test_these_passwords_are_accepted(password):
    passwords.check_strength(password)


def test_there_are_no_composition_rules():
    """Length decides; nothing demands a capital, a digit or a symbol."""
    for plain in ("qzxjwvkbnmpl", "QZXJWVKBNMPL", "482915037261", "tidal meadow x"):
        passwords.check_strength(plain)


# ---- what is refused, and what it says ----------------------------------------------

@pytest.mark.parametrize("password,fragment", [
    ("tidal-meado", f"at least {MIN} characters"),                    # 11
    ("1234567890", f"at least {MIN} characters"),                     # 10
    ("", "only whitespace"),
    (" " * 20, "only whitespace"),
    ("a   b   c   d", f"at least {MIN} characters"),                  # 13 as typed, 7 with spaces counted once
    ("x" + " " * 30, f"at least {MIN} characters"),                   # 31 as typed, 2 once collapsed
    ("a" * (MAX + 1), f"at most {MAX} characters"),                   # 129
    ("a" * 1024, f"at most {MAX} characters"),
    ("password1234", "data breaches"),
    ("PASSWORD1234", "data breaches"),                                # case does not matter
    ("ｐａｓｓｗｏｒｄ１２３４", "data breaches"),                   # full-width form of it
    ("iloveyou1234", "data breaches"),
])
def test_these_passwords_are_refused_with_a_message_that_says_what_to_fix(password, fragment):
    message = refused(password)
    assert fragment in message
    assert password.strip() == "" or password not in message, "the message must not echo the password"


def test_spaces_in_a_row_count_once_so_twelve_spaces_are_not_a_password():
    assert passwords.effective_length("a" + " " * 11 + "b") == 3
    refused("   ".join(["ab", "cd", "ef", "gh"]))          # 17 as typed, 11 once collapsed
    passwords.check_strength("ab cd ef gh ij kl")            # 17 with single spaces


def test_every_listed_example_really_is_on_the_bundled_list():
    """The tests above lean on these four, so say so loudly if the list changes."""
    for entry in LISTED:
        assert passwords.is_common(entry), entry


# ---- where the rules apply ----------------------------------------------------------

def test_sign_up_refuses_a_weak_password_and_creates_no_account(client):
    for password, fragment in (("1234567890", f"at least {MIN}"), ("password1234", "breaches"),
                               ("a" * 129, f"at most {MAX}")):
        r = register(client, "saad@example.com", password)
        assert r.status_code == 400, password[:12]
        assert fragment in r.json()["detail"]
    assert accounts.count() == 0
    assert register(client, "saad@example.com", "tidal-meadow").status_code == 200   # exactly 12 is fine


def test_changing_a_password_applies_the_same_rules(client):
    register(client, "saad@example.com")

    def change(new, current=GOOD_PASSWORD):
        return client.post("/api/auth/password", json={"current_password": current,
                                                       "new_password": new})
    assert change("short-one").status_code == 400
    assert "at least 12" in change("tidal-meado").json()["detail"]
    assert "data breaches" in change("password1234").json()["detail"]
    assert "at most 128" in change("a" * 129).json()["detail"]
    assert change("tidal-meadow", current="not-my-password-xyz").status_code == 403   # current is still checked first
    assert change("tidal-meadow").status_code == 200
    sign_out(client)
    assert sign_in(client, "saad@example.com", "tidal-meadow").status_code == 200


def test_an_admin_creating_an_account_is_held_to_the_rules_too(client):
    register(client, "admin01@example.com")
    weak = client.post("/api/auth/users", json={"email": "user01@example.com", "password": "iloveyou1234"})
    assert weak.status_code == 400 and "data breaches" in weak.json()["detail"]
    ok = client.post("/api/auth/users", json={"email": "user01@example.com", "password": OTHER_PASSWORD})
    assert ok.status_code == 200


def test_the_command_line_tools_use_the_same_rules():
    """`main.py users create` and `passwd` go through these two functions."""
    with pytest.raises(accounts.AuthError, match="at least 12"):
        accounts.create("user01@example.com", "short-one")
    user = accounts.create("user01@example.com", OTHER_PASSWORD)
    with pytest.raises(accounts.AuthError, match="data breaches"):
        accounts.set_password(user.id, "password1234")


def test_the_sign_in_screen_is_told_the_new_minimum(client):
    assert client.get("/api/auth/status").json()["min_password_length"] == 12


def test_no_part_of_a_long_password_is_dropped(client):
    """A hasher that silently stopped at 72 bytes would accept any ending."""
    right = "correct horse battery staple " * 3 + "one"          # 92 characters
    register(client, "saad@example.com", right)
    sign_out(client)
    wrong_tail = right[:-1] + "X"
    wrong_middle = right[:80] + "X" + right[81:]                  # differs after character 72
    assert sign_in(client, "saad@example.com", wrong_tail).status_code == 401
    assert sign_in(client, "saad@example.com", wrong_middle).status_code == 401
    assert sign_in(client, "saad@example.com", right).status_code == 200


# ---- people who already have an account ---------------------------------------------

def make_legacy(email, password, hasher=None):
    """An account as it could have been made under the old rules (no policy check)."""
    digest = (hasher or PasswordHasher()).hash(password)
    return accounts._insert(email, digest)


@pytest.mark.parametrize("password", ["short-one1", "password1234", "x" * 200, "y" * 1000])
def test_an_account_made_under_the_old_rules_can_still_sign_in(client, password):
    make_legacy("old@example.com", password)
    assert sign_in(client, "old@example.com", password).status_code == 200


def test_signing_in_upgrades_an_old_hash_without_applying_the_new_rules(client):
    """The trap: the upgrade hashes the password again. If that applied the rules, a
    10-character password would fail to sign in whenever argon2's settings moved on."""
    cheap = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    make_legacy("old@example.com", "short-one1", hasher=cheap)
    before = _stored_hash("old@example.com")
    assert passwords.needs_rehash(before)

    assert sign_in(client, "old@example.com", "short-one1").status_code == 200
    after = _stored_hash("old@example.com")
    assert after != before and not passwords.needs_rehash(after)
    sign_out(client)
    assert sign_in(client, "old@example.com", "short-one1").status_code == 200


def test_the_new_rules_apply_when_that_person_next_changes_their_password(client):
    make_legacy("old@example.com", "short-one1")
    sign_in(client, "old@example.com", "short-one1")

    def change(new):
        return client.post("/api/auth/password", json={"current_password": "short-one1",
                                                       "new_password": new})
    assert change("short-two1").status_code == 400
    assert change("iloveyou1234").status_code == 400
    assert change("a-much-better-phrase").status_code == 200
    sign_out(client)
    assert sign_in(client, "old@example.com", "short-one1").status_code == 401
    assert sign_in(client, "old@example.com", "a-much-better-phrase").status_code == 200


def test_a_password_over_the_old_limit_is_still_refused_at_sign_in(client):
    """The cap that remains only keeps silly payloads out of the hasher."""
    assert sign_in(client, "old@example.com", "z" * 1025).status_code == 422


def _stored_hash(email):
    with db.get_engine().connect() as c:
        return c.execute(select(db.users.c.password_hash).where(db.users.c.email == email)).scalar()


# ---- the bundled list ---------------------------------------------------------------

@pytest.fixture
def fresh_list_cache():
    passwords._common_passwords.cache_clear()
    yield
    passwords._common_passwords.cache_clear()


def test_the_bundled_list_is_what_its_record_says_it_is():
    entries = passwords.COMMON_PASSWORDS_FILE.read_text(encoding="utf-8").splitlines()
    meta = json.loads(passwords.COMMON_PASSWORDS_FILE.with_name("common_passwords.meta.json")
                      .read_text(encoding="utf-8"))
    assert len(entries) == len(set(entries)) == meta["output"]["entries"] >= 20000
    assert all(passwords.normalise(e) == e for e in entries), "entries are stored normalised"
    assert all(MIN <= passwords.effective_length(e) <= MAX for e in entries), \
        "only passwords that could otherwise pass are kept"
    # Hashed line by line, so a Windows checkout that adds carriage returns still matches.
    assert hashlib.sha256(("\n".join(entries) + "\n").encode("utf-8")).hexdigest() \
        == meta["output"]["sha256_of_lines"]
    source = meta["source"]
    assert re.fullmatch(r"[0-9a-f]{64}", source["sha256"]) and re.fullmatch(r"[0-9a-f]{40}", source["commit"])
    assert source["commit"] in source["url"] and "MIT" in source["licence"]


def test_a_missing_list_stops_sign_ups_instead_of_letting_everything_through(
        tmp_path, monkeypatch, fresh_list_cache):
    monkeypatch.setattr(passwords, "COMMON_PASSWORDS_FILE", tmp_path / "nope.txt")
    with pytest.raises(RuntimeError, match="cannot be read"):
        passwords.check_strength("tidal-meadow")


def test_a_list_cut_short_is_an_error_too(tmp_path, monkeypatch, fresh_list_cache):
    short = tmp_path / "short.txt"
    short.write_text("password1234\niloveyou1234\n", encoding="utf-8")
    monkeypatch.setattr(passwords, "COMMON_PASSWORDS_FILE", short)
    with pytest.raises(RuntimeError, match="truncated"):
        passwords.check_strength("tidal-meadow")


def test_the_build_script_keeps_only_what_could_pass_and_normalises_it(tmp_path):
    spec = importlib.util.spec_from_file_location("build_common_passwords",
                                                  ROOT / "scripts" / "build_common_passwords.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    source = tmp_path / "source.txt"
    source.write_bytes("\n".join([
        "123456",                 # too short
        "Password1234",           # kept, folded to lower case
        "password1234",           # duplicate of the one above once folded
        "ｉｌｏｖｅｙｏｕ１２３４",    # full-width, folded to ascii
        "a" * 129,                # too long
        "x" + " " * 20,           # long as typed, short once its spaces collapse
        "tidal   meadow ok",      # kept: 17 characters once collapsed
        "",
    ]).encode("utf-8") + b"\n\xff\xfe broken line\n")          # and one that is not text
    assert build.build(source) == ["password1234", "iloveyou1234", "tidal   meadow ok"]


def test_even_a_very_long_password_gets_the_message_not_a_bare_422(client):
    """Up to a few thousand characters the answer is the readable 400. Nothing that
    long is ever hashed: it is refused first."""
    for length in (1025, 2000):
        r = register(client, "saad@example.com", "a" * length)
        assert r.status_code == 400 and f"at most {MAX} characters" in r.json()["detail"]
    register(client, "saad@example.com")
    r = client.post("/api/auth/password", json={"current_password": GOOD_PASSWORD,
                                                "new_password": "b" * 3000})
    assert r.status_code == 400 and f"at most {MAX} characters" in r.json()["detail"]


# ---- the check at sign-in (requirement 2.1.7): a notice, never a refusal -------------

@pytest.mark.parametrize("password,code", [("short-one1", "too_short"),
                                           ("password1234", "breached"),
                                           ("iloveyou1234", "breached")])
def test_signing_in_with_a_password_the_rules_would_refuse_works_and_says_so(client, password, code):
    make_legacy("old@example.com", password)
    r = sign_in(client, "old@example.com", password)
    assert r.status_code == 200
    notice = r.json()["password_notice"]
    assert notice["code"] == code and "change it" in notice["message"]
    assert password not in r.text, "the notice must not repeat the password"
    assert client.get("/api/auth/me").status_code == 200           # and the session is a normal one


@pytest.mark.parametrize("password", ["a-decent-passphrase", "x" * 200, "y" * 1000])
def test_a_good_password_or_merely_long_one_gets_no_notice(client, password):
    make_legacy("old@example.com", password)
    r = sign_in(client, "old@example.com", password)
    assert r.status_code == 200 and r.json()["password_notice"] is None


def test_the_notice_goes_away_once_the_password_is_changed(client):
    make_legacy("old@example.com", "short-one1")
    assert sign_in(client, "old@example.com", "short-one1").json()["password_notice"]["code"] == "too_short"
    client.post("/api/auth/password", json={"current_password": "short-one1",
                                            "new_password": "a-much-better-phrase"})
    sign_out(client)
    assert sign_in(client, "old@example.com", "a-much-better-phrase").json()["password_notice"] is None


def test_nothing_is_checked_for_a_failed_sign_in(client, monkeypatch):
    """The notice is computed only after the right password. A failure must not reveal
    anything about the password it was given, or about whether the account exists."""
    make_legacy("old@example.com", "password1234")

    def must_not_run(_password):
        raise AssertionError("sign_in_notice ran for a failed sign-in")

    monkeypatch.setattr(passwords, "sign_in_notice", must_not_run)
    assert sign_in(client, "old@example.com", "password1235").status_code == 401   # a listed-looking wrong one
    assert sign_in(client, "nobody@example.com", "password1234").status_code == 401


def test_the_log_says_which_account_but_never_the_password(client, caplog):
    import logging
    make_legacy("old@example.com", "password1234")
    with caplog.at_level(logging.INFO):
        sign_in(client, "old@example.com", "password1234")
    lines = [r.getMessage() for r in caplog.records if "signed in with a password" in r.getMessage()]
    assert len(lines) == 1 and "breached" in lines[0] and "password1234" not in lines[0]
    assert "password1234" not in caplog.text
