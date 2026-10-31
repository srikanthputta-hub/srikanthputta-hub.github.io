import json, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest
import pipeline as P

@pytest.fixture(autouse=True)
def _reset_provider_state():
    P._dead_providers.clear()
    yield
    P._dead_providers.clear()


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
                    str(tmp_path / "work" / "v0_0.mp4")], check=True)
    from PIL import Image
    monkeypatch.setitem(P.CFG, "ai_thumbnails", False)
    assert Image.open(P.make_thumbnail(None, "STOP SAVING $250K WRONG")).size == (1280, 720)


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


def test_groq_model_pick():
    llama = [{"id": "llama-3.1-8b-instant"}, {"id": "llama-3.3-70b-versatile"}, {"id": "llama-guard-4-12b"},
             {"id": "whisper-large-v3"}, {"id": "llama-9-400b-old", "active": False}]
    assert P.pick_groq_model(llama) == "llama-3.3-70b-versatile"
    modern = [{"id": "openai/gpt-oss-120b"}, {"id": "openai/gpt-oss-20b"}, {"id": "qwen/qwen3-32b"},
              {"id": "meta-llama/llama-4-scout-17b-16e-instruct"}, {"id": "meta-llama/llama-prompt-guard-2-22m"},
              {"id": "playai-tts"}, {"id": "groq/compound"}, {"id": "whisper-large-v3-turbo"}]
    assert P.pick_groq_model(modern) == "openai/gpt-oss-120b"
    assert P.pick_groq_model([{"id": "some-new-chat-model"}, {"id": "playai-tts"}]) == "some-new-chat-model"
    with pytest.raises(RuntimeError, match="models seen: whisper-large-v3"):
        P.pick_groq_model([{"id": "whisper-large-v3"}])


def test_llm_falls_back_to_groq_when_gemini_out_of_credit(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g"); monkeypatch.setenv("GROQ_API_KEY", "q")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False); monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False); P._gemini_cache.clear()
    class R:
        def __init__(self, ok, d=None, code=200): self.ok, self._d, self.status_code, self.text = ok, d, code, "credits depleted"
        def raise_for_status(self): pass
        def json(self): return self._d
    def get(url, **kw):
        if "groq" in url: return R(True, {"data": [{"id": "llama-3.3-70b-versatile"}]})
        return R(True, {"models": [{"name": "models/gemini-3-flash", "supportedGenerationMethods": ["generateContent"]}]})
    def post(url, **kw):
        if "googleapis" in url: return R(False, code=402)
        return R(True, {"choices": [{"message": {"content": "from groq"}}]})
    monkeypatch.setattr(P.requests, "get", get); monkeypatch.setattr(P.requests, "post", post)
    assert P.llm("p") == "from groq"


def test_scene_normalisation_accepts_old_and_new_shape(monkeypatch):
    new = dict(SCRIPT, scenes=[{"narration": "x", "search_queries": ["a", "b"], "callout": "RULE OF 72"}] * P.CFG["scenes"])
    fake_llm(monkeypatch, [json.dumps(new)])
    s = P.ask_json("x", P.CFG["scenes"])["scenes"][0]
    assert s["search_query"] == "a" and s["search_queries"] == ["a", "b"]
    fake_llm(monkeypatch, [json.dumps(SCRIPT)])
    assert P.ask_json("x", P.CFG["scenes"])["scenes"][0]["search_queries"] == ["money"]


def test_scene_clip_cuts_and_callout(tmp_path, monkeypatch):
    monkeypatch.setitem(P.CFG, "format", "long")
    monkeypatch.setattr(P, "SIZE", (640, 360))
    used = []
    def fake_fetch(q, dest, secs):
        used.append(q)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=blue:s=640x360:d={secs}:r=30",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest)], check=True)
    monkeypatch.setattr(P, "fetch_clip", fake_fetch)
    sc = {"narration": "x", "search_query": "a", "search_queries": ["a", "b", "c"], "callout": "$200/MONTH = $250K"}
    out = P.build_scene_clip(sc, 0, 14.0, tmp_path)
    assert used == ["a", "b", "c"] and out.exists() and abs(P.duration(out) - 14.0) < 0.5


def test_rate_limit_wait_parsing():
    assert P.rate_limit_wait("Groq 429 retry-after=12: x") == 13
    assert abs(P.rate_limit_wait("Groq 429 retry-after=: ... Please try again in 3.4s. ") - 4.4) < 0.01
    assert abs(P.rate_limit_wait("Groq 429 retry-after=: try again in 1m2.5s") - 63.5) < 0.01
    assert P.rate_limit_wait("Groq 429 retry-after=: no hint") == 20
    assert P.rate_limit_wait("Gemini 402 retry-after=: out of credit") is None
    assert P.rate_limit_wait("Groq 500: boom") is None


def test_llm_waits_on_429_and_skips_dead_provider(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g"); monkeypatch.setenv("GROQ_API_KEY", "q")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    P._dead_providers.clear()
    calls, sleeps = [], []
    def gem(prompt, mt, key): calls.append("gemini"); raise ValueError("Gemini 402 retry-after=: prepay depleted")
    state = {"n": 0}
    def groq(prompt, mt, key):
        calls.append("groq"); state["n"] += 1
        if state["n"] < 3: raise ValueError("Groq 429 retry-after=2: tpm")
        return "ok"
    monkeypatch.setattr(P, "_gemini", gem); monkeypatch.setattr(P, "_groq", groq)
    monkeypatch.setattr(P.time, "sleep", lambda s: sleeps.append(s))
    assert P.llm("p") == "ok" and sleeps == [3, 3]
    assert P.llm("p") == "ok"
    assert calls.count("gemini") == 1  # 402 marked Gemini dead for the rest of the run
    P._dead_providers.clear()


def test_missing_youtube_secrets_named_clearly(monkeypatch):
    monkeypatch.setenv("YT_CLIENT_ID", "x")
    monkeypatch.delenv("YT_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("YT_REFRESH_TOKEN", "")
    with pytest.raises(RuntimeError, match="YT_CLIENT_SECRET, YT_REFRESH_TOKEN"):
        P.yt_client()
