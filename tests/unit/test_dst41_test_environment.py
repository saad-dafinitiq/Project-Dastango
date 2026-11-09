"""DST-41 (QA finding M6): a developer's real .env must never reach the tests.

`import moviepy` runs `load_dotenv(find_dotenv())` (moviepy/config.py), so the first test that
imported it put every key in the repository's .env into the process, after tests/conftest.py had
scrubbed them. Three tests then saw real keys and failed on any machine that has a .env (CI has
none). conftest now imports MoviePy first and drops whatever that import added.
"""
import os
import sys

# What conftest scrubs, plus the other things a real .env holds (storage, sign-in, voices).
SECRETS_AND_MACHINE_SETTINGS = (
    "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_API_TOKEN", "AGNES_API_KEY", "ELEVENLABS_API_KEY", "MYMEMORY_EMAIL",
    "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "MONITOR_PING_BASE",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "S3_ENDPOINT_URL", "R2_API_TOKEN",
    "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "KOKORO_MODEL", "KOKORO_VOICES",
)


def test_moviepy_is_loaded_before_any_test_runs():
    # If this is false, the first test to import it would load .env in the middle of the run.
    assert "moviepy" in sys.modules


def test_no_credential_or_machine_setting_is_in_the_test_process():
    leaked = [name for name in SECRETS_AND_MACHINE_SETTINGS if os.environ.get(name)]
    assert leaked == [], f"a real .env reached the tests: {leaked}"


def test_importing_moviepy_later_changes_nothing(monkeypatch):
    before = dict(os.environ)
    import moviepy  # noqa: F401  (already loaded: a second import must not touch the environment)
    assert dict(os.environ) == before

