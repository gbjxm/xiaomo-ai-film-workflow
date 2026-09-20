"""Compact document layout integration tests; fixtures contain text only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from studio_io import Project, StudioError, canonical_bytes, sha_bytes
import studio_worksets as ws
import studio_adoption as adoption


class CompactLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="xiaomo-compact-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.entry = "# Isolated text fixture\nworkflowStudio: studio-v0.9\n"
        self.write("开始这里.md", self.entry + "workflowDocsLayout: production-docs-v1\n")
        self.write("工作室能力.json", canonical_bytes({
            "schema": "xiaomo.studio-capabilities/v1", "workflowStudio": "studio-v0.9",
            "runtime_schema": "xiaomo.studio-runtime/v1", "candidate_design": True,
            "workset_runtime": True, "adoption_transactions": True,
            "media_execution": False, "test_mode": True,
        }))
        self.p = Project(self.root, adoption=True)

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = content if isinstance(content, bytes) else content.encode("utf-8")
        path.write_bytes(raw)
        return sha_bytes(raw)

    def session(self, key="one", **extra):
        state = ws.load_state(self.p, key)
        return {"workset_key": key, "holder": state["lease"]["holder"],
                "lease_id": state["lease"]["id"], "expected_revision": state["revision"], **extra}

    def create(self, key="one", department="C2"):
        return ws.create(self.p, {"workset_key": key, "department": department,
                        "title": "完整候选", "holder": "test-" + key})

    def save(self, key="one", content="完整候选正文\n", **extra):
        state = ws.load_state(self.p, key)
        projection = self.p.document_path(f"工作稿/{state['department']}/{key}/当前.md")
        return ws.save_draft(self.p, self.session(key, content=content,
            expected_draft_sha256=self.p.hash(projection), **extra))

    def target(self, relative, owner="C1", content="采用完整正文\n", object_id=None):
        source = self.p.document_path("工作稿/输入/" + sha_bytes(relative.encode())[:12] + ".md")
        raw_hash = self.write(source, content)
        current = self.p.catalogue()["objects"].get(relative)
        value = {"owner": owner, "path": relative, "source": source, "source_sha256": raw_hash,
                 "expected_sha256": current["sha256"] if current else self.p.hash(relative),
                 "expected_version": current["version"] if current else None}
        if object_id:
            value["object_id"] = object_id
        return value

    def request(self, writes, key="one", transaction="tx-one"):
        return self.session(key, transaction_id=transaction,
            draft_sha256=ws.load_state(self.p, key)["draft"]["sha256"],
            adoption_evidence={"text": "采用本次完整稿", "source": "隔离测试决定"}, writes=writes)

    def assert_error(self, code, function, *args):
        with self.assertRaises(StudioError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code, str(caught.exception))

    def assert_no_legacy_runtime_dirs(self):
        self.assertFalse((self.root / "工作稿").exists())
        self.assertFalse((self.root / "续接缓存").exists())
        self.assertFalse((self.root / "90_制作资料/.工作室").exists())

    def test_marker_requires_one_known_value_and_does_not_write(self):
        for marker in ("workflowDocsLayout: unknown\n", "workflowDocsLayout:\n",
                       "  workflowDocsLayout : unknown\n", " \t> workflowDocsLayout \t: \t\n",
                       "workflowDocsLayout: production-docs-v1\n  workflowDocsLayout: future\n",
                       "workflowDocsLayout: production-docs-v1\n> workflowDocsLayout: production-docs-v1\n"):
            with self.subTest(marker=marker):
                self.write("开始这里.md", self.entry + marker)
                before = set(self.root.rglob("*"))
                self.assert_error("unsupported_document_layout", Project, self.root)
                self.assertEqual(before, set(self.root.rglob("*")))
        self.write("开始这里.md", self.entry + "> workflowDocsLayout: production-docs-v1\r\n")
        self.assertEqual(Project(self.root).document_path("剧本/正文.md"), "90_制作资料/剧本/正文.md")

    def test_launcher_marker_whitespace_forms_keep_compact_runtime_paths(self):
        markers = ("  workflowDocsLayout: production-docs-v1\n",
                   "\t> workflowDocsLayout \t: production-docs-v1 \t\r\n")
        for index, marker in enumerate(markers):
            with self.subTest(marker=marker):
                self.write("开始这里.md", self.entry + marker)
                self.p = Project(self.root)
                key = f"whitespace-{index}"
                self.create(key)
                saved = self.save(key)
                self.assertEqual(saved["projection"]["path"], f"90_制作资料/工作稿/C2/{key}/当前.md")
                self.assertEqual(saved["cache"]["path"], f"90_制作资料/续接缓存/{key}.json")
        self.assert_no_legacy_runtime_dirs()

    def test_document_resolution_is_explicit_and_dependencies_remain_physical(self):
        for name in self.p.document_layout["documentDirectories"]:
            self.assertEqual(self.p.document_path(name + "/内容.md"), "90_制作资料/" + name + "/内容.md")
        for relative in ("A_总控.md", ".工作室/state.json", "图片/场景/参考.png", "制作统筹/计划.md"):
            self.assertEqual(self.p.document_path(relative), relative)
        old_hash = self.write("剧本/参考.md", "旧位置原文")
        new_hash = self.write("90_制作资料/剧本/参考.md", "收拢位置原文")
        self.assertEqual(self.p.rel("剧本/参考.md"), "剧本/参考.md")
        self.assertEqual(self.p.path("剧本/参考.md"), self.root / "剧本/参考.md")
        values = self.p.check_dependencies([
            {"path": "剧本/参考.md", "sha256": old_hash},
            {"path": "90_制作资料/剧本/参考.md", "sha256": new_hash},
        ])
        self.assertEqual([value["status"] for value in values], ["unchanged", "unchanged"])

    def test_all_departments_save_inspect_and_rebuild_missing_caches(self):
        for department in ("A", "B", "C1", "C2", "D", "E"):
            with self.subTest(department=department):
                key = department.lower()
                created = self.create(key, department)
                self.assertEqual(created["cache_path"], f"90_制作资料/续接缓存/{key}.json")
                saved = self.save(key)
                draft = saved["draft"]
                self.assertTrue(draft["snapshot"].startswith(f"90_制作资料/工作稿/{department}/{key}/历史/"))
                self.assertEqual(saved["projection"]["path"], f"90_制作资料/工作稿/{department}/{key}/当前.md")
                cache = self.root / saved["cache"]["path"]
                cache.unlink()
                inspected = ws.inspect(self.p, {"workset_key": key})
                self.assertEqual(inspected["cache"]["status"], "missing")
                self.assertEqual(inspected["snapshot"]["status"], "valid")
                self.assertFalse(cache.exists())  # Inspect is read-only.
                rebuilt = self.save(key)
                self.assertEqual(rebuilt["status"], "no_change")
                self.assertEqual(rebuilt["cache"]["status"], "valid")
                self.assertLessEqual(cache.stat().st_size, 6144)
                self.assertTrue((self.root / f".工作室/工作集/{key}/state.json").is_file())
        self.assert_no_legacy_runtime_dirs()

    def test_private_cache_replacement_and_public_draft_guards(self):
        self.create()
        self.save(content="第一版")
        original = self.p._guarded_projection
        with mock.patch.object(self.p, "_guarded_projection", wraps=original) as guarded:
            self.save(content="第二版")
        paths = [call.args[0] for call in guarded.call_args_list]
        self.assertIn("90_制作资料/工作稿/C2/one/当前.md", paths)
        self.assertNotIn("90_制作资料/续接缓存/one.json", paths)
        self.assertTrue(all(not path.startswith(".工作室/工作集/") for path in paths))
        self.assert_no_legacy_runtime_dirs()

    def test_user_projection_edits_are_preserved_under_compact_history(self):
        self.create()
        saved = self.save()
        self.write(saved["projection"]["path"], "用户保存的完整修改")
        result = self.save(content="经合并的新完整稿")
        preserved = result["preserved_user_edit"]
        self.assertTrue(preserved["path"].startswith("90_制作资料/工作稿/C2/one/历史/"))
        self.assertEqual(self.p.read_bytes(preserved["path"]), "用户保存的完整修改".encode())
        self.assert_no_legacy_runtime_dirs()

    def test_interrupted_draft_recovers_from_state_without_cache(self):
        self.create()
        previous = self.save(content="旧完整稿")
        request = self.session(content="新完整稿", expected_draft_sha256=previous["draft"]["sha256"])
        with mock.patch.dict(os.environ, {"XIAOMO_STUDIO_FAILPOINT": "draft_after_state"}):
            self.assert_error("injected_failure", ws.save_draft, self.p, request)
        (self.root / previous["cache"]["path"]).unlink()
        inspected = ws.inspect(self.p, {"workset_key": "one"})
        self.assertEqual(inspected["projection"]["status"], "pending_safe_rebuild")
        resumed = ws.claim(self.p, self.session())
        self.assertEqual(resumed["projection"]["status"], "synced")
        self.assertEqual(self.p.read_bytes(resumed["projection"]["path"]), "新完整稿".encode())
        self.assertEqual(resumed["cache"]["status"], "valid")
        self.assert_no_legacy_runtime_dirs()

    def test_owner_matrix_rejects_old_roots_and_container_bypasses(self):
        allowed = {
            "A": ["A_总控.md", "制作统筹/计划.md"],
            "B": ["B_故事剧本.md", "90_制作资料/剧本/正文.md"],
            "C1": ["90_制作资料/资产设计/角色.md", "90_制作资料/色卡/角色.md", "90_制作资料/提示词卡/资产/角色.md", "90_制作资料/生成记录/资产/角色.json"],
            "C2": ["90_制作资料/场景制作/场景.md", "90_制作资料/镜头规划/镜头.md", "90_制作资料/表演设计/动作.md", "90_制作资料/提示词卡/镜头.md", "90_制作资料/生成记录/镜头/镜头.json"],
            "D": ["D_后期剪辑.md", "90_制作资料/后期方案/方案.md"],
            "E": ["E_发行复盘.md", "90_制作资料/发布包/文案.md"],
        }
        for owner, paths in allowed.items():
            for path in paths:
                with self.subTest(owner=owner, path=path):
                    self.assertTrue(self.p.owner_allowed(owner, path))
                    if path.startswith("90_制作资料/"):
                        self.assertFalse(self.p.owner_allowed(owner, path.removeprefix("90_制作资料/")))
                    for other in allowed:
                        if other != owner:
                            self.assertFalse(self.p.owner_allowed(other, path))
        for owner in allowed:
            for path in ("90_制作资料/A_总控.md", "90_制作资料/B_故事剧本.md", "90_制作资料/C_视觉生成.md", "90_制作资料/D_后期剪辑.md", "90_制作资料/E_发行复盘.md", "90_制作资料/制作统筹/计划.md", "C_视觉生成.md", "90_制作资料/工作稿/当前.md", "90_制作资料/续接缓存/cache.json", "90_制作资料/流程改进日志/记录.md", "90_制作资料/资产设计/视频.mp4", "图片/参考.md"):
                with self.subTest(owner=owner, path=path):
                    self.assertFalse(self.p.owner_allowed(owner, path))

    def test_adopt_resolve_keep_physical_identity_and_internal_transactions(self):
        self.create()
        self.save()
        targets = [
            self.target("A_总控.md", "A"),
            self.target("90_制作资料/剧本/片段.md", "B", object_id="S01"),
            self.target("90_制作资料/资产设计/角色.md", "C1", object_id="A01"),
            self.target("90_制作资料/场景制作/镜头.md", "C2", object_id="V01"),
            self.target("90_制作资料/后期方案/剪辑.md", "D", object_id="CUT01"),
            self.target("90_制作资料/发布包/文案.md", "E", object_id="PUB01"),
        ]
        result = adoption.adopt(self.p, self.request(targets))
        self.assertEqual(result["status"], "committed")
        resolved = adoption.resolve(self.p, {"transaction_id": "tx-one"})
        self.assertEqual({item["path"] for item in resolved["objects"]}, {item["path"] for item in targets})
        self.assertTrue(all(item["adopted"] for item in resolved["objects"]))
        self.assertTrue(result["manifest_path"].startswith(".工作室/事务/"))
        self.assertTrue((self.root / ".工作室/采用索引.json").is_file())
        self.assertTrue((self.root / ".工作室/采用索引.md").is_file())
        for target in targets:
            self.assertEqual(self.p.read_bytes(target["path"]), self.p.read_bytes(target["source"]))
        old = adoption.resolve(self.p, {"paths": ["资产设计/角色.md"]})["objects"][0]
        self.assertFalse(old["adopted"])
        self.assertEqual(self.p.catalogue()["ids"]["A01"], "90_制作资料/资产设计/角色.md")
        self.assert_no_legacy_runtime_dirs()

    def test_adopt_rejects_c1_c2_d_e_wrong_owner_and_old_root_before_transaction(self):
        self.create()
        self.save()
        cases = [("C1", "90_制作资料/场景制作/一.md"), ("C2", "90_制作资料/提示词卡/资产/一.md"),
                 ("D", "90_制作资料/发布包/一.md"), ("E", "90_制作资料/后期方案/一.md"),
                 ("C1", "资产设计/一.md"), ("C2", "镜头规划/一.md"),
                 ("D", "后期方案/一.md"), ("E", "发布包/一.md"),
                 ("A", "90_制作资料/A_总控.md")]
        for owner, path in cases:
            with self.subTest(owner=owner, path=path):
                request = self.request([self.target(path, owner)])
                self.assert_error("owner_forbidden", adoption.adopt, self.p, request)
                self.assertFalse((self.root / path).exists())
                self.assertEqual(self.p.catalogue()["revision"], 0)
                self.assertFalse((self.root / ".工作室/事务").exists())
        self.assert_no_legacy_runtime_dirs()

    def test_resume_sync_after_prepare_and_commit_failures(self):
        for point in ("after_prepare", "after_commit"):
            with self.subTest(point=point):
                key = point.replace("_", "-")
                self.create(key)
                self.save(key)
                relative = f"90_制作资料/资产设计/{key}.md"
                request = self.request([self.target(relative)], key, "tx-" + key)
                with mock.patch.dict(os.environ, {"XIAOMO_STUDIO_FAILPOINT": point}):
                    self.assert_error("injected_failure", adoption.adopt, self.p, request)
                cache = self.root / self.p.document_path(f"续接缓存/{key}.json")
                cache.unlink()
                inspected = ws.inspect(self.p, {"workset_key": key})
                self.assertEqual(inspected["cache"]["status"], "missing")
                resumed = adoption.resume_sync(self.p, self.session(key, transaction_id="tx-" + key))
                self.assertEqual(resumed["status"], "committed")
                self.assertEqual(ws.inspect(self.p, {"workset_key": key})["cache"]["status"], "valid")
                self.assertEqual(self.p.read_bytes(relative), "采用完整正文\n".encode())
        self.assert_no_legacy_runtime_dirs()

    def test_resume_sync_preserves_user_edits_after_commit(self):
        self.create()
        self.save()
        path = "90_制作资料/资产设计/角色.md"
        self.write(path, "旧版")
        request = self.request([self.target(path)])
        with mock.patch.dict(os.environ, {"XIAOMO_STUDIO_FAILPOINT": "after_commit"}):
            self.assert_error("injected_failure", adoption.adopt, self.p, request)
        self.write(path, "用户后改")
        result = adoption.resume_sync(self.p, self.session(transaction_id="tx-one"))
        self.assertEqual(result["status"], "committed_sync_pending")
        self.assertEqual(self.p.read_bytes(path), "用户后改".encode())
        obj = adoption.resolve(self.p, {"paths": [path]})["objects"][0]
        self.assertEqual(obj["projection"]["status"], "conflict")
        self.assertEqual(self.p.read_bytes(obj["snapshot"]), "采用完整正文\n".encode())
        self.assert_no_legacy_runtime_dirs()

    def test_legacy_worksets_adoption_and_resolve_keep_old_paths(self):
        self.write("开始这里.md", self.entry)
        self.p = Project(self.root, adoption=True)
        self.assertIsNone(self.p.document_layout)
        self.assertEqual(self.p.document_path("剧本/正文.md"), "剧本/正文.md")
        self.create()
        saved = self.save()
        self.assertEqual(saved["projection"]["path"], "工作稿/C2/one/当前.md")
        self.assertEqual(saved["cache"]["path"], "续接缓存/one.json")
        target = self.target("资产设计/角色.md", object_id="A01")
        self.assertEqual(adoption.adopt(self.p, self.request([target]))["status"], "committed")
        resolved = adoption.resolve(self.p, {"paths": ["资产设计/角色.md"]})["objects"][0]
        self.assertTrue(resolved["adopted"])
        self.assertEqual(self.p.catalogue()["ids"]["A01"], "资产设计/角色.md")
        self.assertFalse(self.p.owner_allowed("C1", "90_制作资料/资产设计/角色.md"))
        self.assertFalse((self.root / "90_制作资料").exists())

    def test_cli_create_save_inspect_use_compact_paths(self):
        def cli(action, request):
            request_file = self.root / "request.json"
            request_file.write_bytes(canonical_bytes(request))
            run = subprocess.run([sys.executable, "-B", "-X", "utf8", str(SCRIPTS / "studio_runtime.py"),
                "--project-root", str(self.root), "--action", action, "--request-file", str(request_file)],
                text=True, encoding="utf-8", capture_output=True, check=False)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            return json.loads(run.stdout)
        created = cli("Create", {"workset_key": "cli", "department": "D", "title": "后期候选", "holder": "cli-test"})
        self.assertEqual(created["cache_path"], "90_制作资料/续接缓存/cli.json")
        cli("SaveDraft", self.session("cli", content="完整后期方案", expected_draft_sha256=None))
        result = cli("Inspect", {"workset_key": "cli"})
        self.assertEqual(result["projection"]["path"], "90_制作资料/工作稿/D/cli/当前.md")
        self.assertEqual(result["snapshot"]["status"], "valid")
        self.assert_no_legacy_runtime_dirs()


if __name__ == "__main__":
    unittest.main()
