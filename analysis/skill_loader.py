"""Dynamic skill loader used by L2/L3 skills to compose other skills."""

import functools
import importlib.util
import os
from pathlib import Path


@functools.cache
def load_skill(name: str):
    """Load a sibling skill's ``lib.py`` by directory name.

    Returns the loaded module, or ``None`` if the skill directory is missing.
    The skill name may contain hyphens (e.g. ``market-s-r``); the resulting
    module name has hyphens replaced with underscores so it is a valid Python
    identifier.
    """
    lib_path = os.path.join(os.path.dirname(__file__), "..", "skills", name, "lib.py")
    if not os.path.exists(lib_path):
        return None
    spec = importlib.util.spec_from_file_location(name.replace("-", "_") + "_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_lib_for_script(script_file: str):
    """Load the ``lib.py`` for the skill that owns ``script_file``.

    Single-call replacement for the duplicated
    ``importlib.util.spec_from_file_location`` boilerplate that used to live
    in every ``skills/*/scripts/run.py``. The script path must be inside a
    ``skills/<name>/...`` directory; the helper derives the skill name from
    the path and delegates to :func:`load_skill` (so the result is cached).

    Examples:
        >>> _lib = load_lib_for_script(__file__)

    Raises ``RuntimeError`` when the script is not under a ``skills/`` directory
    or the corresponding ``lib.py`` is missing.
    """
    try:
        resolved = Path(script_file).resolve()
        parts = resolved.parts
        idx = parts.index("skills")
    except (ValueError, IndexError):
        raise RuntimeError(f"load_lib_for_script: {script_file!r} is not under a 'skills/' directory") from None
    if idx + 1 >= len(parts):
        raise RuntimeError(f"load_lib_for_script: {script_file!r} is not under a 'skills/<name>/...' directory")
    return load_skill(parts[idx + 1])
