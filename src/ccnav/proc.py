"""The single place cc_navigator spawns a subprocess."""
from __future__ import annotations

import subprocess
from typing import Callable, Sequence, Tuple

Runner = Callable[[Sequence[str]], Tuple[int, str]]


def run_command(argv: Sequence[str]) -> Tuple[int, str]:
    completed = subprocess.run(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        universal_newlines=True,
    )
    return completed.returncode, completed.stdout
