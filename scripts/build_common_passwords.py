"""Build auth/common_passwords.txt from a public list of breached passwords (DST-24).

The sign-up and password-change rules refuse any password on this list, so that
no password ever has to be sent to an outside service to be checked. Only
entries that could otherwise pass the length rules are kept: anything shorter
than the minimum is refused anyway, and the file stays small.

    python scripts/build_common_passwords.py Pwdb_top-1000000.txt \\
        --source-url https://raw.githubusercontent.com/danielmiessler/SecLists/<commit>/Passwords/Common-Credentials/Pwdb_top-1000000.txt \\
        --source-commit <commit> --source-date 2025-03-22

It writes `auth/common_passwords.txt` (one lowercase, NFKC-normalised password
per line, most common first) and `auth/common_passwords.meta.json` (where the
list came from and its hashes). A test checks the file against that record, so
a hand edit or a corrupted copy fails the suite.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auth import passwords  # noqa: E402

LICENCE = "MIT (the SecLists repository, Copyright (c) 2018 Daniel Miessler)"
NAME = "SecLists Passwords/Common-Credentials/Pwdb_top-1000000.txt"


def canonical_hash(entries) -> str:
    """SHA-256 of the entries joined by newlines, so the hash does not change when
    Git turns line endings into CRLF on a Windows checkout."""
    return hashlib.sha256(("\n".join(entries) + "\n").encode("utf-8")).hexdigest()


def build(source: Path) -> list:
    kept, seen = [], set()
    with source.open("rb") as handle:
        for raw in handle:
            try:
                line = raw.decode("utf-8").rstrip("\r\n")
            except UnicodeDecodeError:
                continue                      # not text we can compare against
            entry = passwords.normalise(line)
            if entry in seen or not entry.strip():
                continue
            seen.add(entry)
            # The same measure the rules use, so nothing that could pass is dropped.
            if passwords.MIN_PASSWORD_LENGTH <= passwords.effective_length(line) <= passwords.MAX_PASSWORD_LENGTH:
                kept.append(entry)
    return kept


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="the downloaded list, one password per line")
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-date", required=True, help="date of that commit, YYYY-MM-DD")
    args = parser.parse_args()

    source_bytes = args.source.read_bytes()
    entries = build(args.source)
    if len(entries) < 1000:
        print(f"only {len(entries)} entries survived the filter: wrong input file?", file=sys.stderr)
        return 1

    out = passwords.COMMON_PASSWORDS_FILE
    out.write_bytes(("\n".join(entries) + "\n").encode("utf-8"))
    meta = {
        "source": {
            "name": NAME, "url": args.source_url, "commit": args.source_commit,
            "commit_date": args.source_date, "retrieved": date.today().isoformat(),
            "licence": LICENCE, "bytes": len(source_bytes),
            "lines": source_bytes.count(b"\n"),
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
        },
        "filter": (f"NFKC and case-folded; duplicates removed; kept only entries of "
                   f"{passwords.MIN_PASSWORD_LENGTH} to {passwords.MAX_PASSWORD_LENGTH} "
                   "characters with each run of whitespace counted as one"),
        "output": {"entries": len(entries), "bytes": out.stat().st_size,
                   "sha256_of_lines": canonical_hash(entries)},
    }
    out.with_name("common_passwords.meta.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"kept {len(entries)} of {meta['source']['lines']} entries, {out.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
