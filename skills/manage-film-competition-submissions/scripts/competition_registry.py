#!/usr/bin/env python3
"""Initialize and validate the local film-competition submission registry.

This utility is deliberately local-only. It does not use the network and cannot
register, pay, email, upload, or submit anything.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit


SKILL_ROOT = Path(__file__).resolve().parent.parent
ASSETS_ROOT = SKILL_ROOT / "assets"
EXPECTED_LEAF = "投稿管理"
DATABASE_NAME = "比赛数据库.csv"

FILE_MAP = {
    "参赛者档案模板.md": Path("参赛者档案.md"),
    "作品档案模板.md": Path("作品档案.md"),
    "比赛数据库模板.csv": Path(DATABASE_NAME),
    "投稿策略模板.md": Path("投稿策略.md"),
    "行动看板模板.md": Path("行动看板.md"),
    "投稿历史模板.md": Path("投稿历史.md"),
    "变更日志模板.md": Path("变更日志.md"),
    "主办方问询模板.md": Path("主办方问询.md"),
    "比赛档案模板.md": Path("模板") / "比赛档案模板.md",
}

FIELDNAMES = [
    "competition_id",
    "year",
    "name_zh",
    "name_en",
    "organizer",
    "status",
    "verification_state",
    "time_window",
    "registration_deadline",
    "submission_deadline",
    "deadline_timezone",
    "beijing_deadline",
    "official_url",
    "submission_url",
    "eligible_projects",
    "eligibility_gate",
    "ai_policy",
    "premiere_risk",
    "rights_risk",
    "fee_original",
    "fee_cny_estimate",
    "recommendation",
    "risk",
    "confidence",
    "last_verified",
]

STATUSES = {
    "候选",
    "推荐",
    "准备中",
    "已提交",
    "入围",
    "获奖",
    "未入围",
    "已截止",
    "已放弃",
    "已归档",
}
VERIFICATION_STATES = {"已核实", "待核实"}
TIME_WINDOWS = {"0-60天", "61-180天", "年度关注"}
RECOMMENDATIONS = {"S", "A", "B", "C", "暂缓", "不建议"}
RISKS = {"低风险", "中等风险", "高风险", "不建议参加"}
CONFIDENCES = {"高", "中", "低"}
ID_PATTERN = re.compile(r"^CMP-(?P<year>\d{4})-(?P<number>\d{4})$")
ISO_DATE_FIELDS = {
    "registration_deadline",
    "submission_deadline",
    "beijing_deadline",
    "last_verified",
}


class RegistryError(RuntimeError):
    pass


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def safe_root(raw: str) -> Path:
    root = Path(raw).expanduser().resolve()
    if root.name != EXPECTED_LEAF:
        raise RegistryError(f"目标目录末级名称必须是“{EXPECTED_LEAF}”：{root}")
    if root.parent == root:
        raise RegistryError("拒绝把文件系统根目录作为投稿管理目录")
    return root


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def initialize(root: Path, dry_run: bool) -> dict[str, object]:
    if root.exists() and not root.is_dir():
        raise RegistryError(f"目标存在但不是目录：{root}")

    required_directories = [root, root / "比赛档案", root / "模板"]
    would_create: list[str] = []
    created: list[str] = []
    skipped: list[str] = []

    for directory in required_directories:
        if directory.exists():
            skipped.append(str(directory))
        elif dry_run:
            would_create.append(str(directory))
        else:
            directory.mkdir(parents=True, exist_ok=False)
            created.append(str(directory))

    for source_name, relative_target in FILE_MAP.items():
        source = ASSETS_ROOT / source_name
        target = root / relative_target
        if not source.is_file():
            raise RegistryError(f"Skill 模板缺失：{source}")
        if target.exists():
            skipped.append(str(target))
        elif dry_run:
            would_create.append(str(target))
        else:
            atomic_copy(source, target)
            created.append(str(target))

    return {
        "success": True,
        "mode": "dry-run" if dry_run else "init",
        "root": str(root),
        "created": created,
        "would_create": would_create,
        "skipped": skipped,
        "overwritten": [],
    }


def load_database(root: Path) -> tuple[list[str], list[dict[str, str]]]:
    database = root / DATABASE_NAME
    if not database.is_file():
        raise RegistryError(f"比赛数据库不存在：{database}")
    with database.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        rows = [dict(row) for row in reader if any((value or "").strip() for value in row.values())]
    return headers, rows


def valid_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))
    except ValueError:
        return False


def valid_url(value: str) -> bool:
    if not value:
        return True
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def canonical_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+", "/", parsed.path).rstrip("/").lower()
    return f"{host}{path}"


def validate_registry(root: Path) -> dict[str, object]:
    errors: list[str] = []
    warnings: list[str] = []

    if not root.is_dir():
        return {
            "success": False,
            "root": str(root),
            "rows": 0,
            "errors": [f"投稿管理目录不存在：{root}"],
            "warnings": [],
        }

    for relative_target in FILE_MAP.values():
        target = root / relative_target
        if not target.is_file():
            errors.append(f"缺少管理文件：{relative_target.as_posix()}")
    for directory_name in ("比赛档案", "模板"):
        if not (root / directory_name).is_dir():
            errors.append(f"缺少目录：{directory_name}")

    try:
        headers, rows = load_database(root)
    except RegistryError as exc:
        errors.append(str(exc))
        headers, rows = [], []

    if headers and headers != FIELDNAMES:
        errors.append("比赛数据库字段或顺序不符合数据契约")

    seen_ids: set[str] = set()
    seen_urls: dict[str, str] = {}
    seen_identity: dict[tuple[str, str, str], str] = {}

    for index, row in enumerate(rows, start=2):
        label = f"CSV 第 {index} 行"
        competition_id = (row.get("competition_id") or "").strip()
        year = (row.get("year") or "").strip()
        match = ID_PATTERN.fullmatch(competition_id)
        if not match:
            errors.append(f"{label} competition_id 无效：{competition_id or '[空]'}")
        elif match.group("year") != year:
            errors.append(f"{label} ID 年份与 year 不一致")
        if competition_id in seen_ids:
            errors.append(f"{label} competition_id 重复：{competition_id}")
        seen_ids.add(competition_id)

        enum_fields = {
            "status": STATUSES,
            "verification_state": VERIFICATION_STATES,
            "time_window": TIME_WINDOWS,
            "recommendation": RECOMMENDATIONS,
            "risk": RISKS,
            "confidence": CONFIDENCES,
        }
        for field, allowed in enum_fields.items():
            value = (row.get(field) or "").strip()
            if value and value not in allowed:
                errors.append(f"{label} {field} 取值无效：{value}")

        required_fields = ("year", "status", "verification_state", "time_window")
        for field in required_fields:
            if not (row.get(field) or "").strip():
                errors.append(f"{label} 缺少必填字段：{field}")

        for field in ISO_DATE_FIELDS:
            value = (row.get(field) or "").strip()
            if value and not valid_iso_date(value):
                errors.append(f"{label} {field} 不是 YYYY-MM-DD：{value}")
        if not (row.get("last_verified") or "").strip():
            errors.append(f"{label} 缺少 last_verified")

        for field in ("official_url", "submission_url"):
            value = (row.get(field) or "").strip()
            if value and not valid_url(value):
                errors.append(f"{label} {field} 不是有效 HTTP(S) URL：{value}")

        official_url = (row.get("official_url") or "").strip()
        if official_url:
            key = canonical_url(official_url)
            if key in seen_urls:
                errors.append(
                    f"{label} 官方 URL 与 {seen_urls[key]} 重复：{official_url}"
                )
            else:
                seen_urls[key] = competition_id or label
        else:
            warnings.append(f"{label} 尚无官方 URL，只能保持候选")

        name = ((row.get("name_zh") or row.get("name_en") or "").strip().casefold())
        organizer = (row.get("organizer") or "").strip().casefold()
        if not name:
            errors.append(f"{label} 缺少中文或英文比赛名称")
        identity = (year, name, organizer)
        if all(identity):
            if identity in seen_identity:
                errors.append(
                    f"{label} 与 {seen_identity[identity]} 的年份、名称和主办方重复"
                )
            else:
                seen_identity[identity] = competition_id or label

        status = (row.get("status") or "").strip()
        verification = (row.get("verification_state") or "").strip()
        if status in {"推荐", "准备中"}:
            if verification != "已核实":
                errors.append(f"{label} {status} 必须已核实")
            if not official_url:
                errors.append(f"{label} {status} 必须提供官方 URL")

        recommendation = (row.get("recommendation") or "").strip()
        risk = (row.get("risk") or "").strip()
        if risk == "高风险" and recommendation in {"S", "A"}:
            errors.append(f"{label} 高风险推荐等级不得高于 B")
        if verification == "待核实" and recommendation in {"S", "A", "B", "C"}:
            errors.append(f"{label} 待核实候选不得使用正式 S/A/B/C 等级")

    return {
        "success": not errors,
        "root": str(root),
        "rows": len(rows),
        "errors": errors,
        "warnings": warnings,
        "status_values": sorted(STATUSES),
    }


def next_id(root: Path, year: int) -> dict[str, object]:
    headers, rows = load_database(root)
    if headers != FIELDNAMES:
        raise RegistryError("比赛数据库字段或顺序不符合数据契约")
    maximum = 0
    for row in rows:
        competition_id = (row.get("competition_id") or "").strip()
        match = ID_PATTERN.fullmatch(competition_id)
        if match and int(match.group("year")) == year:
            maximum = max(maximum, int(match.group("number")))
    if maximum >= 9999:
        raise RegistryError(f"{year} 年的比赛 ID 已耗尽")
    return {
        "success": True,
        "root": str(root),
        "year": year,
        "next_id": f"CMP-{year:04d}-{maximum + 1:04d}",
    }


def snapshot(root: Path) -> dict[str, object]:
    if not root.exists():
        return {"success": True, "root": str(root), "exists": False, "files": {}}
    if not root.is_dir():
        raise RegistryError(f"目标存在但不是目录：{root}")
    files: dict[str, str] = {}
    for path in sorted((item for item in root.rglob("*") if item.is_file())):
        relative = path.relative_to(root).as_posix()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        files[relative] = digest.hexdigest().upper()
    return {
        "success": True,
        "root": str(root),
        "exists": True,
        "files": files,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="initialize missing registry files")
    init_parser.add_argument("--root", required=True)
    init_parser.add_argument("--dry-run", action="store_true")

    for name in ("validate", "snapshot"):
        command_parser = subparsers.add_parser(name)
        command_parser.add_argument("--root", required=True)

    id_parser = subparsers.add_parser("next-id", help="print the next stable ID")
    id_parser.add_argument("--root", required=True)
    id_parser.add_argument("--year", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = safe_root(args.root)
        if args.command == "init":
            payload = initialize(root, args.dry_run)
        elif args.command == "validate":
            payload = validate_registry(root)
        elif args.command == "next-id":
            if not 2000 <= args.year <= 2999:
                raise RegistryError("year 必须位于 2000–2999")
            payload = next_id(root, args.year)
        elif args.command == "snapshot":
            payload = snapshot(root)
        else:  # pragma: no cover
            raise RegistryError(f"未知命令：{args.command}")
        emit(payload)
        return 0 if payload.get("success") else 1
    except (OSError, RegistryError, csv.Error) as exc:
        emit({"success": False, "error": str(exc)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
