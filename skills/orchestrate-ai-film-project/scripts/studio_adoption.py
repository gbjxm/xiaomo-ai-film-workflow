"""Atomic publication of complete, owner-supplied studio text bundles.

The catalogue and immutable snapshots are authoritative.  Professional files,
the catalogue Markdown and workset caches are recoverable projections only.
"""
from __future__ import annotations

from copy import deepcopy
import re

from studio_io import (
    ANY, StudioError, canonical_bytes, now_utc, require_hash, require_int,
    require_str, sha_bytes, valid_key,
)
from studio_worksets import load_state, persist_state, require_session


MANIFEST_SCHEMA = "xiaomo.studio-adoption/v1"
INTENT_SCHEMA = "xiaomo.studio-adoption-intent/v1"
JOURNAL_SCHEMA = "xiaomo.studio-adoption-journal/v1"
INDEX_VIEW = ".工作室/采用索引.md"
INDEX_VIEW_STATE = ".工作室/索引投影.json"
TEXT_SUFFIXES = {".md", ".txt", ".json"}
ID_PREFIXES = {"B": "S", "C1": "A", "C2": "V", "D": "CUT", "E": "PUB"}


def _error(code, message, **details):
    raise StudioError(code, message, details or None)


def _capability(p):
    if p.capabilities.get("adoption_transactions") is not True:
        _error("capability_disabled", "采用事务尚未启用。", capability="adoption_transactions")


def _base(transaction_id):
    return f".工作室/事务/{valid_key(transaction_id, 'transaction_id')}"


def _object(catalogue, path):
    folded = path.casefold()
    for stored_path, value in catalogue["objects"].items():
        if stored_path.casefold() == folded:
            return stored_path, value
    return path, None


def _pending_id(state):
    pending = state.get("pending_transaction")
    return pending.get("transaction_id") if isinstance(pending, dict) else pending


def _request(p, request):
    key = valid_key(request.get("workset_key"))
    transaction_id = valid_key(request.get("transaction_id"), "transaction_id")
    evidence = request.get("adoption_evidence")
    if not isinstance(evidence, dict):
        _error("adoption_evidence_required", "需要本次采用决定的文字与来源。")
    evidence = {
        "text": require_str(evidence.get("text"), "adoption_evidence.text"),
        "source": require_str(evidence.get("source"), "adoption_evidence.source"),
    }
    raw_writes = request.get("writes")
    if not isinstance(raw_writes, list) or not raw_writes:
        _error("invalid_writes", "writes 必须是非空完整文本列表。")
    writes, seen = [], set()
    for raw in raw_writes:
        if not isinstance(raw, dict):
            _error("invalid_writes", "每个 writes 元素必须是对象。")
        owner = require_str(raw.get("owner"), "writes.owner")
        path = p.rel(require_str(raw.get("path"), "writes.path"))
        source = p.rel(require_str(raw.get("source"), "writes.source"))
        target_path, source_path = p.path(path), p.path(source)
        if path.casefold() in seen:
            _error("duplicate_target", "事务包含重复或大小写别名目标。", path=path)
        seen.add(path.casefold())
        if target_path.suffix.lower() not in TEXT_SUFFIXES or source_path.suffix.lower() not in TEXT_SUFFIXES:
            _error("text_only", "采用事务只接受 Markdown、文本和 JSON。", path=path, source=source)
        if not p.owner_allowed(owner, path):
            _error("owner_forbidden", "声明的专业所有者不能写入该目标。", owner=owner, path=path)
        if "expected_sha256" not in raw or "expected_version" not in raw:
            _error("target_baseline_required", "目标必须显式声明原 hash 和版本，新目标可为 null。", path=path)
        version = raw["expected_version"]
        if version is not None:
            version = require_int(version, "expected_version", minimum=0)
        item = {
            "owner": owner, "path": path, "source": source,
            "source_sha256": require_hash(raw.get("source_sha256"), "source_sha256").lower(),
            "expected_sha256": require_hash(raw["expected_sha256"], "expected_sha256", allow_none=True),
            "expected_version": version,
        }
        if item["expected_sha256"] is not None:
            item["expected_sha256"] = item["expected_sha256"].lower()
        if raw.get("object_id") is not None:
            object_id = require_str(raw["object_id"], "object_id").upper()
            prefix = ID_PREFIXES.get(owner)
            if not prefix or not re.fullmatch(re.escape(prefix) + r"[0-9]{2,}", object_id):
                _error("object_id_owner", "影视对象 ID 与声明的专业所有者不符。", owner=owner, object_id=object_id)
            item["object_id"] = object_id
        writes.append(item)
    writes.sort(key=lambda item: item["path"].casefold())
    normalized = {
        "workset_key": key, "transaction_id": transaction_id,
        "draft_sha256": require_hash(request.get("draft_sha256"), "draft_sha256").lower(),
        "adoption_evidence": evidence, "writes": writes,
    }
    signature = deepcopy(normalized)
    for item in signature["writes"]:
        item["path"] = item["path"].casefold()
        item["source"] = item["source"].casefold()
    return normalized, sha_bytes(canonical_bytes(signature))


def _read_intent(p, transaction_id):
    intent = p.read_json(_base(transaction_id) + "/intent.json", default=None)
    if intent is not None and (
        not isinstance(intent, dict) or intent.get("schema") != INTENT_SCHEMA
        or not isinstance(intent.get("request"), dict)
        or intent["request"].get("transaction_id") != transaction_id
    ):
        _error("transaction_corrupt", "事务意图记录无效。", transaction_id=transaction_id)
    return intent


def _manifest(p, transaction_id):
    value = p.read_json(_base(transaction_id) + "/manifest.json", default=None)
    if value is not None and (
        not isinstance(value, dict) or value.get("schema") != MANIFEST_SCHEMA
        or value.get("transaction_id") != transaction_id
        or not isinstance(value.get("writes"), list) or not value["writes"]
    ):
        _error("transaction_corrupt", "事务 manifest 无效。", transaction_id=transaction_id)
    return value


def _journal(p, transaction_id):
    value = p.read_json(_base(transaction_id) + "/journal.json", default=None)
    if value is None:
        return {"schema": JOURNAL_SCHEMA, "transaction_id": transaction_id}
    if not isinstance(value, dict) or value.get("schema") != JOURNAL_SCHEMA:
        _error("transaction_corrupt", "事务恢复日志无效。", transaction_id=transaction_id)
    return value


def _write_journal(p, transaction_id, **changes):
    value = _journal(p, transaction_id)
    value.update(changes)
    value["updated_at"] = now_utc()
    p.atomic_json(_base(transaction_id) + "/journal.json", value, expected=ANY)
    return value


def _session(state, request, transaction_id):
    require_session(state, request, allow_pending=True)
    pending = _pending_id(state)
    if pending and pending != transaction_id:
        _error("adoption_pending", "先恢复或取消当前工作集的准备事务。", transaction_id=pending)


def _draft(p, state, draft_sha256):
    draft = state.get("draft")
    if not isinstance(draft, dict) or str(draft.get("sha256", "")).lower() != draft_sha256:
        _error("draft_conflict", "当前完整稿已变化或不存在。", expected_sha256=draft_sha256, draft=draft)
    if p.hash(draft["snapshot"]) != draft_sha256:
        _error("draft_snapshot_changed", "完整稿不可变快照丢失或被修改。", snapshot=draft["snapshot"])
    projection = draft.get("projection_path")
    if projection:
        actual = p.hash(projection)
        known = {None, draft_sha256}
        if draft.get("projection_status") == "pending":
            known.add(draft.get("previous_projection_sha256"))
        if actual not in known:
            _error("draft_projection_conflict", "当前稿含未纳入快照的手工修改，先合并并保存。", path=projection, current_sha256=actual)
    return draft


def _dependencies(p, dependencies, catalogue):
    changes = p.check_dependencies(dependencies, catalogue=catalogue)
    conflicts = [item for item in changes if item.get("status") != "unchanged"]
    if conflicts:
        _error("dependency_conflict", "采用依赖已变化；完整候选保留。", dependency_changes=conflicts)


def _identities(p, writes, catalogue):
    ids = {key.upper(): value for key, value in catalogue.get("ids", {}).items()}
    claimed = {}
    for item in writes:
        path, current = _object(catalogue, item["path"])
        if current and current["owner"] != item["owner"]:
            _error("owner_conflict", "已采用目标属于其他专业。", path=path, current_owner=current["owner"], owner=item["owner"])
        object_id = item.get("object_id")
        existing_id = current.get("object_id") if current else None
        if existing_id and object_id and object_id != existing_id:
            _error("object_id_conflict", "已有目标不能更换影视对象身份。", path=path, object_id=object_id, current_object_id=existing_id)
        if not object_id:
            continue
        occupied = ids.get(object_id) or claimed.get(object_id)
        if occupied and occupied.casefold() != path.casefold():
            _error("object_id_conflict", "影视对象 ID 已绑定其他路径。", object_id=object_id, path=path, occupied_path=occupied)
        claimed[object_id] = path


def _view_identity(p, path):
    try:
        info = p.path(path).stat()
    except FileNotFoundError:
        return None
    except OSError as error:
        _error("projection_unavailable", "无法确认可读视图的文件身份。", path=path, reason=str(error))
    return {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}


def _view_observation(p, path):
    before = _view_identity(p, path)
    physical = p.hash(path)
    after = _view_identity(p, path)
    if before != after or ((physical is None) != (after is None)):
        _error("projection_unavailable", "可读视图正在替换或保存，请重试。", path=path)
    return {"sha256": physical, "identity": after}


def _known_projection(p, catalogue, path, current, observation=None):
    known = {None, current["sha256"]}
    entry = catalogue["transactions"].get(current["transaction_id"])
    manifest = _manifest(p, current["transaction_id"])
    manifest_path = _base(current["transaction_id"]) + "/manifest.json"
    if (not entry or not manifest or manifest.get("request_hash") != entry.get("request_hash")
            or p.hash(manifest_path) != entry.get("manifest_sha256")):
        _error("catalogue_corrupt", "采用对象无法回到已发布的完整 manifest。", path=path)
    item = next((item for item in manifest["writes"] if item["path"].casefold() == path.casefold()
                 and item["sha256"] == current["sha256"]), None)
    if not item:
        _error("catalogue_corrupt", "采用对象与已发布 manifest 不符。", path=path)
    try:
        completed = _journal(p, current["transaction_id"]).get("projection_completed", {})
    except StudioError:
        return known  # Corrupt optional progress cannot authorise an overwrite.
    if not isinstance(completed, dict) or any(key.casefold() == path.casefold() for key in completed):
        return known
    bases = entry.get("projection_bases", {})
    baseline = next((value for key, value in bases.items() if key.casefold() == path.casefold()), None)
    if baseline is None:
        baseline = {"sha256": item.get("projection_base_sha256"), "identity": item.get("projection_base_identity")}
    observation = observation if observation is not None else _view_observation(p, path)
    # A hash alone cannot distinguish an untouched original from a user's
    # later rollback to the same bytes. Identity also closes the crash gap
    # between publishing this view and recording its completion.
    if (isinstance(baseline, dict) and baseline.get("identity") is not None
            and observation["identity"] == baseline["identity"]
            and observation["sha256"] == baseline.get("sha256")):
        known.add(baseline["sha256"])
    return known


def _complete_projection(p, transaction_id, path, sha256):
    journal = _journal(p, transaction_id)
    completed = journal.get("projection_completed", {})
    if not isinstance(completed, dict):
        _error("transaction_corrupt", "投影完成记录无效。", transaction_id=transaction_id)
    existing = next((value for key, value in completed.items() if key.casefold() == path.casefold()), None)
    if existing:
        if existing.get("sha256") != sha256:
            _error("transaction_corrupt", "投影完成记录与采用内容不符。", path=path)
        return
    completed[path] = {"sha256": sha256, "completed_at": now_utc()}
    _write_journal(p, transaction_id, projection_completed=completed)


def _targets(p, writes, catalogue):
    observations = []
    for item in writes:
        path, current = _object(catalogue, item["path"])
        observation = _view_observation(p, path)
        physical = observation["sha256"]
        if current:
            if item["expected_version"] != current["version"] or item["expected_sha256"] != current["sha256"]:
                _error("target_conflict", "采用目标的版本或内容已变化。", path=path, current=deepcopy(current), expected_version=item["expected_version"], expected_sha256=item["expected_sha256"])
            if physical not in _known_projection(p, catalogue, path, current, observation):
                _error("target_projection_conflict", "目标存在未知手改，不能覆盖。", path=path, current_sha256=physical, adopted_sha256=current["sha256"])
        else:
            if item["expected_version"] not in (None, 0) or physical != item["expected_sha256"]:
                _error("target_conflict", "目标文件与已读基线不符。", path=path, current_sha256=physical, expected_sha256=item["expected_sha256"], expected_version=item["expected_version"])
        observations.append({"path": path, "current": deepcopy(current), "physical_sha256": physical,
                             "physical_identity": observation["identity"]})
    return observations


def _equivalent(writes, catalogue):
    for item in writes:
        _, current = _object(catalogue, item["path"])
        if not current or current["owner"] != item["owner"] or current["sha256"] != item["source_sha256"]:
            return False
        if item.get("object_id") and item["object_id"] != current.get("object_id"):
            return False
    return True


def _sources(p, writes):
    result = []
    for item in writes:
        raw = p.read_bytes(item["source"])
        if sha_bytes(raw) != item["source_sha256"]:
            _error("source_conflict", "专业完整文本与声明 hash 不符。", source=item["source"])
        try:
            raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            _error("invalid_text_encoding", "完整文本必须是 UTF-8，原字节不会被转码。", source=item["source"])
        result.append(raw)
    return result


def _ensure_intent(p, normalized, request_hash):
    transaction_id = normalized["transaction_id"]
    existing = _read_intent(p, transaction_id)
    if existing:
        if existing.get("request_hash") != request_hash:
            _error("idempotency_conflict", "相同 transaction_id 不能提交不同内容。", transaction_id=transaction_id)
        return existing
    value = {"schema": INTENT_SCHEMA, "request_hash": request_hash, "request": normalized, "created_at": now_utc()}
    p.immutable(_base(transaction_id) + "/intent.json", canonical_bytes(value))
    return value


def _verify_manifest(p, manifest):
    transaction_id = manifest["transaction_id"]
    intent = _read_intent(p, transaction_id)
    if not intent:
        _error("manifest_conflict", "准备 manifest 缺少原始采用意图。", transaction_id=transaction_id)
    normalized, request_hash = _request(p, intent["request"])
    if request_hash != intent.get("request_hash") or request_hash != manifest.get("request_hash"):
        _error("manifest_conflict", "准备 manifest 与原始内容请求不符。", transaction_id=transaction_id)
    for field in ("workset_key", "draft_sha256", "adoption_evidence"):
        if manifest.get(field) != normalized[field]:
            _error("manifest_conflict", "准备 manifest 的采用范围被修改。", field=field)
    if manifest.get("created_at") != intent["created_at"] or len(manifest["writes"]) != len(normalized["writes"]):
        _error("manifest_conflict", "准备 manifest 的范围或时间记录不符。")
    journal = _journal(p, transaction_id)
    manifest_hash = journal.get("manifest_sha256")
    if manifest_hash and p.hash(_base(transaction_id) + "/manifest.json") != manifest_hash:
        _error("manifest_conflict", "准备 manifest 在写入后发生变化。", transaction_id=transaction_id)
    for number, (item, original) in enumerate(zip(manifest["writes"], normalized["writes"])):
        for field in ("owner", "source_sha256", "expected_sha256", "expected_version"):
            if item.get(field) != original[field]:
                _error("manifest_conflict", "准备对象与采用请求不符。", field=field)
        for field in ("path", "source"):
            if p.rel(item.get(field)).casefold() != original[field].casefold():
                _error("manifest_conflict", "准备对象路径与采用请求不符。", field=field)
        if not p.owner_allowed(item["owner"], item["path"]):
            _error("owner_forbidden", "准备对象不在所有者写入白名单内。", path=item["path"])
        if item.get("sha256") != original["source_sha256"]:
            _error("manifest_conflict", "准备内容不是用户采用的完整文本。", path=item["path"])
        if original.get("object_id") and item.get("object_id") != original["object_id"]:
            _error("manifest_conflict", "准备对象身份与采用请求不符。", path=item["path"])
        if original["expected_version"] in (None, 0) and not original.get("object_id") and item.get("object_id"):
            _error("manifest_conflict", "准备记录不能为新对象补造稳定 ID。", path=item["path"])
        expected_version = (original["expected_version"] or 0) + 1
        suffix = p.path(original["path"]).suffix.lower()
        if item.get("version") != expected_version or item.get("snapshot") != f"{_base(transaction_id)}/内容/{number:04}{suffix}":
            _error("manifest_conflict", "准备对象版本或快照坐标不符。", path=item["path"])
        retained = item.get("original_snapshot")
        if retained and retained != f"{_base(transaction_id)}/原文/{number:04}{suffix}":
            _error("manifest_conflict", "原文保留快照坐标不符。", path=item["path"])


def _snapshot_check(p, manifest):
    for item in manifest["writes"]:
        if p.hash(item["snapshot"]) != item["sha256"]:
            _error("snapshot_conflict", "采用内容快照缺失或被修改。", snapshot=item["snapshot"])
        original = item.get("original_snapshot")
        if original and p.hash(original) != item["original_sha256"]:
            _error("snapshot_conflict", "原目标保留快照缺失或被修改。", snapshot=original)


def _save_state(p, state):
    state["revision"] += 1
    state["updated_at"] = now_utc()
    return persist_state(p, state)


def _prepare(p, state, normalized, request_hash):
    transaction_id = normalized["transaction_id"]
    draft = _draft(p, state, normalized["draft_sha256"])
    raw_sources = _sources(p, normalized["writes"])
    with p.lock("catalogue"):
        catalogue = p.catalogue()
        _dependencies(p, state.get("dependencies", []), catalogue)
        _identities(p, normalized["writes"], catalogue)
        intent = _ensure_intent(p, normalized, request_hash)
        if _equivalent(normalized["writes"], catalogue):
            return _no_change(p, state, normalized, request_hash, catalogue)
        observations = _targets(p, normalized["writes"], catalogue)
    p.failpoint("adoption_after_intent")
    prepared = []
    for number, (item, raw, observed) in enumerate(zip(normalized["writes"], raw_sources, observations)):
        suffix = p.path(item["path"]).suffix.lower()
        snapshot = f"{_base(transaction_id)}/内容/{number:04}{suffix}"
        p.immutable(snapshot, raw)
        p.failpoint("adoption_after_content_snapshot")
        original_snapshot = None
        original_sha256 = None
        base_hash = observed["physical_sha256"]
        if base_hash is not None:
            original_snapshot = f"{_base(transaction_id)}/原文/{number:04}{suffix}"
            previous = p.hash(original_snapshot)
            if previous is not None:
                # A preparation interrupted after retaining the original may be
                # retried after a known older projection caught up. Keep both.
                original_sha256 = previous
            else:
                original = p.read_bytes(observed["path"])
                if sha_bytes(original) != base_hash:
                    _error("target_conflict", "准备期间目标被修改，原文件未覆盖。", path=observed["path"])
                p.immutable(original_snapshot, original)
                original_sha256 = base_hash
            p.failpoint("adoption_after_original_snapshot")
        current = observed["current"]
        prepared_item = dict(item, path=observed["path"], snapshot=snapshot, sha256=sha_bytes(raw),
                             version=(current["version"] + 1 if current else 1),
                             projection_base_sha256=base_hash, projection_base_identity=observed["physical_identity"],
                             original_snapshot=original_snapshot,
                             original_sha256=original_sha256)
        if current and current.get("object_id") and not prepared_item.get("object_id"):
            prepared_item["object_id"] = current["object_id"]
        prepared.append(prepared_item)
    manifest = {
        "schema": MANIFEST_SCHEMA, "transaction_id": transaction_id,
        "request_hash": request_hash, "workset_key": state["workset_key"],
        "created_at": intent["created_at"], "draft_sha256": normalized["draft_sha256"],
        "draft_snapshot": draft["snapshot"], "dependencies": deepcopy(state.get("dependencies", [])),
        "adoption_evidence": normalized["adoption_evidence"], "writes": prepared,
    }
    p.immutable(_base(transaction_id) + "/manifest.json", canonical_bytes(manifest))
    p.failpoint("adoption_after_manifest")
    _write_journal(p, transaction_id, status="prepared", request_hash=request_hash,
                   manifest_sha256=p.hash(_base(transaction_id) + "/manifest.json"))
    p.failpoint("adoption_after_prepare_journal")
    if _pending_id(state) != transaction_id:
        state["pending_transaction"] = {
            "transaction_id": transaction_id, "request_hash": request_hash,
            "manifest_path": _base(transaction_id) + "/manifest.json", "status": "prepared",
        }
        _save_state(p, state)
    p.failpoint("adoption_after_pending_state")
    p.failpoint("after_prepare")
    return manifest


def _no_change(p, state, normalized, request_hash, catalogue):
    # Called while holding the catalogue lock. This is an idempotency receipt,
    # not a new adopted transaction or new object version.
    transaction_id = normalized["transaction_id"]
    objects = []
    for item in normalized["writes"]:
        path, current = _object(catalogue, item["path"])
        _published_manifest(p, current["transaction_id"], catalogue)
        objects.append(dict(deepcopy(current), path=path))
    _write_journal(p, transaction_id, status="no_change", request_hash=request_hash,
                   adopted_objects=objects, catalogue_revision=catalogue["revision"])
    state["pending_transaction"] = None
    state["last_adoption"] = {
        "transaction_id": transaction_id, "status": "no_change", "request_hash": request_hash,
        "adopted_transaction_ids": sorted({item["transaction_id"] for item in objects}),
    }
    _save_state(p, state)
    return {"status": "no_change", "transaction_id": transaction_id,
            "catalogue_revision": catalogue["revision"],
            "objects": [_resolved_object(p, obj["path"], obj, catalogue) for obj in objects],
            "workset_revision": state["revision"], "no_change": True}


def _published_manifest(p, transaction_id, catalogue):
    entry = catalogue["transactions"].get(transaction_id)
    if not entry:
        return None
    manifest = _manifest(p, transaction_id)
    if (not manifest or manifest.get("request_hash") != entry.get("request_hash")
            or manifest.get("workset_key") != entry.get("workset_key")
            or p.hash(_base(transaction_id) + "/manifest.json") != entry.get("manifest_sha256")):
        _error("catalogue_corrupt", "已发布事务的 manifest 不匹配。", transaction_id=transaction_id)
    _snapshot_check(p, manifest)
    return manifest


def _unavailable_projection(path, error):
    return {"status": "unavailable", "path": path, "current_sha256": None,
            "reason": {"code": getattr(error, "code", "io_error"), "message": str(error)}}


def _projection(p, item, catalogue, current=True):
    # Only this non-authoritative view probe can degrade. The catalogue,
    # manifest and immutable content have already been verified separately.
    try:
        observation = _view_observation(p, item["path"])
        physical = observation["sha256"]
    except (StudioError, OSError) as error:
        result = _unavailable_projection(item["path"], error)
        result["expected_sha256"] = item["sha256"]
        if not current:
            result["status"] = "superseded"
        return result
    if not current:
        return {"status": "superseded", "current_sha256": physical}
    if physical is None:
        return {"status": "pending", "current_sha256": None, "expected_sha256": item["sha256"],
                "reason": {"code": "projection_missing", "message": "可读视图暂缺；采用快照仍可读取。"}}
    if physical == item["sha256"]:
        return {"status": "synced", "current_sha256": physical}
    path, obj = _object(catalogue, item["path"])
    known = _known_projection(p, catalogue, path, obj, observation)
    return {"status": "pending" if physical in known else "conflict",
            "current_sha256": physical, "expected_sha256": item["sha256"]}


def _resolved_object(p, path, obj, catalogue, *, historical=False):
    manifest = _published_manifest(p, obj["transaction_id"], catalogue)
    proof = next((item for item in manifest["writes"] if item["path"].casefold() == path.casefold()), None) if manifest else None
    if not proof or any(proof.get(field) != obj.get(field) for field in ("owner", "version", "sha256", "snapshot", "object_id")):
        _error("catalogue_corrupt", "对象索引与已发布 manifest 的完整对象不符。", path=path)
    if p.hash(obj["snapshot"]) != obj["sha256"]:
        _error("snapshot_conflict", "采用快照缺失或被修改，不能返回 adopted。", path=path, snapshot=obj["snapshot"])
    _, latest = _object(catalogue, path)
    current = bool(latest and latest["transaction_id"] == obj["transaction_id"]
                   and latest["version"] == obj["version"] and latest["sha256"] == obj["sha256"])
    result = dict(deepcopy(obj), path=path, adopted=True, current=current)
    result["projection"] = _projection(p, result, catalogue, current=current)
    if historical or not current:
        result["historical"] = not current
    return result


def _result(p, manifest, catalogue):
    transaction_id = manifest["transaction_id"]
    objects = []
    for item in manifest["writes"]:
        obj = {key: item[key] for key in ("owner", "version", "sha256", "snapshot")}
        obj["transaction_id"] = transaction_id
        if item.get("object_id"):
            obj["object_id"] = item["object_id"]
        objects.append(_resolved_object(p, item["path"], obj, catalogue, historical=True))
    pending = [item for item in objects if item["projection"]["status"] in ("pending", "conflict", "unavailable")]
    index_projection = _index_status(p, catalogue)
    index_pending = index_projection["status"] != "synced"
    historical = any(not item["current"] for item in objects)
    status = "committed_sync_pending" if pending or index_pending else "committed"
    if objects and all(not item["current"] for item in objects):
        status = "superseded"
    return {"status": status, "transaction_id": transaction_id, "adopted": True,
            "historical": historical, "catalogue_revision": catalogue["revision"],
            "manifest_path": _base(transaction_id) + "/manifest.json", "objects": objects,
            "projection_conflicts": [item for item in pending if item["projection"]["status"] == "conflict"],
            "catalogue_projection": index_projection}


def _index_status(p, catalogue):
    desired = sha_bytes(_render_catalogue(catalogue))
    try:
        observation = _view_observation(p, INDEX_VIEW)
        physical = observation["sha256"]
    except (StudioError, OSError) as error:
        result = _unavailable_projection(INDEX_VIEW, error)
        result.update(expected_sha256=desired, revision=catalogue["revision"])
        return result
    if physical is None:
        return {"status": "pending", "path": INDEX_VIEW, "current_sha256": None,
                "expected_sha256": desired, "revision": catalogue["revision"],
                "reason": {"code": "projection_missing", "message": "采用索引的 Markdown 视图暂缺。"}}
    if physical == desired:
        return {"status": "synced", "path": INDEX_VIEW, "sha256": desired,
                "revision": catalogue["revision"]}
    try:
        previous = p.read_json(INDEX_VIEW_STATE, default={})
        known = _index_known_hashes(previous, observation, desired)
        status = "pending" if physical in known else "conflict"
    except StudioError:
        status = "conflict" if physical is not None else "pending"
    return {"status": status, "path": INDEX_VIEW, "current_sha256": physical,
            "expected_sha256": desired, "revision": catalogue["revision"]}


def _render_catalogue(catalogue):
    rows = ["# 工作室采用索引", "", f"采用索引 revision: {catalogue['revision']}", "",
            "本页仅为可重建视图；采用事实请使用 Resolve 返回的不可变快照。", ""]
    for path, obj in sorted(catalogue["objects"].items(), key=lambda pair: pair[0].casefold()):
        rows.append(f"- {obj['owner']} | {obj.get('object_id', '')} | {path} | v{obj['version']} | {obj['sha256']} | {obj['snapshot']}")
    return ("\n".join(rows) + "\n").encode("utf-8")


def _index_known_hashes(previous, observation, desired):
    known = {None, desired, previous.get("sha256")}
    if (previous.get("status") == "pending" and previous.get("previous_identity") is not None
            and observation["identity"] == previous["previous_identity"]
            and observation["sha256"] == previous.get("previous_sha256")):
        known.add(previous["previous_sha256"])
    return known


def _sync_index(p):
    with p.lock("catalogue"):
        catalogue = p.catalogue()
        content = _render_catalogue(catalogue)
        desired = sha_bytes(content)
        observation = _view_observation(p, INDEX_VIEW)
        physical = observation["sha256"]
        previous = p.read_json(INDEX_VIEW_STATE, default={})
        known = _index_known_hashes(previous, observation, desired)
        if physical not in known:
            return {"status": "conflict", "path": INDEX_VIEW, "current_sha256": physical,
                    "expected_sha256": desired}
        meta = {"schema": "xiaomo.studio-catalogue-projection/v1", "revision": catalogue["revision"],
                "sha256": desired, "previous_sha256": physical,
                "previous_identity": observation["identity"], "status": "pending"}
        p.atomic_json(INDEX_VIEW_STATE, meta, expected=ANY)
        p.failpoint("adoption_after_index_projection_state")
        if physical != desired:
            p.atomic_bytes(INDEX_VIEW, content, expected=physical)
        p.failpoint("adoption_after_index_projection")
        meta["status"] = "synced"
        p.atomic_json(INDEX_VIEW_STATE, meta, expected=ANY)
        return {"status": "synced", "path": INDEX_VIEW, "sha256": desired, "revision": catalogue["revision"]}


def _finish_historical(p, state, result, errors=()):
    """Caller holds workset then catalogue locks through this state change."""
    transaction_id = result["transaction_id"]
    if errors:
        result["historical_projection_errors"] = list(errors)
    if _pending_id(state) == transaction_id:
        state["pending_transaction"] = None
        if (state.get("last_adoption") or {}).get("transaction_id") == transaction_id:
            state["last_adoption"]["status"] = "superseded"
        _save_state(p, state)
    result["workset_revision"] = state["revision"]
    return result


def _sync(p, state, manifest):
    transaction_id = manifest["transaction_id"]
    with p.lock("catalogue"):
        initial = p.catalogue()
        if all((_object(initial, item["path"])[1] or {}).get("transaction_id") != transaction_id for item in manifest["writes"]):
            # A wholly historical transaction owns no current projection. Its
            # diagnostic must not repair the shared view or block newer work.
            return _finish_historical(p, state, _result(p, manifest, initial))
    errors = []
    for number, item in enumerate(manifest["writes"]):
        try:
            with p.lock("catalogue"):
                catalogue = p.catalogue()
                path, current = _object(catalogue, item["path"])
                if not current or current["transaction_id"] != transaction_id:
                    continue  # Never write a superseded transaction's view.
                observation = _view_observation(p, path)
                physical = observation["sha256"]
                if physical == item["sha256"]:
                    pass
                elif physical not in _known_projection(p, catalogue, path, current, observation):
                    errors.append({"path": path, "status": "conflict", "current_sha256": physical})
                    continue
                else:
                    raw = p.read_bytes(item["snapshot"])
                    if sha_bytes(raw) != item["sha256"]:
                        _error("snapshot_conflict", "内容快照被修改，不能同步。", snapshot=item["snapshot"])
                    p.atomic_bytes(path, raw, expected=physical)
                    p.failpoint("adoption_after_projection_write")
                _complete_projection(p, transaction_id, path, item["sha256"])
            if number == 0:
                p.failpoint("after_first_projection")
            p.failpoint("adoption_after_projection")
        except StudioError as error:
            errors.append({"path": item["path"], "status": "error", "code": getattr(error, "code", "io_error"), "message": str(error)})
        except OSError as error:
            errors.append({"path": item["path"], "status": "error", "message": str(error)})
    try:
        index_projection = _sync_index(p)
    except (StudioError, OSError) as error:
        index_projection = {"status": "error", "path": INDEX_VIEW, "message": str(error)}
    # A different workset can publish between individual projections and the
    # shared-view update. Classify against one final catalogue and keep that
    # publication lock through journal/state persistence, closing the TOCTOU.
    with p.lock("catalogue"):
        catalogue = p.catalogue()
        result = _result(p, manifest, catalogue)
        if result["status"] == "superseded":
            return _finish_historical(p, state, result, errors)
        current_paths = {item["path"].casefold() for item in result["objects"] if item["current"]}
        active_errors = [error for error in errors if error["path"].casefold() in current_paths]
        historical_errors = [error for error in errors if error["path"].casefold() not in current_paths]
        if active_errors:
            result["status"] = "committed_sync_pending"
            result["projection_errors"] = active_errors
        if historical_errors:
            result["historical_projection_errors"] = historical_errors
        if index_projection.get("status") != result["catalogue_projection"].get("status"):
            result["catalogue_projection_attempt"] = index_projection
        _write_journal(p, transaction_id, status=result["status"], projection_errors=active_errors,
                       historical_projection_errors=historical_errors,
                       catalogue_projection=result["catalogue_projection"])
        receipt = {
            "transaction_id": transaction_id, "request_hash": manifest["request_hash"],
            "manifest_path": result["manifest_path"], "status": result["status"],
        }
        previous = state.get("last_adoption") or {}
        previous_ids = [previous.get("transaction_id")] + previous.get("adopted_transaction_ids", [])
        previous_order = max((catalogue["transactions"].get(key, {}).get("catalogue_revision", 0) for key in previous_ids), default=0)
        current_order = catalogue["transactions"][transaction_id].get("catalogue_revision", 0)
        if previous_order <= current_order:
            state["last_adoption"] = receipt
        state["pending_transaction"] = deepcopy(receipt) if result["status"] == "committed_sync_pending" else None
        _save_state(p, state)
        p.failpoint("adoption_after_finalize_state")
        result["workset_revision"] = state["revision"]
        return result


def _execute(p, state, manifest):
    transaction_id = manifest["transaction_id"]
    _verify_manifest(p, manifest)
    _snapshot_check(p, manifest)
    with p.lock("catalogue"):
        catalogue = p.catalogue()
        if transaction_id not in catalogue["transactions"]:
            _draft(p, state, manifest["draft_sha256"])
            if canonical_bytes(state.get("dependencies", [])) != canonical_bytes(manifest["dependencies"]):
                _error("dependency_conflict", "工作集依赖定义已变化，不能提交旧准备事务。")
            _dependencies(p, manifest["dependencies"], catalogue)
            _identities(p, manifest["writes"], catalogue)
            observations = _targets(p, manifest["writes"], catalogue)
            new_catalogue = deepcopy(catalogue)
            new_catalogue.setdefault("ids", {})
            for item in manifest["writes"]:
                path, current = _object(catalogue, item["path"])
                expected_version = current["version"] + 1 if current else 1
                if item["version"] != expected_version:
                    _error("target_conflict", "准备版本与当前目标不连续。", path=path)
                obj = {key: item[key] for key in ("owner", "version", "sha256", "snapshot")}
                obj["transaction_id"] = transaction_id
                if item.get("object_id"):
                    obj["object_id"] = item["object_id"]
                    new_catalogue["ids"][item["object_id"]] = path
                new_catalogue["objects"][path] = obj
            new_catalogue["transactions"][transaction_id] = {
                "request_hash": manifest["request_hash"], "manifest_path": _base(transaction_id) + "/manifest.json",
                "manifest_sha256": p.hash(_base(transaction_id) + "/manifest.json"),
                "workset_key": manifest["workset_key"], "committed_at": now_utc(),
                "catalogue_revision": catalogue["revision"] + 1,
                "projection_bases": {observed["path"]: {"sha256": observed["physical_sha256"],
                                      "identity": observed["physical_identity"]} for observed in observations},
            }
            new_catalogue["revision"] = catalogue["revision"] + 1
            p.publish_catalogue(new_catalogue, expected_revision=catalogue["revision"])
            p.failpoint("after_commit")
        else:
            _published_manifest(p, transaction_id, catalogue)
    return _sync(p, state, manifest)


def _cached_result(p, transaction_id, catalogue):
    manifest = _published_manifest(p, transaction_id, catalogue)
    if manifest:
        result = _result(p, manifest, catalogue)
        result["idempotent"] = True
        return result
    journal = _journal(p, transaction_id)
    if journal.get("status") == "cancelled":
        _error("transaction_cancelled", "该事务已取消，不能复用其 ID。", transaction_id=transaction_id)
    if journal.get("status") == "no_change":
        objects = []
        intent = _read_intent(p, transaction_id)
        requested = {item["path"].casefold(): item for item in intent["request"]["writes"]} if intent else {}
        recorded_objects = journal.get("adopted_objects", [])
        if len(recorded_objects) != len(requested) or not requested:
            _error("transaction_corrupt", "无变化回执与采用请求的范围不符。")
        for recorded in recorded_objects:
            requested_item = requested.get(recorded["path"].casefold())
            if not requested_item or requested_item["source_sha256"] != recorded["sha256"] or requested_item["owner"] != recorded["owner"]:
                _error("transaction_corrupt", "无变化回执不是该请求已有的采用内容。")
            original = _published_manifest(p, recorded["transaction_id"], catalogue)
            if not original:
                _error("catalogue_corrupt", "无变化回执指向未发布的事务。")
            objects.append(_resolved_object(p, recorded["path"], recorded, catalogue, historical=True))
        return {"status": "no_change", "transaction_id": transaction_id, "no_change": True,
                "idempotent": True, "historical": any(not obj["current"] for obj in objects),
                "catalogue_revision": catalogue["revision"], "objects": objects}
    return None


def adopt(p, request):
    _capability(p)
    normalized, request_hash = _request(p, request)
    key, transaction_id = normalized["workset_key"], normalized["transaction_id"]
    with p.lock(f"workset:{key}"):
        state = load_state(p, key)
        intent = _read_intent(p, transaction_id)
        if intent and intent.get("request_hash") != request_hash:
            _error("idempotency_conflict", "相同 transaction_id 不能提交不同内容。", transaction_id=transaction_id)
        if intent:
            cached = _cached_result(p, transaction_id, p.catalogue())
            if cached:
                return cached  # Read-only replay does not renew a stale lease.
        _session(state, request, transaction_id)
        manifest = _manifest(p, transaction_id)
        try:
            if manifest is None:
                manifest = _prepare(p, state, normalized, request_hash)
                if manifest.get("no_change"):
                    return manifest
            return _execute(p, state, manifest)
        except StudioError as error:
            if _read_intent(p, transaction_id) and transaction_id not in p.catalogue()["transactions"]:
                _write_journal(p, transaction_id, status="prepared_conflict", code=getattr(error, "code", "error"), message=str(error), details=getattr(error, "details", None))
            raise


def resume_sync(p, request):
    _capability(p)
    key = valid_key(request.get("workset_key"))
    transaction_id = valid_key(request.get("transaction_id"), "transaction_id")
    with p.lock(f"workset:{key}"):
        state = load_state(p, key)
        _session(state, request, transaction_id)
        intent = _read_intent(p, transaction_id)
        if not intent:
            _error("transaction_not_found", "找不到可恢复的准备事务。", transaction_id=transaction_id)
        if intent["request"]["workset_key"] != key:
            _error("transaction_workset_conflict", "事务不属于该工作集。")
        catalogue = p.catalogue()
        journal = _journal(p, transaction_id)
        if journal.get("status") == "cancelled":
            _error("transaction_cancelled", "取消事务不能恢复提交。", transaction_id=transaction_id)
        if journal.get("status") == "no_change":
            return _cached_result(p, transaction_id, catalogue)
        manifest = _manifest(p, transaction_id)
        if manifest is None:
            manifest = _prepare(p, state, intent["request"], intent["request_hash"])
            if manifest.get("no_change"):
                return manifest
        return _execute(p, state, manifest)


def cancel_adoption(p, request):
    _capability(p)
    key = valid_key(request.get("workset_key"))
    transaction_id = valid_key(request.get("transaction_id"), "transaction_id")
    with p.lock(f"workset:{key}"):
        state = load_state(p, key)
        _session(state, request, transaction_id)
        intent = _read_intent(p, transaction_id)
        if not intent or intent["request"]["workset_key"] != key:
            _error("transaction_not_found", "该工作集没有此准备事务。", transaction_id=transaction_id)
        with p.lock("catalogue"):
            if transaction_id in p.catalogue()["transactions"]:
                _error("already_committed", "已采用事务不能取消；需要新的专业候选和采用。", transaction_id=transaction_id)
            if _journal(p, transaction_id).get("status") == "no_change":
                _error("transaction_finalized", "无变化回执已完成，不能取消。", transaction_id=transaction_id)
            _write_journal(p, transaction_id, status="cancelled", cancelled_at=now_utc(), request_hash=intent["request_hash"])
        p.failpoint("adoption_after_cancel_journal")
        if _pending_id(state) == transaction_id:
            state["pending_transaction"] = None
            _save_state(p, state)
        return {"status": "cancelled", "transaction_id": transaction_id, "adopted": False,
                "snapshots_retained": True, "workset_revision": state["revision"]}


def resolve(p, request):
    _capability(p)
    catalogue = p.catalogue()  # One atomic read for the entire returned bundle.
    selectors = [name for name in ("paths", "transaction_id", "all") if name in request]
    if len(selectors) > 1:
        _error("invalid_resolve", "paths、transaction_id 与 all 只能选择一种。")
    if "all" in request and not isinstance(request["all"], bool):
        _error("invalid_resolve", "all 必须是布尔值；全量读取需显式 all=true。")
    if not selectors or (selectors == ["all"] and request["all"] is False):
        return {"status": "overview", "catalogue_revision": catalogue["revision"],
                "available_count": len(catalogue["objects"]), "objects": []}
    if "transaction_id" in request:
        transaction_id = valid_key(request["transaction_id"], "transaction_id")
        manifest = _published_manifest(p, transaction_id, catalogue)
        if not manifest:
            if _journal(p, transaction_id).get("status") == "no_change":
                return _cached_result(p, transaction_id, catalogue)
            return {"status": "not_committed", "transaction_id": transaction_id,
                    "adopted": False, "objects": [], "catalogue_revision": catalogue["revision"]}
        return _result(p, manifest, catalogue)
    paths = request.get("paths", list(catalogue["objects"]))
    if not isinstance(paths, list):
        _error("invalid_resolve", "paths 必须是项目内相对路径数组。")
    objects, seen = [], set()
    for raw in paths:
        path = p.rel(require_str(raw, "paths"))
        p.path(path)
        if path.casefold() in seen:
            _error("duplicate_target", "Resolve 中存在重复或大小写别名。", path=path)
        seen.add(path.casefold())
        path, obj = _object(catalogue, path)
        if not obj:
            objects.append({"path": path, "adopted": False, "status": "not_adopted"})
            continue
        _published_manifest(p, obj["transaction_id"], catalogue)
        objects.append(_resolved_object(p, path, obj, catalogue))
    return {"status": "resolved", "catalogue_revision": catalogue["revision"], "objects": objects}
