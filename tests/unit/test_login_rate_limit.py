"""DST-23: a limit per address on sign-in and sign-up, and one answer for every failure.

Before this, 40 failed logins for 40 different emails from one address were all
answered in 3 s, and a locked or disabled account answered differently (and five
times faster) than a wrong password (DST-21 audit, probes P3 and P6).
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from auth import accounts, passwords, ratelimit
from shared import db
# The account helpers and the app fixture the M5 tests use.
from tests.unit.test_m5_auth import (GOOD_PASSWORD, OTHER_PASSWORD, client,  # noqa: F401
                                     register, sign_in, sign_out)

ROOT = Path(__file__).resolve().parents[2]
GENERIC = "email or password is incorrect"


@pytest.fixture(autouse=True)
def small_limits(isolated_dirs, monkeypatch):
    """3 failures per 15 minutes, so a test does not need twenty slow password checks.

    Also puts the database in a temp dir for every test here, including the ones that
    call the limiter directly: without it they wrote into the developer's real
    data/state.db."""
    for name in ("LOGIN_IP_MAX_FAILURES", "LOGIN_IP_WINDOW_MINUTES",
                 "SIGNUP_IP_MAX_ATTEMPTS", "SIGNUP_IP_WINDOW_MINUTES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LOGIN_IP_MAX_FAILURES", "3")


class Clock:
    """Time that only moves when a test says so. Patched into the limiter alone:
    patching the global `time` breaks anything else that waits on it."""

    def __init__(self):
        self.t = datetime(2026, 11, 2, 12, 0, 0)

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(ratelimit, "now", c)
    return c


def attempts() -> int:
    with db.get_engine().connect() as c:
        return int(c.execute(select(func.count()).select_from(db.auth_attempts)).scalar())


def keys() -> set:
    with db.get_engine().connect() as c:
        return set(c.execute(select(db.auth_attempts.c.key)).scalars().all())


class HashSpy:
    """Counts the password work done: real checks and the stand-in for no account."""

    def __init__(self, monkeypatch):
        self.verify = self.dummy = 0
        real_verify, real_dummy = passwords.verify, passwords.dummy_verify

        def verify(*a, **kw):
            self.verify += 1
            return real_verify(*a, **kw)

        def dummy(*a, **kw):
            self.dummy += 1
            return real_dummy(*a, **kw)

        monkeypatch.setattr(passwords, "verify", verify)
        monkeypatch.setattr(passwords, "dummy_verify", dummy)

    @property
    def total(self):
        return self.verify + self.dummy


def as_peer(app, host, trusted=None):
    """A client whose connection comes from `host`. With `trusted`, the app sits
    behind uvicorn's proxy handling exactly as in production (FORWARDED_ALLOW_IPS)."""
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=trusted) if trusted else app
    return TestClient(wrapped, client=(host, 50000))


# ---- under the limit, over it, and the reset ---------------------------------------

def test_under_the_limit_everything_works_and_a_success_is_not_counted(client, clock):
    register(client, "saad@example.com")
    sign_out(client)
    assert attempts() == 1                                  # the sign-up

    for _ in range(2):
        assert sign_in(client, "saad@example.com", "wrong-password").status_code == 401
    assert sign_in(client, "saad@example.com").status_code == 200
    sign_out(client)
    # Two failures stay counted, the success took itself back out.
    assert attempts() == 1 + 2


def test_over_the_limit_gets_429_with_retry_after_and_no_password_is_checked(client, clock, monkeypatch):
    register(client, "saad@example.com")
    sign_out(client)
    for _ in range(3):
        assert sign_in(client, "saad@example.com", "wrong-password").status_code == 401

    spy = HashSpy(monkeypatch)
    blocked = sign_in(client, "saad@example.com", GOOD_PASSWORD)    # even the right one
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == str(15 * 60)
    assert "storygen_session" not in blocked.cookies
    assert spy.total == 0, "a refused attempt must not cost a password check"


def test_the_refusal_says_nothing_about_the_account(client, clock):
    register(client, "saad@example.com")
    sign_out(client)
    for _ in range(3):
        sign_in(client, "nobody@example.com", "wrong-password")

    real = sign_in(client, "saad@example.com", GOOD_PASSWORD)
    unknown = sign_in(client, "nobody@example.com", GOOD_PASSWORD)
    assert real.status_code == unknown.status_code == 429
    assert real.json() == unknown.json()
    assert real.headers["Retry-After"] == unknown.headers["Retry-After"]
    assert "saad" not in real.text and "account" not in real.text


def test_retry_after_counts_down_and_the_limit_resets_after_the_window(client, clock):
    register(client, "saad@example.com")
    sign_out(client)
    for _ in range(3):
        sign_in(client, "saad@example.com", "wrong-password")

    clock.advance(minutes=5)
    assert sign_in(client, "saad@example.com").headers["Retry-After"] == str(10 * 60)

    # Hammering while blocked does not push the end of the block further out.
    for _ in range(10):
        assert sign_in(client, "saad@example.com").status_code == 429
    clock.advance(minutes=10, seconds=1)
    assert sign_in(client, "saad@example.com").status_code == 200


def test_signups_are_limited_too_whether_or_not_they_are_open(client, clock, monkeypatch):
    monkeypatch.setenv("SIGNUP_IP_MAX_ATTEMPTS", "2")
    assert register(client, "saad@example.com").status_code == 200          # the first account
    sign_out(client)
    assert register(client, "second@example.com").status_code == 403        # sign-ups closed
    over = register(client, "third@example.com")
    assert over.status_code == 429
    assert over.headers["Retry-After"] == str(60 * 60)
    clock.advance(hours=1, seconds=1)
    assert register(client, "third@example.com").status_code == 403         # counted again, closed


def test_one_address_failing_does_not_lock_out_another(client, clock):
    register(client, "saad@example.com")
    attacker, other = as_peer(client.app, "203.0.113.9"), as_peer(client.app, "198.51.100.4")
    for _ in range(3):
        sign_in(attacker, "saad@example.com", "wrong-password")
    assert sign_in(attacker, "saad@example.com").status_code == 429
    assert sign_in(other, "saad@example.com").status_code == 200


# ---- one answer for every kind of failure ------------------------------------------

def test_wrong_unknown_locked_and_disabled_answer_the_same_after_the_same_work(
        client, clock, monkeypatch):
    monkeypatch.setenv("LOGIN_IP_MAX_FAILURES", "100")     # this test is not about the limit
    register(client, "admin01@example.com")
    monkeypatch.setenv("ALLOW_SIGNUPS", "1")
    for email in ("user01@example.com", "user02@example.com", "user03@example.com"):
        register(client, email, OTHER_PASSWORD)
    sign_out(client)

    for _ in range(accounts.MAX_FAILED_ATTEMPTS):           # user02: locked
        sign_in(client, "user02@example.com", "wrong-password")
    accounts.set_active(accounts.get_by_email("user03@example.com").id, False)   # user03: disabled

    cases = {
        "wrong password": ("user01@example.com", "wrong-password"),
        "unknown account": ("nobody@example.com", "wrong-password"),
        "locked, wrong password": ("user02@example.com", "wrong-password"),
        "locked, right password": ("user02@example.com", OTHER_PASSWORD),
        "disabled, right password": ("user03@example.com", OTHER_PASSWORD),
        "disabled, wrong password": ("user03@example.com", "wrong-password"),
    }
    seen = {}
    for name, (email, password) in cases.items():
        spy = HashSpy(monkeypatch)
        r = sign_in(client, email, password)
        # Same status, same body, and exactly one password hash computed each
        # time (a real check or the stand-in), so the timing cannot tell them apart.
        seen[name] = (r.status_code, r.json()["detail"], "set-cookie" in r.headers, spy.total)
    assert set(seen.values()) == {(401, GENERIC, False, 1)}, seen


def test_a_locked_account_is_still_refused_the_right_password(client, clock, monkeypatch):
    monkeypatch.setenv("LOGIN_IP_MAX_FAILURES", "100")
    register(client, "saad@example.com")
    sign_out(client)
    for _ in range(accounts.MAX_FAILED_ATTEMPTS):
        sign_in(client, "saad@example.com", "wrong-password")
    assert sign_in(client, "saad@example.com").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


# ---- which address a request is counted against ------------------------------------

def test_a_forged_forwarded_header_from_an_untrusted_peer_is_ignored(client, clock):
    register(client, "saad@example.com")
    forger = as_peer(client.app, "203.0.113.9", trusted="10.0.0.0/8")
    for n in range(3):
        sign_in_with = forger.post("/api/auth/login",
                                   json={"email": "saad@example.com", "password": "wrong-password"},
                                   headers={"X-Forwarded-For": f"198.51.100.{n}"})
        assert sign_in_with.status_code == 401
    # Rotating the claimed address did not help: all three count against the real peer.
    assert keys() - {"testclient"} == {"203.0.113.9"}
    blocked = forger.post("/api/auth/login",
                          json={"email": "saad@example.com", "password": GOOD_PASSWORD},
                          headers={"X-Forwarded-For": "198.51.100.77"})
    assert blocked.status_code == 429


def test_the_forwarded_address_is_used_when_it_comes_from_the_trusted_proxy(client, clock):
    register(client, "saad@example.com")
    proxy = as_peer(client.app, "10.0.0.5", trusted="10.0.0.0/8")    # Caddy's place

    def via_proxy(client_address, password):
        return proxy.post("/api/auth/login", json={"email": "saad@example.com", "password": password},
                          headers={"X-Forwarded-For": client_address})

    for _ in range(3):
        via_proxy("203.0.113.9", "wrong-password")
    assert via_proxy("203.0.113.9", GOOD_PASSWORD).status_code == 429
    # Someone else through the same proxy has their own budget.
    assert via_proxy("198.51.100.4", GOOD_PASSWORD).status_code == 200
    assert "10.0.0.5" not in keys()


@pytest.mark.parametrize("host,key", [
    ("203.0.113.9", "203.0.113.9"),
    ("2001:db8:aaaa:bbbb::1", "2001:db8:aaaa:bbbb::/64"),
    ("2001:db8:aaaa:bbbb:ffff:ffff:ffff:ffff", "2001:db8:aaaa:bbbb::/64"),
    ("2001:db8:aaaa:bbbc::1", "2001:db8:aaaa:bbbc::/64"),
    ("::ffff:203.0.113.9", "203.0.113.9"),
    ("fe80::1%eth0", "fe80::/64"),
    ("", "unknown"), (None, "unknown"),
    ("testclient", "testclient"),
])
def test_what_an_address_is_counted_as(host, key):
    assert ratelimit.address_key(host) == key


def test_addresses_in_one_ipv6_range_share_one_budget(clock):
    for n in range(3):
        assert ratelimit.begin(ratelimit.LOGIN, f"2001:db8:1:2::{n + 1}").allowed
    assert not ratelimit.begin(ratelimit.LOGIN, "2001:db8:1:2:9::1").allowed
    assert ratelimit.begin(ratelimit.LOGIN, "2001:db8:1:3::1").allowed      # the next range


def test_production_trusts_forwarded_headers_only_because_the_api_has_no_public_port():
    """FORWARDED_ALLOW_IPS=* is safe only while nothing but Caddy can reach the API.
    Caddy replaces X-Forwarded-For with the real address (measured 2026-11-02, docs/SECURITY.md).
    This pins the two settings together: loosening one without the other fails here."""
    compose = (ROOT / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert 'FORWARDED_ALLOW_IPS: "*"' in compose
    assert 'ports: !reset []' in compose and 'expose: ["8000"]' in compose


# ---- the limit lives in the database -----------------------------------------------

def test_the_limit_survives_a_restart(client, clock):
    register(client, "saad@example.com")
    sign_out(client)
    for _ in range(3):
        sign_in(client, "saad@example.com", "wrong-password")
    db.dispose_all()                 # every engine and connection gone, as after a restart
    assert sign_in(client, "saad@example.com").status_code == 429


def test_a_second_process_shares_the_count(client):
    register(client, "saad@example.com")
    code = (
        "import sys; sys.path.insert(0, sys.argv[1])\n"
        "from auth import ratelimit\n"
        "print([ratelimit.begin(ratelimit.LOGIN, 'testclient').allowed for _ in range(3)])\n")
    env = {**os.environ, "DATABASE_URL": db.default_url(), "PIPELINE_SKIP_DOTENV": "1",
           "LOGIN_IP_MAX_FAILURES": "3"}
    out = subprocess.run([sys.executable, "-c", code, str(ROOT)], env=env, cwd=ROOT,
                         capture_output=True, text=True, timeout=120)
    assert out.stdout.strip() == "[True, True, True]", out.stderr[-500:]
    # Three attempts made elsewhere use up this process's budget too.
    assert sign_in(client, "saad@example.com").status_code == 429


def test_parallel_attempts_cannot_all_slip_under_the_limit(client, clock):
    register(client, "saad@example.com")
    sign_out(client)
    barrier = threading.Barrier(12)

    def attempt(_):
        c = TestClient(client.app)
        barrier.wait()
        return sign_in(c, "saad@example.com", "wrong-password").status_code

    with ThreadPoolExecutor(12) as pool:
        statuses = sorted(pool.map(attempt, range(12)))
    # Exactly the limit got a password check; everyone else was refused.
    assert statuses == [401] * 3 + [429] * 9, statuses


def test_old_attempts_are_swept_away_by_the_next_one(clock):
    with db.get_engine().begin() as c:
        c.execute(insert(db.auth_attempts).values(
            scope="login", key="198.51.100.1", at=clock() - timedelta(hours=3)))
    assert attempts() == 1
    ratelimit.begin(ratelimit.LOGIN, "203.0.113.9")
    assert keys() == {"203.0.113.9"}


# ---- the settings -------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [("5", 5), ("", 20), ("abc", 20), ("0", 20),
                                            ("-3", 20), ("2.5", 20)])
def test_the_limit_comes_from_the_environment_and_a_typo_never_switches_it_off(
        monkeypatch, value, expected):
    monkeypatch.setenv("LOGIN_IP_MAX_FAILURES", value)
    assert ratelimit.limit_for(ratelimit.LOGIN)[0] == expected


def test_the_defaults_are_the_documented_ones(monkeypatch):
    for name in ("LOGIN_IP_MAX_FAILURES", "LOGIN_IP_WINDOW_MINUTES",
                 "SIGNUP_IP_MAX_ATTEMPTS", "SIGNUP_IP_WINDOW_MINUTES"):
        monkeypatch.delenv(name, raising=False)
    assert ratelimit.limit_for(ratelimit.LOGIN) == (20, timedelta(minutes=15))
    assert ratelimit.limit_for(ratelimit.SIGNUP) == (10, timedelta(hours=1))
    monkeypatch.setenv("LOGIN_IP_WINDOW_MINUTES", "30")
    assert ratelimit.limit_for(ratelimit.LOGIN)[1] == timedelta(minutes=30)
