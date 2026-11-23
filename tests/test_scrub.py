"""Tests for secret redaction.

Every secret used here is synthetic. Tests pass ``literals=`` explicitly so they
never depend on the machine's real denylist.
"""

from __future__ import annotations

import dataclasses

import pytest

from claude_brain.core import scrub as S

FAKE_SECRET = "Qx7!tempPassw0rd#42"


# --------------------------------------------------------------------------
# Literal denylist
# --------------------------------------------------------------------------


def test_deny_literal_is_redacted():
    result = S.scrub(f"Password: {FAKE_SECRET}", literals=[FAKE_SECRET])
    assert FAKE_SECRET not in result.text
    assert "deny_literal" in result.redactions


@pytest.mark.parametrize(
    "shape",
    [
        "Password: {s}",
        "Root password is {s}",
        "root@host with password {s} over ssh",
        "the VPS root password {s} was pasted in chat",
    ],
)
def test_literal_redacted_in_every_phrasing(shape):
    """The same credential appears in many shapes across a transcript."""
    result = S.scrub(shape.format(s=FAKE_SECRET), literals=[FAKE_SECRET])
    assert FAKE_SECRET not in result.text


def test_longest_literal_wins():
    """A longer secret must be redacted before any shorter substring of it."""
    result = S.scrub("value=abcdef12345", literals=["abcdef", "abcdef12345"])
    assert "abcdef" not in result.text


def test_load_deny_literals_ignores_comments_and_blanks(tmp_path):
    path = tmp_path / "deny.txt"
    path.write_text("# comment\n\nalpha\n  beta  \n", encoding="utf-8")
    assert set(S.load_deny_literals(path)) == {"alpha", "beta"}


def test_load_deny_literals_missing_file_is_not_fatal(tmp_path):
    """Structural rules must still apply if the denylist is absent."""
    assert S.load_deny_literals(tmp_path / "nope.txt") == ()


# --------------------------------------------------------------------------
# Structural rules
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("api_key = AKIAIOSFODNN7EXAMPLE", "aws_access_key"),
        ("token: ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123", "github_token"),
        ("key: sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWx", "anthropic_key"),
        ("slack xoxb-123456789012-AbCdEfGhIjKl", "slack_token"),
        ("db at postgres://admin:s3cr3tPw@host/db", "url_userinfo"),
        (
            "Authorization: Bearer "
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27u",
            "jwt",
        ),
        (
            "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
            "pem_block",
        ),
    ],
)
def test_structural_rule_fires(text, rule):
    result = S.scrub(text, literals=[])
    assert rule in result.redactions
    assert S.REDACTION in result.text


def test_keyword_assignment_keeps_the_label():
    """The record must stay readable: redact the value, keep the key."""
    result = S.scrub("password: hunter2hunter2", literals=[])
    assert result.text.startswith("password:")
    assert "hunter2" not in result.text


def test_url_userinfo_keeps_host():
    result = S.scrub("postgres://admin:s3cr3tPw@db.example.com/x", literals=[])
    assert "db.example.com" in result.text
    assert "s3cr3tPw" not in result.text


def test_pem_block_redacts_entire_body():
    text = "-----BEGIN OPENSSH PRIVATE KEY-----\nSECRETBODY\n-----END OPENSSH PRIVATE KEY-----"
    result = S.scrub(text, literals=[])
    assert "SECRETBODY" not in result.text


# --------------------------------------------------------------------------
# False positives. Over-redaction destroys the archive's value, so these
# matter as much as the positive cases.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "benign",
    [
        "Remuxing MKV to MP4 dropped TTFB from 3.29s to 0.66s",
        r"D:\Videos\Movies\Arrival (2016)\Arrival (2016).mp4",
        "The cold prefill was 67 tok/s versus 4568 cached",
        "https://movies.example.com/web/index.html",
        # Hyphenated identifiers are segmented names, not secrets.
        "Gemma4-26B-A4B-QAT-Uncensored-HauhauCS-Balanced-MTP",
        "hf.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive",
        # Hex digests are quoted in notes constantly.
        "sha1 3d0d7e5fb2ce288813306e4d4636395e047a3d28",
        "commit 72ff7da94c1b3e5f8a2d6b4c9e7f1a3d5b8c0e2f",
        "session 6c971d85-6b6a-4be9-ad7f-26cfaf36f92a",
        "OLLAMA_KEEP_ALIVE=-1",
    ],
)
def test_benign_text_is_untouched(benign):
    result = S.scrub(benign, literals=[])
    assert not result.changed, f"false positive: {result.redactions}"
    assert result.text == benign


# --------------------------------------------------------------------------
# Entropy heuristic
# --------------------------------------------------------------------------


def test_entropy_catches_unbroken_base64_blob():
    assert S.looks_secret("aGVsbG9Xb3JsZFRoaXNJc0FTZWNyZXRWYWx1ZTEyMw")


@pytest.mark.parametrize(
    "token",
    [
        "short",  # below the length floor
        "Gemma4-26B-A4B-QAT-Uncensored",  # segmented identifier
        "3d0d7e5fb2ce288813306e4d4636395e047a3d28",  # pure hex
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",  # low entropy
        "ALLUPPERCASELETTERSONLYHERE",  # single character class
    ],
)
def test_entropy_rejects_non_secrets(token):
    assert not S.looks_secret(token)


def test_shannon_entropy_bounds():
    assert S.shannon_entropy("") == 0.0
    assert S.shannon_entropy("aaaa") == 0.0
    assert S.shannon_entropy("ab") == pytest.approx(1.0)


# --------------------------------------------------------------------------
# API contract
# --------------------------------------------------------------------------


def test_scrub_handles_none_and_empty():
    assert S.scrub(None).text == ""
    assert S.scrub("").text == ""
    assert not S.scrub(None).changed


def test_result_is_immutable():
    result = S.scrub("password: abcdefgh", literals=[])
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.text = "mutated"  # type: ignore[misc]


def test_scrub_mapping_does_not_mutate_input():
    source = {"summary": f"pw {FAKE_SECRET}", "tool_count": 327}
    out = S.scrub_mapping(source, literals=[FAKE_SECRET])

    assert source["summary"] == f"pw {FAKE_SECRET}", "input dict was mutated"
    assert FAKE_SECRET not in out["summary"]
    assert out["tool_count"] == 327, "non-string values must pass through"


def test_scrub_mapping_respects_field_filter():
    source = {"safe": f"pw {FAKE_SECRET}", "unsafe": f"pw {FAKE_SECRET}"}
    out = S.scrub_mapping(source, fields=["unsafe"], literals=[FAKE_SECRET])

    assert FAKE_SECRET in out["safe"], "unlisted field should be skipped"
    assert FAKE_SECRET not in out["unsafe"]


# --------------------------------------------------------------------------
# Over-redaction regressions, all found on the real digest corpus
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/mnt/c/Users/jdoe/Desktop/Scripting_Projects/Overture/CLAUDE.md",
        "C:/Users/jdoe/Desktop/OSM/Southwest",
        "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7331277/",
        "https://youtube.com/shorts/i85UZEMKixk",
        "dist/ImageryBrowser/ImageryBrowser.exe",
    ],
)
def test_paths_and_urls_survive(path):
    """`/` and `=` used to be inside the entropy scan's character class, so a
    long path was one token. `/mnt/c/Users/jdoe/Desktop/Scripting` was redacted
    104 times across the corpus."""
    assert S.scrub(path, literals=()).text == path


@pytest.mark.parametrize(
    "identifier",
    ["RequestsDependencyWarning", "OSINTEnrichmentPanel", "SharedDrivePolicyManager"],
)
def test_camelcase_identifiers_survive(identifier):
    """Case alone cannot tell a random string from a concatenated name; these
    are real class and warning names quoted in session notes."""
    assert not S.looks_secret(identifier)
    assert S.scrub(identifier, literals=()).text == identifier


def test_entropy_still_catches_a_real_token_shape():
    """The carve-outs must not hollow out the backstop."""
    assert S.looks_secret("01HUvXtPaYXQwNgZJWFhTfq9")
    assert S.looks_secret("aGVsbG9Xb3JsZFRoaXNJc0FTZWNyZXRWYWx1ZTEyMw")
    assert S.REDACTION in S.scrub("ref 01HUvXtPaYXQwNgZJWFhTfq9 ok", literals=()).text


@pytest.mark.parametrize(
    "prose",
    [
        "SELFTEST PASS (Python 3.14.0)",
        "all 119 tests pass cleanly",
        "password protect the option with the admin pin",
        "auth is broken on the staging host",
    ],
)
def test_prose_about_secrets_is_not_mistaken_for_one(prose):
    """The prose rule has no `:` or `=` to lean on, so it must not fire on
    ordinary developer English. All four shapes occur in the real corpus."""
    assert S.scrub(prose, literals=()).text == prose


@pytest.mark.parametrize(
    "line",
    [
        "Root password Cyb3rW@rri0r3942",
        "the ssh password is Hunter2",
        "admin token aGVsbG9Xb3JsZFRoaXNJc0E",
    ],
)
def test_prose_rule_still_catches_a_real_credential(line):
    assert S.REDACTION in S.scrub(line, literals=()).text


def test_assignment_form_stays_aggressive():
    """`pass:` and `--auth=` keep the broad keyword set - an operator is far
    less likely to write those in a sentence."""
    assert S.REDACTION in S.scrub("pass: hunter2", literals=()).text
    assert S.REDACTION in S.scrub("--auth=abc123xyz", literals=()).text
