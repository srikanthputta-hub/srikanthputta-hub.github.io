"""Live Office board writer: updates public.studio_board (Supabase) as each role starts and finishes.

Same idea as the S.P.E.H.R. office-task-bridge. It only ever uses the public publishable key and can only update
status columns (row-level security + column grants enforce that). Strictly best-effort: a board problem must never
stop a video, so every call swallows errors and gives up quietly after a few consecutive failures.
"""
import os
from datetime import datetime, timezone

import requests

URL = os.environ.get("STUDIO_BOARD_URL", "https://qxyvmvtecnnwqeqaouzh.supabase.co/rest/v1/studio_board")
KEY = os.environ.get("STUDIO_BOARD_KEY", "sb_publishable_Ki9OarmjSVz_KdSuenGZMQ_jB4eIvGO")  # public by design, like the Office bridge


class Board:
    def __init__(self, pipeline, total, enabled=None):
        self.pipeline, self.total = pipeline, total
        self.enabled = (os.environ.get("STUDIO_BOARD", "on") != "off") if enabled is None else enabled
        self.fails = 0
        self.active = {}
        self.headers = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json", "Prefer": "return=minimal"}

    def _patch(self, query, body):
        if not self.enabled:
            return False
        try:
            r = requests.patch(f"{URL}?{query}", json=body, headers=self.headers, timeout=6)
            if r.status_code >= 300:
                raise RuntimeError(f"{r.status_code} {r.text[:120]}")
            self.fails = 0
            return True
        except Exception as e:
            self.fails += 1
            print(f"[office] board update skipped: {str(e)[:120]}")
            if self.fails >= 3:
                self.enabled = False
                print("[office] board disabled for the rest of this run")
            return False

    def reset(self):
        """Start of a run: everyone idle, no pipeline, no leftover text."""
        self._patch("role=neq.__none__", {"status": "idle", "description": None, "pipeline_name": None,
                                          "pipeline_stage": None, "pipeline_total": None})

    def start(self, role, text, stage=None):
        self.active[role] = text
        self._patch(f"role=eq.{role}", {"status": "working", "description": str(text)[:280], "pipeline_name": self.pipeline,
                                        "pipeline_stage": stage, "pipeline_total": self.total,
                                        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})

    def done(self, role, note=""):
        self.active.pop(role, None)
        self._patch(f"role=eq.{role}", {"status": "idle", "description": (str(note)[:280] or None), "pipeline_name": None,
                                        "pipeline_stage": None, "pipeline_total": None})

    def mark(self, role, status, note=""):
        """status: blocked | failed"""
        self.active.pop(role, None)
        self._patch(f"role=eq.{role}", {"status": status, "description": str(note)[:280] or None, "pipeline_name": None})

    def fail_active(self, note):
        for role in list(self.active):
            self.mark(role, "failed", note)
