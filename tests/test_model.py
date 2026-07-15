import unittest

from ccnav import hookstate, model

SOCK = "/tmp/tmux-1000/default"


class StaleWorkingTest(unittest.TestCase):
    def _row(self, state, updated_at):
        return {
            "session_id": "s", "cwd": "/p", "tmux_socket": SOCK, "tmux_pane": "%1",
            "state": state, "reason": "", "message": "", "updated_at": updated_at,
        }

    def test_a_long_untouched_working_row_reads_as_idle(self):
        rec = self._row(hookstate.WORKING, updated_at=0)
        rows = model.build_rows([rec], {SOCK: {"%1": "demo"}}, {SOCK: {}},
                                now=10_000, stale_seconds=900)
        self.assertEqual(rows[0].state, hookstate.WAITING)
        self.assertEqual(rows[0].reason, hookstate.STOP_IDLE)  # green/reported

    def test_a_recently_updated_working_row_stays_working(self):
        rec = self._row(hookstate.WORKING, updated_at=9_950)
        rows = model.build_rows([rec], {SOCK: {"%1": "demo"}}, {SOCK: {}},
                                now=10_000, stale_seconds=900)
        self.assertEqual(rows[0].state, hookstate.WORKING)

    def test_a_live_claude_title_spinner_overrides_the_stale_timestamp(self):
        for frame in model.CLAUDE_TITLE_SPINNER_FRAMES:
            rec = self._row(hookstate.WORKING, updated_at=0)
            rows = model.build_rows(
                [rec], {SOCK: {"%1": "demo"}},
                {SOCK: {"%1": frame + " long task"}},
                now=10_000, stale_seconds=900,
            )
            self.assertEqual(rows[0].state, hookstate.WORKING, frame)

    def test_a_live_codex_title_spinner_overrides_the_stale_timestamp(self):
        rec = dict(
            self._row(hookstate.WORKING, updated_at=0), provider="codex")
        rows = model.build_rows(
            [rec], {SOCK: {"%1": "demo"}},
            {SOCK: {"%1": "⠸ long task"}},
            now=10_000, stale_seconds=900,
        )
        self.assertEqual(rows[0].state, hookstate.WORKING)

    def test_a_spinner_title_does_not_promote_an_idle_hook_state(self):
        rec = self._row(hookstate.WAITING, updated_at=0)
        rec["reason"] = hookstate.STOP_IDLE
        rows = model.build_rows(
            [rec], {SOCK: {"%1": "demo"}},
            {SOCK: {"%1": "⠂ old title"}},
            now=10_000, stale_seconds=900,
        )
        self.assertEqual(rows[0].state, hookstate.WAITING)
        self.assertEqual(rows[0].reason, hookstate.STOP_IDLE)

    def test_without_now_staleness_is_not_applied(self):
        rec = self._row(hookstate.WORKING, updated_at=0)
        rows = model.build_rows([rec], {SOCK: {"%1": "demo"}}, {SOCK: {}})
        self.assertEqual(rows[0].state, hookstate.WORKING)


def record(session_id, pane, state=hookstate.WAITING, updated_at=100, socket=SOCK):
    return {
        "session_id": session_id,
        "cwd": "/data/projects/demo_project",
        "tmux_socket": socket,
        "tmux_pane": pane,
        "state": state,
        "reason": "idle",
        "message": "",
        "updated_at": updated_at,
    }


class BuildRowsTest(unittest.TestCase):
    def test_row_carries_the_tmux_session_and_title(self):
        rows = model.build_rows(
            [record("a", "%1")],
            {SOCK: {"%1": "demo"}},
            {SOCK: {"%1": "✳ 작업 중"}},
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].tmux_session, "demo")
        self.assertEqual(rows[0].title, "✳ 작업 중")
        self.assertEqual(rows[0].window_title, "ccnav:demo")
        self.assertTrue(rows[0].waiting)
        self.assertEqual(rows[0].provider, "claude")

    def test_codex_provider_is_carried_to_the_row(self):
        rec = dict(record("a", "%1"), provider="codex")
        rows = model.build_rows([rec], {SOCK: {"%1": "demo"}}, {SOCK: {}})
        self.assertEqual(rows[0].provider, "codex")

    def test_legacy_codex_permission_record_is_not_shown_as_user_input(self):
        for reason in ("permission", "permission_request", "PermissionRequest"):
            rec = dict(
                record("a", "%1"), provider="codex", state=hookstate.WAITING,
                reason=reason, message="permission")
            rows = model.build_rows([rec], {SOCK: {"%1": "demo"}}, {SOCK: {}})
            self.assertEqual(rows[0].state, hookstate.WORKING)
            self.assertEqual(rows[0].reason, "")
            self.assertEqual(rows[0].message, "")

    def test_claude_permission_record_still_requires_user_input(self):
        rec = dict(
            record("a", "%1"), provider="claude", state=hookstate.WAITING,
            reason="permission", message="permission")
        rows = model.build_rows([rec], {SOCK: {"%1": "demo"}}, {SOCK: {}})
        self.assertEqual(rows[0].state, hookstate.WAITING)
        self.assertEqual(rows[0].reason, "permission")

    def test_old_claude_agent_attention_record_is_not_shown_red(self):
        rec = dict(
            record("a", "%1"), provider="claude", state=hookstate.WAITING,
            reason="agent_needs_input", message="a teammate asks something")
        rows = model.build_rows([rec], {SOCK: {"%1": "demo"}}, {SOCK: {}})
        self.assertEqual(rows[0].state, hookstate.WAITING)
        self.assertEqual(rows[0].reason, hookstate.STOP_IDLE)
        self.assertEqual(rows[0].message, "")

    def test_claude_agent_attention_with_live_title_spinner_is_working(self):
        for frame in model.CLAUDE_TITLE_SPINNER_FRAMES:
            rec = dict(
                record("a", "%1"), provider="claude", state=hookstate.WAITING,
                reason=hookstate.AGENT_NEEDS_INPUT,
                message="a teammate asks something")
            rows = model.build_rows(
                [rec], {SOCK: {"%1": "demo"}},
                {SOCK: {"%1": frame + " active task"}},
            )
            self.assertEqual(rows[0].state, hookstate.WORKING, frame)
            self.assertEqual(rows[0].reason, "", frame)
            self.assertEqual(rows[0].message, "", frame)

    def test_provisional_is_strictly_carried_to_the_row(self):
        provisional = dict(record("a", "%1"), provider="codex", provisional=True)
        rows = model.build_rows([provisional], {SOCK: {"%1": "demo"}}, {SOCK: {}})
        self.assertTrue(rows[0].provisional)
        not_boolean = dict(record("b", "%2"), provisional="true")
        rows = model.build_rows([not_boolean], {SOCK: {"%2": "demo"}}, {SOCK: {}})
        self.assertFalse(rows[0].provisional)

    def test_record_whose_pane_is_gone_produces_no_row(self):
        rows = model.build_rows([record("a", "%9")], {SOCK: {"%1": "demo"}}, {SOCK: {}})
        self.assertEqual(rows, [])

    def test_missing_title_falls_back_to_the_pane_id(self):
        rows = model.build_rows([record("a", "%1")], {SOCK: {"%1": "demo"}}, {SOCK: {}})
        self.assertEqual(rows[0].title, "%1")

    def test_two_records_on_one_pane_keep_the_newest(self):
        rows = model.build_rows(
            [record("old", "%1", updated_at=1), record("new", "%1", updated_at=2)],
            {SOCK: {"%1": "demo"}},
            {SOCK: {"%1": "t"}},
        )
        self.assertEqual([r.session_id for r in rows], ["new"])

    def test_same_pane_id_on_different_sockets_are_distinct_rows(self):
        other = "/tmp/tmux-1000/other"
        rows = model.build_rows(
            [record("a", "%1"), record("b", "%1", socket=other)],
            {SOCK: {"%1": "demo"}, other: {"%1": "sandbox"}},
            {SOCK: {"%1": "t1"}, other: {"%1": "t2"}},
        )
        self.assertEqual(sorted(r.session_id for r in rows), ["a", "b"])

    def test_waiting_rows_sort_first_then_newest_first(self):
        rows = model.build_rows(
            [
                record("w-old", "%1", updated_at=1),
                record("working", "%2", state=hookstate.WORKING, updated_at=50),
                record("w-new", "%3", updated_at=9),
            ],
            {SOCK: {"%1": "a", "%2": "b", "%3": "c"}},
            {SOCK: {}},
        )
        self.assertEqual([r.session_id for r in rows], ["w-new", "w-old", "working"])

    def test_subagent_ids_populate_the_active_flag(self):
        rec = dict(record("a", "%1", state=hookstate.WORKING), subagent_ids=["s1", "s2"])
        rows = model.build_rows([rec], {SOCK: {"%1": "demo"}}, {SOCK: {}})
        self.assertEqual(rows[0].subagent_ids, ("s1", "s2"))
        self.assertTrue(rows[0].subagent_active)

    def test_missing_or_garbage_subagent_ids_mean_not_active(self):
        plain = model.build_rows([record("a", "%1")], {SOCK: {"%1": "demo"}}, {SOCK: {}})
        self.assertEqual(plain[0].subagent_ids, ())
        self.assertFalse(plain[0].subagent_active)
        junk = dict(record("b", "%2"), subagent_ids="not-a-list")
        rows = model.build_rows([junk], {SOCK: {"%2": "demo"}}, {SOCK: {}})
        self.assertFalse(rows[0].subagent_active)

    def test_only_kernel_verified_background_processes_are_active(self):
        rec = dict(
            record("a", "%1", state=hookstate.WORKING),
            background_process_ids=["42:900", "43:901"],
        )
        rows = model.build_rows(
            [rec], {SOCK: {"%1": "demo"}}, {SOCK: {}},
            live_background_ids={"43:901"},
        )
        self.assertEqual(rows[0].background_process_ids, ("43:901",))
        self.assertTrue(rows[0].background_process_active)
        self.assertTrue(rows[0].auxiliary_activity)

    def test_garbage_or_dead_background_processes_are_inactive(self):
        rec = dict(record("a", "%1"), background_process_ids="42:900")
        rows = model.build_rows(
            [rec], {SOCK: {"%1": "demo"}}, {SOCK: {}},
            live_background_ids=set(),
        )
        self.assertFalse(rows[0].background_process_active)
        self.assertFalse(rows[0].auxiliary_activity)

    def test_claude_shell_and_monitor_tasks_are_auxiliary_activity(self):
        rec = dict(
            record("a", "%1", state=hookstate.WAITING), provider="claude",
            background_task_ids=["shell:b123", "monitor:m456"],
        )
        rows = model.build_rows(
            [rec], {SOCK: {"%1": "demo"}}, {SOCK: {}})
        self.assertEqual(
            rows[0].background_task_ids, ("shell:b123", "monitor:m456"))
        self.assertTrue(rows[0].background_task_active)
        self.assertTrue(rows[0].auxiliary_activity)

    def test_a_positively_inactive_pane_drops_only_stale_shell_tasks(self):
        rec = dict(
            record("a", "%1", state=hookstate.WAITING), provider="claude",
            background_task_ids=["shell:ended", "monitor:watching"],
        )
        rows = model.build_rows(
            [rec], {SOCK: {"%1": "demo"}}, {SOCK: {}},
            inactive_background_shell_panes={(SOCK, "%1")},
        )
        self.assertEqual(rows[0].background_task_ids, ("monitor:watching",))
        self.assertTrue(rows[0].background_task_active)

    def test_invalid_background_task_ids_are_ignored(self):
        rec = dict(record("a", "%1"), background_task_ids=[
            "workflow:w1", "shell:", "monitor:bad id", "shell:b1", 42,
        ])
        rows = model.build_rows(
            [rec], {SOCK: {"%1": "demo"}}, {SOCK: {}})
        self.assertEqual(rows[0].background_task_ids, ("shell:b1",))

    def test_records_without_socket_or_pane_are_dropped(self):
        bad = {"session_id": "x", "tmux_socket": "", "tmux_pane": "", "updated_at": 1}
        self.assertEqual(model.build_rows([bad], {}, {}), [])

    def test_a_record_without_a_socket_cannot_acquire_a_window_address(self):
        # Pins the empty socket/pane guard in _newest_per_pane specifically.
        # test_records_without_socket_or_pane_are_dropped above passes even
        # without that guard, because build_rows's `pane not in sessions`
        # guard catches the blank key first (sessions_by_socket is {}). This
        # test steers a live socket keyed "" into place so that second guard
        # would let the garbage through: without the _newest_per_pane guard
        # the record joins against {"": "ghost-session"} and its window_title
        # becomes "ccnav:ghost-session" -- a corrupted/hand-edited state file
        # naming a real window that Task 7 would then activate. Both tests are
        # deliberate; neither is a duplicate of the other. Do not delete either.
        bad = {"session_id": "x", "tmux_socket": "", "tmux_pane": "", "updated_at": 1}
        rows = model.build_rows([bad], {"": {"": "ghost-session"}}, {"": {"": ""}})
        self.assertEqual(rows, [])

    def test_present_but_empty_title_falls_back_to_the_pane_id(self):
        # titles.get(pane) or pane vs titles.get(pane, pane): they differ only
        # when the title is present but empty. tmux can return "", and the UI's
        # primary line must not be blank.
        rows = model.build_rows(
            [record("a", "%1")], {SOCK: {"%1": "demo"}}, {SOCK: {"%1": ""}}
        )
        self.assertEqual(rows[0].title, "%1")

    def test_unparseable_updated_at_coerces_to_zero_and_never_raises(self):
        # build_rows runs on a one-second GTK timer; a raise here freezes the
        # model for the life of the process. A hand-edited state file must not
        # be able to do that. Bad timestamp -> 0 (maximally stale), matching
        # statestore.prune's policy.
        for value in ("abc", None):
            rec = record("a", "%1", updated_at=value)
            rows = model.build_rows([rec], {SOCK: {"%1": "demo"}}, {SOCK: {}})
            self.assertEqual(rows[0].updated_at, 0)

    def test_absent_updated_at_coerces_to_zero(self):
        rec = record("a", "%1")
        del rec["updated_at"]
        rows = model.build_rows([rec], {SOCK: {"%1": "demo"}}, {SOCK: {}})
        self.assertEqual(rows[0].updated_at, 0)

    def test_numeric_string_updated_at_is_still_honoured(self):
        # The coercion must not over-narrow: int("100") succeeds, so a numeric
        # string timestamp must survive as 100, not be flattened to 0.
        rows = model.build_rows(
            [record("a", "%1", updated_at="100")], {SOCK: {"%1": "demo"}}, {SOCK: {}}
        )
        self.assertEqual(rows[0].updated_at, 100)


class LastPromptRowTest(unittest.TestCase):
    def test_build_rows_carries_last_prompt(self):
        records = [{"session_id": "s", "tmux_socket": "/x", "tmux_pane": "%1",
                    "state": "working", "updated_at": 1, "last_prompt": "do X"}]
        rows = model.build_rows(records, {"/x": {"%1": "sess"}}, {"/x": {"%1": "t"}})
        self.assertEqual(rows[0].last_prompt, "do X")


class LivePaneKeysTest(unittest.TestCase):
    def test_flattens_sockets_and_panes(self):
        keys = model.live_pane_keys({SOCK: {"%1": "a", "%2": "b"}})
        self.assertEqual(keys, {(SOCK, "%1"), (SOCK, "%2")})

    def test_empty(self):
        self.assertEqual(model.live_pane_keys({}), set())


class SectioningTest(unittest.TestCase):
    def _row(self, **kw):
        base = dict(session_id="s", socket="/x", pane="%1", tmux_session="d",
                    title="t", state=hookstate.WAITING, reason="permission_prompt",
                    message="", cwd="/home/u/projects/cc_navigator", updated_at=1,
                    last_prompt="")
        base.update(kw)
        return model.Row(**base)

    def test_status_key_maps_the_three_sections(self):
        self.assertEqual(
            model.status_key(self._row(state=hookstate.WORKING)), model.WORKING_SECTION)
        self.assertEqual(
            model.status_key(self._row(state=hookstate.WAITING, reason="idle")),
            model.REPORTED)
        self.assertEqual(
            model.status_key(self._row(state=hookstate.WAITING, reason="permission_prompt")),
            model.INPUT_NEEDED)

    def test_group_key_is_the_cwd(self):
        self.assertEqual(model.group_key(self._row(cwd="/a/b")), "/a/b")
        self.assertEqual(model.group_key(self._row(cwd="")), "")

    def test_group_label_is_the_last_path_segment(self):
        self.assertEqual(model.group_label("/home/u/projects/cc_navigator"), "cc_navigator")
        self.assertEqual(model.group_label("/home/u/projects/cc_navigator/"), "cc_navigator")
        self.assertEqual(model.group_label("proj"), "proj")
        self.assertEqual(model.group_label("/"), "~")
        self.assertEqual(model.group_label(""), "~")
