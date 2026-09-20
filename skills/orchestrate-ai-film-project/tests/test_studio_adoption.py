"""Publication/recovery tests in isolated, legal V0.9 temporary projects."""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import json
import ntpath
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from studio_io import Project, StudioError, canonical_bytes, sha_bytes
from studio_worksets import create, load_state, save_draft
from studio_adoption import adopt, resume_sync, cancel_adoption, resolve
import studio_adoption as adoption_module

TEST_ROOT = Path(__file__).resolve().parents[4] / "verification" / "batch2" / "adoption"


def write(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = content if isinstance(content, bytes) else content.encode("utf-8")
    path.write_bytes(raw)
    return sha_bytes(raw)


def project_at(root):
    write(root, "开始这里.md", "# 隔离试验\nworkflowStudio: studio-v0.9\n")
    write(root, "工作室能力.json", canonical_bytes({
        "schema": "xiaomo.studio-capabilities/v1", "workflowStudio": "studio-v0.9",
        "runtime_schema": "xiaomo.studio-runtime/v1", "candidate_design": True,
        "workset_runtime": True, "adoption_transactions": True,
        "media_execution": False, "test_mode": True,
    }))
    return Project(root, adoption=True)


def session(p, key):
    state = load_state(p, key)
    return {"workset_key": key, "holder": state["lease"]["holder"],
            "lease_id": state["lease"]["id"], "expected_revision": state["revision"]}


def make_workset(p, key="one", dependencies=None):
    create(p, {"workset_key": key, "department": "C2", "title": "独立完整候选",
               "holder": "task-" + key})
    save_draft(p, dict(session(p, key), content="完整候选：中文人物与空间。\n",
                       expected_draft_sha256=None, dependencies=dependencies or [],
                       next_step="审阅并采用精确版本"))
    return load_state(p, key)


def target(p, path, content, owner="C1", object_id=None, name=None):
    name = name or sha_bytes(path.encode("utf-8"))[:12]
    source = f"输入/{name}.md"
    source_hash = write(p.root, source, content)
    matching = [obj for key, obj in p.catalogue()["objects"].items() if key.casefold() == path.casefold()]
    current = matching[0] if matching else None
    result = {"owner": owner, "path": path, "source": source, "source_sha256": source_hash,
              "expected_sha256": current["sha256"] if current else p.hash(path),
              "expected_version": current["version"] if current else None}
    if object_id:
        result["object_id"] = object_id
    return result


def adoption_request(p, writes, key="one", transaction_id="tx-one"):
    return dict(session(p, key), transaction_id=transaction_id,
                draft_sha256=load_state(p, key)["draft"]["sha256"],
                adoption_evidence={"text": "采用本次展示的完整稿", "source": "隔离用户决定"}, writes=writes)


@contextmanager
def deny_file_sharing(path):
    """A real Windows handle, not a mocked permission error."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Cannot establish exclusive test handle")
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


def worker_args(root, request_file, start=None, ready=None):
    args = [sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()),
            "--worker", str(root), str(request_file)]
    if start is not None:
        args += [str(start), str(ready)]
    return args


def worker():
    root, request_file = Path(sys.argv[2]), Path(sys.argv[3])
    if len(sys.argv) > 4:
        start, ready = Path(sys.argv[4]), Path(sys.argv[5])
        ready.write_text("ready", encoding="utf-8")
        deadline = time.monotonic() + 20
        while not start.exists():
            if time.monotonic() > deadline:
                return 3
            time.sleep(0.01)
    p = Project(root, adoption=True)
    request = json.loads(request_file.read_text(encoding="utf-8"))
    try:
        result = adopt(p, request)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except StudioError as error:
        print(json.dumps({"ok": False, "code": error.code, "message": error.message, "details": error.details}, ensure_ascii=False))
        return 2


def reader_worker():
    root, stop, ready, output = map(Path, sys.argv[2:6])
    p = Project(root, adoption=True)
    observations = []
    errors = []
    ready.write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 40
    while not stop.exists() and time.monotonic() < deadline:
        try:
            result = resolve(p, {"all": True})
            adopted = [obj for obj in result["objects"] if obj["adopted"]]
            versions = {obj["version"] for obj in adopted}
            if len(adopted) not in (0, 2) or len(versions) > 1:
                errors.append(result)
            observations.append((result["catalogue_revision"], len(adopted)))
        except StudioError as error:
            errors.append({"code": error.code, "message": str(error)})
        time.sleep(0.002)
    output.write_text(json.dumps({"observations": observations, "errors": errors}), encoding="utf-8")
    return 0


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="adoption-", dir=TEST_ROOT)
        self.root = Path(self.temp.name)
        self.p = project_at(self.root)
        make_workset(self.p)

    def tearDown(self):
        self.temp.cleanup()

    def assert_code(self, code, function, *args):
        with self.assertRaises(StudioError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        return caught.exception

    def fail_adopt(self, request, point="after_prepare"):
        with patch.dict(os.environ, {"XIAOMO_STUDIO_FAILPOINT": point}, clear=False):
            self.assert_code("injected_failure", adopt, self.p, request)

    def fresh(self, transaction_id="tx-one", key="one"):
        return dict(session(self.p, key), transaction_id=transaction_id)

    def test_atomic_bundle_and_original_bytes_retained(self):
        before = b"\xef\xbb\xbf" + "原始角色\r\n".encode("utf-8")
        write(self.root, "资产设计/角色.md", before)
        content = b"\xef\xbb\xbf" + "角色新完整文本\r\n".encode("utf-8")
        writes = [target(self.p, "资产设计/角色.md", content, object_id="A01"),
                  target(self.p, "剧本/段落.md", "完整剧情\n", owner="B", object_id="S01")]
        result = adopt(self.p, adoption_request(self.p, writes))
        self.assertEqual(result["status"], "committed")
        self.assertEqual(self.p.catalogue()["revision"], 1)
        resolved = resolve(self.p, {"transaction_id": "tx-one"})
        self.assertEqual(len(resolved["objects"]), 2)
        self.assertTrue(all(obj["adopted"] and obj["current"] for obj in resolved["objects"]))
        self.assertEqual(self.p.read_bytes("资产设计/角色.md"), content)
        manifest = self.p.read_json(result["manifest_path"])
        role = next(item for item in manifest["writes"] if item["path"] == "资产设计/角色.md")
        self.assertEqual(self.p.read_bytes(role["original_snapshot"]), before)
        self.assertEqual(self.p.catalogue()["ids"], {"A01": "资产设计/角色.md", "S01": "剧本/段落.md"})
        self.assertIsNone(load_state(self.p, "one")["pending_transaction"])

    def test_idempotent_replay_and_equivalent_new_id_do_not_increment(self):
        writes = [target(self.p, "资产设计/角色.md", "角色", object_id="A01")]
        request = adoption_request(self.p, writes)
        first = adopt(self.p, request)
        revision = load_state(self.p, "one")["revision"]
        replay = adopt(self.p, request)  # Original session revision is now stale.
        self.assertTrue(replay["idempotent"])
        self.assertEqual(load_state(self.p, "one")["revision"], revision)
        equivalent = dict(request, **session(self.p, "one"), transaction_id="tx-equivalent")
        no_change = adopt(self.p, equivalent)
        self.assertEqual(no_change["status"], "no_change")
        self.assertEqual(self.p.catalogue()["revision"], 1)
        self.assertEqual(next(iter(self.p.catalogue()["objects"].values()))["version"], 1)
        self.assertEqual(len(self.p.catalogue()["transactions"]), 1)
        self.assertEqual(adopt(self.p, equivalent)["status"], "no_change")
        resolved = resolve(self.p, {"transaction_id": "tx-equivalent"})
        self.assertEqual(resolved["status"], "no_change")
        self.assertTrue(all(obj["adopted"] and obj["transaction_id"] == "tx-one" for obj in resolved["objects"]))
        self.assertEqual(first["objects"][0]["snapshot"], no_change["objects"][0]["snapshot"])

    def test_same_id_different_content_rejected(self):
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "一")])
        adopt(self.p, request)
        changed = deepcopy(request)
        changed["writes"][0] = target(self.p, "资产设计/一.md", "二")
        self.assert_code("idempotency_conflict", adopt, self.p, changed)
        self.assertEqual(self.p.read_bytes("资产设计/一.md"), "一".encode())

    def test_adoption_evidence_and_source_are_required(self):
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "一")])
        no_evidence = deepcopy(request)
        no_evidence.pop("adoption_evidence")
        self.assert_code("adoption_evidence_required", adopt, self.p, no_evidence)
        write(self.root, request["writes"][0]["source"], "用户又改了来源")
        self.assert_code("source_conflict", adopt, self.p, request)
        self.assertEqual(self.p.catalogue()["revision"], 0)
        self.assertFalse((self.root / ".工作室/事务").exists())

    def test_owner_path_duplicate_and_id_owner_are_rejected(self):
        base = adoption_request(self.p, [target(self.p, "资产设计/一.md", "一")])
        for path in ("AGENTS.md", "工作室能力.json", "C_视觉生成.md", "影片.mp4", "../外部.md", "剧本/con.md"):
            with self.subTest(path=path):
                request = deepcopy(base)
                request["writes"][0]["path"] = path
                with self.assertRaises(StudioError):
                    adopt(self.p, request)
        duplicate = deepcopy(base)
        duplicate["writes"].append(dict(duplicate["writes"][0], path="资产设计/一.MD"))
        self.assert_code("duplicate_target", adopt, self.p, duplicate)
        wrong_id = deepcopy(base)
        wrong_id["writes"][0]["object_id"] = "S01"
        self.assert_code("object_id_owner", adopt, self.p, wrong_id)
        self.assertEqual(self.p.catalogue()["revision"], 0)

    def test_draft_revision_and_view_hand_edit_block_adoption(self):
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "一")])
        stale = dict(request, expected_revision=request["expected_revision"] - 1)
        with self.assertRaises(StudioError):
            adopt(self.p, stale)
        draft = load_state(self.p, "one")["draft"]
        write(self.root, draft["projection_path"], "尚未合并的用户改稿")
        self.assert_code("draft_projection_conflict", adopt, self.p, request)
        self.assertEqual(self.p.catalogue()["revision"], 0)

    def test_file_dependency_change_rejects_without_losing_draft(self):
        digest = write(self.root, "参考/规则.md", "规则一")
        make_workset(self.p, "dependent", [{"path": "参考/规则.md", "kind": "file", "sha256": digest}])
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "一")], key="dependent")
        write(self.root, "参考/规则.md", "规则二")
        error = self.assert_code("dependency_conflict", adopt, self.p, request)
        self.assertTrue(error.details["dependency_changes"])
        self.assertIsNotNone(load_state(self.p, "dependent")["draft"])
        self.assertEqual(self.p.catalogue()["revision"], 0)

    def test_prepared_dependency_and_target_rechecked_on_resume(self):
        digest = write(self.root, "参考/规则.md", "规则一")
        make_workset(self.p, "dependent", [{"path": "参考/规则.md", "kind": "file", "sha256": digest}])
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "一")], key="dependent")
        self.fail_adopt(request)
        write(self.root, "参考/规则.md", "规则二")
        self.assert_code("dependency_conflict", resume_sync, self.p, self.fresh(key="dependent"))
        self.assertFalse(resolve(self.p, {"transaction_id": "tx-one"})["adopted"])
        self.assertIsNotNone(load_state(self.p, "dependent")["pending_transaction"])

    def test_prepare_cancel_retains_snapshots_and_cannot_be_reused(self):
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "一")])
        self.fail_adopt(request)
        manifest_path = ".工作室/事务/tx-one/manifest.json"
        before = self.p.read_bytes(manifest_path)
        result = cancel_adoption(self.p, self.fresh())
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.p.read_bytes(manifest_path), before)
        self.assertIsNone(load_state(self.p, "one")["pending_transaction"])
        self.assert_code("transaction_cancelled", resume_sync, self.p, self.fresh())
        self.assert_code("transaction_cancelled", adopt, self.p, request)
        self.assertEqual(self.p.catalogue()["revision"], 0)

    def test_committed_cannot_be_cancelled(self):
        adopt(self.p, adoption_request(self.p, [target(self.p, "资产设计/一.md", "一")]))
        self.assert_code("already_committed", cancel_adoption, self.p, self.fresh())

    def test_committed_hand_edits_never_overwritten(self):
        write(self.root, "资产设计/一.md", "原文")
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "采用文本")])
        self.fail_adopt(request, "after_commit")
        write(self.root, "资产设计/一.md", "用户手改必须保留")
        result = resume_sync(self.p, self.fresh())
        self.assertEqual(result["status"], "committed_sync_pending")
        self.assertEqual(self.p.read_bytes("资产设计/一.md"), "用户手改必须保留".encode())
        resolved = resolve(self.p, {"paths": ["资产设计/一.md"]})["objects"][0]
        self.assertEqual(self.p.read_bytes(resolved["snapshot"]), "采用文本".encode())
        self.assertEqual(resolved["projection"]["status"], "conflict")
        write(self.root, "手改保留.md", self.p.read_bytes("资产设计/一.md"))
        (self.root / "资产设计/一.md").unlink()  # Explicit user recovery action in the fixture.
        self.assertEqual(resume_sync(self.p, self.fresh())["status"], "committed")
        self.assertEqual(self.p.read_bytes("手改保留.md"), "用户手改必须保留".encode())

    def test_manual_rollback_after_completed_projection_is_conflict(self):
        write(self.root, "资产设计/一.md", "原稿")
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "采用稿")])
        self.assertEqual(adopt(self.p, request)["status"], "committed")
        write(self.root, "资产设计/一.md", "原稿")
        before = resolve(self.p, {"transaction_id": "tx-one"})
        self.assertEqual(before["objects"][0]["projection"]["status"], "conflict")
        self.assertEqual(resume_sync(self.p, self.fresh())["status"], "committed_sync_pending")
        self.assertEqual(self.p.read_bytes("资产设计/一.md"), "原稿".encode())

    def test_completed_target_in_partially_synced_bundle_protects_rollback(self):
        write(self.root, "资产设计/一.md", "原一")
        write(self.root, "资产设计/二.md", "原二")
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "新一"),
                                           target(self.p, "资产设计/二.md", "新二")])
        self.fail_adopt(request, "after_commit")
        write(self.root, "资产设计/二.md", "第二个目标的手改")
        first_sync = resume_sync(self.p, self.fresh())
        self.assertEqual(first_sync["status"], "committed_sync_pending")
        self.assertEqual(self.p.read_bytes("资产设计/一.md"), "新一".encode())
        write(self.root, "资产设计/一.md", "原一")
        result = resolve(self.p, {"transaction_id": "tx-one"})
        reverted = next(obj for obj in result["objects"] if obj["path"] == "资产设计/一.md")
        self.assertEqual(reverted["projection"]["status"], "conflict")
        self.assertEqual(resume_sync(self.p, self.fresh())["status"], "committed_sync_pending")
        self.assertEqual(self.p.read_bytes("资产设计/一.md"), "原一".encode())
        self.assertEqual(self.p.read_bytes("资产设计/二.md"), "第二个目标的手改".encode())

    def test_crash_after_view_write_before_completion_protects_rollback(self):
        write(self.root, "资产设计/一.md", "原稿")
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "采用稿")])
        request_file = self.root / "crash-request.json"
        request_file.write_bytes(canonical_bytes(request))
        env = dict(os.environ, XIAOMO_STUDIO_CRASHPOINT="adoption_after_projection_write")
        env.pop("XIAOMO_STUDIO_FAILPOINT", None)
        process = subprocess.run(worker_args(self.root, request_file), env=env, capture_output=True, timeout=30)
        self.assertEqual(process.returncode, 97, (process.stdout, process.stderr))
        self.assertEqual(self.p.read_bytes("资产设计/一.md"), "采用稿".encode())
        journal = self.p.read_json(".工作室/事务/tx-one/journal.json")
        self.assertFalse(journal.get("projection_completed", {}))
        write(self.root, "资产设计/一.md", "原稿")
        resolved = resolve(self.p, {"transaction_id": "tx-one"})
        self.assertEqual(resolved["objects"][0]["projection"]["status"], "conflict")
        self.assertEqual(resume_sync(self.p, self.fresh())["status"], "committed_sync_pending")
        self.assertEqual(self.p.read_bytes("资产设计/一.md"), "原稿".encode())

    def test_historical_replay_with_index_conflict_does_not_reopen_workset(self):
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "第一版")])
        adopt(self.p, request)
        adopt(self.p, adoption_request(self.p, [target(self.p, "资产设计/一.md", "第二版")], transaction_id="tx-two"))
        write(self.root, ".工作室/采用索引.md", "用户手改汇总")
        state_path = ".工作室/工作集/one/state.json"
        state_before = self.p.read_bytes(state_path)
        journal_before = self.p.read_bytes(".工作室/事务/tx-one/journal.json")
        resumed = resume_sync(self.p, self.fresh("tx-one"))
        replayed = adopt(self.p, request)
        for result in (resumed, replayed):
            self.assertEqual(result["status"], "superseded")
            self.assertTrue(all(not obj["current"] for obj in result["objects"]))
            self.assertEqual(result["catalogue_projection"]["status"], "conflict")
        self.assertEqual(self.p.read_bytes(state_path), state_before)
        self.assertEqual(self.p.read_bytes(".工作室/事务/tx-one/journal.json"), journal_before)
        self.assertEqual(self.p.read_bytes("资产设计/一.md"), "第二版".encode())
        self.assertEqual(self.p.read_bytes(".工作室/采用索引.md"), "用户手改汇总".encode())
        state = load_state(self.p, "one")
        self.assertIsNone(state["pending_transaction"])
        self.assertEqual(state["last_adoption"]["transaction_id"], "tx-two")
        save_draft(self.p, dict(session(self.p, "one"), content="下一版完整工作稿",
                               expected_draft_sha256=state["draft"]["sha256"]))

    def supersede_from_process_at_index_sync(self, *, partial=False):
        make_workset(self.p, "two")
        first_writes = [target(self.p, "资产设计/一.md", "第一采用稿", name="initial-one")]
        if partial:
            first_writes.append(target(self.p, "资产设计/二.md", "保留采用稿", name="initial-two"))
        adopt(self.p, adoption_request(self.p, first_writes))
        write(self.root, "资产设计/一.md", "用户手改")
        state_before = self.p.read_bytes(".工作室/工作集/one/state.json")
        original_sync_index = adoption_module._sync_index
        interleaved = [False]

        def replace_from_other_workset(project):
            if not interleaved[0]:
                interleaved[0] = True
                write(self.root, "手改保留.md", self.p.read_bytes("资产设计/一.md"))
                (self.root / "资产设计/一.md").unlink()
                replacement = adoption_request(self.p, [target(self.p, "资产设计/一.md", "第二采用稿", name="replacement")],
                                               key="two", transaction_id="tx-two")
                request_file = self.root / "replacement-request.json"
                request_file.write_bytes(canonical_bytes(replacement))
                process = subprocess.run(worker_args(self.root, request_file), capture_output=True, timeout=30)
                self.assertEqual(process.returncode, 0, (process.stdout, process.stderr))
                self.assertEqual(json.loads(process.stdout.decode("utf-8"))["result"]["status"], "committed")
            return original_sync_index(project)

        with patch.object(adoption_module, "_sync_index", replace_from_other_workset):
            result = resume_sync(self.p, self.fresh())
        self.assertTrue(interleaved[0])
        self.assertEqual(self.p.read_bytes("资产设计/一.md"), "第二采用稿".encode())
        self.assertEqual(self.p.read_bytes("手改保留.md"), "用户手改".encode())
        self.assertEqual(result["catalogue_projection"]["status"], "synced")
        self.assertTrue(result["historical_projection_errors"])
        self.assertIsNone(load_state(self.p, "one")["pending_transaction"])
        if not partial:
            self.assertEqual(self.p.read_bytes(".工作室/工作集/one/state.json"), state_before)
        state = load_state(self.p, "one")
        save_draft(self.p, dict(session(self.p, "one"), content="可继续的后续完整稿",
                               expected_draft_sha256=state["draft"]["sha256"]))
        return result

    def test_mid_sync_supersede_from_real_process_does_not_rehang_pending(self):
        result = self.supersede_from_process_at_index_sync()
        self.assertEqual(result["status"], "superseded")
        self.assertTrue(all(not obj["current"] for obj in result["objects"]))

    def test_mid_sync_partial_supersede_discards_only_obsolete_errors(self):
        result = self.supersede_from_process_at_index_sync(partial=True)
        self.assertEqual(result["status"], "committed")
        self.assertEqual(sum(obj["current"] for obj in result["objects"]), 1)
        self.assertFalse(result.get("projection_errors"))

    def test_final_classification_and_state_write_hold_publication_lock(self):
        adopt(self.p, adoption_request(self.p, [target(self.p, "资产设计/一.md", "第一版", name="first")]))
        make_workset(self.p, "two")
        replacement = adoption_request(self.p, [target(self.p, "资产设计/一.md", "第二版", name="second")],
                                       key="two", transaction_id="tx-two")
        request_file = self.root / "finalize-race-request.json"
        request_file.write_bytes(canonical_bytes(replacement))
        start, ready = self.root / "finalize-start.flag", self.root / "finalize-ready.flag"
        process = subprocess.Popen(worker_args(self.root, request_file, start, ready),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 20
        while not ready.exists():
            if time.monotonic() > deadline:
                process.kill()
                process.communicate()
                self.fail("Competing publisher did not reach the start barrier")
            time.sleep(0.01)
        original_result = adoption_module._result
        original_save = adoption_module._save_state
        signalled, saved = [False], [False]

        def observe_classification(project, manifest, catalogue):
            result = original_result(project, manifest, catalogue)
            if manifest["transaction_id"] == "tx-one" and not signalled[0]:
                signalled[0] = True
                start.write_text("go", encoding="utf-8")
                # The real competing process cannot publish between this
                # classification and the following workset persistence.
                with self.assertRaises(subprocess.TimeoutExpired):
                    process.communicate(timeout=0.75)
                self.assertEqual(project.catalogue()["revision"], 1)
            return result

        def observe_state_write(project, state):
            if state["workset_key"] == "one" and signalled[0]:
                saved[0] = True
                self.assertIsNone(process.poll())
                self.assertEqual(project.catalogue()["revision"], 1)
            return original_save(project, state)

        try:
            with patch.object(adoption_module, "_result", observe_classification), patch.object(adoption_module, "_save_state", observe_state_write):
                result = resume_sync(self.p, self.fresh())
        finally:
            if not start.exists():
                start.write_text("go", encoding="utf-8")
            out, err = process.communicate(timeout=40)
        self.assertEqual(process.returncode, 0, (out, err))
        self.assertTrue(signalled[0] and saved[0])
        self.assertEqual(result["catalogue_revision"], 1)
        self.assertEqual(self.p.catalogue()["revision"], 2)
        self.assertIsNone(load_state(self.p, "one")["pending_transaction"])

    def test_superseded_pending_transaction_is_cleared_without_restoring_old_views(self):
        write(self.root, "资产设计/一.md", "原稿")
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "第一版")])
        self.fail_adopt(request, "after_commit")
        make_workset(self.p, "two")
        newer = adoption_request(self.p, [target(self.p, "资产设计/一.md", "第二版")], key="two", transaction_id="tx-two")
        self.assertEqual(adopt(self.p, newer)["status"], "committed")
        write(self.root, ".工作室/采用索引.md", "手改汇总")
        result = resume_sync(self.p, self.fresh())
        self.assertEqual(result["status"], "superseded")
        state = load_state(self.p, "one")
        self.assertIsNone(state["pending_transaction"])
        self.assertEqual(self.p.read_bytes("资产设计/一.md"), "第二版".encode())
        save_draft(self.p, dict(session(self.p, "one"), content="可继续新工作",
                               expected_draft_sha256=state["draft"]["sha256"]))

    def test_completed_index_projection_does_not_accept_manual_old_hash(self):
        adopt(self.p, adoption_request(self.p, [target(self.p, "资产设计/一.md", "第一版")]))
        old_index = self.p.read_bytes(".工作室/采用索引.md")
        adopt(self.p, adoption_request(self.p, [target(self.p, "资产设计/一.md", "第二版")], transaction_id="tx-two"))
        write(self.root, ".工作室/采用索引.md", old_index)
        result = resolve(self.p, {"transaction_id": "tx-two"})
        self.assertEqual(result["catalogue_projection"]["status"], "conflict")
        self.assertEqual(resume_sync(self.p, self.fresh("tx-two"))["status"], "committed_sync_pending")
        self.assertEqual(self.p.read_bytes(".工作室/采用索引.md"), old_index)

    def test_catalogue_markdown_hand_edit_preserved(self):
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "一")])
        self.fail_adopt(request, "after_commit")
        write(self.root, ".工作室/采用索引.md", "手改汇总")
        result = resume_sync(self.p, self.fresh())
        self.assertEqual(result["status"], "committed_sync_pending")
        self.assertEqual(result["catalogue_projection"]["status"], "conflict")
        self.assertEqual(self.p.read_bytes(".工作室/采用索引.md"), "手改汇总".encode())

    def test_historical_retry_does_not_restore_old_projection(self):
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "第一版", object_id="A01")])
        adopt(self.p, request)
        second = adoption_request(self.p, [target(self.p, "资产设计/一.md", "第二版", object_id="A01")], transaction_id="tx-two")
        adopt(self.p, second)
        replay = adopt(self.p, request)
        self.assertEqual(replay["status"], "superseded")
        self.assertTrue(replay["historical"])
        resumed = resume_sync(self.p, self.fresh("tx-one"))
        self.assertEqual(resumed["status"], "superseded")
        self.assertEqual(self.p.read_bytes("资产设计/一.md"), "第二版".encode())
        self.assertEqual(self.p.catalogue()["revision"], 2)
        self.assertEqual(load_state(self.p, "one")["last_adoption"]["transaction_id"], "tx-two")

    def test_unknown_projection_blocks_new_content_but_equivalent_is_read_only(self):
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "采用稿")])
        adopt(self.p, request)
        write(self.root, "资产设计/一.md", "手改")
        repeated = dict(request, **session(self.p, "one"), transaction_id="tx-same")
        result = adopt(self.p, repeated)
        self.assertEqual(result["status"], "no_change")
        self.assertEqual(result["objects"][0]["projection"]["status"], "conflict")
        changed = adoption_request(self.p, [target(self.p, "资产设计/一.md", "新稿")], transaction_id="tx-new")
        self.assert_code("target_projection_conflict", adopt, self.p, changed)
        self.assertEqual(self.p.read_bytes("资产设计/一.md"), "手改".encode())

    def test_resolve_is_read_only_and_rejects_corrupt_published_evidence(self):
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "一")])
        adopt(self.p, request)
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        resolve(self.p, {})
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        manifest_path = ".工作室/事务/tx-one/manifest.json"
        write(self.root, manifest_path, self.p.read_bytes(manifest_path) + b" ")
        self.assert_code("catalogue_corrupt", resolve, self.p, {"transaction_id": "tx-one"})

    def test_prepared_manifest_hand_edit_cannot_change_adopted_content(self):
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "一")])
        self.fail_adopt(request)
        path = ".工作室/事务/tx-one/manifest.json"
        manifest = self.p.read_json(path)
        manifest["writes"][0]["path"] = "资产设计/未采用对象.md"
        write(self.root, path, canonical_bytes(manifest))
        self.assert_code("manifest_conflict", resume_sync, self.p, self.fresh())
        self.assertEqual(self.p.catalogue()["revision"], 0)
        self.assertIsNone(self.p.hash("资产设计/未采用对象.md"))

    def test_prepared_manifest_crash_before_journal_still_binds_request(self):
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "一")])
        self.fail_adopt(request, "adoption_after_manifest")
        path = ".工作室/事务/tx-one/manifest.json"
        manifest = self.p.read_json(path)
        manifest["writes"][0]["path"] = "资产设计/未采用对象.md"
        write(self.root, path, canonical_bytes(manifest))
        self.assert_code("manifest_conflict", resume_sync, self.p, self.fresh())
        self.assertEqual(self.p.catalogue()["revision"], 0)

    def test_adopted_dependency_uses_snapshot_and_detects_new_version(self):
        adopt(self.p, adoption_request(self.p, [target(self.p, "资产设计/源.md", "源一")]))
        obj = self.p.catalogue()["objects"]["资产设计/源.md"]
        make_workset(self.p, "dependent", [{"path": "资产设计/源.md", "kind": "adopted",
                                           "sha256": obj["sha256"], "version": obj["version"]}])
        write(self.root, "资产设计/源.md", "非权威手改")
        request = adoption_request(self.p, [target(self.p, "镜头规划/一.md", "镜头", owner="C2")],
                                   key="dependent", transaction_id="tx-dependent")
        self.assertEqual(adopt(self.p, request)["status"], "committed")
        write(self.root, "资产设计/源.md", "源一")
        adopt(self.p, adoption_request(self.p, [target(self.p, "资产设计/源.md", "源二")], transaction_id="tx-source-two"))
        request = adoption_request(self.p, [target(self.p, "镜头规划/二.md", "镜头二", owner="C2")],
                                   key="dependent", transaction_id="tx-dependent-two")
        self.assert_code("dependency_conflict", adopt, self.p, request)
        self.assertIsNone(self.p.hash("镜头规划/二.md"))

    def test_target_changed_after_prepare_prevents_publication(self):
        write(self.root, "资产设计/一.md", "原文")
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "新文")])
        self.fail_adopt(request)
        write(self.root, "资产设计/一.md", "用户新改动")
        self.assert_code("target_conflict", resume_sync, self.p, self.fresh())
        self.assertEqual(self.p.read_bytes("资产设计/一.md"), "用户新改动".encode())
        self.assertEqual(self.p.catalogue()["revision"], 0)

    def test_corrupt_snapshot_is_not_adopted_or_projected(self):
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "一")])
        self.fail_adopt(request)
        manifest = self.p.read_json(".工作室/事务/tx-one/manifest.json")
        write(self.root, manifest["writes"][0]["snapshot"], "未知内容")
        self.assert_code("snapshot_conflict", resume_sync, self.p, self.fresh())
        self.assertEqual(self.p.catalogue()["revision"], 0)
        self.assertIsNone(self.p.hash("资产设计/一.md"))

    def test_default_resolve_is_overview_without_reading_object_snapshots(self):
        adopted = adopt(self.p, adoption_request(self.p, [target(self.p, "资产设计/一.md", "一")]))
        write(self.root, adopted["objects"][0]["snapshot"], "已故意损坏的内容")
        result = resolve(self.p, {})
        self.assertEqual(result["status"], "overview")
        self.assertEqual(result["available_count"], 1)
        self.assertEqual(result["catalogue_revision"], 1)
        self.assertEqual(result["objects"], [])
        self.assert_code("snapshot_conflict", resolve, self.p, {"all": True})

    def test_explicit_all_and_resolve_selector_exclusivity(self):
        adopt(self.p, adoption_request(self.p, [target(self.p, "资产设计/一.md", "一"),
                                               target(self.p, "资产设计/二.md", "二")]))
        result = resolve(self.p, {"all": True})
        self.assertEqual(len(result["objects"]), 2)
        self.assertTrue(all(obj["adopted"] for obj in result["objects"]))
        for request in ({"all": True, "paths": []}, {"all": True, "transaction_id": "tx-one"},
                        {"paths": [], "transaction_id": "tx-one"}, {"all": "true"}):
            with self.subTest(request=request):
                self.assert_code("invalid_resolve", resolve, self.p, request)

    def test_no_lock_or_projection_write_during_empty_resolve(self):
        with tempfile.TemporaryDirectory(prefix="empty-resolve-", dir=TEST_ROOT) as folder:
            p = project_at(Path(folder))
            before = sorted(path.name for path in p.root.iterdir())
            self.assertEqual(resolve(p, {})["objects"], [])
            self.assertEqual(before, sorted(path.name for path in p.root.iterdir()))

    @unittest.skipUnless(os.name == "nt", "Windows mandatory file sharing")
    def test_busy_views_do_not_block_resolve_or_allow_sync_overwrite(self):
        request = adoption_request(self.p, [target(self.p, "资产设计/一.md", "一"),
                                           target(self.p, "资产设计/二.md", "二")])
        adopt(self.p, request)
        original = self.p.read_bytes("资产设计/一.md")
        with deny_file_sharing(self.root / "资产设计/一.md"), deny_file_sharing(self.root / ".工作室/采用索引.md"):
            resolved = resolve(self.p, {"transaction_id": "tx-one"})
            self.assertEqual(resolved["status"], "committed_sync_pending")
            self.assertEqual(len(resolved["objects"]), 2)
            self.assertTrue(all(obj["adopted"] for obj in resolved["objects"]))
            occupied = next(obj for obj in resolved["objects"] if obj["path"] == "资产设计/一.md")
            self.assertEqual(occupied["projection"]["status"], "unavailable")
            self.assertIn("reason", occupied["projection"])
            self.assertEqual(resolved["catalogue_projection"]["status"], "unavailable")
            for obj in resolved["objects"]:
                self.assertEqual(self.p.hash(obj["snapshot"]), obj["sha256"])
            pending = resume_sync(self.p, self.fresh())
            self.assertEqual(pending["status"], "committed_sync_pending")
            self.assertTrue(pending["projection_errors"])
        self.assertEqual(self.p.read_bytes("资产设计/一.md"), original)
        self.assertEqual(resume_sync(self.p, self.fresh())["status"], "committed")

    @unittest.skipUnless(os.name == "nt", "Windows mandatory file sharing")
    def test_busy_authoritative_evidence_still_blocks_resolve(self):
        result = adopt(self.p, adoption_request(self.p, [target(self.p, "资产设计/一.md", "一")]))
        protected = [".工作室/采用索引.json", result["manifest_path"], result["objects"][0]["snapshot"]]
        for relative in protected:
            with self.subTest(path=relative), deny_file_sharing(self.root / relative):
                with self.assertRaises(StudioError):
                    resolve(self.p, {"transaction_id": "tx-one"})

    def test_missing_views_return_pending_reason_with_complete_snapshots(self):
        adopt(self.p, adoption_request(self.p, [target(self.p, "资产设计/一.md", "一"),
                                               target(self.p, "资产设计/二.md", "二")]))
        (self.root / "资产设计/一.md").unlink()
        (self.root / ".工作室/采用索引.md").unlink()
        result = resolve(self.p, {"transaction_id": "tx-one"})
        self.assertEqual(result["status"], "committed_sync_pending")
        self.assertEqual(len(result["objects"]), 2)
        missing = next(obj for obj in result["objects"] if obj["path"] == "资产设计/一.md")
        self.assertEqual(missing["projection"]["reason"]["code"], "projection_missing")
        self.assertEqual(result["catalogue_projection"]["reason"]["code"], "projection_missing")
        self.assertTrue(all(obj["adopted"] for obj in result["objects"]))
        self.assertEqual(resume_sync(self.p, self.fresh())["status"], "committed")

    def test_stable_id_cannot_move_or_change_on_existing_path(self):
        adopt(self.p, adoption_request(self.p, [target(self.p, "资产设计/一.md", "一", object_id="A01")]))
        for path, object_id in (("资产设计/二.md", "A01"), ("资产设计/一.md", "A02")):
            request = adoption_request(self.p, [target(self.p, path, "二", object_id=object_id)], transaction_id="tx-conflict")
            self.assert_code("object_id_conflict", adopt, self.p, request)
        self.assertEqual(self.p.catalogue()["ids"], {"A01": "资产设计/一.md"})

    def test_disabled_or_legacy_project_is_zero_write(self):
        with tempfile.TemporaryDirectory(prefix="disabled-", dir=TEST_ROOT) as folder:
            root = Path(folder)
            project_at(root)
            for legacy in (False, True):
                if legacy:
                    write(root, "开始这里.md", "# 原 v0.8 项目\n")
                else:
                    caps = json.loads((root / "工作室能力.json").read_text(encoding="utf-8"))
                    caps["adoption_transactions"] = False
                    write(root, "工作室能力.json", canonical_bytes(caps))
                before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
                with self.assertRaises(StudioError):
                    Project(root, adoption=True)
                self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()})
                self.assertFalse((root / ".工作室").exists())

    def test_real_process_crashes_recover_each_durable_boundary(self):
        points = ["adoption_after_intent", "adoption_after_content_snapshot", "adoption_after_original_snapshot",
                  "adoption_after_manifest", "adoption_after_prepare_journal", "adoption_after_pending_state",
                  "after_prepare", "after_commit", "after_first_projection", "adoption_after_projection_write", "adoption_after_projection",
                  "adoption_after_index_projection_state", "adoption_after_index_projection", "adoption_after_finalize_state"]
        for point in points:
            with self.subTest(point=point), tempfile.TemporaryDirectory(prefix="crash-", dir=TEST_ROOT) as folder:
                p = project_at(Path(folder))
                make_workset(p)
                write(p.root, "资产设计/一.md", "原一")
                write(p.root, "资产设计/二.md", "原二")
                request = adoption_request(p, [target(p, "资产设计/一.md", "新一"), target(p, "资产设计/二.md", "新二")])
                request_file = p.root / "request.json"
                request_file.write_bytes(canonical_bytes(request))
                env = dict(os.environ, XIAOMO_STUDIO_CRASHPOINT=point)
                env.pop("XIAOMO_STUDIO_FAILPOINT", None)
                process = subprocess.run(worker_args(p.root, request_file), env=env, capture_output=True, timeout=30)
                self.assertEqual(process.returncode, 97, (point, process.stdout, process.stderr))
                before = resolve(p, {"transaction_id": "tx-one"})
                if before["adopted"]:
                    self.assertEqual(len(before["objects"]), 2)
                    for obj in before["objects"]:
                        self.assertEqual(p.hash(obj["snapshot"]), obj["sha256"])
                result = resume_sync(p, dict(session(p, "one"), transaction_id="tx-one"))
                self.assertEqual(result["status"], "committed", point)
                self.assertEqual(p.catalogue()["revision"], 1, point)
                self.assertEqual(p.read_bytes("资产设计/一.md"), "新一".encode())
                self.assertEqual(p.read_bytes("资产设计/二.md"), "新二".encode())

    def parallel(self, requests):
        start = self.root / "start.flag"
        processes, readiness = [], []
        for number, request in enumerate(requests):
            request_file = self.root / f"request-{number}.json"
            request_file.write_bytes(canonical_bytes(request))
            ready = self.root / f"ready-{number}.flag"
            readiness.append(ready)
            processes.append(subprocess.Popen(worker_args(self.root, request_file, start, ready), stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        deadline = time.monotonic() + 20
        while not all(path.exists() for path in readiness):
            if time.monotonic() > deadline:
                self.fail("Workers did not reach the shared start barrier")
            time.sleep(0.01)
        start.write_text("go", encoding="utf-8")
        results = []
        for process in processes:
            out, err = process.communicate(timeout=40)
            self.assertIn(process.returncode, (0, 2), (out, err))
            results.append((process.returncode, json.loads(out.decode("utf-8"))))
        return results

    def test_parallel_distinct_objects_do_not_lose_catalogue_entries(self):
        make_workset(self.p, "two")
        requests = [adoption_request(self.p, [target(self.p, "资产设计/一.md", "一")]),
                    adoption_request(self.p, [target(self.p, "资产设计/二.md", "二")], key="two", transaction_id="tx-two")]
        results = self.parallel(requests)
        self.assertEqual([code for code, _ in results], [0, 0], results)
        self.assertEqual(len(self.p.catalogue()["objects"]), 2)
        self.assertEqual(len(self.p.catalogue()["transactions"]), 2)
        self.assertEqual(self.p.catalogue()["revision"], 2)

    def test_parallel_same_object_has_one_winner(self):
        make_workset(self.p, "two")
        requests = [adoption_request(self.p, [target(self.p, "资产设计/一.md", "一", name="one")]),
                    adoption_request(self.p, [target(self.p, "资产设计/一.md", "二", name="two")], key="two", transaction_id="tx-two")]
        results = self.parallel(requests)
        self.assertEqual(sorted(code for code, _ in results), [0, 2], results)
        self.assertEqual(len(self.p.catalogue()["transactions"]), 1)
        self.assertEqual(next(iter(self.p.catalogue()["objects"].values()))["version"], 1)

    @unittest.skipUnless(os.name == "nt", "Windows native path resolution race")
    def test_parent_created_between_native_resolution_probes_is_inside_project(self):
        relative = "资产设计/新对象.md"
        target_path = self.root / relative
        native_resolve = ntpath._getfinalpathname
        errors = []
        created = False

        def resolve_with_parent_creation(path):
            nonlocal created
            try:
                return native_resolve(path)
            except OSError as error:
                if os.path.normcase(str(path)) == os.path.normcase(str(target_path)):
                    errors.append(error.winerror)
                    if not created:
                        self.assertEqual(error.winerror, 3)
                        created = True
                        # Preserve real Win32 queries and use a second process
                        # to create the parent in the native resolution gap.
                        result = subprocess.run([
                            sys.executable, "-B", "-X", "utf8", "-c",
                            "from pathlib import Path; import sys; Path(sys.argv[1]).mkdir(parents=True, exist_ok=True)",
                            str(target_path.parent),
                        ], capture_output=True, timeout=20)
                        self.assertEqual(result.returncode, 0, result.stderr)
                raise

        with patch.object(ntpath, "_getfinalpathname", resolve_with_parent_creation):
            self.assertEqual(self.p.path(relative), target_path)
        self.assertTrue(created)
        self.assertEqual(errors[0], 3)
        self.assertIn(2, errors[1:])
        self.assertTrue(target_path.parent.is_dir())
        self.assertFalse(target_path.parent.lstat().st_file_attributes & 0x400)
        self.assertFalse(target_path.exists())
        self.p.atomic_bytes(relative, b"safe new content", expected=None)
        self.assertEqual(target_path.read_bytes(), b"safe new content")

    @unittest.skipUnless(os.name == "nt", "Windows extended path representation")
    def test_extended_resolved_path_outside_project_is_still_rejected(self):
        outside = self.root.parent / "outside-project" / "object.md"
        extended = Path("\\\\?\\" + str(outside))
        with patch.object(Path, "resolve", return_value=extended):
            with self.assertRaises(StudioError) as caught:
                self.p.path("资产设计/不能越界.md")
        self.assertEqual(caught.exception.code, "unsafe_path")
        self.assertEqual(caught.exception.message, "Path escapes the project")
        self.assertFalse(outside.exists())

    @unittest.skipUnless(os.name == "nt", "Windows path aliases")
    def test_resolved_aliases_preserve_drive_unc_and_project_boundaries(self):
        cases = [
            (r"D:\Project", r"\\?\d:\PROJECT\child.md", True),
            (r"\\?\D:\Project", r"d:\project\child.md", True),
            (r"\\Server\Share\Project", r"\\?\UNC\server\SHARE\PROJECT\child.md", True),
            (r"\\?\UNC\Server\Share\Project", r"\\server\share\project\child.md", True),
            (r"D:\Project", r"\\?\E:\Project\child.md", False),
            (r"D:\Project", r"\\?\D:\Project-other\child.md", False),
            (r"\\Server\Share\Project", r"\\?\UNC\other\Share\Project\child.md", False),
            (r"\\Server\Share\Project", r"\\?\UNC\Server\other\Project\child.md", False),
            (r"D:\Project", r"\\?\GLOBALROOT\Device\HarddiskVolume1\Project\child.md", False),
        ]
        original_root = self.p.root
        try:
            for root, resolved, inside in cases:
                with self.subTest(root=root, resolved=resolved):
                    self.p.root = Path(root)
                    # Representation-only regression: no network share lookup.
                    # Real reparse rejection is covered separately by IO tests.
                    with patch("studio_io._is_reparse", return_value=False), patch.object(Path, "resolve", return_value=Path(resolved)):
                        if inside:
                            self.assertEqual(self.p.path("child.md"), self.p.root / "child.md")
                        else:
                            with self.assertRaises(StudioError) as caught:
                                self.p.path("child.md")
                            self.assertEqual(caught.exception.code, "unsafe_path")
        finally:
            self.p.root = original_root

    def test_parallel_different_paths_same_stable_id_has_one_winner(self):
        make_workset(self.p, "two")
        requests = [adoption_request(self.p, [target(self.p, "资产设计/一.md", "一", object_id="A01")]),
                    adoption_request(self.p, [target(self.p, "资产设计/二.md", "二", object_id="A01")], key="two", transaction_id="tx-two")]
        results = self.parallel(requests)
        self.assertEqual(sorted(code for code, _ in results), [0, 2], results)
        failure = next(result for code, result in results if code == 2)
        self.assertEqual(failure["code"], "object_id_conflict", failure)
        self.assertIn("occupied_path", failure["details"])
        self.assertEqual(len(self.p.catalogue()["ids"]), 1)

    def test_live_reader_never_observes_half_a_bundle(self):
        stop, ready, report = (self.root / name for name in ("stop.flag", "reader.flag", "reader.json"))
        args = [sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()), "--reader",
                str(self.root), str(stop), str(ready), str(report)]
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 20
        try:
            while not ready.exists():
                if time.monotonic() > deadline:
                    self.fail("Reader did not start")
                time.sleep(0.01)
            for number in range(5):
                writes = [target(self.p, "资产设计/一.md", f"一版{number}"), target(self.p, "资产设计/二.md", f"二版{number}")]
                transaction_id = f"tx-{number}"
                result = adopt(self.p, adoption_request(self.p, writes, transaction_id=transaction_id))
                # A real Windows reader may briefly prevent guarded view replacement.
                # Finish the reported pending sync before submitting another transaction.
                sync_deadline = time.monotonic() + 5
                while result["status"] == "committed_sync_pending" and time.monotonic() < sync_deadline:
                    time.sleep(0.01)
                    result = resume_sync(self.p, dict(session(self.p, "one"), transaction_id=transaction_id))
                self.assertEqual(result["status"], "committed", result)
        finally:
            stop.write_text("stop", encoding="utf-8")
            out, err = process.communicate(timeout=40)
        self.assertEqual(process.returncode, 0, (out, err))
        result = json.loads(report.read_text(encoding="utf-8"))
        self.assertTrue(result["observations"])
        self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        raise SystemExit(worker())
    if len(sys.argv) > 1 and sys.argv[1] == "--reader":
        raise SystemExit(reader_worker())
    unittest.main()
