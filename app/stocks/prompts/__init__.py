"""The stocks package's prompts: one Markdown template per model call in this folder, with
`{{name}}` placeholders. A template's version is its name plus a hash of its text, so any edit to
a prompt changes the `prompt_version` stored with what it produced (stock-oracle's scheme), without
anyone having to remember to bump a number.

Every template puts outside text (pages, documents, search results, our own earlier notes) inside
`<data-{{fence}} ...>` blocks whose fence is random per call, and says that nothing inside them is
an instruction."""

import hashlib
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
HERE = Path(__file__).parent


@dataclass(frozen=True)
class Prompt:
    name: str
    template: str

    @property
    def version(self):
        return f"{self.name}@{hashlib.sha256(self.template.encode()).hexdigest()[:12]}"

    @property
    def placeholders(self):
        return frozenset(PLACEHOLDER.findall(self.template))

    def render(self, **values):
        """Every placeholder filled; a missing or unexpected value is a ValueError. One pass, so a
        value holding `{{x}}` (a fetched page, say) is never expanded."""
        missing, extra = self.placeholders - values.keys(), values.keys() - self.placeholders
        if missing or extra:
            raise ValueError(f"prompt {self.name}: missing {sorted(missing)}, unexpected {sorted(extra)}")
        return PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), self.template)


@cache
def load(name):
    return Prompt(name, (HERE / f"{name}.md").read_text(encoding="utf-8"))
