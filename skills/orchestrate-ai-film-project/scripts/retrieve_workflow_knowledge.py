#!/usr/bin/env python3
"""Call the optional knowledge bridge through the workflow's local config."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def config_candidates(explicit: str | None) -> list[Path]:
    if explicit:
        return [Path(explicit)]
    rows: list[Path] = []
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        rows.append(Path(codex_home) / "xiaomo-ai-film-workflow.json")
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        rows.append(Path(user_profile) / ".agents" / "xiaomo-ai-film-workflow.json")
    return rows


def load_workflow_config(explicit: str | None) -> tuple[Path, dict]:
    found = [path.resolve() for path in config_candidates(explicit) if path.is_file()]
    if len(found) != 1:
        raise RuntimeError("无法唯一定位小陌 AI 影视工作流本机配置")
    path = found[0]
    return path, json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-config")
    parser.add_argument("--query", required=True)
    parser.add_argument("--role", required=True, choices=("A", "B", "C", "D", "E"))
    parser.add_argument("--stage", required=True)
    parser.add_argument("--task-type", default="")
    parser.add_argument("--object", default="")
    parser.add_argument("--constraints", default="")
    parser.add_argument("--expected-output", default="")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--review", action="store_true", help="按必查项提供来源证据包")
    parser.add_argument("--question", action="append", default=[])
    parser.add_argument("--material", default="")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()
    try:
        workflow_config_path, config = load_workflow_config(args.workflow_config)
        bridge = config.get("knowledgeBridge") or {}
        if bridge.get("enabled") is not True:
            raise RuntimeError("知识桥未启用；请重新运行工作流安装器并显式提供知识树配置")
        knowledge_config = Path(str(bridge.get("configPath", ""))).resolve(strict=True)
        skills_root = Path(str(config.get("skillsRoot", ""))).resolve(strict=True)
        skill_name = str(bridge.get("skillName") or "apply-film-knowledge")
        script_name = "knowledge_review.py" if args.review else "retrieve_knowledge.py"
        retriever = skills_root / skill_name / "scripts" / script_name
        if not retriever.is_file():
            raise RuntimeError(f"知识调用 Skill 不存在：{retriever}")
        command = [
            sys.executable, "-B", "-X", "utf8", str(retriever),
            "--config", str(knowledge_config), "--query", args.query,
            "--role", args.role, "--stage", args.stage,
        ]
        if args.review:
            for question in args.question:
                command.extend(("--question", question))
            if args.material:
                command.extend(("--material", args.material))
        else:
            command.extend(("--limit", str(args.limit)))
        for flag, value in (
            ("--task-type", args.task_type), ("--object", args.object),
            ("--constraints", args.constraints), ("--expected-output", args.expected_output),
        ):
            if value:
                command.extend((flag, value))
        if args.debug and not args.review:
            command.append("--debug")
        if args.no_cache:
            command.append("--no-cache")
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "知识调用失败")
        payload = json.loads(completed.stdout)
        payload["workflow_bridge"] = {
            "workflow_config": str(workflow_config_path),
            "enabled": True,
            "skill": skill_name,
        }
        print(json.dumps(
            payload,
            ensure_ascii=False,
            indent=2 if args.debug else None,
            separators=None if args.debug else (",", ":"),
        ))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
