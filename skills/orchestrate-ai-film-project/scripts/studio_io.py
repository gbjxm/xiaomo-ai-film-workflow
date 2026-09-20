"""Small, filesystem-only runtime primitives for the V0.9 studio."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ANY = object()
INDEX_PATH = ".工作室/采用索引.json"
CATALOGUE_SCHEMA = "xiaomo.studio-catalogue/v1"
RUNTIME_SCHEMA = "xiaomo.studio-runtime/v1"
DEPARTMENTS = ("A", "B", "C1", "C2", "D", "E")
_DEVICES = {"con", "prn", "aux", "nul", "clock$"} | {f"{kind}{i}" for kind in ("com", "lpt") for i in range(1, 10)}


class StudioError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details if details is not None else {}


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def canonical_bytes(value):
    try:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise StudioError("invalid_json", str(exc)) from exc


def require_str(value, name):
    if not isinstance(value, str) or not value.strip():
        raise StudioError("invalid_input", f"{name} must be a nonempty string")
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
        raise StudioError("invalid_input", f"{name} contains an invalid Unicode surrogate")
    return value


def require_int(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise StudioError("invalid_input", f"{name} must be an integer >= {minimum}")
    return value


def require_hash(value, name, allow_none=False):
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise StudioError("invalid_input", f"{name} must be SHA-256")
    return value.lower()


def valid_key(value, name="workset_key"):
    require_str(value, name)
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value) is None or value in _DEVICES:
        raise StudioError("invalid_key", f"Invalid {name}")
    return value


def now_utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value):
    require_str(value, "timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone missing")
        return parsed.astimezone(timezone.utc)
    except ValueError as exc:
        raise StudioError("invalid_time", "Timestamp must contain a timezone") from exc


def _is_reparse(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _comparison_path(path):
    """Compare Win32 and extended-length aliases without changing I/O paths.

    realpath may retain the extended prefix if a concurrent mkdir/removal
    changes the error between its native probes. Only normal drive and UNC
    aliases are equivalent here; device namespaces are not stripped.
    """
    value = os.fspath(path)
    if os.name == "nt":
        if value[:8].casefold() == "\\\\?\\unc\\":
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\") and re.match(r"^[A-Za-z]:[\\/]", value[4:]):
            value = value[4:]
    return os.path.normcase(value)


class Project:
    def __init__(self, root, adoption=False):
        candidate = Path(root).absolute()
        if not candidate.is_dir():
            raise StudioError("project_missing", "Project directory does not exist")
        if _is_reparse(candidate):
            raise StudioError("unsafe_path", "Project root cannot be a reparse point")
        self.root = candidate.resolve()
        entry = self.read_bytes("开始这里.md")
        try:
            entry_text = entry.decode("utf-8-sig")
        except UnicodeError as exc:
            raise StudioError("invalid_encoding", "Project entry must be UTF-8") from exc
        markers = re.findall(r"(?im)^[ \t]*(?:>[ \t]*)?workflowStudio[ \t]*:[ \t]*([^\r\n]*)\r?$", entry_text)
        markers = [value.strip() for value in markers]
        if markers != ["studio-v0.9"]:
            raise StudioError("unsupported_project", "Explicit studio-v0.9 project entry is required")
        releases = re.findall(r"(?im)^[ \t]*(?:>[ \t]*)?workflowRelease[ \t]*:[ \t]*([^\r\n]*)\r?$", entry_text)
        releases = [value.strip() for value in releases]
        if releases and releases != ["1.0"]:
            raise StudioError("unsupported_release", "Expected one explicit workflowRelease: 1.0, or no release marker for legacy projects")
        caps = self.read_json("工作室能力.json")
        if not isinstance(caps, dict) or caps.get("schema") != "xiaomo.studio-capabilities/v1" or caps.get("workflowStudio") != "studio-v0.9":
            raise StudioError("invalid_capabilities", "Invalid studio capability file")
        for key in ("candidate_design", "workset_runtime", "adoption_transactions", "media_execution"):
            if not isinstance(caps.get(key), bool):
                raise StudioError("invalid_capabilities", f"{key} must be a boolean")
        if caps.get("media_execution"):
            raise StudioError("unsupported_capability", "This text runtime does not enable media execution")
        if not caps["candidate_design"] or not caps["workset_runtime"]:
            raise StudioError("runtime_unavailable", "Workset runtime is not enabled")
        if caps.get("runtime_schema") != RUNTIME_SCHEMA:
            raise StudioError("unsupported_runtime", "Runtime schema is missing or unsupported")
        if adoption and not caps["adoption_transactions"]:
            raise StudioError("adoption_unavailable", "Adoption transactions are not enabled")
        self.capabilities = caps
        self.document_layout = self._document_layout(entry_text)

    def _document_layout(self, entry_text):
        markers = re.findall(r"(?m)^[ \t]*(?:>[ \t]*)?workflowDocsLayout[ \t]*:[ \t]*([^\r\n]*)\r?$", entry_text)
        markers = [value.strip() for value in markers]
        if not markers:
            return None
        if markers != ["production-docs-v1"]:
            raise StudioError("unsupported_document_layout", "One explicit production-docs-v1 layout marker is required")
        definition = Path(__file__).resolve().parents[1] / "references" / "studio-project-layout-v1.json"
        try:
            layout = json.loads(definition.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, UnicodeError) as exc:
            raise StudioError("invalid_document_layout", "Document layout definition cannot be read") from exc
        if not isinstance(layout, dict) or layout.get("schema") != "xiaomo.production-docs-layout/v1" or layout.get("marker") != markers[0]:
            raise StudioError("invalid_document_layout", "Unsupported document layout definition")
        directory, children = layout.get("directory"), layout.get("documentDirectories")
        if not isinstance(directory, str) or not isinstance(children, list) or not children:
            raise StudioError("invalid_document_layout", "Document layout requires a directory and named children")
        names = [directory, *children]
        if any(not isinstance(name, str) or "/" in self.rel(name) for name in names):
            raise StudioError("invalid_document_layout", "Layout directory names must be single safe components")
        if len({name.casefold() for name in names}) != len(names) or not {"工作稿", "续接缓存"}.issubset(children):
            raise StudioError("invalid_document_layout", "Document directories must be unique and include draft and cache storage")
        return layout

    def document_path(self, logical):
        """Resolve an explicit logical document path; ordinary paths stay physical."""
        relative = self.rel(logical)
        if self.document_layout and relative.split("/", 1)[0] in self.document_layout["documentDirectories"]:
            return self.document_layout["directory"] + "/" + relative
        return relative

    def rel(self, relative):
        require_str(relative, "path")
        value = relative.replace("\\", "/")
        if value.startswith("/") or ":" in value or "\x00" in value:
            raise StudioError("unsafe_path", "Path must be project-relative", {"path": relative})
        parts = value.split("/")
        for part in parts:
            if part in ("", ".", "..") or part.endswith((".", " ")) or any(ord(ch) < 32 for ch in part):
                raise StudioError("unsafe_path", "Unsafe path component", {"path": relative})
            if any(ch in part for ch in '<>"|?*'):
                raise StudioError("unsafe_path", "Invalid Windows path character", {"path": relative})
            if part.split(".", 1)[0].casefold() in _DEVICES:
                raise StudioError("unsafe_path", "Reserved Windows path name", {"path": relative})
        return "/".join(parts)

    def path(self, relative):
        relative = self.rel(relative)
        current = self.root
        for part in relative.split("/"):
            current = current / part
            try:
                if _is_reparse(current):
                    raise StudioError("unsafe_path", "Reparse points are not permitted", {"path": relative})
            except FileNotFoundError:
                pass
            except NotADirectoryError as exc:
                raise StudioError("unsafe_path", "A parent component is not a directory") from exc
        resolved = current.resolve(strict=False)
        try:
            root_key = _comparison_path(self.root)
            inside = os.path.commonpath((root_key, _comparison_path(resolved))) == root_key
        except ValueError:
            inside = False
        if not inside:
            raise StudioError("unsafe_path", "Path escapes the project", {"path": relative})
        return current

    def read_bytes(self, relative):
        try:
            return self.path(relative).read_bytes()
        except FileNotFoundError as exc:
            raise StudioError("file_missing", "Required file is missing", {"path": relative}) from exc
        except OSError as exc:
            raise StudioError("io_error", str(exc), {"path": relative}) from exc

    def read_json(self, relative, default=None):
        path = self.path(relative)
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeError, OSError) as exc:
            raise StudioError("corrupt_json", "JSON cannot be read", {"path": relative, "reason": str(exc)}) from exc

    def hash(self, relative):
        path = self.path(relative)
        if not path.exists():
            return None
        if not path.is_file():
            raise StudioError("not_a_file", "Expected a file", {"path": relative})
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        except OSError as exc:
            raise StudioError("io_error", str(exc), {"path": relative}) from exc
        return digest.hexdigest()


    def _guarded_projection(self, relative, temporary, expected):
        """Preserve the exact checked file, then publish without clobbering.

        Windows mandatory sharing closes the editor-write gap. Public views
        may briefly be absent; canonical state/catalogue remains atomic and
        points to already-durable immutable content.
        """
        if os.name != "nt":
            raise StudioError("unsupported_platform", "Guarded replacement of existing editable views requires Windows")
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        kernel.ReadFile.restype = wintypes.BOOL
        kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        kernel.SetFileInformationByHandle.restype = wintypes.BOOL
        kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]

        class RenameInfo(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("RootDirectory", wintypes.HANDLE), ("FileNameLength", wintypes.DWORD), ("FileName", wintypes.WCHAR * 1)]

        def native(value):
            value = str(value)
            if value.startswith("\\\\?\\"):
                return value
            return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value

        target = self.path(relative)
        handle = kernel.CreateFileW(native(target), 0x80000000 | 0x10000, 1, None, 3, 0x200000, None)
        if handle == ctypes.c_void_p(-1).value:
            error = ctypes.get_last_error()
            raise StudioError("file_busy", "Cannot exclusively guard the current file; it may be open for editing", {"path": relative, "winerror": error})
        backup_rel = None
        renamed = False
        try:
            count = kernel.GetFinalPathNameByHandleW(handle, None, 0, 0)
            if not count:
                raise OSError(ctypes.get_last_error(), "Cannot identify guarded file")
            name_buffer = ctypes.create_unicode_buffer(count + 1)
            if not kernel.GetFinalPathNameByHandleW(handle, name_buffer, len(name_buffer), 0):
                raise OSError(ctypes.get_last_error(), "Cannot identify guarded file")
            actual_name = name_buffer.value
            if actual_name.startswith("\\\\?\\UNC\\"):
                actual_name = "\\\\" + actual_name[8:]
            elif actual_name.startswith("\\\\?\\"):
                actual_name = actual_name[4:]
            if os.path.normcase(os.path.commonpath((str(self.root), actual_name))) != os.path.normcase(str(self.root)):
                raise StudioError("unsafe_path", "Guarded file resolved outside project")
            digest = hashlib.sha256()
            buffer = ctypes.create_string_buffer(64 * 1024)
            read = wintypes.DWORD()
            while True:
                if not kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(read), None):
                    raise OSError(ctypes.get_last_error(), "Cannot read guarded file")
                if not read.value:
                    break
                digest.update(buffer.raw[:read.value])
            if digest.hexdigest() != expected:
                raise StudioError("file_conflict", "An editor saved newer bytes before replacement", {"path": relative, "expected_sha256": expected, "current_sha256": digest.hexdigest()})
            self.failpoint("projection_after_guard")
            recovery_id = uuid.uuid4().hex
            backup_rel = f".工作室/恢复备份/{recovery_id}.original"
            backup = self.path(backup_rel)
            backup.parent.mkdir(parents=True, exist_ok=True)
            self.path(backup_rel)
            self.immutable(f".工作室/恢复备份/{recovery_id}.json", canonical_bytes({"schema": "xiaomo.studio-preserved-view/v1", "path": relative, "original_sha256": expected, "backup": backup_rel, "replacement_sha256": sha_bytes(temporary.read_bytes()), "created_at": now_utc()}))
            encoded = native(backup).encode("utf-16-le")
            rename_buffer = ctypes.create_string_buffer(RenameInfo.FileName.offset + len(encoded) + 2)
            info = ctypes.cast(rename_buffer, ctypes.POINTER(RenameInfo)).contents
            info.Flags = 0
            info.RootDirectory = None
            info.FileNameLength = len(encoded)
            ctypes.memmove(ctypes.addressof(rename_buffer) + RenameInfo.FileName.offset, encoded, len(encoded))
            if not kernel.SetFileInformationByHandle(handle, 3, rename_buffer, len(rename_buffer)):
                raise OSError(ctypes.get_last_error(), "Cannot preserve guarded original")
            renamed = True
            self.failpoint("projection_after_preserve")
            self.path(relative)
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise StudioError("file_conflict", "An editor created a newer view during publication; neither version was overwritten", {"path": relative, "preserved_path": backup_rel, "current_sha256": self.hash(relative)}) from exc
        except (OSError, StudioError):
            if renamed and not target.exists():
                # Do not clobber a file that appears during restoration.
                try:
                    os.link(self.path(backup_rel), target)
                except OSError:
                    pass
            raise
        finally:
            kernel.CloseHandle(handle)

    def atomic_bytes(self, relative, data, expected=ANY):
        if not isinstance(data, bytes):
            raise StudioError("invalid_input", "atomic_bytes expects bytes")
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.path(relative)
        temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
        created = False
        try:
            with temporary.open("xb") as stream:
                created = True
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            self.path(relative)
            if expected is not ANY:
                wanted = require_hash(expected, "expected", allow_none=True)
                actual = self.hash(relative)
                if actual != wanted:
                    raise StudioError("file_conflict", "File changed before replacement", {"path": relative, "expected_sha256": wanted, "current_sha256": actual})
            normalized = self.rel(relative)
            private_prefixes = (".工作室/", self.document_path("续接缓存") + "/")
            public_view = not normalized.startswith(private_prefixes) or normalized == ".工作室/采用索引.md"
            if expected is None or (expected is ANY and not target.exists()):
                try:
                    os.link(temporary, target)
                except FileExistsError as exc:
                    raise StudioError("file_conflict", "New target appeared; refusing to replace it", {"path": relative, "current_sha256": self.hash(relative)}) from exc
            elif public_view:
                if expected is ANY:
                    raise StudioError("missing_guard", "Replacing an editable view requires its expected SHA-256", {"path": relative})
                self._guarded_projection(relative, temporary, require_hash(expected, "expected"))
            else:
                os.replace(temporary, target)
            if os.name != "nt":
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except StudioError:
            raise
        except OSError as exc:
            raise StudioError("io_error", str(exc), {"path": relative}) from exc
        finally:
            if created and temporary.exists():
                temporary.unlink()

    def atomic_json(self, relative, data, expected=ANY):
        self.atomic_bytes(relative, canonical_bytes(data), expected=expected)

    def immutable(self, relative, data):
        path = self.path(relative)
        if path.exists():
            if path.read_bytes() == data:
                return relative
            raise StudioError("immutable_conflict", "Immutable content already differs", {"path": relative})
        self.atomic_bytes(relative, data, expected=None)
        return relative

    @contextlib.contextmanager
    def lock(self, name, timeout=10):
        require_str(name, "lock_name")
        lock_rel = ".工作室/锁/" + sha_bytes(name.casefold().encode("utf-8")) + ".lock"
        path = self.path(lock_rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path(lock_rel)
        stream = path.open("a+b")
        acquired = False
        try:
            if path.stat().st_size == 0:
                stream.write(b"\0")
                stream.flush()
            start = time.monotonic()
            while True:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (OSError, BlockingIOError):
                    if time.monotonic() - start >= timeout:
                        raise StudioError("lock_busy", "Another operation is updating this scope", {"scope": name})
                    time.sleep(0.025)
            yield
        finally:
            if acquired:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()

    def _validate_catalogue(self, value):
        if not isinstance(value, dict) or value.get("schema") != CATALOGUE_SCHEMA:
            raise StudioError("corrupt_catalogue", "Invalid catalogue schema")
        require_int(value.get("revision"), "catalogue revision")
        for key in ("objects", "transactions"):
            if not isinstance(value.get(key), dict):
                raise StudioError("corrupt_catalogue", f"{key} must be an object")
        if not isinstance(value.get("ids", {}), dict):
            raise StudioError("corrupt_catalogue", "ids must be an object")
        paths = set()
        for relative, obj in value["objects"].items():
            normal = self.rel(relative)
            if normal.casefold() in paths:
                raise StudioError("corrupt_catalogue", "Case-alias duplicate object path")
            paths.add(normal.casefold())
            if not isinstance(obj, dict) or obj.get("owner") not in DEPARTMENTS:
                raise StudioError("corrupt_catalogue", "Invalid object owner")
            require_int(obj.get("version"), "object version", 1)
            require_hash(obj.get("sha256"), "object sha256")
            snapshot = self.rel(obj.get("snapshot"))
            if not snapshot.startswith(".工作室/事务/"):
                raise StudioError("corrupt_catalogue", "Adopted snapshot must be immutable transaction content")
            valid_key(obj.get("transaction_id"), "transaction_id")
        for key, entry in value["transactions"].items():
            valid_key(key, "transaction_id")
            if not isinstance(entry, dict):
                raise StudioError("corrupt_catalogue", "Invalid transaction entry")
            require_hash(entry.get("request_hash"), "request_hash")
            require_hash(entry.get("manifest_sha256"), "manifest_sha256")
            if not self.rel(entry.get("manifest_path")).startswith(".工作室/事务/"):
                raise StudioError("corrupt_catalogue", "Invalid transaction manifest")
        value.setdefault("ids", {})
        for stable_id, relative in value["ids"].items():
            if re.fullmatch(r"(?:S|A|V|CUT|PUB)\d{2,}", stable_id) is None:
                raise StudioError("corrupt_catalogue", "Invalid stable ID")
            normal = self.rel(relative)
            if normal.casefold() not in paths:
                raise StudioError("corrupt_catalogue", "Stable ID points to no adopted object")
        return value

    def catalogue(self):
        value = self.read_json(INDEX_PATH)
        if value is None:
            return {"schema": CATALOGUE_SCHEMA, "revision": 0, "objects": {}, "transactions": {}, "ids": {}}
        return self._validate_catalogue(value)

    def publish_catalogue(self, catalogue, expected_revision):
        require_int(expected_revision, "expected_revision")
        path = self.path(INDEX_PATH)
        old_raw = path.read_bytes() if path.exists() else None
        if old_raw is None:
            current = {"schema": CATALOGUE_SCHEMA, "revision": 0, "objects": {}, "transactions": {}, "ids": {}}
        else:
            try:
                current = self._validate_catalogue(json.loads(old_raw.decode("utf-8-sig")))
            except (ValueError, UnicodeError) as exc:
                raise StudioError("corrupt_catalogue", str(exc)) from exc
        candidate = self._validate_catalogue(catalogue)
        if current["revision"] != expected_revision or candidate["revision"] != expected_revision + 1:
            raise StudioError("catalogue_conflict", "Catalogue revision changed")
        if not set(current["objects"]).issubset(candidate["objects"]) or not set(current["transactions"]).issubset(candidate["transactions"]):
            raise StudioError("catalogue_conflict", "A publication cannot erase previous records")
        for key, entry in current["transactions"].items():
            if candidate["transactions"][key] != entry:
                raise StudioError("catalogue_conflict", "A published transaction is immutable")
        for stable_id, relative in current.get("ids", {}).items():
            if candidate.get("ids", {}).get(stable_id, "").casefold() != relative.casefold():
                raise StudioError("catalogue_conflict", "A stable ID cannot be rebound")
        for key, entry in candidate["transactions"].items():
            if key not in current["transactions"] and self.hash(entry["manifest_path"]) != entry["manifest_sha256"].lower():
                raise StudioError("manifest_invalid", "Transaction manifest must exist before publication")
        for key, obj in candidate["objects"].items():
            if obj != current["objects"].get(key) and self.hash(obj["snapshot"]) != obj["sha256"].lower():
                raise StudioError("snapshot_invalid", "All content snapshots must exist before publication", {"path": key})
        self.atomic_json(INDEX_PATH, candidate, expected=sha_bytes(old_raw) if old_raw is not None else None)
        return candidate

    def check_dependencies(self, dependencies, catalogue=None):
        if not isinstance(dependencies, list):
            raise StudioError("invalid_input", "dependencies must be an array")
        catalogue = self.catalogue() if catalogue is None else catalogue
        result = []
        for dep in dependencies:
            if not isinstance(dep, dict):
                raise StudioError("invalid_input", "dependency must be an object")
            relative = self.rel(dep.get("path"))
            kind = dep.get("kind", "file")
            expected = require_hash(dep.get("sha256"), "dependency sha256")
            expected_version = dep.get("version")
            current_version = None
            current = None
            if kind == "file":
                current = self.hash(relative)
            elif kind == "adopted":
                require_int(expected_version, "dependency version", 1)
                matches = [obj for key, obj in catalogue["objects"].items() if key.casefold() == relative.casefold()]
                obj = matches[0] if matches else None
                if obj:
                    current_version = obj["version"]
                    current = self.hash(obj["snapshot"])
                    if current != obj["sha256"].lower():
                        current = None
            else:
                raise StudioError("invalid_input", "Unknown dependency kind")
            status = "missing" if current is None else ("unchanged" if current == expected and (kind == "file" or current_version == expected_version) else "changed")
            result.append({"path": relative, "kind": kind, "expected_sha256": expected, "current_sha256": current, "expected_version": expected_version, "current_version": current_version, "status": status})
        return result

    def owner_allowed(self, owner, relative):
        relative = self.rel(relative)
        if owner not in DEPARTMENTS or Path(relative).suffix.casefold() not in {".md", ".txt", ".json"}:
            return False
        key = relative.casefold()
        roots = {"A": "a_总控.md", "B": "b_故事剧本.md", "D": "d_后期剪辑.md", "E": "e_发行复盘.md"}
        if key == roots.get(owner):
            return True
        if self.document_layout:
            container = self.document_layout["directory"].casefold() + "/"
            document_roots = {name.casefold() for name in self.document_layout["documentDirectories"]}
            if key.startswith(container):
                key = key[len(container):]
                # Only the configured document directories move. A root role
                # file or an arbitrary subfolder cannot acquire new ownership.
                if key.split("/", 1)[0] not in document_roots:
                    return False
            elif key.split("/", 1)[0] in document_roots:
                return False
        prefixes = {
            "A": ("制作统筹",),
            "B": ("剧本",),
            "C1": ("资产设计", "色卡", "提示词卡/资产", "生成记录/资产"),
            "C2": ("场景制作", "镜头规划", "表演设计", "提示词卡", "生成记录/镜头"),
            "D": ("后期方案",),
            "E": ("发布包",),
        }
        if owner == "C2" and (key == "提示词卡/资产" or key.startswith("提示词卡/资产/")):
            return False
        return any(key.startswith(prefix.casefold() + "/") for prefix in prefixes[owner])

    def failpoint(self, name):
        if self.capabilities.get("test_mode") is not True:
            return
        if os.environ.get("XIAOMO_STUDIO_CRASHPOINT") == name:
            os._exit(97)
        if os.environ.get("XIAOMO_STUDIO_FAILPOINT") == name:
            raise StudioError("injected_failure", "Injected test failure", {"point": name})

