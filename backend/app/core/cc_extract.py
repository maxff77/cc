"""CC extraction — EXACT port of legacy ``extraer_cc``/``RE_CC`` (core.py).

🔒 project-context rule: each captured value is truncated at the literal
substring ``Status`` — this is INTENTIONAL parsing of the bot's reply format;
do not "fix" it. No dedup here: the per-session dedup lives in
``repos/responses.add_new_cc`` (mirror of ``services/batches.apply_gate``,
which ported ``agregar_prefijo`` just as literally).
"""

import re

# Captures the datum following "CC:" (case-insensitive) up to end of line.
RE_CC = re.compile(r"(?i)\bCC\s*:\s*([^\n]+)")


def extract_cc(text: str) -> list[str]:
    """The data following ``CC:`` in ``text`` (without the ``Status…`` tail).

    Per match: truncate at the literal ``Status``, strip, discard empties,
    preserve order — line-by-line port of legacy ``extraer_cc``.
    """
    values: list[str] = []
    for match in RE_CC.findall(text):
        value = match.split("Status")[0].strip()
        if value:
            values.append(value)
    return values
