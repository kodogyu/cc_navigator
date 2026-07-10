### Task 1: Scaffolding and state directory

**Files:**
- Create: `run-tests`
- Create: `src/ccnav/__init__.py`
- Create: `src/ccnav/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `paths.state_dir() -> pathlib.Path`, `paths.ensure_state_dir() -> pathlib.Path`.

- [ ] **Step 1: Create the test runner**

`run-tests`:

```sh
#!/bin/sh
# cc_navigator test runner. /usr/bin/python3 is mandatory: `which python3` is
# Anaconda and has no PyGObject.
set -e
exec env PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -v "$@"
```

```bash
chmod +x run-tests
mkdir -p src/ccnav tests
touch src/ccnav/__init__.py
```

- [ ] **Step 2: Write the failing test**

`tests/test_paths.py`:

```python
import os
import pathlib
import unittest
from unittest import mock

from ccnav import paths


class StateDirTest(unittest.TestCase):
    def test_uses_xdg_runtime_dir_when_set(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000"}):
            self.assertEqual(
                paths.state_dir(), pathlib.Path("/run/user/1000/cc-navigator")
            )

    def test_falls_back_to_uid_scoped_tmp(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                paths.state_dir(), pathlib.Path("/tmp/cc-navigator-%d" % os.getuid())
            )

    def test_empty_xdg_runtime_dir_is_treated_as_unset(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": ""}):
            self.assertEqual(
                paths.state_dir(), pathlib.Path("/tmp/cc-navigator-%d" % os.getuid())
            )
```

- [ ] **Step 3: Run test to verify it fails**

Run: `./run-tests -k StateDirTest`
Expected: FAIL with `ModuleNotFoundError: No module named 'ccnav.paths'`

- [ ] **Step 4: Write minimal implementation**

`src/ccnav/paths.py`:

```python
"""Where cc_navigator keeps its per-session state files."""
from __future__ import annotations

import os
import pathlib


def state_dir() -> pathlib.Path:
    """Directory holding one JSON file per live Claude session.

    XDG_RUNTIME_DIR is tmpfs and is wiped at logout, which is exactly the
    lifetime we want. Fall back to a uid-scoped /tmp path when it is unset.
    """
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        return pathlib.Path(base) / "cc-navigator"
    return pathlib.Path("/tmp/cc-navigator-%d" % os.getuid())


def ensure_state_dir() -> pathlib.Path:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(str(directory), 0o700)
    return directory
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./run-tests -k StateDirTest`
Expected: `Ran 3 tests` / `OK`

- [ ] **Step 6: Commit**

```bash
git add run-tests src/ccnav/__init__.py src/ccnav/paths.py tests/test_paths.py
git commit -m "feat: state directory resolution and test runner"
```

---

