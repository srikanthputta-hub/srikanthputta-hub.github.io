import json, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest
import pipeline as P

SCRIPT = {"title": "T", "description": "D", "tags": [], "thumbnail_text": "STOP NOW",
          "scenes": [{"narration": f"Scene {i} words here for you.", "search_query": "money"} for i in range(P.CFG["scenes"])]}


def fake_llm(responses):
    """Patch anthropic so each call returns the next response string."""
    class Msg:
        def __init__(self, t): self.content = [type("B", (), {"text": t})]
    class Client:
        def __init__(self): self.messages = self
        def create(self, **kw): return Msg(responses.pop(0))
    import types
    return types.SimpleNamespace(Anthropic=Client)


def test_srt_uses_real_word_times():
    timings = [[("a", 0.0, 0.3), ("b", 0.3, 0.6), ("c", 0.6, 0.9), ("d", 0.9, 1.2), ("e", 1.2, 1.5)]]
    srt = P.build_srt(timings, [2.0])
    assert "00:00:00,000 --> 00:00:01,200" in srt and "a b c d" in srt and "\ne\n" in srt


def test_ask_json_retries_then_succeeds(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", fake_llm(["not json", json.dumps(SCRIPT)]))
    monkeypatch.setattr(P.time, "sleep", lambda s: None)
    assert P.ask_json("x", P.CFG["scenes"])["title"] == "T"


def test_ask_json_rejects_wrong_scene_count(monkeypatch):
    bad = dict(SCRIPT, scenes=SCRIPT["scenes"][:2])
    monkeypatch.setitem(sys.modules, "anthropic", fake_llm([json.dumps(bad)] * 3))
    monkeypatch.setattr(P.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError):
        P.ask_json("x", P.CFG["scenes"])


def test_fact_check_failure_blocks_publishing(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", fake_llm(["garbage", "garbage"]))
    monkeypatch.setattr(P.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="fact-check failed"):
        P.fact_check(SCRIPT)


def test_idea_queue(tmp_path, monkeypatch):
    f = tmp_path / "ideas.json"
    f.write_text(json.dumps([{"title": "a", "score": 3}, {"title": "b", "score": 9}, {"title": "c", "score": 5, "used": True}]))
    monkeypatch.setattr(P, "IDEAS", f)
    assert P.next_idea()["title"] == "b"
    P.mark_used("b")
    assert P.next_idea()["title"] == "a"


def test_thumbnail_renders(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "OUT", tmp_path)
    (tmp_path / "work").mkdir()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=1080x1920:d=2",
                    str(tmp_path / "work" / "v0.mp4")], check=True)
    from PIL import Image
    assert Image.open(P.make_thumbnail(None, "STOP SAVING WRONG")).size == (1280, 720)
