"""Secret redaction, applied before anything reaches SQLite or a vault note.

Session transcripts routinely contain pasted credentials. This module is the single
gate every captured field passes through, and it fails closed: when a value looks
secret but cannot be classified confidently, it is redacted rather than stored.

Two sources of patterns:

  * Structural rules below, which catch the shapes secrets take (``key = value``,
    PEM blocks, JWTs, cloud keys, high-entropy blobs).
  * Literal strings loaded from ``%LOCALAPPDATA%/ClaudeBrain/deny_literals.txt``,
    one per line.

Known-secret literals deliberately live *outside* this repository. Hardcoding a
real credential in source — in a project intended for GitHub — would publish the
very thing this module exists to contain.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path

__all__ = [
    "REDACTION",
    "ScrubResult",
    "load_deny_literals",
    "looks_secret",
    "scrub",
    "scrub_mapping",
]

REDACTION = "[REDACTED]"

#: Minimum Shannon entropy (bits/char) for an unrecognised token to count as secret.
_ENTROPY_THRESHOLD = 3.6

#: Tokens shorter than this are never judged on entropy alone — too many false positives.
_MIN_ENTROPY_LEN = 20

_SECRET_KEYWORD = (
    r"(?:pass(?:word|wd|phrase)?|secret|token|api[-_ ]?key|apikey|auth|bearer"
    r"|credential|private[-_ ]?key|client[-_ ]?secret|access[-_ ]?key|session[-_ ]?key)"
)

#: Narrower set for the *prose* rule, which has no `:` or `=` to lean on.
#: Bare ``pass`` and ``auth`` are ordinary English in developer notes - "tests
#: pass", "SELFTEST PASS (Python 3.14.0)", "auth is broken" - so matching them
#: without an assignment operator redacts prose. With an operator they stay in
#: `_SECRET_KEYWORD`, where `pass:` or `--auth=` is genuinely suspicious.
_PROSE_KEYWORD = (
    r"(?:pass(?:word|wd|phrase)|secret|token|api[-_ ]?key|apikey|bearer"
    r"|credential|private[-_ ]?key|client[-_ ]?secret|access[-_ ]?key|session[-_ ]?key)"
)

# Ordered: the most specific shapes first, so a JWT is not mangled by the
# generic key/value rule before it is recognised.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # -- PEM / OpenSSH private keys: redact the whole block, not just the header.
    (
        "pem_block",
        re.compile(
            r"-----BEGIN[A-Z ]*PRIVATE KEY-----.*?-----END[A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    # -- JWTs: three base64url segments.
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    # -- Cloud / provider key shapes.
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[abpsr]-[A-Za-z0-9-]{10,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    # -- URLs carrying inline credentials: scheme://user:pass@host
    ("url_userinfo", re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@]+:)([^\s@/]+)(@)")),
    # -- `password: value`, `--token=value`, `SECRET = "value"`. The bare "is"
    #    form belongs to keyword_prose: "auth is broken" is a sentence.
    (
        "keyword_assignment",
        re.compile(
            rf"((?:--)?{_SECRET_KEYWORD}\s*[:=]\s*)"
            r"""(?!\s*$)(["']?)([^\s"',;)]{4,})(\2)""",
            re.IGNORECASE,
        ),
    ),
    # -- Prose form: "root password Hunter2", "the password for X is Hunter2".
    (
        "keyword_prose",
        re.compile(
            rf"((?:root|admin|user|login|ssh|vps|db|database)?\s*{_PROSE_KEYWORD}"
            r"\s+(?:is\s+)?)"
            # Not a short all-letter word: "password protect the option" is a
            # sentence, not a credential. A real one carries a digit or symbol,
            # or is long and opaque; both still match.
            r"(?!(?:[A-Za-z]{1,11})(?:[\s,;.]|$))"
            r"([^\s,;.]{6,})",
            re.IGNORECASE,
        ),
    ),
)

_DENY_LITERALS_FILENAME = "deny_literals.txt"


def _deny_literals_path() -> Path:
    """Location of the operator-managed literal denylist, outside this repo."""
    from claude_brain.config import APP_DIR

    return APP_DIR / _DENY_LITERALS_FILENAME


def load_deny_literals(path: Path | None = None) -> tuple[str, ...]:
    """Read literal secrets to redact, one per line.

    Blank lines and ``#`` comments are ignored. A missing or unreadable file is not
    an error — the structural rules still apply. Returned longest-first so that a
    longer secret is redacted before any shorter substring of it.
    """
    target = path or _deny_literals_path()
    try:
        raw = target.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return ()

    literals = {
        line.strip()
        for line in raw.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    return tuple(sorted(literals, key=len, reverse=True))


def shannon_entropy(value: str) -> float:
    """Shannon entropy in bits per character. Empty string scores 0.0."""
    if not value:
        return 0.0
    length = len(value)
    counts: dict[str, int] = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def looks_secret(token: str) -> bool:
    """Whether an unlabelled token is high-entropy enough to treat as a secret.

    Deliberately conservative, because over-redaction destroys the value of the
    archive — and this is only the *backstop*. Operator-known literals and
    labelled values (``token=``, ``Authorization: Bearer``) are already gone by
    the time a token reaches here, so every carve-out below costs nothing real:

    * **Only unbroken alphanumeric runs are judged.** Hyphen-, underscore- and
      slash-separated text is segmented, not secret. Without this,
      ``Gemma4-26B-A4B-QAT-Uncensored-HauhauCS-Balanced-MTP`` is redacted, and —
      measured on the real digest corpus — so are 32 file paths and URLs, among
      them ``/mnt/c/Users/jdoe/Desktop/Scripting`` **104 times**.
    * **Pure hexadecimal is exempt.** Git SHAs and file digests are routinely
      quoted in notes and are not credentials.
    * **A digit is required.** Case alone does not distinguish a random string
      from a CamelCase identifier, and the corpus is full of the latter:
      ``RequestsDependencyWarning``, ``OSINTEnrichmentPanel``,
      ``SharedDrivePolicyManager``. Generated credentials are base62-ish and
      effectively always carry digits; a digit-free one would have to be both
      unlabelled and unknown to be missed.

    Secrets that *are* separated (``sk-ant-…``, ``ghp_…``) are matched by the
    provider-specific rules above, so nothing real is lost here.
    """
    if len(token) < _MIN_ENTROPY_LEN:
        return False
    if not re.fullmatch(r"[A-Za-z0-9]+", token):
        return False
    if re.fullmatch(r"[0-9a-f]+|[0-9A-F]+", token):
        return False
    if not re.search(r"[0-9]", token) or not re.search(r"[A-Za-z]", token):
        return False
    return shannon_entropy(token) >= _ENTROPY_THRESHOLD


@dataclass(frozen=True)
class ScrubResult:
    """Outcome of scrubbing one string. Immutable; ``text`` is always safe to store."""

    text: str
    redactions: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.redactions)

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.text


def _redact_literals(text: str, literals: Iterable[str]) -> tuple[str, list[str]]:
    hits: list[str] = []
    out = text
    for literal in literals:
        if literal and literal in out:
            out = out.replace(literal, REDACTION)
            hits.append("deny_literal")
    return out, hits


#: Rules that keep their matched prefix and redact only the captured value, so
#: the record stays readable ("password: [REDACTED]" rather than "[REDACTED]").
_PREFIX_PRESERVING = frozenset({"keyword_assignment", "keyword_prose", "url_userinfo"})


def _replace_value(match: re.Match[str], *, keep_suffix: bool) -> str:
    """Rebuild a match as prefix + redaction, optionally re-appending the suffix.

    ``url_userinfo`` captures a trailing ``@`` that must survive so the host
    remains readable; the keyword rules capture no meaningful suffix.
    """
    groups = match.groups()
    suffix = groups[-1] if keep_suffix and len(groups) > 1 else ""
    return f"{groups[0]}{REDACTION}{suffix}"


def _redact_rules(text: str) -> tuple[str, list[str]]:
    hits: list[str] = []
    out = text
    for name, pattern in _RULES:
        if name in _PREFIX_PRESERVING:
            # functools.partial binds `name`'s effect now rather than closing
            # over the loop variable, which would be read at call time.
            out, count = pattern.subn(
                partial(_replace_value, keep_suffix=(name == "url_userinfo")), out
            )
        else:
            out, count = pattern.subn(REDACTION, out)
        if count:
            hits.extend([name] * count)
    return out, hits


def _redact_entropy(text: str) -> tuple[str, list[str]]:
    hits: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        token = match.group(0)
        if looks_secret(token):
            hits.append("high_entropy")
            return REDACTION
        return token

    # Alphanumeric runs only. Every separator - including / and = - splits the
    # scan, so segmented identifiers and paths break into pieces too short to
    # reach the length floor. See looks_secret for what this is protecting.
    return re.sub(r"[A-Za-z0-9]{20,}", _sub, text), hits


def scrub(
    text: str | None,
    *,
    literals: Iterable[str] | None = None,
    check_entropy: bool = True,
) -> ScrubResult:
    """Redact secrets from ``text``.

    Order matters: literals first (an operator-known secret must never survive),
    then structural rules, then the entropy sweep as a backstop. ``None`` and
    non-string input return an empty result rather than raising, because this runs
    on every captured field and must never be the thing that breaks ingest.
    """
    if not text or not isinstance(text, str):
        return ScrubResult("" if text is None else str(text or ""))

    deny = tuple(literals) if literals is not None else load_deny_literals()

    out, hits = _redact_literals(text, deny)
    out, rule_hits = _redact_rules(out)
    hits.extend(rule_hits)

    if check_entropy:
        out, entropy_hits = _redact_entropy(out)
        hits.extend(entropy_hits)

    return ScrubResult(out, tuple(hits))


def scrub_mapping(
    data: Mapping[str, object],
    *,
    fields: Iterable[str] | None = None,
    literals: Iterable[str] | None = None,
) -> dict[str, object]:
    """Return a new dict with string values scrubbed.

    Never mutates the input. ``fields`` limits scrubbing to specific keys; by
    default every string value is scrubbed.
    """
    deny = tuple(literals) if literals is not None else load_deny_literals()
    targets = set(fields) if fields is not None else None

    return {
        key: (
            scrub(value, literals=deny).text
            if isinstance(value, str) and (targets is None or key in targets)
            else value
        )
        for key, value in data.items()
    }
