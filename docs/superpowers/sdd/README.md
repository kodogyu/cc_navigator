# Subagent-driven development workspace

These files were the working state of the implementation. They lived in a
git-ignored scratch directory and were moved here so the work survives a change
of machine.

- `implementation-log.md` — the ledger. One entry per task: what was built, which
  mutations survived, which defects were found, and every decision that is not
  obvious from the code. **Read this before writing any new code.**
- `task-N-brief.md` — what the implementer for task N was told to do. Each one
  restates the global constraints, names the mutations to run, and lists the
  investigations whose answers were needed. Use them as the template for tasks 11
  and 12.
- `task-N-report.md` — what the implementer found. The mutation tables and the
  real-tmux / real-desktop evidence live here.

The briefs are not neutral copies of the plan. Each one carries forward the
mistakes of the previous tasks as calibration notes, and several deliberately
predict which mutation will survive so the implementer has to check rather than
agree. That pattern found a real hole in the plan's tests six times out of ten.

One caveat on the reports: they are the implementers' own accounts, and two of
them were wrong in ways the controller caught only by re-running the experiment.
Task 6's report originally claimed tmux 3.0a segfaults on `send-keys -l`; it does
not. Task 9's report claims a `wmctrl` check was run, and `wmctrl` is not
installed on that machine. Treat a report as evidence to verify, not as fact.
That is the whole thesis of this project.
