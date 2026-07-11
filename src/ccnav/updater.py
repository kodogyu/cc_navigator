"""Self-update via git.

Fetch, and if the working tree has no tracked changes and the upstream is a
fast-forward away, fast-forward to it. Anything risky -- tracked local changes,
diverged history, no upstream, an unreachable remote, or not a git checkout --
aborts WITHOUT touching the tree, so local edits are never lost. Untracked files
are left out of the "dirty" test: a fast-forward does not touch them, and
git's own --ff-only refuses any merge that would clobber one.

The git calls go through an injected runner so the decision logic is tested
without a real repository or network.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from typing import Callable, List, Optional, Tuple

# (git args, without the leading "git") -> (returncode, stdout, stderr).
GitRun = Callable[[List[str]], Tuple[int, str, str]]

_TIMEOUT = 30.0


def repo_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def _git_runner(repo: pathlib.Path) -> GitRun:
    def run(args: List[str]) -> Tuple[int, str, str]:
        try:
            completed = subprocess.run(
                ["git", "-C", str(repo)] + args,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=_TIMEOUT)
            return (completed.returncode, completed.stdout, completed.stderr)
        except Exception as exc:  # noqa: BLE001 -- git missing, timeout, ...
            return (1, "", str(exc))
    return run


def update(git: Optional[GitRun] = None, repo: Optional[pathlib.Path] = None) -> Tuple[bool, str]:
    """Attempt a fast-forward to the upstream. Returns (updated, message).

    Never raises and never modifies the tree unless a clean fast-forward is
    possible: the tracked-changes check runs before any fetch, and the only
    tree-touching command (merge --ff-only) is the final gate, which git itself
    refuses if it would overwrite anything.
    """
    if git is None:
        git = _git_runner(repo or repo_dir())

    rc, out, _ = git(["status", "--porcelain"])
    if rc != 0:
        return (False, "git 저장소가 아니거나 상태를 확인할 수 없습니다.")
    tracked_changes = [line for line in out.splitlines() if line[:2] != "??"]
    if tracked_changes:
        return (False, "로컬 변경사항이 있어 업데이트를 중단했습니다. "
                       "커밋하거나 보관(stash)한 뒤 다시 시도하세요.")

    rc, _, _ = git(["fetch", "--quiet"])
    if rc != 0:
        return (False, "원격에서 가져오지 못했습니다. 네트워크 연결을 확인하세요.")

    rc, out, _ = git(["rev-list", "--count", "HEAD..@{u}"])
    if rc != 0:
        return (False, "업스트림 브랜치가 없어 업데이트할 수 없습니다.")
    if out.strip() in ("", "0"):
        return (False, "이미 최신 버전입니다.")

    rc, _, _ = git(["merge", "--ff-only", "@{u}"])
    if rc != 0:
        return (False, "fast-forward가 불가능합니다(로컬 커밋 존재). 수동 업데이트가 필요합니다.")
    return (True, "최신 버전으로 업데이트했습니다. 재시작합니다…")


def restart() -> None:  # pragma: no cover -- replaces the process image
    """Re-exec the launcher so the running (old-code) process is replaced by a
    fresh one on the new code. Called only after a successful update."""
    launcher = repo_dir() / "bin" / "cc-navigator"
    os.execv(str(launcher), [str(launcher)] + sys.argv[1:])
