from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from studio_io import INDEX_PATH, Project, StudioError, canonical_bytes, require_hash, sha_bytes


class IOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "中文 studio"
        self.root.mkdir()
        (self.root / "开始这里.md").write_text("# 开始这里\n> workflowStudio: studio-v0.9\n", encoding="utf-8")
        self.caps = {"schema": "xiaomo.studio-capabilities/v1", "workflowStudio": "studio-v0.9", "runtime_schema": "xiaomo.studio-runtime/v1", "candidate_design": True, "workset_runtime": True, "adoption_transactions": True, "media_execution": False, "test_mode": True}
        self.write_caps()
        self.p = Project(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def write_caps(self):
        (self.root / "工作室能力.json").write_bytes(canonical_bytes(self.caps))

    def test_read_only_operations_create_nothing(self):
        self.assertEqual(self.p.catalogue()["revision"], 0)
        self.assertIsNone(self.p.hash("不存在.md"))
        self.assertEqual(self.p.read_json("missing.json", {"fallback": 1}), {"fallback": 1})
        self.assertFalse((self.root / ".工作室").exists())

    def test_legacy_and_false_capabilities_fail_without_writes(self):
        self.caps["workset_runtime"] = False
        self.write_caps()
        with self.assertRaises(StudioError):
            Project(self.root)
        (self.root / "开始这里.md").write_text("# 开始这里\n", encoding="utf-8")
        with self.assertRaises(StudioError):
            Project(self.root)
        self.assertFalse((self.root / ".工作室").exists())

    def test_string_boolean_and_wrong_schema_are_rejected(self):
        self.caps["workset_runtime"] = "true"
        self.write_caps()
        with self.assertRaises(StudioError):
            Project(self.root)
        self.caps["workset_runtime"] = True
        self.caps["runtime_schema"] = "future"
        self.write_caps()
        with self.assertRaises(StudioError):
            Project(self.root)

    def test_v1_release_accepts_existing_protocol_without_enabling_media(self):
        (self.root / "开始这里.md").write_text("# 开始这里\n> workflowStudio: studio-v0.9\n> workflowRelease: 1.0\n", encoding="utf-8")
        p = Project(self.root, adoption=True)
        self.assertFalse(p.capabilities["media_execution"])
        self.assertFalse((self.root / ".工作室").exists())

    def test_unknown_empty_and_duplicate_release_markers_fail_without_writes(self):
        for marker in ("workflowRelease: 2.0", "workflowRelease:", "workflowRelease: 1.0\n> workflowRelease: 1.0", "workflowRelease: 1.0\n> workflowRelease: 0.9"):
            with self.subTest(marker=marker):
                (self.root / "开始这里.md").write_text("# 开始这里\n> workflowStudio: studio-v0.9\n> " + marker + "\n", encoding="utf-8")
                with self.assertRaises(StudioError) as caught:
                    Project(self.root)
                self.assertEqual(caught.exception.code, "unsupported_release")
                self.assertFalse((self.root / ".工作室").exists())

    def test_v1_release_does_not_override_capability_or_protocol_guards(self):
        (self.root / "开始这里.md").write_text("# 开始这里\n> workflowStudio: studio-v0.9\n> workflowRelease: 1.0\n", encoding="utf-8")
        self.caps["media_execution"] = True
        self.write_caps()
        with self.assertRaises(StudioError) as caught:
            Project(self.root)
        self.assertEqual(caught.exception.code, "unsupported_capability")
        (self.root / "开始这里.md").write_text("# 开始这里\n> workflowRelease: 1.0\n", encoding="utf-8")
        with self.assertRaises(StudioError) as caught:
            Project(self.root)
        self.assertEqual(caught.exception.code, "unsupported_project")
        self.assertFalse((self.root / ".工作室").exists())

    def test_path_escape_ads_devices_and_ambiguous_paths(self):
        for value in ("../outside.txt", "/tmp/out", "C:/outside.md", r"\\host\share\x", "资产设计/NUL.md", "资产设计/a.md:stream", "资产设计/../x", "资产设计//x", "资产设计/a."):
            with self.subTest(value=value), self.assertRaises(StudioError):
                self.p.atomic_bytes(value, b"bad")
        self.assertFalse((self.root / "资产设计").exists())

    def test_unicode_paths_and_exact_bytes(self):
        relative = "资产设计/人物'甲 [白].md"
        raw = "衣物\r\n第二行\n".encode("utf-8")
        self.p.atomic_bytes(relative, raw, expected=None)
        self.assertEqual(self.p.read_bytes(relative), raw)
        self.assertEqual(self.p.hash(relative), sha_bytes(raw))
        self.assertEqual(require_hash(sha_bytes(raw).upper(), "hash"), sha_bytes(raw))

    def test_invalid_unicode_is_structured_and_does_not_write(self):
        with self.assertRaises(StudioError):
            canonical_bytes({"bad": "\ud800"})
        with self.assertRaises(StudioError):
            self.p.path("资产设计/\ud800.md")
        self.assertFalse((self.root / "资产设计").exists())

    def test_compare_and_swap_preserves_existing_file(self):
        self.p.atomic_bytes("资产设计/a.md", b"user", expected=None)
        with self.assertRaises(StudioError) as caught:
            self.p.atomic_bytes("资产设计/a.md", b"replacement", expected=sha_bytes(b"old"))
        self.assertEqual(caught.exception.code, "file_conflict")
        self.assertEqual(self.p.read_bytes("资产设计/a.md"), b"user")
        self.assertFalse(list(self.root.rglob("*.tmp")))


    def test_edit_after_last_hash_is_not_overwritten(self):
        from unittest import mock
        relative = "资产设计/race.md"
        self.p.atomic_bytes(relative, b"old", expected=None)
        original_hash = self.p.hash
        injected = False
        def racing_hash(path):
            nonlocal injected
            value = original_hash(path)
            if path == relative and not injected:
                injected = True
                self.p.path(relative).write_bytes(b"new editor text")
            return value
        with mock.patch.object(self.p, "hash", side_effect=racing_hash):
            with self.assertRaises(StudioError) as caught:
                self.p.atomic_bytes(relative, b"runtime text", expected=sha_bytes(b"old"))
        self.assertEqual(caught.exception.code, "file_conflict")
        self.assertEqual(self.p.read_bytes(relative), b"new editor text")

    def test_creation_race_never_clobbers_new_user_file(self):
        from unittest import mock
        relative = "资产设计/new-race.md"
        original_hash = self.p.hash
        injected = False
        def racing_hash(path):
            nonlocal injected
            value = original_hash(path)
            if path == relative and not injected:
                injected = True
                self.p.path(relative).write_bytes(b"user created")
            return value
        with mock.patch.object(self.p, "hash", side_effect=racing_hash):
            with self.assertRaises(StudioError):
                self.p.atomic_bytes(relative, b"runtime created", expected=None)
        self.assertEqual(self.p.read_bytes(relative), b"user created")

    @unittest.skipUnless(os.name == "nt", "Windows mandatory sharing")
    def test_checked_handle_blocks_external_writer(self):
        from unittest import mock
        relative = "资产设计/guarded.md"
        self.p.atomic_bytes(relative, b"old", expected=None)
        attempts = []
        def hook(name):
            if name == "projection_after_guard":
                code = "from pathlib import Path;import sys;Path(sys.argv[1]).write_bytes(b'editor')"
                writer = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", code, str(self.p.path(relative))], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                attempts.append(writer.returncode)
        with mock.patch.object(self.p, "failpoint", side_effect=hook):
            self.p.atomic_bytes(relative, b"new", expected=sha_bytes(b"old"))
        self.assertTrue(attempts and attempts[0] != 0)
        self.assertEqual(self.p.read_bytes(relative), b"new")
        originals = list((self.root / ".工作室/恢复备份").glob("*.original"))
        self.assertTrue(any(path.read_bytes() == b"old" for path in originals))

    @unittest.skipUnless(os.name == "nt", "Windows mandatory sharing")
    def test_editor_recreates_name_after_preserve(self):
        from unittest import mock
        relative = "资产设计/recreated.md"
        self.p.atomic_bytes(relative, b"old", expected=None)
        def hook(name):
            if name == "projection_after_preserve":
                self.p.path(relative).write_bytes(b"new editor name")
        with mock.patch.object(self.p, "failpoint", side_effect=hook):
            with self.assertRaises(StudioError) as caught:
                self.p.atomic_bytes(relative, b"runtime", expected=sha_bytes(b"old"))
        self.assertEqual(caught.exception.code, "file_conflict")
        self.assertEqual(self.p.read_bytes(relative), b"new editor name")
        self.assertEqual(self.p.read_bytes(caught.exception.details["preserved_path"]), b"old")

    @unittest.skipUnless(os.name == "nt", "Windows mandatory sharing")
    def test_crash_after_preserve_keeps_original(self):
        relative = "资产设计/crash.md"
        self.p.atomic_bytes(relative, b"old", expected=None)
        code = "import sys;sys.path.insert(0,sys.argv[1]);from studio_io import Project,sha_bytes;p=Project(sys.argv[2]);p.atomic_bytes('资产设计/crash.md',b'new',expected=sha_bytes(b'old'))"
        env = os.environ.copy()
        env["XIAOMO_STUDIO_CRASHPOINT"] = "projection_after_preserve"
        child = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", code, str(SCRIPTS), str(self.root)], env=env, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(child.returncode, 97, child.stderr)
        self.assertFalse(self.p.path(relative).exists())
        self.assertTrue(any(path.read_bytes() == b"old" for path in (self.root / ".工作室/恢复备份").glob("*.original")))
        self.p.atomic_bytes(relative, b"new", expected=None)
        self.assertEqual(self.p.read_bytes(relative), b"new")

    def test_immutable_content_cannot_be_changed(self):
        self.p.immutable(".工作室/事务/t1/content.md", b"one")
        self.p.immutable(".工作室/事务/t1/content.md", b"one")
        with self.assertRaises(StudioError):
            self.p.immutable(".工作室/事务/t1/content.md", b"two")
        self.assertEqual(self.p.read_bytes(".工作室/事务/t1/content.md"), b"one")

    def test_owner_white_lists_exclude_controls_media_and_shared_c(self):
        for owner, path in (("A", "A_总控.md"), ("B", "剧本/场景.md"), ("C1", "资产设计/A01.md"), ("C2", "镜头规划/V01.md"), ("D", "后期方案/CUT01.md"), ("E", "发布包/PUB01.json")):
            self.assertTrue(self.p.owner_allowed(owner, path))
        for owner, path in (("C1", "B_故事剧本.md"), ("C2", "提示词卡/资产/a.md"), ("C1", "C_视觉生成.md"), ("C2", "AGENTS.md"), ("C2", "assets/视频/a.mp4"), ("B", "剧本/a.docx"), ("A", "工作室能力.json")):
            self.assertFalse(self.p.owner_allowed(owner, path))

    def prepared_catalogue(self):
        raw = b"new text"
        snapshot = ".工作室/事务/t1/content.md"
        manifest = ".工作室/事务/t1/manifest.json"
        self.p.immutable(snapshot, raw)
        manifest_bytes = canonical_bytes({"transaction_id": "t1", "content": snapshot})
        self.p.immutable(manifest, manifest_bytes)
        index = self.p.catalogue()
        index["revision"] = 1
        index["objects"]["资产设计/A01.md"] = {"owner": "C1", "version": 1, "sha256": sha_bytes(raw), "snapshot": snapshot, "transaction_id": "t1"}
        index["transactions"]["t1"] = {"request_hash": sha_bytes(b"request"), "manifest_path": manifest, "manifest_sha256": sha_bytes(manifest_bytes), "workset_key": "character"}
        index["ids"]["A01"] = "资产设计/A01.md"
        return index

    def test_publication_requires_all_snapshots_and_manifest(self):
        index = self.prepared_catalogue()
        self.p.path(".工作室/事务/t1/content.md").unlink()
        with self.assertRaises(StudioError):
            self.p.publish_catalogue(index, 0)
        self.assertFalse(self.p.path(INDEX_PATH).exists())

    def test_publication_is_atomic_cas_and_keeps_history(self):
        index = self.prepared_catalogue()
        with self.p.lock("catalogue"):
            self.p.publish_catalogue(index, 0)
        self.assertEqual(self.p.catalogue()["objects"], index["objects"])
        with self.assertRaises(StudioError):
            self.p.publish_catalogue(index, 0)
        broken = copy.deepcopy(index)
        broken["revision"] = 2
        broken["objects"] = {}
        broken["ids"] = {}
        with self.assertRaises(StudioError):
            self.p.publish_catalogue(broken, 1)
        self.assertEqual(self.p.catalogue()["revision"], 1)

    def test_case_alias_catalogue_and_invalid_ids_are_rejected(self):
        index = self.prepared_catalogue()
        index["objects"]["资产设计/a01.MD"] = dict(index["objects"]["资产设计/A01.md"])
        with self.assertRaises(StudioError):
            self.p.publish_catalogue(index, 0)
        index = self.prepared_catalogue()
        index["ids"]["not-a-film-id"] = "资产设计/A01.md"
        with self.assertRaises(StudioError):
            self.p.publish_catalogue(index, 0)

    def test_dependency_checks_use_selected_snapshots(self):
        self.p.atomic_bytes("reference.txt", b"a", expected=None)
        rows = self.p.check_dependencies([{"path": "reference.txt", "sha256": sha_bytes(b"a")}])
        self.assertEqual(rows[0]["status"], "unchanged")
        self.p.atomic_bytes("reference.txt", b"b", expected=sha_bytes(b"a"))
        self.assertEqual(self.p.check_dependencies([{"path": "reference.txt", "sha256": sha_bytes(b"a")}])[0]["status"], "changed")
        index = self.prepared_catalogue()
        self.p.publish_catalogue(index, 0)
        dep = {"kind": "adopted", "path": "资产设计/A01.md", "sha256": sha_bytes(b"new text"), "version": 1}
        self.assertEqual(self.p.check_dependencies([dep])[0]["status"], "unchanged")
        self.p.path(".工作室/事务/t1/content.md").write_bytes(b"corrupt")
        self.assertEqual(self.p.check_dependencies([dep])[0]["status"], "missing")

    def test_process_lock_releases_after_kill(self):
        marker = Path(self.temp.name) / "locked.txt"
        code = "import sys,time;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from studio_io import Project;p=Project(sys.argv[2]);\nwith p.lock('test-shared'):\n Path(sys.argv[3]).write_text('yes');time.sleep(30)"
        child = subprocess.Popen([sys.executable, "-B", "-X", "utf8", "-c", code, str(SCRIPTS), str(self.root), str(marker)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(marker.exists(), "Child never acquired test lock")
            with self.assertRaises(StudioError) as caught:
                with self.p.lock("test-shared", timeout=0.05):
                    pass
            self.assertEqual(caught.exception.code, "lock_busy")
            child.kill()
            child.wait(timeout=5)
            with self.p.lock("test-shared", timeout=0.3):
                pass
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    def test_real_directory_reparse_point_is_rejected(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        link = self.root / "linked"
        if os.name == "nt":
            import base64
            command = "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path '" + str(link).replace("'", "''") + "' -Target '" + str(outside).replace("'", "''") + "' | Out-Null"
            encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
            shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            run = subprocess.run([str(shell), "-NoProfile", "-EncodedCommand", encoded], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", errors="replace"))
        else:
            link.symlink_to(outside, target_is_directory=True)
        try:
            with self.assertRaises(StudioError):
                self.p.atomic_bytes("linked/escaped.txt", b"bad")
            self.assertFalse((outside / "escaped.txt").exists())
        finally:
            if os.name == "nt":
                link.rmdir()
            else:
                link.unlink()


if __name__ == "__main__":
    unittest.main()

