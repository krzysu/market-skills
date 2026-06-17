"""Dynamic skill loader used by L2/L3 skills to compose other skills."""

import functools
import importlib.util
import os


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
