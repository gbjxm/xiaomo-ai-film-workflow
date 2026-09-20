"""Mechanical tests use invalid-video binary placeholders, never media QA."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "media_catalog.py"
spec = importlib.util.spec_from_file_location("media_catalog", SCRIPT)
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        # Always inside this isolated candidate batch; never a real project.
        batch = Path(__file__).resolve().parents[4]
        self.temp = tempfile.TemporaryDirectory(prefix="helper-test-", dir=batch)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.entry = self.root / "开始这里.md"
        self.write_entry()
        for category in ("Aroll", "Broll", "其他素材"):
            (self.root / "02_视频" / category).mkdir(parents=True)
        self.project = media.Project(str(self.root))
        self.source = self.make_source("download.mp4", b"NOT A VIDEO: first original")

    def write_entry(self, version="v2", docs=True):
        self.entry.write_text("workflowStudio: studio-v0.9\nworkflowMediaLayout: media-layout-" + version +
                              ("\nworkflowDocsLayout: production-docs-v1\n" if docs else "\n"), encoding="utf-8")

    def make_source(self, name, payload):
        path = self.base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def request(self, sources=None, **extra):
        value = {"category": "Aroll", "segment": "巷口回头", "probe_duration": False,
                 "sources": sources or [{"path": str(self.source), "in_editor": False}]}
        value.update(extra)
        return value

    def import_one(self, **extra):
        plan = media.prepare(self.project, self.request(**extra))
        result = media.apply(self.project, plan)
        catalog, digest = self.project.catalog()
        return catalog["entries"][0], digest, result

    def assert_error(self, code, action, *args):
        with self.assertRaises(media.CatalogError) as captured:
            action(*args)
        self.assertEqual(code, captured.exception.code)
        return captured.exception

    def tree(self):
        return {str(p.relative_to(self.root)): p.read_bytes() if p.is_file() else None
                for p in self.root.rglob("*")}

    def test_prepare_and_list_are_zero_write(self):
        before = self.tree()
        plan = media.prepare(self.project, self.request())
        listing = media.list_entries(self.project, {})
        self.assertEqual(before, self.tree())
        self.assertIsNone(plan["expected_catalog_sha256"])
        self.assertEqual([], listing["entries"])

    def test_copy_preserves_original_and_accepts_unlocated_note(self):
        original = self.source.read_bytes()
        entry, _, result = self.import_one(sources=[{"path": str(self.source), "in_editor": False,
                                                    "note": "后段有表情可考虑，尚未定位"}])
        target = self.project.resolve_media(entry["path"])
        self.assertEqual("巷口回头_生成01.mp4", target.name)
        self.assertEqual(original, target.read_bytes())
        self.assertEqual(original, self.source.read_bytes())
        self.assertEqual([], entry["ranges"])
        self.assertEqual("unknown", entry["duration_source"])
        self.assertIn("尚未定位", entry["note"])
        self.assertTrue(result["catalog_updated"])

    def test_true_and_unknown_editor_state_only_reference(self):
        second = self.make_source("already.mov", b"NOT A VIDEO: second")
        third = self.make_source("omitted.mp4", b"NOT A VIDEO: third")
        plan = media.prepare(self.project, self.request(sources=[
            {"path": str(self.source), "in_editor": True}, {"path": str(second), "in_editor": None},
            {"path": str(third)}]))
        result = media.apply(self.project, plan)
        entries = self.project.catalog()[0]["entries"]
        self.assertEqual([], result["copied_paths"])
        self.assertEqual([str(self.source), str(second), str(third)], [e["path"] for e in entries])
        self.assertFalse((self.root / "02_视频/Aroll/巷口回头").exists())

    def test_same_bytes_reuse_and_keep_every_origin(self):
        duplicate = self.make_source("other-name.mp4", self.source.read_bytes())
        plan = media.prepare(self.project, self.request(sources=[
            {"path": str(self.source), "in_editor": False}, {"path": str(duplicate), "in_editor": False}]))
        result = media.apply(self.project, plan)
        catalog, _ = self.project.catalog()
        self.assertEqual(1, len(catalog["entries"]))
        self.assertEqual(1, len(result["copied_paths"]))
        target = str(self.project.resolve_media(catalog["entries"][0]["path"]))
        self.assertEqual([target], result["reused_paths"])
        self.assertEqual(2, len(catalog["entries"][0]["origins"]))
        followup = media.prepare(self.project, self.request(sources=[{"path": str(duplicate), "in_editor": False}]))
        followup_result = media.apply(self.project, followup)
        self.assertEqual([], followup_result["copied_paths"])
        self.assertEqual([target], followup_result["reused_paths"])
        self.assertEqual(2, len(self.project.catalog()[0]["entries"][0]["origins"]))

    def test_distinct_content_and_occupied_name_never_overwrite(self):
        occupied = self.root / "02_视频/Aroll/巷口回头/巷口回头_生成01.mp4"
        occupied.parent.mkdir()
        occupied.write_bytes(b"Uncatalogued file, preserve this")
        second = self.make_source("another/download.mp4", b"different original")
        plan = media.prepare(self.project, self.request(sources=[
            {"path": str(self.source), "in_editor": False}, {"path": str(second), "in_editor": False}]))
        media.apply(self.project, plan)
        names = [Path(e["path"]).name for e in self.project.catalog()[0]["entries"]]
        self.assertEqual(["巷口回头_生成02.mp4", "巷口回头_生成03.mp4"], names)
        self.assertEqual(b"Uncatalogued file, preserve this", occupied.read_bytes())

    def test_cross_group_same_bytes_remain_independent(self):
        self.import_one()
        plan = media.prepare(self.project, self.request(segment="另一用途", category="Broll"))
        media.apply(self.project, plan)
        self.assertEqual(2, len(self.project.catalog()[0]["entries"]))
        self.assertTrue(self.source.exists())

    def test_source_drift_rejects_before_any_project_write(self):
        plan = media.prepare(self.project, self.request())
        before = self.tree()
        self.source.write_bytes(b"changed source")
        self.assert_error("source_changed", media.apply, self.project, plan)
        self.assertEqual(before, self.tree())

    def test_catalog_drift_and_lock_reject(self):
        plan = media.prepare(self.project, self.request())
        self.import_one()
        self.assert_error("catalog_changed", media.apply, self.project, plan)
        fresh = media.prepare(self.project, self.request())
        lock = self.project.catalog_path.with_suffix(".lock")
        lock.write_text("other-process", encoding="ascii")
        before = self.tree()
        self.assert_error("catalog_locked", media.apply, self.project, fresh)
        self.assertEqual(before, self.tree())

    def test_project_marker_change_rejects(self):
        plan = media.prepare(self.project, self.request())
        self.entry.write_text(self.entry.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        self.assert_error("project_changed", media.apply, self.project, plan)

    def test_malicious_names_and_tampered_destinations_reject(self):
        for name in ("..", "../outside", "CON", "LPT¹.txt", "bad:ads", "trailing.", "bad\\child"):
            with self.subTest(name=name):
                self.assert_error("unsafe_path", media.prepare, self.project, self.request(segment=name))
        plan = media.prepare(self.project, self.request())
        outside = self.base / "outside.mp4"
        plan["operations"][0]["entry"]["path"] = str(outside)
        before = self.tree()
        self.assert_error("invalid_catalog", media.apply, self.project, plan)
        self.assertEqual(before, self.tree())
        self.assertFalse(outside.exists())

    def test_copy_cannot_be_injected_for_unknown_import_state(self):
        plan = media.prepare(self.project, self.request())
        plan["operations"][0]["in_editor"] = None
        self.assert_error("invalid_plan", media.apply, self.project, plan)

    def test_reparse_point_is_rejected(self):
        link = self.base / "linked"
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(self.root)], capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
        try:
            self.assert_error("unsafe_path", media.Project, str(link))
        finally:
            if os.name == "nt" and not link.is_symlink():
                link.rmdir()  # remove only the known test junction itself
            else:
                link.unlink()

    def test_multiple_disjoint_or_overlapping_source_intervals(self):
        first, _, _ = self.import_one(sources=[{"path": str(self.source), "in_editor": False,
                                               "duration_ms": 10000, "duration_source": "用户报告原片为10秒"}])
        second = self.make_source("second.mp4", b"second original")
        media.apply(self.project, media.prepare(self.project, self.request(sources=[{"path": str(second), "in_editor": False}])))
        before, digest = self.project.catalog()
        untouched = copy.deepcopy(before["entries"][1])
        payload = [{"clock": "source", "in_ms": start, "out_ms": end, "evidence": "用户逐段审看", "note": note}
                   for start, end, note in [(1000, 3000, "眼神"), (2000, 3500, "另一剪法"), (8000, 10000, "结尾")]]
        response = media.set_ranges(self.project, {"entry_key": first["entry_key"], "expected_catalog_sha256": digest,
            "expected_source_sha256": first["source_sha256"], "ranges": payload})
        self.assertEqual(3, len(response["entry"]["ranges"]))
        self.assertEqual(untouched, self.project.catalog()[0]["entries"][1])
        self.assertTrue(all(r["boundary_check"] == "within_recorded_duration" for r in response["entry"]["ranges"]))

    def test_bad_ranges_and_missing_evidence_reject(self):
        good = {"clock": "source", "in_ms": 0, "out_ms": 1000, "evidence": "用户审看"}
        for changed in ({"in_ms": -1}, {"in_ms": True}, {"out_ms": 0}, {"out_ms": 1001},
                        {"out_ms": 1.5}, {"clock": "editor"}, {"evidence": ""}):
            with self.subTest(changed=changed):
                item = dict(good, **changed)
                with self.assertRaises(media.CatalogError):
                    media.ranges([item], 1000)

    def test_unknown_duration_never_claims_verified_boundary(self):
        first, digest, _ = self.import_one()
        result = media.set_ranges(self.project, {"entry_key": first["entry_key"], "expected_catalog_sha256": digest,
            "expected_source_sha256": first["source_sha256"], "ranges": [
                {"clock": "source", "in_ms": 1000, "out_ms": 150000, "evidence": "用户给定范围，片长未知"}]})
        self.assertEqual("unverified_duration", result["entry"]["ranges"][0]["boundary_check"])

    def test_set_ranges_requires_both_fingerprints_and_detects_media_change(self):
        first, digest, _ = self.import_one()
        request = {"entry_key": first["entry_key"], "expected_catalog_sha256": digest,
                   "expected_source_sha256": first["source_sha256"], "ranges": []}
        for key in ("expected_catalog_sha256", "expected_source_sha256"):
            altered = dict(request)
            del altered[key]
            self.assert_error("invalid_input", media.set_ranges, self.project, altered)
        self.project.resolve_media(first["path"]).write_bytes(b"replaced")
        self.assert_error("source_changed", media.set_ranges, self.project, request)

    def test_later_duration_revalidates_retained_ranges_and_updates_bounds(self):
        first, digest, _ = self.import_one(sources=[{"path": str(self.source), "in_editor": False,
            "ranges": [{"clock": "source", "in_ms": 1000, "out_ms": 3000,
                        "evidence": "用户指出，时长待补", "note": "保留这一段"}]}])
        self.assertEqual("unverified_duration", first["ranges"][0]["boundary_check"])
        result = media.set_ranges(self.project, {"entry_key": first["entry_key"],
            "expected_catalog_sha256": digest, "expected_source_sha256": first["source_sha256"],
            "duration_ms": 4000, "duration_source": "用户补充原片为4秒"})
        updated = result["entry"]
        self.assertEqual(4000, updated["duration_ms"])
        self.assertEqual("用户补充原片为4秒", updated["duration_source"])
        expected_range = dict(first["ranges"][0], boundary_check="within_recorded_duration")
        self.assertEqual([expected_range], updated["ranges"])
        note_only = media.set_ranges(self.project, {"entry_key": first["entry_key"],
            "expected_catalog_sha256": result["catalog_sha256"],
            "expected_source_sha256": first["source_sha256"], "note": "仅补备注"})
        self.assertEqual(4000, note_only["entry"]["duration_ms"])
        self.assertEqual(updated["ranges"], note_only["entry"]["ranges"])

    def test_duration_update_requires_a_positive_value_and_source_together(self):
        first, digest, _ = self.import_one()
        request = {"entry_key": first["entry_key"], "expected_catalog_sha256": digest,
                   "expected_source_sha256": first["source_sha256"]}
        before = self.tree()
        for extra in ({"duration_ms": 5000}, {"duration_source": "用户报告"},
                      {"duration_ms": 5000, "duration_source": " "},
                      {"duration_ms": 0, "duration_source": "用户报告"},
                      {"duration_ms": True, "duration_source": "用户报告"},
                      {"duration_ms": 1.5, "duration_source": "用户报告"}):
            with self.subTest(extra=extra):
                with self.assertRaises(media.CatalogError):
                    media.set_ranges(self.project, dict(request, **extra))
                self.assertEqual(before, self.tree())

    def test_shorter_duration_rejects_unmodified_old_range_without_dropping_it(self):
        first, digest, _ = self.import_one(sources=[{"path": str(self.source), "in_editor": False,
            "duration_ms": 5000, "duration_source": "先前报告5秒",
            "ranges": [{"clock": "source", "in_ms": 2000, "out_ms": 4500, "evidence": "用户审看"}]}])
        before = self.project.catalog_path.read_bytes()
        self.assert_error("invalid_range", media.set_ranges, self.project, {
            "entry_key": first["entry_key"], "expected_catalog_sha256": digest,
            "expected_source_sha256": first["source_sha256"],
            "duration_ms": 4000, "duration_source": "修正为4秒"})
        self.assertEqual(before, self.project.catalog_path.read_bytes())
        self.assertEqual(first["ranges"], self.project.catalog()[0]["entries"][0]["ranges"])

    def test_partial_copy_failure_reports_and_plan_retry_is_safe(self):
        second = self.make_source("second.mp4", b"second original")
        plan = media.prepare(self.project, self.request(sources=[
            {"path": str(self.source), "in_editor": False}, {"path": str(second), "in_editor": False}]))
        original_copy = media.copy_exclusive
        calls = []
        def fail_second(source, target, digest):
            calls.append(str(target))
            if len(calls) == 2:
                raise OSError("simulated disk full")
            return original_copy(source, target, digest)
        with mock.patch.object(media, "copy_exclusive", side_effect=fail_second):
            error = self.assert_error("io_failed", media.apply, self.project, plan)
        self.assertFalse(error.details["catalog_updated"])
        self.assertEqual(1, len(error.details["copied_paths"]))
        self.assertFalse(self.project.catalog_path.exists())
        self.assertFalse(self.project.catalog_path.with_suffix(".lock").exists())
        retry = media.apply(self.project, plan)
        self.assertEqual(1, len(retry["copied_paths"]))
        self.assertEqual(1, len(retry["reused_paths"]))
        self.assertEqual(2, len(self.project.catalog()[0]["entries"]))

    def test_atomic_catalog_failure_retains_previous_index_and_retries(self):
        self.import_one()
        before = self.project.catalog_path.read_bytes()
        second = self.make_source("second.mp4", b"second original")
        plan = media.prepare(self.project, self.request(sources=[{"path": str(second), "in_editor": False}]))
        with mock.patch.object(media.os, "replace", side_effect=OSError("simulated catalog failure")):
            error = self.assert_error("io_failed", media.apply, self.project, plan)
        self.assertEqual(before, self.project.catalog_path.read_bytes())
        self.assertEqual(1, len(error.details["copied_paths"]))
        media.apply(self.project, plan)
        self.assertEqual(2, len(self.project.catalog()[0]["entries"]))

    def test_target_drift_does_not_overwrite(self):
        plan = media.prepare(self.project, self.request())
        target = self.project.resolve_media(plan["operations"][0]["entry"]["path"])
        target.parent.mkdir()
        target.write_bytes(b"Someone else's new file")
        self.assert_error("target_changed", media.apply, self.project, plan)
        self.assertEqual(b"Someone else's new file", target.read_bytes())

    def test_v1_and_old_document_path_and_no_marker_fail_closed(self):
        self.write_entry("v1", docs=False)
        old = media.Project(str(self.root))
        self.assertEqual("生成记录/素材整理/素材索引.json", old.catalog_rel)
        media.apply(old, media.prepare(old, self.request()))
        self.assertFalse((self.root / "90_制作资料").exists())
        self.entry.write_text("workflowStudio: studio-v0.9\n", encoding="utf-8")
        before = self.tree()
        self.assert_error("unsupported_layout", media.Project, str(self.root))
        self.assertEqual(before, self.tree())

    def test_list_filters_return_actual_paths_and_no_write_or_media_hash(self):
        first, _, _ = self.import_one(sources=[{"path": str(self.source), "in_editor": False, "note": "可考虑眼神"}])
        before = self.tree()
        with mock.patch.object(media, "sha", side_effect=AssertionError("list must not hash media")):
            result = media.list_entries(self.project, {"segment": "巷口回头", "category": "Aroll", "text": "眼神"})
        self.assertEqual(first["path"], result["entries"][0]["path"])
        self.assertEqual(str(self.project.resolve_media(first["path"])), result["entries"][0]["resolved_path"])
        self.assertTrue(result["entries"][0]["available"])
        self.assertFalse(result["entries"][0]["content_checked"])
        self.assertFalse(result["media_hashes_rechecked"])
        self.assertEqual(before, self.tree())

    def test_project_move_keeps_internal_paths_portable(self):
        first, _, _ = self.import_one()
        self.assertFalse(Path(first["path"]).is_absolute())
        planned = media.prepare(self.project, self.request())
        moved = self.base / "moved-project"
        self.root.rename(moved)
        project = media.Project(str(moved))
        listed = media.list_entries(project, {})
        self.assertTrue(listed["entries"][0]["available"])
        self.assertTrue(listed["entries"][0]["resolved_path"].startswith(str(moved)))
        self.assertEqual(self.source.read_bytes(), Path(listed["entries"][0]["resolved_path"]).read_bytes())
        self.assert_error("invalid_plan", media.apply, project, planned)

    def test_equal_bytes_do_not_mix_copy_and_reference_locations(self):
        second = self.make_source("imported.mp4", self.source.read_bytes())
        third = self.make_source("unknown.mp4", self.source.read_bytes())
        plan = media.prepare(self.project, self.request(sources=[
            {"path": str(second), "in_editor": True}, {"path": str(self.source), "in_editor": False},
            {"path": str(third), "in_editor": None}]))
        media.apply(self.project, plan)

        entries = self.project.catalog()[0]["entries"]
        self.assertEqual(["reference", "copy", "reference"], [e["storage"] for e in entries])
        self.assertEqual(str(second), entries[0]["path"])
        self.assertEqual(str(third), entries[2]["path"])
        retry = media.prepare(self.project, self.request(sources=[{"path": str(second), "in_editor": True}]))
        self.assertEqual(entries[0]["entry_key"], retry["operations"][0]["entry_key"])
        tampered = copy.deepcopy(retry)
        tampered["operations"][0]["entry_key"] = entries[1]["entry_key"]
        self.assert_error("invalid_plan", media.apply, self.project, tampered)
        self.assertEqual([str(second)], media.apply(self.project, retry)["reused_paths"])

    def test_missing_reference_is_visible_without_replacement_search(self):
        self.import_one(sources=[{"path": str(self.source), "in_editor": True}])
        self.source.unlink()
        before = self.tree()
        result = media.list_entries(self.project, {})
        self.assertEqual(str(self.source), result["entries"][0]["resolved_path"])
        self.assertFalse(result["entries"][0]["available"])
        self.assertFalse(result["entries"][0]["content_checked"])
        self.assertEqual(before, self.tree())

    def test_existing_managed_copy_can_be_registered_in_place_without_a_second_entry(self):
        first, _, _ = self.import_one()
        target = self.project.resolve_media(first["path"])
        original = target.read_bytes()
        for in_editor in (True, None):
            with self.subTest(in_editor=in_editor):
                plan = media.prepare(self.project, self.request(sources=[{
                    "path": str(target), "in_editor": in_editor}]))
                self.assertEqual("reuse", plan["operations"][0]["kind"])
                result = media.apply(self.project, plan)
                self.assertEqual([], result["copied_paths"])
                self.assertEqual([str(target)], result["reused_paths"])
                entries = self.project.catalog()[0]["entries"]
                self.assertEqual(1, len(entries))
                self.assertEqual(first["entry_key"], entries[0]["entry_key"])
                self.assertEqual("copy", entries[0]["storage"])
                self.assertIn({"path": str(target), "in_editor": in_editor}, entries[0]["origins"])
                self.assertEqual(original, target.read_bytes())

    def test_two_distinct_sources_reserve_distinct_relative_names(self):
        other = self.make_source("other.mp4", b"new bytes")
        plan = media.prepare(self.project, self.request(sources=[
            {"path": str(self.source), "in_editor": False}, {"path": str(other), "in_editor": False}]))
        self.assertEqual(["巷口回头_生成01.mp4", "巷口回头_生成02.mp4"],
                         [Path(o["entry"]["path"]).name for o in plan["operations"]])
        media.apply(self.project, plan)


if __name__ == "__main__":
    unittest.main(verbosity=2)
