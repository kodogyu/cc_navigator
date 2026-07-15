import json
import pathlib
import sqlite3
import tempfile
import unittest

from ccnav import vscodestate


class VscodeSessionVisibilityTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name) / "workspaceStorage"
        self.workspace = self.root / "abc"
        self.workspace.mkdir(parents=True)
        self.cwd = "/data/projects/Interactive PRISM"
        (self.workspace / "workspace.json").write_text(json.dumps({
            "folder": "file:///data/projects/Interactive%20PRISM",
        }))
        self.database = self.workspace / "state.vscdb"
        with sqlite3.connect(str(self.database)) as connection:
            connection.execute("CREATE TABLE ItemTable (key TEXT, value BLOB)")

    def _put(self, key, value):
        with sqlite3.connect(str(self.database)) as connection:
            connection.execute("DELETE FROM ItemTable WHERE key = ?", (key,))
            connection.execute(
                "INSERT INTO ItemTable (key, value) VALUES (?, ?)", (key, value))

    def _closed_sidebar(self, session_id="v1"):
        self._put(vscodestate.EDITOR_STATE_KEY, "{}")
        self._put(vscodestate.SIDEBAR_MEMENTO_KEY, json.dumps({
            "webviewState": json.dumps({"sessionID": session_id}),
        }))
        self._put(vscodestate.SIDEBAR_STATE_KEY, json.dumps({
            "claudeVSCodeSidebarSecondary": {
                "collapsed": False, "isHidden": True,
            },
        }))
        self._put(vscodestate.AUXILIARY_HIDDEN_KEY, "true")
        self._put(
            vscodestate.AUXILIARY_ACTIVE_KEY,
            "workbench.view.extension.codexSecondaryViewContainer")

    def _visible(self, session_id="v1"):
        return vscodestate.session_visible(
            session_id, self.cwd, roots=[self.root])

    def test_hidden_sidebar_and_no_editor_means_closed(self):
        self._closed_sidebar()
        self.assertFalse(self._visible())

    def test_a_matching_open_claude_editor_means_visible(self):
        self._closed_sidebar()
        # The production query returns only a boolean from this opaque editor
        # memento; this fixture pins the two exact membership requirements.
        self._put(vscodestate.EDITOR_STATE_KEY, json.dumps({
            "viewType": "claudeVSCodePanel",
            "extensionId": "Anthropic.claude-code",
            "state": {"sessionID": "v1"},
        }))
        self.assertTrue(self._visible())

    def test_visible_active_claude_sidebar_means_visible(self):
        self._closed_sidebar()
        self._put(vscodestate.SIDEBAR_STATE_KEY, json.dumps({
            "claudeVSCodeSidebarSecondary": {
                "collapsed": False, "isHidden": False,
            },
        }))
        self._put(vscodestate.AUXILIARY_HIDDEN_KEY, "false")
        self._put(
            vscodestate.AUXILIARY_ACTIVE_KEY,
            vscodestate.CLAUDE_SIDEBAR_CONTAINER)
        self.assertTrue(self._visible())

    def test_a_different_active_sidebar_container_is_not_visible(self):
        self._closed_sidebar()
        self._put(vscodestate.SIDEBAR_STATE_KEY, json.dumps({
            "claudeVSCodeSidebarSecondary": {"isHidden": False},
        }))
        self._put(vscodestate.AUXILIARY_HIDDEN_KEY, "false")
        self.assertFalse(self._visible())

    def test_session_absent_from_both_surfaces_is_closed(self):
        self._closed_sidebar(session_id="other")
        self.assertFalse(self._visible("v1"))

    def test_missing_workspace_or_schema_is_unknown(self):
        self.assertIsNone(vscodestate.session_visible(
            "v1", "/different", roots=[self.root]))
        self.assertIsNone(self._visible())  # empty/unrecognised database
        self._put(vscodestate.SIDEBAR_MEMENTO_KEY, json.dumps({
            "webviewState": json.dumps({"sessionID": "v1"}),
        }))
        self.assertIsNone(self._visible())

    def test_non_file_remote_workspace_is_not_matched(self):
        (self.workspace / "workspace.json").write_text(json.dumps({
            "folder": "vscode-remote://ssh-remote+host/data/projects/Interactive_PRISM",
        }))
        self.assertIsNone(self._visible())

    def test_editor_membership_requires_both_panel_type_and_session(self):
        self._closed_sidebar(session_id="other")
        self._put(vscodestate.EDITOR_STATE_KEY, json.dumps({
            "viewType": "unrelatedPanel", "extensionId": "unrelated.extension",
            "state": {"sessionID": "v1"},
        }))
        self.assertFalse(self._visible("v1"))
