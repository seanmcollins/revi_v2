"""Sensitive-payload guard for outbound LLM prompts (design §15).

Prompts carry governed vocabulary (ids, labels, descriptions) and the
analyst's own words — never data rows, file paths, or connection material.
``assert_safe_payload`` is the last line of defense before a prompt leaves
the process: it rejects anything that looks like

- credentialed URLs or database connection strings,
- secret/key/password assignments,
- filesystem paths (POSIX home/system trees, Windows drives),
- identifier patterns with PHI shape (SSN-style),
- raw tabular payloads (many delimiter-heavy lines) or serialized row
  arrays (long ``[{...},{...}]`` runs).

The checks are deliberately coarse: a false rejection costs a clarifying
code change; a false pass leaks data. Carried from the old prototype's
guard concept (plan: "Lessons carried").
"""

from __future__ import annotations

import re

from revi_kernel.errors import PolicyDeniedError

_PATTERN_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "credentialed_url",
        re.compile(r"\b[a-zA-Z][\w+.-]*://[^\s/@]+:[^\s@]+@"),
    ),
    (
        "connection_string",
        re.compile(
            r"\b(?:postgres(?:ql)?|mysql|mssql|oracle|snowflake|duckdb|jdbc|odbc|redis|mongodb)"
            r"(?:\+\w+)?://",
            re.IGNORECASE,
        ),
    ),
    (
        "secret_assignment",
        re.compile(
            r"\b(?:password|passwd|pwd|secret|api[_-]?key|auth[_-]?token|access[_-]?key|"
            r"private[_-]?key|connection[_-]?string)\s*[=:]\s*\S+",
            re.IGNORECASE,
        ),
    ),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "filesystem_path",
        re.compile(
            r"(?:^|[\s\"'`(\[=])(?:/(?:Users|home|var|tmp|private|etc|opt|srv)/[^\s\"'`)\]]+"
            r"|[A-Za-z]:\\[^\s\"'`)\]]+)"
        ),
    ),
    ("ssn_shaped_identifier", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
)

# Structural raw-row checks. Vocabulary lists are line-per-id prose; result
# tables are many lines dense with the same delimiter, or serialized arrays
# of row objects.
_DELIMITED_LINE_THRESHOLD = 4  # delimiters per line to count as tabular
_DELIMITED_LINES_LIMIT = 8  # tabular lines before the payload is rejected
_ROW_OBJECT_LIMIT = 10  # "},{"-style boundaries before rejection

_ROW_BOUNDARY = re.compile(r"\}\s*,\s*\{")


def _tabular_line_count(text: str) -> int:
    count = 0
    for line in text.splitlines():
        if line.count("|") >= _DELIMITED_LINE_THRESHOLD or line.count("\t") >= _DELIMITED_LINE_THRESHOLD:
            count += 1
    return count


def assert_safe_payload(text: str) -> None:
    """Raise :class:`PolicyDeniedError` when ``text`` must not reach a model."""
    for rule, pattern in _PATTERN_RULES:
        match = pattern.search(text)
        if match is not None:
            raise PolicyDeniedError(
                f"outbound LLM payload rejected: {rule.replace('_', ' ')} detected",
                details={"rule": rule},
            )
    tabular = _tabular_line_count(text)
    if tabular >= _DELIMITED_LINES_LIMIT:
        raise PolicyDeniedError(
            "outbound LLM payload rejected: raw tabular payload detected",
            details={"rule": "tabular_payload", "lines": tabular},
        )
    row_boundaries = len(_ROW_BOUNDARY.findall(text))
    if row_boundaries >= _ROW_OBJECT_LIMIT:
        raise PolicyDeniedError(
            "outbound LLM payload rejected: serialized row array detected",
            details={"rule": "row_payload", "rows": row_boundaries + 1},
        )
