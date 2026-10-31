import json, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest
import pipeline as P

SCRIPT = {"title": "T", "description": "D", "tags": [], "thumbnail_text": "STOP NOW",
          "scenes": [{"narration": f"Scene {i} words here for you.", "search_query": "money"} for i in range(P.CFG["scenes"])]}


def fake_llm(monkeypatch, responses):
    monkeypatch.setattr(P, "llm", lambda prompt, max_tokens=6000: responses.pop(0))


def test_srt_uses_real_word_times():
    timings = [[("a", 0.0, 0.3), ("b", 0.3, 0.6), ("c", 0.6, 0.9), ("d", 0.9, 1.2), ("e", 1.2, 1.5)]]
    srt = P.build_srt(timings, [2.0])
    assert "00:00:00,000 --> 00:00:01,200" in srt and "a b c d" in srt and "\ne\n" in srt


def test_ask_json_retries_then_succeeds(monkeypatch):
    fake_llm(monkeypatch, ["not json", json.dumps(SCRIPT)])
    monkeypatch.setattr(P.time, "sleep", lambda s: None)
    assert P.ask_json("x", P.CFG["scenes"])["title"] == "T"


def test_ask_json_rejects_wrong_scene_count(monkeypatch):
    bad = dict(SCRIPT, scenes=SCRIPT["scenes"][:2])
    fake_llm(monkeypatch, [json.dumps(bad)] * 3)
    monkeypatch.setattr(P.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError):
        P.ask_json("x", P.CFG["scenes"])


def test_fact_check_failure_blocks_publishing(monkeypatch):
    fake_llm(monkeypatch, ["garbage", "garbage"])
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


def test_missing_key_fails_fast(monkeypatch):
    for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(P.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("must not retry")))
    with pytest.raises(RuntimeError, match="No LLM key"):
        P.ask_json("x")


def test_gemini_model_autodiscovery():
    models = [{"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
              {"name": "models/gemini-3-flash", "supportedGenerationMethods": ["generateContent"]},
              {"name": "models/gemini-3-flash-image", "supportedGenerationMethods": ["generateContent"]},
              {"name": "models/gemini-4-flash", "supportedGenerationMethods": ["embedContent"]},
              {"name": "models/gemini-3-pro", "supportedGenerationMethods": ["generateContent"]}]
    assert P.pick_gemini_model(models) == "gemini-3-flash"
    only_variants = [{"name": "models/gemini-9-flash-preview", "supportedGenerationMethods": ["generateContent"]}]
    assert P.pick_gemini_model(only_variants) == "gemini-9-flash-preview"
    with pytest.raises(RuntimeError):
        P.pick_gemini_model([{"name": "models/gemini-3-pro", "supportedGenerationMethods": ["generateContent"]}])


def test_gemini_request_shape(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    P._gemini_cache.clear()
    seen = {}
    class R:
        ok = True
        def __init__(self, d): self._d = d
        def raise_for_status(self): pass
        def json(self): return self._d
    def get(url, **kw): return R({"models": [{"name": "models/gemini-3-flash", "supportedGenerationMethods": ["generateContent"]}]})
    def post(url, **kw): seen.update(url=url, **kw); return R({"candidates": [{"content": {"parts": [{"text": "hi"}]}}]})
    monkeypatch.setattr(P.requests, "get", get)
    monkeypatch.setattr(P.requests, "post", post)
    assert P.llm("p") == "hi"
    assert "gemini-3-flash:generateContent" in seen["url"] and seen["headers"]["x-goog-api-key"] == "k"


def test_footage_falls_back_from_pexels_to_pixabay(tmp_path, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "p")
    monkeypatch.setenv("PIXABAY_API_KEY", "x")
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=640x360:d=3", str(src)], check=True)
    class R:
        def __init__(self, data=None, content=b""): self._d, self.content = data, content
        def raise_for_status(self): pass
        def json(self): return self._d
    def get(url, **kw):
        if "pexels" in url: raise RuntimeError("keys paused")
        if "pixabay.com/api" in url:
            return R({"hits": [{"id": 7, "duration": 5, "videos": {"large": {"url": "http://v/clip.mp4"}}}]})
        return R(content=src.read_bytes())
    monkeypatch.setattr(P.requests, "get", get)
    P.USED_CLIPS.clear()
    out = tmp_path / "v.mp4"
    P.fetch_clip("money", out, 2)
    assert out.exists() and ("pixabay", 7) in P.USED_CLIPS
