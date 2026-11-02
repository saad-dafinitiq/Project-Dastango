"""A limit per network address on sign-in and sign-up (DST-23, ASVS 2.2.1).

The lockout in `accounts.py` counts per account, and that is not enough: it does
nothing about one address trying many accounts (the DST-21 audit sent 40 failed
logins for 40 emails in 3 s and every one was answered). This is the other half.

How it works, and why:
- **One row per attempt, in the database.** An in-memory counter would reset on
  every restart and would be per process; rows are shared by every API process
  and survive a restart.
- **Count first, then act.** `begin` records the attempt and counts it in one
  transaction, *before* any password is checked. If the check came first, forty
  parallel requests would all see "no failures yet" and all be let through. An
  attempt over the limit takes its own row back, so hammering a blocked address
  does not extend the block.
- **Failures are what count for sign-in.** `Attempt.forgive` deletes the row
  after a successful sign-in, so people behind one shared address are not
  penalised for each other's successes. A success never clears anybody else's
  failures, so an attacker cannot reset the counter by signing in to an account
  of their own between guesses. Sign-up counts every attempt.
- **IPv6 is counted by /64.** A single customer is handed at least a /64, so
  counting full addresses would let one attacker rotate through billions.
"""
from __future__ import annotations

import ipaddress
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, insert, select, text

from shared import db
from shared.utils.logging import get_logger

log = get_logger("auth.ratelimit")

LOGIN = "login"
SIGNUP = "signup"

# (environment variable for the limit, for the window in minutes, default limit,
# default window). 20 failures in 15 minutes leaves a household or an office
# room to mistype, and still caps one address at 80 password guesses an hour.
_SETTINGS = {
    LOGIN: ("LOGIN_IP_MAX_FAILURES", "LOGIN_IP_WINDOW_MINUTES", 20, 15),
    SIGNUP: ("SIGNUP_IP_MAX_ATTEMPTS", "SIGNUP_IP_WINDOW_MINUTES", 10, 60),
}

_WARNED: set = set()


def now() -> datetime:
    """Naive UTC, like every other timestamp in the database. Tests replace this."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _positive_int(name: str, default: int) -> int:
    """The variable as a positive integer. A typo falls back to the default (with a
    warning) rather than quietly turning the protection off."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        if value > 0:
            return value
    except ValueError:
        pass
    if name not in _WARNED:
        _WARNED.add(name)
        log.warning("%s=%r is not a positive whole number, using the default %d",
                    name, raw, default)
    return default


def limit_for(scope: str) -> tuple:
    """(attempts allowed, window) for a scope, read from the environment each time."""
    max_name, window_name, max_default, window_default = _SETTINGS[scope]
    return (_positive_int(max_name, max_default),
            timedelta(minutes=_positive_int(window_name, window_default)))


def address_key(host: Optional[str]) -> str:
    """What an address is counted as: itself for IPv4, its /64 for IPv6.

    Anything that is not an IP address (no client at all, a test client's
    hostname) is kept as it is, so that it is still limited rather than skipped.
    """
    host = (host or "").strip()
    if not host:
        return "unknown"
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])    # drop an IPv6 zone id
    except ValueError:
        return host[:64]
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:                                  # ::ffff:1.2.3.4 is IPv4
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


@dataclass
class Attempt:
    """One counted attempt. `allowed` False means it was over the limit."""
    allowed: bool
    retry_after: int = 0                 # seconds to wait, when not allowed
    _id: Optional[int] = None

    def forgive(self) -> None:
        """Take a successful sign-in back out of the count."""
        if self._id is None:
            return
        with db.get_engine().begin() as c:
            c.execute(delete(db.auth_attempts).where(db.auth_attempts.c.id == self._id))
        self._id = None


def begin(scope: str, host: Optional[str]) -> Attempt:
    """Count an attempt from `host` and say whether it may go ahead."""
    limit, window = limit_for(scope)
    key = address_key(host)
    t = now()
    t_table = db.auth_attempts
    # Old rows go on every call, which keeps the table to about a window's worth
    # of attempts without a separate cleanup job.
    keep_for = max(limit_for(s)[1] for s in _SETTINGS)

    engine = db.get_engine()
    with engine.begin() as c:
        if engine.dialect.name == "postgresql":
            # Postgres transactions do not see each other's uncommitted rows, so
            # two attempts at the same moment could each count only themselves.
            # Taking turns per address makes the count exact. SQLite already has
            # one writer at a time.
            c.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                      {"k": f"{scope}:{key}"})
        c.execute(delete(t_table).where(t_table.c.at < t - keep_for))
        own = c.execute(insert(t_table).values(scope=scope, key=key, at=t)).inserted_primary_key[0]
        rows = c.execute(
            select(t_table.c.id, t_table.c.at)
            .where(t_table.c.scope == scope, t_table.c.key == key, t_table.c.at > t - window)
            .order_by(t_table.c.at.asc(), t_table.c.id.asc())
        ).all()
        if len(rows) <= limit:
            return Attempt(allowed=True, _id=own)

        # Over the limit: this attempt does not count against the address.
        c.execute(delete(t_table).where(t_table.c.id == own))
        others = [at for row_id, at in rows if row_id != own]
        # It may go ahead again once enough of the oldest rows have aged out to
        # leave room for one more.
        frees_up = others[len(others) - limit] + window
        wait = max(1, math.ceil((frees_up - t).total_seconds()))
    log.warning("%s limit reached for %s (%d attempts in %d min)", scope, key,
                limit, int(window.total_seconds() // 60))
    return Attempt(allowed=False, retry_after=wait)

