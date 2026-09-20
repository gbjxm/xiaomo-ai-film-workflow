"""Independent worksets and recoverable full drafts for the V0.9 studio.

Callers of load_state/persist_state must hold the named workset lock when writing.
A state or cache never establishes adoption; the catalogue owns that fact.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timedelta, timezone

from studio_io import (
    INDEX_PATH,
    StudioError,
    canonical_bytes,
    now_utc,
    parse_time,
    require_hash,
    require_int,
    require_str,
    sha_bytes,
    valid_key,
)

SCHEMA = "xiaomo.studio-workset/v1"
CACHE_SCHEMA = "xiaomo.studio-workset-cache/v1"
MAX_CACHE_BYTES = 6144
DEPARTMENTS = ("A", "B", "C1", "C2", "D", "E")
DEFAULT_TTL = 14400


class _LoadedState(dict):
    """Dictionary with a nonserialized hash of the exact bytes that were read."""

    _disk_sha256: str


def _request(request):
    if not isinstance(request, dict):
        raise StudioError("invalid_input", "request must be an object")
    return request


def _key(request):
    return valid_key(_request(request).get("workset_key"))


def _state_path(key):
    return f".工作室/工作集/{valid_key(key)}/state.json"


def _cache_path(p, key):
    return p.document_path(f"续接缓存/{valid_key(key)}.json")


def _draft_root(p, state):
    return p.document_path(f"工作稿/{state['department']}/{state['workset_key']}")


def _projection_path(p, state):
    return _draft_root(p, state) + "/当前.md"


def _ttl(request):
    value = require_int(request.get("ttl_seconds", DEFAULT_TTL), "ttl_seconds", 60)
    if value > 86400:
        raise StudioError("invalid_input", "ttl_seconds must be between 60 and 86400")
    return value


def _new_lease(holder, ttl, lease_id=None):
    return {
        "id": lease_id or uuid.uuid4().hex,
        "holder": require_str(holder, "holder"),
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z"),
    }


def _active(lease):
    return bool(lease) and parse_time(lease["expires_at"]) > datetime.now(timezone.utc)


def _dependencies(p, value):
    if not isinstance(value, list):
        raise StudioError("invalid_input", "dependencies must be an array")
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict):
            raise StudioError("invalid_input", "dependency must be an object")
        relative = p.rel(item.get("path"))
        kind = item.get("kind", "file")
        if kind not in ("file", "adopted"):
            raise StudioError("invalid_input", "dependency kind must be file or adopted")
        identity = (kind, relative.casefold())
        if identity in seen:
            raise StudioError("duplicate_dependency", "Dependency path has a duplicate or Windows case alias", {"path": relative})
        seen.add(identity)
        dep = {"path": relative, "kind": kind, "sha256": require_hash(item.get("sha256"), "dependency sha256")}
        if kind == "adopted":
            dep["version"] = require_int(item.get("version"), "dependency version", 1)
        else:
            p.path(relative)
        result.append(dep)
    return result


def _validate_state(p, state, key):
    if not isinstance(state, dict) or state.get("schema") != SCHEMA or state.get("workset_key") != key:
        raise StudioError("corrupt_workset", "Invalid workset state identity", {"workset_key": key})
    if state.get("department") not in DEPARTMENTS:
        raise StudioError("corrupt_workset", "Invalid workset department")
    require_str(state.get("title"), "state title")
    require_int(state.get("revision"), "state revision", 1)
    for name in ("lease", "handoff", "draft", "dependencies", "next_step", "pending_transaction", "last_adoption"):
        if name not in state:
            raise StudioError("corrupt_workset", "Workset state field is missing", {"field": name})
    if not isinstance(state["next_step"], str):
        raise StudioError("corrupt_workset", "next_step must be a string")
    lease = state["lease"]
    if lease is not None:
        if not isinstance(lease, dict):
            raise StudioError("corrupt_workset", "lease must be an object or null")
        require_str(lease.get("id"), "lease id")
        require_str(lease.get("holder"), "lease holder")
        parse_time(lease.get("expires_at"))
    for name in ("handoff", "pending_transaction", "last_adoption"):
        if state[name] is not None and not isinstance(state[name], dict):
            raise StudioError("corrupt_workset", f"{name} must be an object or null")
    handoff = state["handoff"]
    if handoff:
        for name in ("handoff_id", "source_holder", "source_lease_id", "prepared_at", "user_switch_evidence"):
            require_str(handoff.get(name), "handoff " + name)
        parse_time(handoff["prepared_at"])
    _dependencies(p, state["dependencies"])
    draft = state["draft"]
    if draft is not None:
        if not isinstance(draft, dict):
            raise StudioError("corrupt_workset", "draft must be an object or null")
        require_hash(draft.get("sha256"), "draft sha256")
        require_int(draft.get("content_version"), "draft content_version", 1)
        snapshot = p.rel(draft.get("snapshot"))
        prefix = _draft_root(p, state) + "/历史/"
        if not snapshot.startswith(prefix) or not snapshot.endswith(".md"):
            raise StudioError("corrupt_workset", "Draft snapshot is outside its own immutable history")
        p.path(snapshot)
        if p.rel(draft.get("projection_path")) != _projection_path(p, state):
            raise StudioError("corrupt_workset", "Draft projection does not belong to this workset")
        p.path(draft["projection_path"])
        if draft.get("projection_status") not in ("pending", "synced", "conflict"):
            raise StudioError("corrupt_workset", "Invalid draft projection state")
        if "previous_projection_sha256" not in draft:
            raise StudioError("corrupt_workset", "Draft recovery baseline is missing")
        require_hash(draft["previous_projection_sha256"], "previous projection sha256", allow_none=True)
        if not isinstance(draft.get("metadata", {}), dict):
            raise StudioError("corrupt_workset", "Draft metadata must be an object")
    canonical_bytes(state)
    return state


def load_state(p, key):
    """Read authoritative runtime registration, never falling back to a cache."""
    key = valid_key(key)
    try:
        raw = p.read_bytes(_state_path(key))
    except StudioError as exc:
        if exc.code == "file_missing":
            raise StudioError("workset_missing", "Workset does not exist", {"workset_key": key}) from exc
        raise
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, ValueError) as exc:
        raise StudioError("corrupt_workset", "Workset JSON cannot be read", {"workset_key": key}) from exc
    _validate_state(p, value, key)
    state = _LoadedState(value)
    state._disk_sha256 = sha_bytes(raw)
    return state


def _require_session(state, request, allow_pending=False, allow_handoff=False):
    _request(request)
    key = _key(request)
    if key != state["workset_key"]:
        raise StudioError("workset_mismatch", "Session belongs to another workset")
    expected = require_int(request.get("expected_revision"), "expected_revision", 1)
    if expected != state["revision"]:
        raise StudioError("revision_conflict", "Workset revision changed", {"expected_revision": expected, "current_revision": state["revision"]})
    holder = require_str(request.get("holder"), "holder")
    token = require_str(request.get("lease_id"), "lease_id")
    lease = state["lease"]
    if not _active(lease):
        raise StudioError("lease_expired", "Claim the workset before writing")
    if lease["holder"] != holder or lease["id"] != token:
        raise StudioError("lease_conflict", "Holder or lease token does not match this workset")
    if state["handoff"] is not None and not allow_handoff:
        raise StudioError("handoff_pending", "Activate or cancel the prepared handoff before ordinary writes")
    if state["pending_transaction"] is not None and not allow_pending:
        raise StudioError("adoption_pending", "Resume or cancel the adoption transaction before changing the draft", _transaction_ref(state["pending_transaction"]))


def require_session(state, request, allow_pending=False):
    """Validate an unexpired session; allow_pending does not permit a handoff."""
    _require_session(state, request, allow_pending=allow_pending)


def _preview(value, byte_limit):
    encoded = value.encode("utf-8")
    return encoded[:byte_limit].decode("utf-8", errors="ignore"), len(encoded) > byte_limit


def _transaction_ref(value):
    if value is None:
        return None
    return {name: copy.deepcopy(value[name]) for name in ("transaction_id", "request_hash", "manifest_path", "manifest_sha256", "status") if name in value}


def _cache_data(state, state_sha256):
    title, title_cut = _preview(state["title"], 256)
    step, step_cut = _preview(state["next_step"], 768)
    draft = state["draft"]
    lease = state["lease"]
    result = {
        "schema": CACHE_SCHEMA,
        "workset_key": state["workset_key"],
        "department": state["department"],
        "title_preview": title,
        "title_truncated": title_cut,
        "revision": state["revision"],
        "state": {"path": _state_path(state["workset_key"]), "sha256": state_sha256},
        "draft": None if draft is None else {name: draft[name] for name in ("snapshot", "sha256", "content_version", "projection_path", "projection_status")},
        "dependency_count": len(state["dependencies"]),
        "next_step_preview": step,
        "next_step_truncated": step_cut,
        "cache_is_authority": False,
        "resume_grants_adoption": False,
        "media_execution": False,
    }
    if lease:
        holder, cut = _preview(lease["holder"], 128)
        token, token_cut = _preview(lease["id"], 128)
        result["lease"] = {"id_preview": token, "holder_preview": holder, "identity_truncated": cut or token_cut, "expires_at": lease["expires_at"]}
    else:
        result["lease"] = None
    result["handoff_id"] = (state["handoff"] or {}).get("handoff_id")
    # Transaction details can be large; the state pointer is the recovery source.
    result["has_pending_transaction"] = state["pending_transaction"] is not None
    result["has_last_adoption"] = state["last_adoption"] is not None
    data = canonical_bytes(result)
    if len(data) > MAX_CACHE_BYTES:
        raise StudioError("cache_too_large", "Short cache exceeds 6144 UTF-8 bytes")
    return result


def _write_state(p, state):
    key = valid_key(state["workset_key"])
    _validate_state(p, state, key)
    relative = _state_path(key)
    previous = state._disk_sha256 if isinstance(state, _LoadedState) else p.hash(relative)
    p.atomic_json(relative, state, expected=previous)
    digest = sha_bytes(canonical_bytes(state))
    if isinstance(state, _LoadedState):
        state._disk_sha256 = digest
    return digest


def _write_cache(p, state, state_sha256):
    relative = _cache_path(p, state["workset_key"])
    value = _cache_data(state, state_sha256)
    try:
        previous = p.hash(relative)
        p.atomic_json(relative, value, expected=previous)
    except StudioError as exc:
        # Runtime registration is already durable. A disposable cache cannot undo it.
        return {"cache_path": relative, "cache_status": "pending", "cache_error": {"code": exc.code, "message": exc.message, "details": exc.details}}
    return {"cache_path": relative, "cache_status": "synced", "cache_bytes": len(canonical_bytes(value))}


def persist_state(p, state):
    """Persist state plus a disposable cache under an already-held workset lock.

    The caller owns revision/updated_at changes. Adoption fields are retained.
    This helper does not publish adoption or modify a draft projection.
    """
    digest = _write_state(p, state)
    result = {"state_path": _state_path(state["workset_key"]), "state_sha256": digest, "revision": state["revision"]}
    result.update(_write_cache(p, state, digest))
    return result


def _cache_info(p, state, state_sha256):
    relative = _cache_path(p, state["workset_key"])
    result = {"path": relative, "sha256": None, "status": "missing"}
    try:
        # Read disposable bytes once: deletion between hash/read is harmless,
        # and an occupied or unreadable cache must not hide a durable draft.
        data = p.read_bytes(relative)
    except StudioError as exc:
        if exc.code != "file_missing":
            result["status"] = "unavailable"
            result["error"] = {"code": exc.code, "message": exc.message, "details": exc.details}
        return result
    result["sha256"] = sha_bytes(data)
    result["bytes"] = len(data)
    if len(data) > MAX_CACHE_BYTES:
        result["status"] = "oversized"
        return result
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeError, ValueError):
        result["status"] = "corrupt"
        return result
    if not isinstance(value, dict) or value.get("schema") != CACHE_SCHEMA or value.get("workset_key") != state["workset_key"]:
        result["status"] = "corrupt"
    elif value != _cache_data(state, state_sha256):
        result["status"] = "stale"
    else:
        result["status"] = "valid"
    return result


def _projection_info(p, state):
    relative = _projection_path(p, state)
    draft = state["draft"]
    result = {"path": relative, "sha256": None}
    if draft is not None:
        result.update({"saved_snapshot_sha256": draft["sha256"].lower(), "previous_projection_sha256": draft["previous_projection_sha256"], "recorded_status": draft["projection_status"]})
    try:
        actual = p.hash(relative)
    except StudioError as exc:
        if exc.code not in ("io_error", "file_busy", "file_missing"):
            raise
        result.update({"status": "pending" if exc.code == "file_missing" and draft else "unavailable", "reason": "projection_read_unavailable", "error": {"code": exc.code, "message": exc.message, "details": exc.details}})
        return result
    result["sha256"] = actual
    if draft is None:
        result["status"] = "uninitialized" if actual is None else "unregistered_user_edit"
        return result
    wanted = draft["sha256"].lower()
    previous = draft["previous_projection_sha256"]
    if actual == wanted:
        status = "synced"
    elif actual is None:
        status = "pending"
        result["reason"] = "projection_missing"
    elif draft["projection_status"] == "pending" and actual == previous:
        status = "pending_safe_rebuild"
    else:
        status = "user_modified"
    result["status"] = status
    return result


def _snapshot_info(p, state):
    draft = state["draft"]
    if draft is None:
        return None
    actual = p.hash(draft["snapshot"])
    status = "missing" if actual is None else ("valid" if actual == draft["sha256"].lower() else "corrupt")
    return {"path": draft["snapshot"], "sha256": draft["sha256"].lower(), "current_sha256": actual, "status": status, "content_version": draft["content_version"]}


def _require_snapshot(p, state):
    info = _snapshot_info(p, state)
    if info and info["status"] != "valid":
        raise StudioError("draft_snapshot_invalid", "Saved full draft is missing or changed; do not recover it from a summary", info)
    return info


def _repair_projection(p, state):
    """Rebuild only missing/known-old projections, keeping unknown user edits."""
    draft = state["draft"]
    if draft is None:
        return
    info = _require_snapshot(p, state)
    try:
        actual = p.hash(draft["projection_path"])
    except StudioError as exc:
        if exc.code not in ("io_error", "file_busy", "file_missing"):
            raise
        # Unreadable is not absent. Keep the complete saved draft and retry only
        # after a later session can actually inspect the editable projection.
        if draft["projection_status"] != "conflict":
            draft["projection_status"] = "pending"
        draft["projection_error"] = {"code": exc.code, "message": exc.message, "details": exc.details, "read_status": "unavailable"}
        return
    desired = info["sha256"]
    if actual == desired:
        draft["projection_status"] = "synced"
        draft.pop("projection_error", None)
        return
    previous = draft["previous_projection_sha256"]
    safe_old = draft["projection_status"] == "pending" and actual == previous
    if actual is not None and not safe_old:
        draft["projection_status"] = "conflict"
        draft["projection_error"] = {"code": "user_edit_conflict", "current_sha256": actual, "expected_previous_sha256": previous}
        return
    content = p.read_bytes(draft["snapshot"])
    if sha_bytes(content) != desired:
        raise StudioError("draft_snapshot_invalid", "Draft snapshot changed before projection recovery", info)
    try:
        p.atomic_bytes(draft["projection_path"], content, expected=actual)
    except StudioError as exc:
        draft["projection_status"] = "conflict" if exc.code == "file_conflict" else "pending"
        draft["projection_error"] = {"code": exc.code, "message": exc.message, "details": exc.details}
        return
    draft["projection_status"] = "synced"
    draft.pop("projection_error", None)


def _inspection(p, state):
    relative = _state_path(state["workset_key"])
    state_hash = state._disk_sha256 if isinstance(state, _LoadedState) else p.hash(relative)
    snapshot = _snapshot_info(p, state)
    projection = _projection_info(p, state)
    catalogue = p.catalogue()
    statuses = p.check_dependencies(state["dependencies"], catalogue=catalogue)
    required = [{"kind": "workset_state", "path": relative, "sha256": state_hash}]
    if snapshot:
        required.append({"kind": "draft_snapshot", **snapshot})
    if projection["sha256"] is not None and (snapshot is None or projection["sha256"] != snapshot["sha256"]):
        required.append({"kind": "editable_projection", "path": projection["path"], "sha256": projection["sha256"], "status": projection["status"]})
    for status in statuses:
        if status["kind"] == "file":
            required.append({"kind": "file_dependency", "path": status["path"], "sha256": status["current_sha256"], "status": status["status"]})
        else:
            match = next((obj for path, obj in catalogue["objects"].items() if path.casefold() == status["path"].casefold()), None)
            required.append({"kind": "adopted_dependency", "object_path": status["path"], "path": match["snapshot"] if match else None, "sha256": status["current_sha256"], "version": status["current_version"], "status": status["status"]})
    draft = state["draft"]
    draft_summary = None if draft is None else {name: copy.deepcopy(draft[name]) for name in ("snapshot", "sha256", "content_version", "projection_path", "projection_status", "previous_projection_sha256")}
    if draft_summary is not None:
        draft_summary["metadata_sha256"] = sha_bytes(canonical_bytes(draft.get("metadata", {})))
    return {
        "workset_key": state["workset_key"], "department": state["department"], "title": state["title"],
        "revision": state["revision"], "lease": copy.deepcopy(state["lease"]), "lease_active": _active(state["lease"]),
        "handoff": copy.deepcopy(state["handoff"]), "draft": draft_summary, "snapshot": snapshot, "projection": projection,
        "required_read_set": required, "dependency_changes": [item for item in statuses if item["status"] != "unchanged"],
        "dependency_count": len(statuses), "catalogue_revision": catalogue["revision"], "cache": _cache_info(p, state, state_hash),
        "next_step": state["next_step"], "pending_transaction": _transaction_ref(state["pending_transaction"]),
        "last_adoption": _transaction_ref(state["last_adoption"]), "draft_status": "candidate" if draft else "none",
        "resume_grants_adoption": False, "media_execution": False,
    }


def inspect(p, request):
    key = _key(request)
    # Do not pair an old revision with a newer projection/cache and call it a
    # user edit. Retry a bounded number of lock-free reads of one state version.
    for _ in range(3):
        state = load_state(p, key)
        loaded_hash = state._disk_sha256
        try:
            result = _inspection(p, state)
        except StudioError:
            if p.hash(_state_path(key)) != loaded_hash:
                continue
            raise
        if p.hash(_state_path(key)) == loaded_hash:
            return {"action": "Inspect", "status": "inspected", **result}
    raise StudioError("workset_busy", "Workset changed during inspection; retry this read", {"workset_key": key})


def list_worksets(p, request):
    _request(request)
    department = request.get("department")
    if department is not None and department not in DEPARTMENTS:
        raise StudioError("invalid_input", "Unknown department")
    directory = p.path(".工作室/工作集")
    values, errors = [], []
    if directory.exists():
        for child in sorted(directory.iterdir(), key=lambda value: value.name):
            try:
                key = valid_key(child.name)
                checked = p.path(f".工作室/工作集/{key}")
                if not checked.is_dir():
                    continue
                state = load_state(p, key)
                if department is not None and state["department"] != department:
                    continue
                draft = state["draft"]
                values.append({"workset_key": key, "department": state["department"], "title": state["title"], "revision": state["revision"], "lease_active": _active(state["lease"]), "holder": (state["lease"] or {}).get("holder"), "draft_snapshot": draft["snapshot"] if draft else None, "next_step": state["next_step"], "has_pending_transaction": state["pending_transaction"] is not None})
            except StudioError as exc:
                errors.append({"workset_key": child.name, "code": exc.code, "message": exc.message, "details": exc.details})
    return {"action": "List", "status": "listed", "worksets": values, "errors": errors}


def create(p, request):
    key = _key(request)
    department = request.get("department")
    if department not in DEPARTMENTS:
        raise StudioError("invalid_input", "department must be A, B, C1, C2, D or E")
    title = require_str(request.get("title"), "title")
    holder = require_str(request.get("holder"), "holder")
    ttl = _ttl(request)
    step = request.get("next_step", "")
    if not isinstance(step, str):
        raise StudioError("invalid_input", "next_step must be a string")
    timestamp = now_utc()
    state = {"schema": SCHEMA, "workset_key": key, "department": department, "title": title, "revision": 1, "created_at": timestamp, "updated_at": timestamp, "lease": _new_lease(holder, ttl), "handoff": None, "draft": None, "dependencies": [], "next_step": step, "pending_transaction": None, "last_adoption": None}
    # Validate output locations before acquiring a lock or creating state files.
    p.path(_state_path(key))
    p.path(_projection_path(p, state))
    with p.lock("workset:" + key):
        if p.hash(_state_path(key)) is not None:
            raise StudioError("workset_exists", "Workset key already exists", {"workset_key": key})
        p.atomic_json(_state_path(key), state, expected=None)
        result = _write_cache(p, state, p.hash(_state_path(key)))
        return {"action": "Create", "status": "created", **_inspection(p, state), **result}


def claim(p, request):
    key = _key(request)
    holder = require_str(request.get("holder"), "holder")
    ttl = _ttl(request)
    expected = require_int(request.get("expected_revision"), "expected_revision", 1)
    with p.lock("workset:" + key):
        state = load_state(p, key)
        if expected != state["revision"]:
            raise StudioError("revision_conflict", "Workset revision changed", {"expected_revision": expected, "current_revision": state["revision"]})
        previous = state["lease"]
        active = _active(previous)
        same = active and previous["holder"] == holder
        if same:
            if request.get("lease_id") != previous["id"]:
                raise StudioError("lease_conflict", "Existing holder must provide its lease token to renew")
            status = "renewed"
            state["lease"] = _new_lease(holder, ttl, previous["id"])
        else:
            if active:
                evidence = request.get("user_switch_evidence")
                stopped = request.get("previous_holder_stopped")
                stop_evidence = request.get("stop_evidence")
                if not isinstance(evidence, str) or not evidence.strip() or stopped is not True or not isinstance(stop_evidence, str) or not stop_evidence.strip():
                    raise StudioError("takeover_requires_evidence", "Active takeover requires switch evidence and confirmed previous-holder stop evidence")
            status = "taken_over" if active else "claimed"
            state["lease"] = _new_lease(holder, ttl)
            # A prepared handoff belongs to the old lease and cannot survive replacement.
            state["handoff"] = None
        _repair_projection(p, state)
        state["revision"] += 1
        state["updated_at"] = now_utc()
        result = persist_state(p, state)
        return {"action": "Claim", "status": status, **_inspection(p, state), **result}


def save_draft(p, request):
    key = _key(request)
    content = request.get("content")
    if not isinstance(content, str):
        raise StudioError("invalid_input", "content must be the complete UTF-8 draft string")
    try:
        content_bytes = content.encode("utf-8")
    except UnicodeError as exc:
        raise StudioError("invalid_encoding", "content must encode as UTF-8") from exc
    if "expected_draft_sha256" not in request:
        raise StudioError("invalid_input", "expected_draft_sha256 is required; use null only if the current view is absent")
    expected_view = require_hash(request["expected_draft_sha256"], "expected_draft_sha256", allow_none=True)
    with p.lock("workset:" + key):
        state = load_state(p, key)
        require_session(state, request)
        _require_snapshot(p, state)
        projection_path = _projection_path(p, state)
        actual_view = p.hash(projection_path)
        if actual_view != expected_view:
            raise StudioError("draft_conflict", "Current draft view changed; inspect and merge the exact saved user edit", {"path": projection_path, "expected_sha256": expected_view, "current_sha256": actual_view, "saved_snapshot": (state["draft"] or {}).get("snapshot")})
        dependencies = _dependencies(p, request.get("dependencies", state["dependencies"]))
        # Validate/check only declared dependencies, never enumerate an asset tree.
        p.check_dependencies(dependencies)
        next_step = request.get("next_step", state["next_step"])
        if not isinstance(next_step, str):
            raise StudioError("invalid_input", "next_step must be a string")
        old = state["draft"]
        metadata = copy.deepcopy(request.get("metadata", (old or {}).get("metadata", {})))
        if not isinstance(metadata, dict):
            raise StudioError("invalid_input", "metadata must be an object")
        canonical_bytes(metadata)
        digest = sha_bytes(content_bytes)
        new_snapshot = old is None or old["sha256"].lower() != digest or canonical_bytes(old.get("metadata", {})) != canonical_bytes(metadata)
        progress_changed = dependencies != state["dependencies"] or next_step != state["next_step"]
        projection_changed = actual_view != digest or (old is not None and old["projection_status"] != "synced")
        if not new_snapshot and not progress_changed and not projection_changed:
            cache_info = _cache_info(p, state, state._disk_sha256)
            result = {} if cache_info["status"] == "valid" else _write_cache(p, state, state._disk_sha256)
            return {"action": "SaveDraft", "status": "no_change", **_inspection(p, state), **result}
        preserved = None
        if actual_view is not None and (old is None or actual_view != old["sha256"].lower()):
            edit = p.read_bytes(projection_path)
            if sha_bytes(edit) != actual_view:
                raise StudioError("draft_conflict", "User draft changed while its history was being preserved", {"path": projection_path})
            preserved_path = _draft_root(p, state) + f"/历史/用户保存_{actual_view}.md"
            p.immutable(preserved_path, edit)
            preserved = {"path": preserved_path, "sha256": actual_view}
        version = (old["content_version"] + 1 if old else 1) if new_snapshot else old["content_version"]
        snapshot_path = (_draft_root(p, state) + f"/历史/稿v{version:06d}_{digest[:16]}.md") if new_snapshot else old["snapshot"]
        p.immutable(snapshot_path, content_bytes)
        state["draft"] = {"snapshot": snapshot_path, "sha256": digest, "content_version": version, "projection_path": projection_path, "projection_status": "pending", "previous_projection_sha256": actual_view, "metadata": metadata}
        if preserved:
            state["draft"]["preserved_user_edit"] = preserved
        state["dependencies"] = dependencies
        state["next_step"] = next_step
        state["revision"] += 1
        state["updated_at"] = now_utc()
        # Publishing this pointer first makes a hard crash recoverable without a cache.
        _write_state(p, state)
        p.failpoint("draft_after_state")
        _repair_projection(p, state)
        result = persist_state(p, state)
        status = {"synced": "saved", "pending": "saved_projection_pending", "conflict": "saved_projection_conflict"}[state["draft"]["projection_status"]]
        return {"action": "SaveDraft", "status": status, **_inspection(p, state), **result, "preserved_user_edit": preserved}


def prepare_handoff(p, request):
    key = _key(request)
    evidence = require_str(request.get("user_switch_evidence"), "user_switch_evidence")
    with p.lock("workset:" + key):
        state = load_state(p, key)
        require_session(state, request, allow_pending=True)
        _repair_projection(p, state)
        handoff_id = uuid.uuid4().hex
        state["handoff"] = {"handoff_id": handoff_id, "source_holder": state["lease"]["holder"], "source_lease_id": state["lease"]["id"], "prepared_at": now_utc(), "user_switch_evidence": evidence}
        state["revision"] += 1
        state["updated_at"] = now_utc()
        result = persist_state(p, state)
        return {"action": "PrepareHandoff", "status": "prepared", "handoff_id": handoff_id, **_inspection(p, state), **result}


def _handoff_state(p, key, request):
    state = load_state(p, key)
    _require_session(state, request, allow_pending=True, allow_handoff=True)
    handoff_id = require_str(request.get("handoff_id"), "handoff_id")
    handoff = state["handoff"]
    if not handoff or handoff.get("handoff_id") != handoff_id or handoff.get("source_holder") != state["lease"]["holder"] or handoff.get("source_lease_id") != state["lease"]["id"]:
        raise StudioError("handoff_mismatch", "Prepared handoff does not match this source session")
    return state


def activate_handoff(p, request):
    key = _key(request)
    target = require_str(request.get("target_holder"), "target_holder")
    ttl = _ttl(request)
    with p.lock("workset:" + key):
        state = _handoff_state(p, key, request)
        if target == state["lease"]["holder"]:
            raise StudioError("invalid_input", "Handoff target must be a different holder")
        _repair_projection(p, state)
        handoff_id = state["handoff"]["handoff_id"]
        state["lease"] = _new_lease(target, ttl)
        state["handoff"] = None
        state["revision"] += 1
        state["updated_at"] = now_utc()
        result = persist_state(p, state)
        return {"action": "ActivateHandoff", "status": "activated", "handoff_id": handoff_id, **_inspection(p, state), **result}


def cancel_handoff(p, request):
    key = _key(request)
    with p.lock("workset:" + key):
        state = _handoff_state(p, key, request)
        _repair_projection(p, state)
        handoff_id = state["handoff"]["handoff_id"]
        state["handoff"] = None
        state["revision"] += 1
        state["updated_at"] = now_utc()
        result = persist_state(p, state)
        return {"action": "CancelHandoff", "status": "cancelled", "handoff_id": handoff_id, **_inspection(p, state), **result}
