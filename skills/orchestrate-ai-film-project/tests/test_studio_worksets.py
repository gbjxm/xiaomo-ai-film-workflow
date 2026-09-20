"""Filesystem and process-level tests; no real project or media fixture is used."""
from __future__ import annotations

import copy
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from studio_io import INDEX_PATH, Project, StudioError, canonical_bytes, now_utc, sha_bytes
import studio_worksets as ws

WORKER = r"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from studio_io import Project, StudioError
import studio_worksets as ws
project, action, req_path, ready, go = sys.argv[2:]
request = json.loads(Path(req_path).read_text(encoding='utf-8'))
if ready != '-':
    Path(ready).write_text('ready',encoding='utf-8')
    deadline=time.monotonic()+15
    while not Path(go).exists():
        if time.monotonic()>deadline:
            raise SystemExit(91)
        time.sleep(0.01)
try:
    value=getattr(ws,action)(Project(project),request)
    print(json.dumps({'ok':True,'result':value},ensure_ascii=False))
except StudioError as exc:
    print(json.dumps({'ok':False,'code':exc.code,'details':exc.details},ensure_ascii=False))
    raise SystemExit(2)
"""


class WorksetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="xiaomo-workset-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        (self.root / "开始这里.md").write_text("# 隔离测试\nworkflowStudio: studio-v0.9\n", encoding="utf-8")
        self.caps = {"schema":"xiaomo.studio-capabilities/v1","workflowStudio":"studio-v0.9","runtime_schema":"xiaomo.studio-runtime/v1","candidate_design":True,"workset_runtime":True,"adoption_transactions":True,"media_execution":False,"test_mode":True}
        (self.root / "工作室能力.json").write_bytes(canonical_bytes(self.caps))
        self.p = Project(self.root)

    def create(self, key="clip", department="C2", holder="task-one", **extra):
        return ws.create(self.p, {"workset_key":key,"department":department,"title":"测试内容", "holder":holder, **extra})

    def session(self, source="clip", **extra):
        state = ws.load_state(self.p, source) if isinstance(source,str) else source
        return {"workset_key":state["workset_key"],"holder":state["lease"]["holder"],"lease_id":state["lease"]["id"],"expected_revision":state["revision"], **extra}

    def save(self, content="完整候选初稿", key="clip", **extra):
        state=ws.load_state(self.p,key)
        relative=f"工作稿/{state['department']}/{key}/当前.md"
        return ws.save_draft(self.p, self.session(state,content=content,expected_draft_sha256=self.p.hash(relative),next_step="审阅完整稿", **extra))

    def assert_error(self, code, function, *args):
        with self.assertRaises(StudioError) as caught:
            function(*args)
        self.assertEqual(code,caught.exception.code)
        return caught.exception

    def tree(self):
        return {str(path.relative_to(self.root)):(path.read_bytes(),path.stat().st_mtime_ns) for path in self.root.rglob("*") if path.is_file()}

    def worker(self, request, *, action="save_draft", env_extra=None, ready="-", go="-", suffix="one"):
        request_path=self.base/("request-"+suffix+".json")
        request_path.write_bytes(canonical_bytes(request))
        env=os.environ.copy()
        env["PYTHONUTF8"]="1"
        env["PYTHONDONTWRITEBYTECODE"]="1"
        env.pop("XIAOMO_STUDIO_FAILPOINT",None)
        env.pop("XIAOMO_STUDIO_CRASHPOINT",None)
        env.update(env_extra or {})
        return subprocess.Popen([sys.executable,"-B","-X","utf8","-c",WORKER,str(SCRIPTS),str(self.root),action,str(request_path),str(ready),str(go)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding="utf-8",env=env)

    def parallel(self, requests):
        go=self.base/"go"
        processes=[]
        try:
            for i,request in enumerate(requests):
                processes.append(self.worker(request,ready=self.base/f"ready-{i}",go=go,suffix=str(i)))
            deadline=time.monotonic()+15
            while not all((self.base/f"ready-{i}").exists() for i in range(len(processes))):
                if time.monotonic()>deadline or any(p.poll() is not None for p in processes):
                    self.fail("Workers did not reach the simultaneous-start barrier")
                time.sleep(0.01)
            go.write_text("go",encoding="utf-8")
            outputs=[]
            for process in processes:
                out,err=process.communicate(timeout=20)
                self.assertFalse(err,err)
                outputs.append((process.returncode,json.loads(out)))
            return outputs
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)

    def interrupted_save(self, old="旧完整稿", new="新完整稿", hard=False):
        self.create()
        previous=self.save(old)
        request=self.session(content=new,expected_draft_sha256=previous["draft"]["sha256"],next_step="新稿待审")
        if hard:
            process=self.worker(request,env_extra={"XIAOMO_STUDIO_CRASHPOINT":"draft_after_state"})
            out,err=process.communicate(timeout=15)
            self.assertEqual(process.returncode,97,(out,err))
        else:
            with mock.patch.dict(os.environ,{"XIAOMO_STUDIO_FAILPOINT":"draft_after_state"}):
                self.assert_error("injected_failure",ws.save_draft,self.p,request)
        return previous,ws.inspect(self.p,{"workset_key":"clip"})

    def test_create_is_independent_and_inspect_list_are_read_only(self):
        one=self.create("image","C1")
        two=self.create("video","C2")
        self.assertNotEqual(one["lease"]["id"],two["lease"]["id"])
        self.assertEqual(one["revision"],1)
        before=self.tree()
        result=ws.inspect(self.p,{"workset_key":"image"})
        listed=ws.list_worksets(self.p,{})
        self.assertEqual([item["workset_key"] for item in listed["worksets"]],["image","video"])
        self.assertEqual(len(ws.list_worksets(self.p,{"department":"C1"})["worksets"]),1)
        self.assertFalse(result["resume_grants_adoption"])
        self.assertEqual(before,self.tree())
        self.assert_error("workset_exists",ws.create,self.p,{"workset_key":"image","department":"C1","title":"覆盖","holder":"other"})
        self.assertEqual(before,self.tree())

    def test_long_complete_draft_is_immutable_and_cache_is_short(self):
        self.create(title="标题"*2000,holder="holder-"+"任"*3000)
        content="完整稿不能被缓存截断【特殊末尾】\n"*12000
        result=self.save(content,metadata={"private_plan":"不要在Inspect输出这段元数据正文"})
        self.assertEqual(self.p.read_bytes(result["draft"]["snapshot"]),content.encode("utf-8"))
        self.assertEqual(self.p.read_bytes(result["projection"]["path"]),content.encode("utf-8"))
        cache=self.p.read_bytes("续接缓存/clip.json")
        self.assertLessEqual(len(cache),6144)
        inspected=ws.inspect(self.p,{"workset_key":"clip"})
        serialized=json.dumps(inspected,ensure_ascii=False)
        self.assertNotIn("完整稿不能被缓存截断",serialized)
        self.assertNotIn("不要在Inspect输出",serialized)
        self.assertEqual(inspected["snapshot"]["status"],"valid")
        self.assertEqual(inspected["draft_status"],"candidate")

    def test_repeated_content_no_change_and_progress_does_not_repeat_snapshot(self):
        self.create()
        first=self.save("第一版全文")
        before=self.tree()
        again=self.save("第一版全文")
        self.assertEqual(again["status"],"no_change")
        self.assertEqual(again["revision"],first["revision"])
        self.assertEqual(before,self.tree())
        request=self.session(content="第一版全文",expected_draft_sha256=again["draft"]["sha256"],next_step="只更新下一步")
        progress=ws.save_draft(self.p,request)
        self.assertEqual(progress["draft"]["snapshot"],first["draft"]["snapshot"])
        self.assertEqual(progress["draft"]["content_version"],1)
        changed=self.save("第二版全文")
        self.assertNotEqual(changed["draft"]["snapshot"],first["draft"]["snapshot"])
        self.assertEqual(self.p.read_bytes(first["draft"]["snapshot"]),"第一版全文".encode())

    def test_metadata_change_is_a_new_candidate_revision(self):
        self.create()
        first=self.save("不变正文",metadata={"duration":20})
        second=self.save("不变正文",metadata={"duration":30})
        self.assertEqual(second["draft"]["sha256"],first["draft"]["sha256"])
        self.assertEqual(second["draft"]["content_version"],2)
        self.assertNotEqual(second["draft"]["snapshot"],first["draft"]["snapshot"])
        self.assertEqual(ws.load_state(self.p,"clip")["draft"]["metadata"],{"duration":30})

    def test_stale_view_hash_never_overwrites_user_edit_and_correct_hash_archives_it(self):
        self.create()
        first=self.save("模型第一版")
        view=self.p.path(first["projection"]["path"])
        view.write_text("用户已保存的重要新文字",encoding="utf-8")
        stale=self.session(content="模型第二版",expected_draft_sha256=first["draft"]["sha256"])
        before=self.tree()
        self.assert_error("draft_conflict",ws.save_draft,self.p,stale)
        self.assertEqual(before,self.tree())
        info=ws.inspect(self.p,{"workset_key":"clip"})
        self.assertEqual(info["projection"]["status"],"user_modified")
        self.assertIn("editable_projection",{item["kind"] for item in info["required_read_set"]})
        merged=self.save("合并后的完整文字")
        preserved=merged["preserved_user_edit"]
        self.assertIsNotNone(preserved)
        self.assertEqual(self.p.read_bytes(preserved["path"]),"用户已保存的重要新文字".encode())
        self.assertEqual(view.read_text(encoding="utf-8"),"合并后的完整文字")

    def test_cache_missing_corrupt_and_oversized_do_not_block_read_only_recovery(self):
        self.create()
        saved=self.save("可从不可变稿恢复")
        cache=self.p.path("续接缓存/clip.json")
        for kind,payload in (("missing",None),("corrupt",b"{broken"),("oversized",b"x"*6145)):
            if payload is None:
                cache.unlink()
            else:
                cache.write_bytes(payload)
            before=self.tree()
            info=ws.inspect(self.p,{"workset_key":"clip"})
            self.assertEqual(info["cache"]["status"],kind)
            self.assertEqual(info["draft"]["snapshot"],saved["draft"]["snapshot"])
            self.assertEqual(before,self.tree())
        renewed=ws.claim(self.p,self.session())
        self.assertEqual(renewed["cache"]["status"],"valid")
        self.assertLessEqual(cache.stat().st_size,6144)

    def test_unreadable_cache_never_blocks_read_only_full_draft_recovery(self):
        self.create()
        saved = self.save("缓存不可读仍保留的完整稿")
        original_read = self.p.read_bytes

        def deny_cache(relative):
            if relative == "续接缓存/clip.json":
                raise StudioError("io_error", "Simulated cache sharing violation")
            return original_read(relative)

        before = self.tree()
        with mock.patch.object(self.p, "read_bytes", side_effect=deny_cache):
            result = ws.inspect(self.p, {"workset_key": "clip"})
        self.assertEqual(result["cache"]["status"], "unavailable")
        self.assertEqual(result["draft"]["snapshot"], saved["draft"]["snapshot"])
        self.assertEqual(result["snapshot"]["status"], "valid")
        self.assertEqual(before, self.tree())

    def test_failed_cache_write_does_not_mask_a_durably_saved_draft(self):
        self.create()
        original_read = self.p.read_bytes
        original_write = self.p.atomic_json

        def deny_cache_read(relative):
            if relative == "续接缓存/clip.json":
                raise StudioError("io_error", "Cache is held by another application")
            return original_read(relative)

        def deny_cache_write(relative, value, **kwargs):
            if relative == "续接缓存/clip.json":
                raise StudioError("io_error", "Cache update unavailable")
            return original_write(relative, value, **kwargs)

        with mock.patch.object(self.p, "read_bytes", side_effect=deny_cache_read), mock.patch.object(self.p, "atomic_json", side_effect=deny_cache_write):
            result = self.save("应准确报告已保存的全文")
        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["cache_status"], "pending")
        self.assertEqual(result["cache"]["status"], "unavailable")
        self.assertEqual(self.p.read_bytes(result["draft"]["snapshot"]), "应准确报告已保存的全文".encode())
        self.assertEqual(self.p.read_bytes(result["projection"]["path"]), "应准确报告已保存的全文".encode())

    def test_cache_disappearing_during_inspect_is_a_recoverable_missing_cache(self):
        self.create()
        saved = self.save("完整稿")
        original_read = self.p.read_bytes
        removed = False

        def remove_before_read(relative):
            nonlocal removed
            if relative == "续接缓存/clip.json" and not removed:
                removed = True
                self.p.path(relative).unlink()
            return original_read(relative)

        with mock.patch.object(self.p, "read_bytes", side_effect=remove_before_read):
            result = ws.inspect(self.p, {"workset_key": "clip"})
        self.assertEqual(result["cache"]["status"], "missing")
        self.assertEqual(result["draft"]["snapshot"], saved["draft"]["snapshot"])
        self.assertFalse(self.p.path("续接缓存/clip.json").exists())

    def test_injected_failure_after_state_recovers_without_cache_or_extra_snapshot(self):
        previous,info=self.interrupted_save()
        self.assertEqual(info["draft"]["projection_status"],"pending")
        self.assertEqual(info["projection"]["status"],"pending_safe_rebuild")
        self.assertEqual(self.p.read_bytes(info["draft"]["snapshot"]),"新完整稿".encode())
        self.assertEqual(self.p.read_bytes(info["projection"]["path"]),"旧完整稿".encode())
        self.p.path("续接缓存/clip.json").unlink()
        renewed=ws.claim(self.p,self.session())
        self.assertEqual(renewed["draft"]["snapshot"],info["draft"]["snapshot"])
        self.assertEqual(renewed["projection"]["status"],"synced")
        self.assertEqual(self.p.read_bytes(renewed["projection"]["path"]),"新完整稿".encode())
        self.assertEqual(len(list(self.p.path("工作稿/C2/clip/历史").glob("稿*.md"))),2)
        self.assertFalse(renewed["resume_grants_adoption"])

    def test_hard_exit_releases_os_lock_and_recovers_state_pointer(self):
        _,info=self.interrupted_save(hard=True)
        self.assertEqual(info["projection"]["status"],"pending_safe_rebuild")
        started=time.monotonic()
        recovered=ws.claim(self.p,self.session())
        self.assertLess(time.monotonic()-started,5)
        self.assertEqual(recovered["projection"]["status"],"synced")
        self.assertEqual(self.p.read_bytes(recovered["projection"]["path"]),"新完整稿".encode())

    def test_post_crash_unknown_manual_projection_is_never_rebuilt_over(self):
        _,info=self.interrupted_save(hard=True)
        view=self.p.path(info["projection"]["path"])
        view.write_text("崩溃后用户保存的第三稿",encoding="utf-8")
        renewed=ws.claim(self.p,self.session())
        self.assertEqual(renewed["draft"]["projection_status"],"conflict")
        self.assertEqual(view.read_text(encoding="utf-8"),"崩溃后用户保存的第三稿")
        merged=self.save("已合并完整新稿")
        self.assertEqual(self.p.read_bytes(merged["preserved_user_edit"]["path"]),"崩溃后用户保存的第三稿".encode())
        self.assertEqual(merged["projection"]["status"],"synced")

    def test_missing_snapshot_is_not_replaced_by_cache_or_projection(self):
        self.create()
        saved=self.save("唯一完整稿")
        self.p.path(saved["draft"]["snapshot"]).unlink()
        before=self.tree()
        info=ws.inspect(self.p,{"workset_key":"clip"})
        self.assertEqual(info["snapshot"]["status"],"missing")
        self.assertEqual(before,self.tree())
        self.assert_error("draft_snapshot_invalid",ws.claim,self.p,self.session())
        self.assertEqual(before,self.tree())

    def test_invalid_or_expired_sessions_make_no_changes(self):
        created=self.create()
        request=self.session(content="不能写",expected_draft_sha256=None)
        before=self.tree()
        self.assert_error("lease_conflict",ws.save_draft,self.p,{**request,"lease_id":"wrong"})
        self.assertEqual(before,self.tree())
        with self.p.lock("workset:clip"):
            state=ws.load_state(self.p,"clip")
            state["lease"]["expires_at"]="2000-01-01T00:00:00Z"
            state["revision"]+=1
            ws.persist_state(self.p,state)
        before=self.tree()
        self.assert_error("lease_expired",ws.save_draft,self.p,self.session(content="不能写",expected_draft_sha256=None))
        self.assertEqual(before,self.tree())
        claimed=ws.claim(self.p,{"workset_key":"clip","holder":"new-task","expected_revision":state["revision"]})
        self.assertNotEqual(claimed["lease"]["id"],created["lease"]["id"])
        self.assert_error("revision_conflict",ws.save_draft,self.p,request)

    def test_claim_same_holder_needs_token_and_takeover_needs_all_evidence(self):
        first=self.create()
        other=self.create("other",holder="other-task")
        request={"workset_key":"clip","holder":"task-one","expected_revision":first["revision"]}
        self.assert_error("lease_conflict",ws.claim,self.p,request)
        request["holder"]="task-new"
        self.assert_error("takeover_requires_evidence",ws.claim,self.p,request)
        self.assert_error("takeover_requires_evidence",ws.claim,self.p,{**request,"user_switch_evidence":"用户要求切换","previous_holder_stopped":True})
        result=ws.claim(self.p,{**request,"user_switch_evidence":"用户要求切换","previous_holder_stopped":True,"stop_evidence":"旧任务已停止"})
        self.assertEqual(result["status"],"taken_over")
        self.assertEqual(ws.load_state(self.p,"other")["lease"],other["lease"])
        before=self.tree()
        self.assert_error("lease_conflict",ws.save_draft,self.p,{**self.session(),"holder":"task-one","lease_id":first["lease"]["id"],"content":"旧任务写入","expected_draft_sha256":None})
        self.assertEqual(before,self.tree())

    def test_handoff_prepare_cancel_and_activate_preserve_independent_state(self):
        self.create()
        saved=self.save()
        prepared=ws.prepare_handoff(self.p,self.session(user_switch_evidence="用户要求新开同内容任务"))
        self.assertEqual(prepared["lease"]["id"],saved["lease"]["id"])
        before=self.tree()
        self.assert_error("handoff_pending",ws.save_draft,self.p,self.session(content="写入应停",expected_draft_sha256=saved["draft"]["sha256"]))
        self.assertEqual(before,self.tree())
        cancelled=ws.cancel_handoff(self.p,self.session(handoff_id=prepared["handoff_id"]))
        self.assertIsNone(cancelled["handoff"])
        self.assertEqual(cancelled["lease"]["id"],saved["lease"]["id"])
        prepared=ws.prepare_handoff(self.p,self.session(user_switch_evidence="用户要求切换"))
        self.assert_error("handoff_mismatch",ws.activate_handoff,self.p,self.session(handoff_id="wrong",target_holder="next"))
        activated=ws.activate_handoff(self.p,self.session(handoff_id=prepared["handoff_id"],target_holder="next"))
        self.assertEqual(activated["lease"]["holder"],"next")
        self.assertNotEqual(activated["lease"]["id"],prepared["lease"]["id"])
        self.assertIsNone(activated["handoff"])
        self.assertEqual(activated["draft"]["snapshot"],saved["draft"]["snapshot"])
        self.assert_error("lease_conflict",ws.save_draft,self.p,{**self.session(),"holder":"task-one","lease_id":prepared["lease"]["id"],"content":"旧持有者","expected_draft_sha256":saved["draft"]["sha256"]})

    def test_pending_adoption_blocks_draft_but_survives_claim_and_handoff(self):
        self.create()
        saved=self.save()
        pending={"transaction_id":"tx_one","request_hash":"a"*64,"manifest_path":".工作室/事务/tx_one/manifest.json","status":"prepared"}
        last={"transaction_id":"tx_old","status":"committed"}
        with self.p.lock("workset:clip"):
            state=ws.load_state(self.p,"clip")
            state["pending_transaction"]=copy.deepcopy(pending)
            state["last_adoption"]=copy.deepcopy(last)
            state["revision"]+=1
            ws.persist_state(self.p,state)
        self.assert_error("adoption_pending",ws.save_draft,self.p,self.session(content="不可改变采用源",expected_draft_sha256=saved["draft"]["sha256"]))
        renewed=ws.claim(self.p,self.session())
        self.assertEqual(ws.load_state(self.p,"clip")["pending_transaction"],pending)
        prepared=ws.prepare_handoff(self.p,self.session(user_switch_evidence="用户要求续接采用恢复"))
        self.assert_error("handoff_pending",ws.require_session,ws.load_state(self.p,"clip"),self.session(),True)
        activated=ws.activate_handoff(self.p,self.session(handoff_id=prepared["handoff_id"],target_holder="resume-task"))
        state=ws.load_state(self.p,"clip")
        ws.require_session(state,self.session(state),allow_pending=True)
        self.assertEqual(state["pending_transaction"],pending)
        self.assertEqual(state["last_adoption"],last)
        self.assertEqual(activated["draft"]["snapshot"],saved["draft"]["snapshot"])

    def test_unreadable_projection_keeps_inspect_and_claim_recoverable(self):
        self.create()
        saved = self.save("可用的完整不可变稿")
        projection = saved["projection"]["path"]
        original_hash = self.p.hash
        original_write = self.p.atomic_bytes
        projection_writes = []

        def deny_projection(relative):
            if relative == projection:
                raise StudioError("io_error", "Projection is open in an editor")
            return original_hash(relative)

        def track_writes(relative, data, **kwargs):
            if relative == projection:
                projection_writes.append(relative)
            return original_write(relative, data, **kwargs)

        before = self.tree()
        with mock.patch.object(self.p, "hash", side_effect=deny_projection):
            info = ws.inspect(self.p, {"workset_key": "clip"})
        self.assertEqual(before, self.tree())
        self.assertEqual(info["projection"]["status"], "unavailable")
        self.assertEqual(info["projection"]["error"]["code"], "io_error")
        self.assertEqual(info["snapshot"]["status"], "valid")
        self.assertIn("draft_snapshot", {item["kind"] for item in info["required_read_set"]})
        with mock.patch.object(self.p, "hash", side_effect=deny_projection), mock.patch.object(self.p, "atomic_bytes", side_effect=track_writes):
            renewed = ws.claim(self.p, self.session())
        self.assertEqual(renewed["status"], "renewed")
        self.assertEqual(renewed["draft"]["projection_status"], "pending")
        self.assertEqual(renewed["projection"]["status"], "unavailable")
        self.assertEqual(projection_writes, [])
        self.assertEqual(self.p.read_bytes(projection), "可用的完整不可变稿".encode())
        self.assertEqual(ws.claim(self.p, self.session())["projection"]["status"], "synced")

    def test_disappearing_projection_returns_pending_without_read_side_writes(self):
        self.create()
        saved = self.save("投影缺失时仍可读的完整稿")
        projection = saved["projection"]["path"]
        self.p.path(projection).unlink()
        before = self.tree()
        info = ws.inspect(self.p, {"workset_key": "clip"})
        self.assertEqual(info["projection"]["status"], "pending")
        self.assertEqual(info["projection"]["reason"], "projection_missing")
        self.assertEqual(info["snapshot"]["status"], "valid")
        self.assertEqual(before, self.tree())
        self.assertFalse(self.p.path(projection).exists())
        repaired = ws.claim(self.p, self.session())
        self.assertEqual(repaired["projection"]["status"], "synced")
        self.assertEqual(self.p.read_bytes(projection), "投影缺失时仍可读的完整稿".encode())

    def test_file_missing_during_projection_read_is_not_an_empty_write_baseline(self):
        _, saved = self.interrupted_save()
        projection = saved["projection"]["path"]
        original_hash = self.p.hash
        original_write = self.p.atomic_bytes
        attempts = []

        def transient_missing(relative):
            if relative == projection:
                raise StudioError("file_missing", "Projection moved during guarded publication")
            return original_hash(relative)

        def track_writes(relative, data, **kwargs):
            if relative == projection:
                attempts.append(relative)
            return original_write(relative, data, **kwargs)

        with mock.patch.object(self.p, "hash", side_effect=transient_missing), mock.patch.object(self.p, "atomic_bytes", side_effect=track_writes):
            info = ws.inspect(self.p, {"workset_key": "clip"})
            renewed = ws.claim(self.p, self.session())
        self.assertEqual(info["projection"]["status"], "pending")
        self.assertEqual(renewed["draft"]["projection_status"], "pending")
        self.assertEqual(attempts, [])
        self.assertEqual(self.p.read_bytes(projection), "旧完整稿".encode())
        self.assertEqual(self.p.read_bytes(renewed["draft"]["snapshot"]), "新完整稿".encode())

    def test_canonical_state_and_snapshot_read_errors_still_fail(self):
        self.create()
        saved = self.save("不可把canonical错误当视图问题")
        original_read = self.p.read_bytes
        original_hash = self.p.hash

        def broken_state(relative):
            if relative == ".工作室/工作集/clip/state.json":
                raise StudioError("io_error", "Canonical state is unavailable")
            return original_read(relative)

        def broken_snapshot(relative):
            if relative == saved["draft"]["snapshot"]:
                raise StudioError("io_error", "Canonical snapshot is unavailable")
            return original_hash(relative)

        before = self.tree()
        with mock.patch.object(self.p, "read_bytes", side_effect=broken_state):
            self.assert_error("io_error", ws.inspect, self.p, {"workset_key": "clip"})
        with mock.patch.object(self.p, "hash", side_effect=broken_snapshot):
            self.assert_error("io_error", ws.inspect, self.p, {"workset_key": "clip"})
            self.assert_error("io_error", ws.claim, self.p, self.session())
        self.assertEqual(before, self.tree())

    def test_real_windows_projection_handle_does_not_block_snapshot_inspection(self):
        self.create()
        saved = self.save("真实句柄占用测试")
        projection = saved["projection"]["path"]
        if os.name != "nt":
            original_hash = self.p.hash
            with mock.patch.object(self.p, "hash", side_effect=lambda relative: (_ for _ in ()).throw(StudioError("file_busy", "Occupied projection")) if relative == projection else original_hash(relative)):
                self.assertEqual(ws.inspect(self.p, {"workset_key": "clip"})["projection"]["status"], "unavailable")
            return
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateFileW(str(self.p.path(projection)), 0x80000000, 0, None, 3, 0x80, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value, ctypes.get_last_error())
        try:
            info = ws.inspect(self.p, {"workset_key": "clip"})
            self.assertEqual(info["projection"]["status"], "unavailable")
            self.assertEqual(info["snapshot"]["status"], "valid")
            renewed = ws.claim(self.p, self.session())
            self.assertEqual(renewed["draft"]["projection_status"], "pending")
            self.assertEqual(renewed["projection"]["status"], "unavailable")
        finally:
            kernel.CloseHandle(handle)
        self.assertEqual(self.p.read_bytes(projection), "真实句柄占用测试".encode())

    def test_file_dependencies_report_only_the_declared_changes(self):
        (self.root/"依赖.md").write_text("采用视觉基准",encoding="utf-8")
        (self.root/"不相关.md").write_text("不应枚举",encoding="utf-8")
        self.create()
        digest=self.p.hash("依赖.md")
        self.save(dependencies=[{"path":"依赖.md","kind":"file","sha256":digest.upper()}])
        (self.root/"不相关.md").write_text("变化也无影响",encoding="utf-8")
        self.assertEqual(ws.inspect(self.p,{"workset_key":"clip"})["dependency_changes"],[])
        (self.root/"依赖.md").write_text("被修改的新基准",encoding="utf-8")
        info=ws.inspect(self.p,{"workset_key":"clip"})
        self.assertEqual(len(info["dependency_changes"]),1)
        self.assertEqual(info["dependency_changes"][0]["status"],"changed")
        self.assertEqual(info["dependency_changes"][0]["expected_sha256"],digest)
        (self.root/"依赖.md").unlink()
        self.assertEqual(ws.inspect(self.p,{"workset_key":"clip"})["dependency_changes"][0]["status"],"missing")

    def test_adopted_dependency_uses_immutable_snapshot_not_editable_view(self):
        body="正式采用资产".encode()
        snapshot=".工作室/事务/tx_one/content/a01.md"
        self.p.immutable(snapshot,body)
        self.p.immutable(".工作室/事务/tx_one/manifest.json",b"{}\n")
        cat=self.p.catalogue()
        cat["revision"]=1
        cat["objects"]["资产设计/A01.md"]={"owner":"C1","version":1,"sha256":sha_bytes(body),"snapshot":snapshot,"transaction_id":"tx_one"}
        cat["transactions"]["tx_one"]={"request_hash":"a"*64,"manifest_path":".工作室/事务/tx_one/manifest.json","manifest_sha256":sha_bytes(b"{}\n"),"workset_key":"asset"}
        with self.p.lock("catalogue"):
            self.p.publish_catalogue(cat,0)
        self.p.atomic_bytes("资产设计/A01.md",b"manual projection",expected=None)
        self.create()
        self.save(dependencies=[{"path":"资产设计/A01.md","kind":"adopted","sha256":sha_bytes(body),"version":1}])
        info=ws.inspect(self.p,{"workset_key":"clip"})
        self.assertEqual(info["dependency_changes"],[])
        reference=next(item for item in info["required_read_set"] if item["kind"]=="adopted_dependency")
        self.assertEqual(reference["path"],snapshot)
        self.assertEqual(reference["sha256"],sha_bytes(body))

    def test_true_parallel_different_worksets_both_save(self):
        self.create("one",holder="worker-one")
        self.create("two",holder="worker-two")
        reqs=[self.session(key,content=f"并行工作集 {key} 的完整稿",expected_draft_sha256=None) for key in ("one","two")]
        results=self.parallel(reqs)
        self.assertEqual([code for code,_ in results],[0,0])
        for key in ("one","two"):
            info=ws.inspect(self.p,{"workset_key":key})
            self.assertEqual(info["revision"],2)
            self.assertEqual(info["projection"]["status"],"synced")
            self.assertEqual(self.p.read_bytes(info["draft"]["snapshot"]),f"并行工作集 {key} 的完整稿".encode())

    def test_true_parallel_same_workset_one_wins_without_overwrite(self):
        self.create()
        original=self.session(expected_draft_sha256=None)
        results=self.parallel([{**original,"content":"候选甲"},{**original,"content":"候选乙"}])
        self.assertEqual(sorted(code for code,_ in results),[0,2])
        rejected=next(result for code,result in results if code==2)
        self.assertEqual(rejected["code"],"revision_conflict")
        info=ws.inspect(self.p,{"workset_key":"clip"})
        self.assertEqual(info["revision"],2)
        self.assertEqual(len(list(self.p.path("工作稿/C2/clip/历史").glob("稿*.md"))),1)
        self.assertIn(self.p.read_bytes(info["draft"]["snapshot"]),("候选甲".encode(),"候选乙".encode()))

    def test_key_path_and_dependency_case_alias_rejected_before_draft_mutation(self):
        for key in ("../escape","CLIP","a/b","c:ads","con","tail "):
            self.assert_error("invalid_key",ws.create,self.p,{"workset_key":key,"department":"C1","title":"test","holder":"one"})
        self.create()
        (self.root/"Base.md").write_text("base",encoding="utf-8")
        digest=self.p.hash("Base.md")
        before=self.tree()
        request=self.session(content="全文",expected_draft_sha256=None,dependencies=[{"path":"Base.md","sha256":digest},{"path":"base.md","sha256":digest}])
        self.assert_error("duplicate_dependency",ws.save_draft,self.p,request)
        self.assertEqual(before,self.tree())
        request["dependencies"]=[{"path":"../outside.md","sha256":digest}]
        self.assert_error("unsafe_path",ws.save_draft,self.p,request)
        self.assertEqual(before,self.tree())

    def test_ttl_bounds_and_disabled_or_legacy_projects_do_not_create_runtime(self):
        before=self.tree()
        for ttl in (True,59,86401):
            self.assert_error("invalid_input",ws.create,self.p,{"workset_key":"ttl","department":"A","title":"test","holder":"one","ttl_seconds":ttl})
            self.assertEqual(before,self.tree())
        self.caps["workset_runtime"]=False
        (self.root/"工作室能力.json").write_bytes(canonical_bytes(self.caps))
        self.assert_error("runtime_unavailable",Project,self.root)
        self.assertFalse((self.root/".工作室").exists())
        (self.root/"开始这里.md").write_text("# v0.8\n",encoding="utf-8")
        self.assert_error("unsupported_project",Project,self.root)
        self.assertFalse((self.root/".工作室").exists())

    def test_loaded_state_hash_survives_a_concurrent_publication(self):
        self.create()
        old_hash = self.p.hash(".工作室/工作集/clip/state.json")
        original_load = ws.load_state
        published = False

        def read_then_publish(project, key):
            nonlocal published
            original = original_load(project, key)
            if not published:
                published = True
                newer = copy.deepcopy(original)
                newer["revision"] += 1
                newer["next_step"] = "另一操作的新进度"
                with project.lock("workset:" + key):
                    ws.persist_state(project, newer)
            return original

        with mock.patch.object(ws, "load_state", side_effect=read_then_publish):
            info = ws.inspect(self.p, {"workset_key": "clip"})
        state_ref = next(item for item in info["required_read_set"] if item["kind"] == "workset_state")
        self.assertNotEqual(state_ref["sha256"], old_hash)
        self.assertEqual(state_ref["sha256"], self.p.hash(".工作室/工作集/clip/state.json"))
        self.assertEqual(info["revision"], 2)
        self.assertEqual(info["next_step"], "另一操作的新进度")
        self.assertEqual(info["cache"]["status"], "valid")

    def test_inspect_retries_a_concurrent_save_before_reporting_user_edits(self):
        self.create()
        initial = self.save("旧版完整稿")
        original_inspection = ws._inspection
        published = False

        def read_with_concurrent_save(project, state):
            nonlocal published
            if not published:
                published = True
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(self.save, "并发保存的新完整稿").result(timeout=10)
            return original_inspection(project, state)

        with mock.patch.object(ws, "_inspection", side_effect=read_with_concurrent_save):
            result = ws.inspect(self.p, {"workset_key": "clip"})
        self.assertEqual(result["revision"], initial["revision"] + 1)
        self.assertEqual(result["projection"]["status"], "synced")
        self.assertEqual(result["snapshot"]["sha256"], result["projection"]["sha256"])
        self.assertNotIn("editable_projection", {item["kind"] for item in result["required_read_set"]})

    def test_inspect_reports_busy_after_bounded_read_retries(self):
        self.create()
        self.save("初稿")
        original_inspection = ws._inspection
        publishing = False
        publications = 0

        def continually_publish(project, state):
            nonlocal publishing, publications
            if not publishing:
                publishing = True
                publications += 1
                try:
                    self.save("连续变更稿 " + str(publications))
                finally:
                    publishing = False
            return original_inspection(project, state)

        with mock.patch.object(ws, "_inspection", side_effect=continually_publish):
            self.assert_error("workset_busy", ws.inspect, self.p, {"workset_key": "clip"})
        self.assertEqual(publications, 3)

    def test_persist_state_rejects_a_stale_loaded_disk_baseline(self):
        self.create()
        stale = ws.load_state(self.p, "clip")
        newer = copy.deepcopy(stale)
        newer["revision"] += 1
        newer["next_step"] = "新状态必须保留"
        with self.p.lock("workset:clip"):
            ws.persist_state(self.p, newer)
        before = self.tree()
        stale["revision"] += 1
        stale["next_step"] = "过时覆盖"
        with self.p.lock("workset:clip"):
            self.assert_error("file_conflict", ws.persist_state, self.p, stale)
        self.assertEqual(before, self.tree())

    def test_reparse_workset_directory_is_not_followed(self):
        self.create()
        outside=self.base/"outside"
        outside.mkdir()
        alias=self.root/".工作室"/"工作集"/"alias"
        if os.name == "nt":
            command = "New-Item -ItemType Junction -Path '" + str(alias).replace("'", "''") + "' -Target '" + str(outside).replace("'", "''") + "' -ErrorAction Stop | Out-Null"
            result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        else:
            os.symlink(outside,alias,target_is_directory=True)
        self.assert_error("unsafe_path",ws.inspect,self.p,{"workset_key":"alias"})
        self.assertEqual(list(outside.iterdir()),[])


if __name__ == "__main__":
    unittest.main(verbosity=2)
