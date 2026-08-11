"""Versioned prompt templates and a minimal, strict substitution renderer.

Templates are markdown files named ``<template_id>@<version>.md`` under
``llm/templates/``. Loading records the content hash so every trace can pin
exactly which prompt text produced a call (design §14: "model and
prompt/template version").

Substitution is deliberately tiny: ``{name}`` placeholders (lowercase
identifiers) replaced from an explicit mapping. Unknown placeholders and
unused values are both errors — a template and its call site drift loudly,
never silently. Brace sequences that do not match the placeholder shape
pass through untouched.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources

_TEMPLATE_PACKAGE = "revi_investigation.application.llm"
_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")


@dataclass(frozen=True, slots=True)
class LoadedTemplate:
    template_id: str
    version: str
    text: str
    sha256: str


def load_template(template_id: str, version: str) -> LoadedTemplate:
    """Load ``templates/<template_id>@<version>.md`` from package data."""
    filename = f"{template_id}@{version}.md"
    resource = resources.files(_TEMPLATE_PACKAGE).joinpath("templates", filename)
    try:
        text = resource.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise LookupError(f"prompt template {filename!r} does not exist") from None
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return LoadedTemplate(template_id=template_id, version=version, text=text, sha256=digest)


#: How every prompt template ends: a heading, then the analyst's own
#: sentence and nothing after it. Everything above is vocabulary and
#: context this platform composed — including, since the reading prompts
#: were given the answer on screen, the analyst's PREVIOUS question.
_UTTERANCE_HEADINGS = ("\nUtterance:\n", "\nQuestion:\n")


def analyst_words(prompt: str) -> str:
    """The tail of a rendered prompt that is the analyst's own sentence.

    For test and demo doubles that pick a canned answer by what was asked.
    Matching on the WHOLE prompt stopped being a test of the utterance the
    moment the prompt started carrying the conversation: "Break that down
    by payer" renders a prompt containing "Why did cash decline last
    week?", and a whole-prompt match served turn one's script on turn two.
    """
    for heading in _UTTERANCE_HEADINGS:
        index = prompt.rfind(heading)
        if index != -1:
            return prompt[index + len(heading) :]
    return prompt


def render_template(text: str, values: Mapping[str, str]) -> str:
    """Substitute ``{name}`` placeholders strictly from ``values``."""
    used: set[str] = set()

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise LookupError(f"template placeholder {{{name}}} has no value")
        used.add(name)
        return values[name]

    rendered = _PLACEHOLDER.sub(substitute, text)
    unused = set(values) - used
    if unused:
        raise LookupError(f"template values never used: {sorted(unused)}")
    return rendered
