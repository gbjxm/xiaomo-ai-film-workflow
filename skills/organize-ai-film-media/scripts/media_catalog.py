"""Bounded video filing and source-clock notes; no media editing or adoption."""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid

SCHEMA = "xiaomo.media-catalog/v1"
PLAN_SCHEMA = "xiaomo.media-catalog-plan/v1"
EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".mts", ".m2ts"}
DEVICES = {"con", "prn", "aux", "nul", "clock$"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in "123456789¹²³"
}


class CatalogError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


def fail(code, message, **details):
    raise CatalogError(code, message, **details)


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        fail("invalid_input", f"{name} must be nonempty text")
    return value


def component(value):
    text(value, "path component")
    if (value in {".", ".."} or value[-1] in ". " or
            any(ord(c) < 32 or c in '<>:"/\\|?*' for c in value) or
            value.split(".")[0].casefold() in DEVICES):
        fail("unsafe_path", "Unsafe Windows path component", value=value)
    return value


def safe_absolute(value):
    path = Path(text(os.fspath(value), "path"))
    if not path.is_absolute() or str(path).startswith(("\\\\?\\", "\\\\.\\")):
        fail("unsafe_path", "An ordinary absolute path is required")
    for part in path.parts[1:]:
        component(part)
    for current in (path, *path.parents):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            fail("unsafe_path", "Reparse points and symbolic links are not supported", path=str(current))
    return path


def sha(path):
    safe_absolute(path)
    if not path.is_file():
        fail("missing_file", "Expected a regular file", path=str(path))
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_value(value, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or re.fullmatch("[a-f0-9]{64}", value) is None:
        fail("invalid_input", "Expected a lowercase SHA-256 value")
    return value


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        fail("invalid_json", str(exc), path=str(path))


def markers(entry, name):
    return [v.strip() for v in re.findall(r"(?m)^[ \t]*(?:>[ \t]*)?" + name + r"[ \t]*:[ \t]*([^\r\n]*)", entry)]


class Project:
    def __init__(self, root):
        self.root = safe_absolute(root)
        self.entry_path = self.path("开始这里.md")
        raw = self.entry_path.read_bytes()
        self.entry_sha256 = hashlib.sha256(raw).hexdigest()
        entry = raw.decode("utf-8-sig")
        if markers(entry, "workflowStudio") != ["studio-v0.9"]:
            fail("unsupported_project", "An explicit studio-v0.9 project is required")
        media = markers(entry, "workflowMediaLayout")
        if len(media) != 1 or media[0] not in {"media-layout-v1", "media-layout-v2"}:
            fail("unsupported_layout", "An existing media-layout-v1/v2 marker is required; no migration is performed")
        skills = Path(__file__).resolve().parents[2]
        definition = read_json(skills / "start-ai-film-project" / "references" / (media[0] + ".json"))
        if definition.get("schema") != "xiaomo.media-layout/" + media[0].rsplit("-", 1)[1]:
            fail("invalid_layout", "Unexpected shared media layout definition")
        self.categories = {p.split("/")[1] for p in definition["fixedDirectories"]
                           if p.startswith("02_视频/") and p.count("/") == 1}
        if self.categories != {"Aroll", "Broll", "其他素材"}:
            fail("invalid_layout", "Shared video categories are unsupported")
        # Required layout directories already belong to the project. Never repair them here.
        for relative in ["02_视频", *("02_视频/" + c for c in self.categories)]:
            if not self.path(relative).is_dir():
                fail("missing_layout", "Existing media layout directory is missing", path=relative)
        docs = markers(entry, "workflowDocsLayout")
        prefix = ""
        if docs:
            definition = read_json(skills / "orchestrate-ai-film-project" / "references" / "studio-project-layout-v1.json")
            if docs != [definition.get("marker")] or definition.get("schema") != "xiaomo.production-docs-layout/v1":
                fail("unsupported_layout", "Unknown or duplicate document layout marker")
            if "生成记录" not in definition["documentDirectories"]:
                fail("invalid_layout", "Shared document layout lacks generation records")
            prefix = component(definition["directory"]) + "/"
        self.catalog_rel = prefix + "生成记录/素材整理/素材索引.json"
        self.catalog_path = self.path(self.catalog_rel)

    def path(self, relative):
        text(relative, "relative path")
        parts = relative.replace("\\", "/").split("/")
        return safe_absolute(self.root.joinpath(*(component(p) for p in parts)))

    def group(self, category, segment):
        if category not in self.categories:
            fail("invalid_category", "Category must be Aroll, Broll or 其他素材")
        return f"02_视频/{category}/{component(segment)}"

    def resolve_media(self, location):
        text(location, "media path")
        return safe_absolute(location) if Path(location).is_absolute() else self.path(location)

    def store_media(self, path):
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def catalog(self):
        safe_absolute(self.catalog_path)
        if not self.catalog_path.exists():
            return {"schema": SCHEMA, "entries": []}, None
        raw = self.catalog_path.read_bytes()
        try:
            catalog = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeError):
            fail("invalid_catalog", "Catalog JSON cannot be parsed")
        if not isinstance(catalog, dict) or catalog.get("schema") != SCHEMA or not isinstance(catalog.get("entries"), list):
            fail("invalid_catalog", "Unsupported catalog schema")
        keys = []
        for entry in catalog["entries"]:
            validate_entry(self, entry)
            keys.append(entry["entry_key"])
        if len(keys) != len(set(keys)):
            fail("invalid_catalog", "Duplicate entry keys")
        return catalog, hashlib.sha256(raw).hexdigest()

    def verify_entry(self, expected):
        if sha(self.entry_path) != expected:
            fail("project_changed", "Project entry changed; prepare again")


def positive_ms(value, name, allow_zero=False):
    if type(value) is not int or value < (0 if allow_zero else 1):
        fail("invalid_range", f"{name} must be integer milliseconds")
    return value


def ranges(value, duration):
    if not isinstance(value, list):
        fail("invalid_range", "ranges must be an array")
    result = []
    for item in value:
        if not isinstance(item, dict) or item.get("clock") != "source":
            fail("invalid_range", "Each interval must explicitly use clock=source")
        start = positive_ms(item.get("in_ms"), "in_ms", True)
        end = positive_ms(item.get("out_ms"), "out_ms")
        if end <= start or (duration is not None and end > duration):
            fail("invalid_range", "Interval is empty, reversed, or exceeds the recorded duration")
        evidence = text(item.get("evidence"), "range evidence")
        note = item.get("note", "")
        if not isinstance(note, str):
            fail("invalid_input", "range note must be text")
        result.append({"clock": "source", "in_ms": start, "out_ms": end,
                       "evidence": evidence, "note": note,
                       "boundary_check": "within_recorded_duration" if duration is not None else "unverified_duration"})
    return result


def duration_info(source, probe=True):
    supplied = source.get("duration_ms")
    if supplied is not None:
        return positive_ms(supplied, "duration_ms"), text(source.get("duration_source"), "duration_source")
    executable = shutil.which("ffprobe") if probe else None
    if executable:
        try:
            result = subprocess.run([executable, "-v", "error", "-show_entries", "format=duration",
                                     "-of", "default=noprint_wrappers=1:nokey=1", source["path"]],
                                    capture_output=True, text=True, timeout=15, check=True)
            milliseconds = int(float(result.stdout.strip()) * 1000)
            if milliseconds > 0:
                return milliseconds, "ffprobe format.duration; metadata only"
        except (OSError, ValueError, OverflowError, subprocess.SubprocessError):
            pass
    return None, "unknown"


def validate_entry(project, entry):
    if not isinstance(entry, dict):
        fail("invalid_catalog", "Entry must be an object")
    if re.fullmatch(r"[a-f0-9]{32}", str(entry.get("entry_key"))) is None:
        fail("invalid_catalog", "Invalid mechanical entry key")
    group = project.group(entry.get("category"), entry.get("segment"))
    path = project.resolve_media(entry.get("path", ""))
    hash_value(entry.get("source_sha256"))
    if entry.get("storage") == "copy":
        if Path(entry["path"]).is_absolute():
            fail("invalid_catalog", "Managed copy paths must be project-relative")
        expected = project.path(group)
        pattern = re.escape(entry["segment"]) + r"_生成[0-9]{2,6}"
        if path.parent != expected or re.fullmatch(pattern, path.stem) is None or path.suffix.lower() not in EXTENSIONS:
            fail("unsafe_path", "Copied entry is outside its exact video group or filename format")
    elif entry.get("storage") != "reference":
        fail("invalid_catalog", "Unknown storage mode")
    if path.suffix.lower() not in EXTENSIONS:
        fail("unsupported_media", "Only supported video filename extensions are accepted")
    duration = entry.get("duration_ms")
    if duration is not None:
        positive_ms(duration, "duration_ms")
    text(entry.get("duration_source"), "duration_source")
    ranges(entry.get("ranges"), duration)
    if not isinstance(entry.get("note"), str) or not isinstance(entry.get("origins"), list):
        fail("invalid_catalog", "Entry note/origins are invalid")


def prepare(project, request):
    group = project.group(request.get("category"), request.get("segment"))
    source_requests = request.get("sources")
    if not isinstance(source_requests, list) or not source_requests:
        fail("invalid_input", "Explicit sources are required; no directory discovery is performed")
    catalog, catalog_hash = project.catalog()
    working = copy.deepcopy(catalog["entries"])
    operations = []
    for source in source_requests:
        if not isinstance(source, dict):
            fail("invalid_input", "Each source must be an object")
        path = safe_absolute(source.get("path", ""))
        if path.suffix.lower() not in EXTENSIONS:
            fail("unsupported_media", "Only video filename extensions are supported")
        in_editor = source.get("in_editor")
        if in_editor is not None and type(in_editor) is not bool:
            fail("invalid_input", "in_editor must be true, false, or null")
        digest = sha(path)
        storage = "copy" if in_editor is False else "reference"
        matched = next((e for e in working if e["segment"] == request["segment"] and
                        e["category"] == request["category"] and e["source_sha256"] == digest and
                        ((storage == "copy" and e["storage"] == "copy") or
                         (storage == "reference" and project.resolve_media(e["path"]) == path))), None)
        origin = {"path": str(path), "in_editor": in_editor}
        operation = {"source_path": str(path), "source_sha256": digest, "in_editor": in_editor,
                     "entry_key": matched["entry_key"] if matched else uuid.uuid4().hex}
        if matched:
            operation["kind"] = "reuse"
            operation["expected_target_sha256"] = digest
        else:
            target = path
            if storage == "copy":
                number = 1
                reserved = {str(project.resolve_media(e["path"])).casefold() for e in working}
                while number <= 999999:
                    target = project.path(f"{group}/{request['segment']}_生成{number:02d}{path.suffix.lower()}")
                    if not target.exists() and str(target).casefold() not in reserved:
                        break
                    number += 1
                else:
                    fail("name_exhausted", "Too many occupied generation filenames")
            duration, duration_source = duration_info(source, request.get("probe_duration", True))
            entry = {"entry_key": operation["entry_key"], "segment": request["segment"],
                     "category": request["category"], "path": project.store_media(target), "storage": storage,
                     "source_sha256": digest, "duration_ms": duration, "duration_source": duration_source,
                     "ranges": ranges(source.get("ranges", []), duration), "note": source.get("note", ""),
                     "origins": [origin]}
            validate_entry(project, entry)
            operation.update(kind="add", entry=entry, expected_target_sha256=None if storage == "copy" else digest)
            working.append(entry)
        operations.append(operation)
    return {"schema": PLAN_SCHEMA, "project_root": str(project.root),
            "category": request["category"], "segment": request["segment"],
            "project_entry_sha256": project.entry_sha256, "catalog_relative_path": project.catalog_rel,
            "expected_catalog_sha256": catalog_hash, "operations": operations}


def ensure_parent(path):
    safe_absolute(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_absolute(path)


@contextlib.contextmanager
def catalog_lock(project):
    path = project.catalog_path.with_suffix(".lock")
    ensure_parent(path)
    token = uuid.uuid4().hex
    try:
        with path.open("x", encoding="ascii") as handle:
            handle.write(token)
    except FileExistsError:
        fail("catalog_locked", "Catalog is locked; do not delete another running process's lock", path=str(path))
    try:
        yield
    finally:
        safe_absolute(path)
        if path.read_text(encoding="ascii") == token:
            path.unlink()


def check_catalog(project, expected):
    hash_value(expected, nullable=True)
    catalog, actual = project.catalog()
    if actual != expected:
        fail("catalog_changed", "Catalog changed; prepare again", expected=expected, actual=actual)
    return catalog


def copy_exclusive(source, target, expected_hash):
    """Publish complete bytes exclusively; Windows rename never replaces a target."""
    ensure_parent(target)
    if target.exists():
        if sha(target) == expected_hash:
            return False  # retry after a completed copy and failed catalog write
        fail("target_changed", "Target exists with different bytes", path=str(target))
    temp = target.parent / (".organize-" + uuid.uuid4().hex + ".part")
    try:
        with source.open("rb") as reader, temp.open("xb") as writer:
            shutil.copyfileobj(reader, writer, 1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        if sha(temp) != expected_hash or sha(source) != expected_hash:
            fail("source_changed", "Source changed during copy", path=str(source))
        safe_absolute(target)
        try:
            if os.name == "nt":
                os.rename(temp, target)
            else:
                os.link(temp, target)
        except FileExistsError:
            if sha(target) == expected_hash:
                return False
            fail("target_changed", "Target appeared with different bytes", path=str(target))
        return True
    finally:
        if temp.exists():
            temp.unlink()  # only this call's exclusive temporary file


def write_catalog(project, catalog, expected):
    path = project.catalog_path
    temp = path.parent / (".catalog-" + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(json_bytes(catalog))
            handle.flush()
            os.fsync(handle.fileno())
        check_catalog(project, expected)
        safe_absolute(path)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def apply(project, plan):
    copied, reused = [], []
    if (plan.get("schema") != PLAN_SCHEMA or plan.get("project_root") != str(project.root) or
            plan.get("catalog_relative_path") != project.catalog_rel):
        fail("invalid_plan", "Plan belongs to a different project or catalog")
    project.verify_entry(hash_value(plan.get("project_entry_sha256")))
    project.group(plan.get("category"), plan.get("segment"))
    if "expected_catalog_sha256" not in plan or not isinstance(plan.get("operations"), list) or not plan["operations"]:
        fail("invalid_plan", "Plan lacks operations or catalog fingerprint")
    # Validate the entire plan before creating directories, locks, or media copies.
    catalog = check_catalog(project, plan["expected_catalog_sha256"])
    preview = copy.deepcopy(catalog)
    for operation in plan["operations"]:
        source = safe_absolute(operation.get("source_path", ""))
        digest = hash_value(operation.get("source_sha256"))
        if sha(source) != digest:
            fail("source_changed", "Source changed since prepare", path=str(source))
        in_editor = operation.get("in_editor")
        if in_editor is not None and type(in_editor) is not bool:
            fail("invalid_plan", "Invalid in_editor state")
        if operation.get("kind") == "add":
            entry = operation.get("entry")
            validate_entry(project, entry)
            if entry["source_sha256"] != digest or entry["entry_key"] != operation.get("entry_key"):
                fail("invalid_plan", "Entry fingerprint differs from source")
            if entry["storage"] != ("copy" if in_editor is False else "reference"):
                fail("invalid_plan", "Storage mode must match the explicit import state")
            if entry["storage"] == "reference" and project.resolve_media(entry["path"]) != source:
                fail("invalid_plan", "References must preserve their source path")
            if any(e["entry_key"] == entry["entry_key"] for e in preview["entries"]):
                fail("invalid_plan", "Entry key already exists")
            entry = copy.deepcopy(entry)
            entry["ranges"] = ranges(entry["ranges"], entry["duration_ms"])
            entry["origins"] = [{"path": str(source), "in_editor": in_editor}]
            preview["entries"].append(entry)
        elif operation.get("kind") == "reuse":
            entry = next((e for e in preview["entries"] if e["entry_key"] == operation.get("entry_key")), None)
            if not entry or entry["source_sha256"] != digest:
                fail("invalid_plan", "Reused entry no longer matches")
            wanted = "copy" if in_editor is False else "reference"
            if ((wanted == "copy" and entry["storage"] != "copy") or
                    (wanted == "reference" and project.resolve_media(entry["path"]) != source)):
                fail("invalid_plan", "Reuse must preserve the managed copy or exact original path")
            origin = {"path": str(source), "in_editor": in_editor}
            if origin not in entry["origins"]:
                entry["origins"].append(origin)
        else:
            fail("invalid_plan", "Unknown operation")
        if entry["category"] != plan["category"] or entry["segment"] != plan["segment"]:
            fail("invalid_plan", "Operation differs from the requested group")
        target_expected = None if operation["kind"] == "add" and entry["storage"] == "copy" else digest
        if "expected_target_sha256" not in operation or operation["expected_target_sha256"] != target_expected:
            fail("invalid_plan", "Target fingerprint does not match the planned operation")
        target = project.resolve_media(entry["path"])
        if target.exists() and sha(target) != digest:
            fail("target_changed", "Target content differs", path=str(target))
        if entry["storage"] == "reference" and not target.exists():
            fail("missing_file", "Referenced file is missing", path=str(target))
    try:
        with catalog_lock(project):
            check_catalog(project, plan["expected_catalog_sha256"])
            project.verify_entry(plan["project_entry_sha256"])
            for operation in plan["operations"]:
                source = safe_absolute(operation["source_path"])
                if sha(source) != operation["source_sha256"]:
                    fail("source_changed", "Source changed before write", path=str(source))
                entry = next(e for e in preview["entries"] if e["entry_key"] == operation["entry_key"])
                target = project.resolve_media(entry["path"])
                if entry["storage"] == "copy" and operation["kind"] == "add":
                    if copy_exclusive(source, target, entry["source_sha256"]):
                        copied.append(str(target))
                    else:
                        reused.append(str(target))
                elif sha(target) != entry["source_sha256"]:
                    fail("target_changed", "Registered target changed before write")
                elif operation["kind"] == "reuse" and str(target) not in reused:
                    reused.append(str(target))
            # Check all explicitly touched files again immediately before catalog publication.
            for operation in plan["operations"]:
                entry = next(e for e in preview["entries"] if e["entry_key"] == operation["entry_key"])
                if sha(project.resolve_media(entry["path"])) != entry["source_sha256"] or sha(safe_absolute(operation["source_path"])) != entry["source_sha256"]:
                    fail("source_changed", "Touched media changed before catalog publication")
            project.verify_entry(plan["project_entry_sha256"])
            write_catalog(project, preview, plan["expected_catalog_sha256"])
    except (CatalogError, OSError) as exc:
        if isinstance(exc, CatalogError):
            exc.details.update(copied_paths=copied, catalog_updated=False)
            raise
        fail("io_failed", str(exc), copied_paths=copied, catalog_updated=False)
    return {"catalog_updated": True, "catalog_path": str(project.catalog_path),
            "catalog_sha256": sha(project.catalog_path), "copied_paths": copied,
            "reused_paths": reused, "entry_keys": [o["entry_key"] for o in plan["operations"]]}


def set_ranges(project, request):
    if "expected_catalog_sha256" not in request:
        fail("invalid_input", "expected_catalog_sha256 is required")
    expected = hash_value(request["expected_catalog_sha256"])
    source_hash = hash_value(request.get("expected_source_sha256"))
    catalog = check_catalog(project, expected)
    entry = next((e for e in catalog["entries"] if e["entry_key"] == request.get("entry_key")), None)
    if entry is None:
        fail("entry_missing", "Exact entry_key was not found")
    source = project.resolve_media(entry["path"])
    if source_hash != entry["source_sha256"] or sha(source) != source_hash:
        fail("source_changed", "Registered original no longer matches the expected bytes")
    if ("duration_ms" in request) != ("duration_source" in request):
        fail("invalid_input", "duration_ms and duration_source must be provided together")
    if "duration_ms" in request:
        entry["duration_ms"] = positive_ms(request["duration_ms"], "duration_ms")
        entry["duration_source"] = text(request["duration_source"], "duration_source")
    entry["ranges"] = ranges(request.get("ranges", entry["ranges"]), entry["duration_ms"])
    if "note" in request:
        if not isinstance(request["note"], str):
            fail("invalid_input", "note must be text")
        entry["note"] = request["note"]
    with catalog_lock(project):
        check_catalog(project, expected)
        project.verify_entry(project.entry_sha256)
        if sha(source) != source_hash:
            fail("source_changed", "Original changed before range update")
        write_catalog(project, catalog, expected)
    return {"catalog_updated": True, "entry_key": entry["entry_key"],
            "catalog_sha256": sha(project.catalog_path), "entry": entry}


def list_entries(project, query):
    catalog, digest = project.catalog()
    result = catalog["entries"]
    for field in ("segment", "category"):
        if query.get(field):
            result = [e for e in result if e[field] == query[field]]
    if query.get("text"):
        needle = str(query["text"]).casefold()
        result = [e for e in result if needle in json.dumps(e, ensure_ascii=False).casefold()]
    return {"catalog_path": str(project.catalog_path), "catalog_sha256": digest,
            "media_hashes_rechecked": False,
            "entries": [dict(e, resolved_path=str(project.resolve_media(e["path"])),
                             available=project.resolve_media(e["path"]).is_file(), content_checked=False)
                        for e in result]}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--action", required=True, choices=("prepare", "apply", "set-ranges", "list"))
    parser.add_argument("--request-file")
    parser.add_argument("--segment")
    parser.add_argument("--category")
    parser.add_argument("--text")
    args = parser.parse_args()
    try:
        if args.action != "list" and not args.request_file:
            fail("invalid_input", "This action requires --request-file")
        request = read_json(safe_absolute(args.request_file)) if args.request_file else {}
        if not isinstance(request, dict):
            fail("invalid_input", "Request must be a JSON object")
        for name in ("segment", "category", "text"):
            if getattr(args, name) is not None:
                request[name] = getattr(args, name)
        action = {"prepare": prepare, "apply": apply, "set-ranges": set_ranges, "list": list_entries}[args.action]
        result = action(Project(args.project_root), request)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (CatalogError, OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"error": getattr(exc, "code", "invalid_input_or_io"), "message": str(exc),
                          "details": getattr(exc, "details", {})}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
