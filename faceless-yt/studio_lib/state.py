"""Run log + the public state file the Office board page reads (studio/state.json)."""
import json
from datetime import datetime, timezone
from pathlib import Path

from .roles import ROLES

ROOT = Path(__file__).resolve().parent.parent.parent
STATE_PATH = ROOT / "studio" / "state.json"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunLog:
    def __init__(self, dry_run):
        self.data = {"id": datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"), "started": now(), "finished": None,
                     "mode": "dry-run" if dry_run else "live", "status": "running", "title": None, "topic": None,
                     "video_id": None, "gates": [], "stages": [], "note": ""}

    def stage(self, role, status, note=""):
        """status: done | skipped | failed | blocked"""
        self.data["stages"].append({"role": role, "status": status, "note": str(note)[:300], "at": now()})
        print(f"[{role}] {status} {str(note)[:140]}")

    def gate(self, name, passed, detail=""):
        self.data["gates"].append({"name": name, "passed": bool(passed), "detail": str(detail)[:300]})
        print(f"[gate] {'PASS' if passed else 'FAIL'} {name} {str(detail)[:140]}")

    def set(self, **kw):
        self.data.update(kw)

    def failed_gates(self):
        return [g for g in self.data["gates"] if not g["passed"]]

    def finish(self, status, note=""):
        self.data.update(status=status, finished=now(), note=str(note)[:500])


def write_state(log, path=None, keep=20):
    path = Path(path or STATE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        prev = json.loads(path.read_text())
    except Exception:
        prev = {}
    runs = [r for r in prev.get("runs", []) if r.get("id") != log.data["id"]] + [log.data]
    path.write_text(json.dumps({"updated": now(), "channel": "The Money Blueprint", "roles": ROLES, "runs": runs[-keep:]}, indent=2))
    return path


def report_text(log):
    d = log.data
    lines = [f"# Run {d['id']} ({d['mode']}): {d['status']}", "", f"Title: {d['title'] or '-'}", f"Topic: {d['topic'] or '-'}",
             f"Video: https://youtu.be/{d['video_id']}" if d["video_id"] else "Video: not published", "", "## Gates"]
    lines += [f"- {'PASS' if g['passed'] else 'FAIL'} {g['name']}: {g['detail']}" for g in d["gates"]]
    lines += ["", "## Stages"] + [f"- {s['role']}: {s['status']} {s['note']}" for s in d["stages"]]
    if d["note"]:
        lines += ["", d["note"]]
    return "\n".join(lines) + "\n"
