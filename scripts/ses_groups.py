"""
Parser for Patran/HyperMesh .ses session files' named element-group
definitions (`ga_group_create(...)` / `ga_group_entity_add(...)` calls).

This is NOT Nastran bulk-data format -- .ses is Patran's own PCL scripting
language. Some case studies (e.g. the NASA CRM wingbox) happen to ship a
bonus .ses file alongside the actual Nastran deck, defining real engineering
group names (ribs, skins, spars, stiffeners, ...) as explicit element-ID
sets. That's a much better "hide by name" interface than raw PSHELL/PBAR
property IDs when it's available -- see scripts/mcp_server.py's render
tools and GitHub issue #9 -- but it's specific to case studies that include
one, not a general Nastran/pyNastran feature; don't assume every model has
one.
"""
from __future__ import annotations

import re
from pathlib import Path

# A line ending in `" // @` continues on the next line, which starts with a
# fresh `"`. The paired quote characters straddling that join must be
# stripped along with the continuation marker itself -- stripping only the
# marker leaves an empty "" pair in the middle of the string, which breaks
# _GROUP_ENTITY_RE's single-quoted-string match.
_CONTINUATION_RE = re.compile(r'"\s*//\s*@\s*\n\s*"')
_GROUP_ENTITY_RE = re.compile(
    r'ga_group_entity_add\(\s*"([^"]+)"\s*,\s*"\s*Element\s+([^"]*)"\s*\)'
)


def parse_ses_groups(ses_path: str | Path) -> dict[str, set[int]]:
    """Parse a Patran/HyperMesh .ses session file and return
    {group_name: {element_id, ...}}.

    Element-ID lists use Nastran-style `a:b` inclusive ranges mixed with
    individual IDs, space-separated. A group name may appear in more than
    one `ga_group_entity_add` call -- their element sets are unioned.
    """
    path = Path(ses_path)
    if not path.is_file():
        raise FileNotFoundError(f".ses file not found: {path}")

    # These files are HyperMesh-exported PCL scripts, not guaranteed UTF-8.
    text = path.read_text(encoding="latin-1")
    text = _CONTINUATION_RE.sub("", text)

    groups: dict[str, set[int]] = {}
    for match in _GROUP_ENTITY_RE.finditer(text):
        name, ids_str = match.group(1), match.group(2)
        eids: set[int] = set()
        for token in ids_str.split():
            if ":" in token:
                lo, hi = token.split(":")
                eids.update(range(int(lo), int(hi) + 1))
            else:
                eids.add(int(token))
        groups.setdefault(name, set()).update(eids)

    return groups
