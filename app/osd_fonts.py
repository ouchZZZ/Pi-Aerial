"""Read locally installed font families through fontconfig without fetching font files."""
from __future__ import annotations

import subprocess

LEGACY_FONT_IDS = frozenset({
    'default', 'arial', 'verdana', 'trebuchet', 'georgia', 'times', 'courier',
})
INSTALLED_PREFIX = 'installed:'
MAX_FAMILY_LENGTH = 160


def valid_family(family: str) -> bool:
    """Keep font names printable and bounded for safe JSON and CSSOM use."""
    return (type(family) is str and 0 < len(family) <= MAX_FAMILY_LENGTH
            and family == family.strip()
            and all(character.isprintable() for character in family))


def valid_font_choice(choice: object) -> bool:
    if type(choice) is not str:
        return False
    return (choice in LEGACY_FONT_IDS
            or (choice.startswith(INSTALLED_PREFIX)
                and valid_family(choice[len(INSTALLED_PREFIX):])))


def installed_font_families() -> list[str]:
    """Unique primary family names from fc-list, sorted for the editor.

    Using family[0] avoids counting aliases for each font face as extra families.
    The command never accepts user-controlled arguments or invokes a shell.
    """
    try:
        output = subprocess.run(
            ['fc-list', '-f', '%{family[0]}\\n'],
            capture_output=True, text=True, errors='replace',
            timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if output.returncode != 0:
        return []
    names = {name.strip() for name in output.stdout.splitlines()
             if valid_family(name.strip())}
    return sorted(names, key=str.casefold)
