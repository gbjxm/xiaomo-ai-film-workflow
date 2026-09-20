"""JSON CLI for scoped V0.9 studio state; never executes media tools."""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

from studio_io import Project, StudioError

ACTIONS = {
    "Create": ("studio_worksets", "create"),
    "List": ("studio_worksets", "list_worksets"),
    "Inspect": ("studio_worksets", "inspect"),
    "Claim": ("studio_worksets", "claim"),
    "SaveDraft": ("studio_worksets", "save_draft"),
    "PrepareHandoff": ("studio_worksets", "prepare_handoff"),
    "ActivateHandoff": ("studio_worksets", "activate_handoff"),
    "CancelHandoff": ("studio_worksets", "cancel_handoff"),
    "Adopt": ("studio_adoption", "adopt"),
    "ResumeSync": ("studio_adoption", "resume_sync"),
    "CancelAdoption": ("studio_adoption", "cancel_adoption"),
    "Resolve": ("studio_adoption", "resolve"),
}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise StudioError("invalid_cli", message)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("--project-root", required=True)
        parser.add_argument("--action", required=True)
        parser.add_argument("--request-file")
        args = parser.parse_args(argv)
        names = {key.casefold(): key for key in ACTIONS}
        if args.action.casefold() not in names:
            raise StudioError("unknown_action", "Unknown studio action", {"actions": list(ACTIONS)})
        action = names[args.action.casefold()]
        request = {}
        if args.request_file:
            try:
                raw = Path(args.request_file).read_bytes()
                if len(raw) > 2 * 1024 * 1024:
                    raise StudioError("request_too_large", "Request JSON exceeds 2 MiB")
                request = json.loads(raw.decode("utf-8-sig"))
            except (OSError, UnicodeError, ValueError) as exc:
                raise StudioError("invalid_request", "Request file must contain UTF-8 JSON", {"reason": str(exc)}) from exc
        if not isinstance(request, dict):
            raise StudioError("invalid_request", "Request must be a JSON object")
        project = Project(args.project_root, adoption=action in {"Adopt", "ResumeSync", "CancelAdoption", "Resolve"})
        module_name, function_name = ACTIONS[action]
        result = getattr(importlib.import_module(module_name), function_name)(project, request)
        if not isinstance(result, dict):
            raise StudioError("invalid_result", "Runtime operation did not return an object")
        payload = dict(result)
        payload.setdefault("ok", True)
        if not isinstance(payload["ok"], bool):
            raise StudioError("invalid_result", "Runtime ok field must be boolean")
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 0 if payload["ok"] else 2
    except StudioError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": exc.message, "details": exc.details}}, ensure_ascii=False, separators=(",", ":")))
        return 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error": {"code": "runtime_error", "message": str(exc), "details": {"type": type(exc).__name__}}}, ensure_ascii=False, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

