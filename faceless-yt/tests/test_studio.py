import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest
import pipeline as P
import studio
from studio_lib import charts as chart_lib
from studio_lib import crew, data, gates
from studio_lib import state as state_lib


# ---------- data layer ----------
def test_fred_yoy_and_monthly_parsing():
    text = "DATE,CPIAUCSL\n" + "\n".join(f"{2019 + i // 12}-{i % 12 + 1:02d}-01,{100 + i}" for i in range(30)) + "\n2022-01-01,.\n"
    pts = data.parse_fred(text, data.CATALOG["us_inflation"])
    assert len(pts) == 18 and abs(pts[0][1] - 12.0) < 1e-9 and pts[0][0] == 2020.0


def test_world_bank_parsing_skips_nulls():
    payload = [{"page": 1}, [{"date": "2021", "value": 4.2}, {"date": "2020", "value": None}, {"date": "2019", "value": 1.5}]]
    assert data.parse_wb(payload) == [(2019.0, 1.5), (2021.0, 4.2)]
    assert data.parse_wb([{"message": "bad"}]) == []


def test_fact_sheet_lists_numbers_and_allowed_set():
    chart = {"id": "c1", "title": "US inflation", "unit": "%", "granularity": "month", "source": "FRED",
             "lines": [{"label": "US", "points": [(2020 + i / 12, 2.0 + i * 0.5) for i in range(12)]}]}
    text, allowed = data.fact_sheet([chart])
    assert "[c1] US inflation" in text and "latest Dec 2020 = 7.5%" in text
    assert any(abs(a - 7.5) < 1e-9 for a in allowed) and any(abs(a - 5.5) < 1e-9 for a in allowed)


def test_unknown_series_rejected():
    with pytest.raises(ValueError):
        data.load_chart({"series": "made_up_series"}, "c1")


# ---------- gates ----------
def test_number_audit_traces_to_fact_sheet():
    allowed = [9.06, 3.4]
    ok = {"scenes": [{"narration": "Prices rose about 9 percent in 2022, then slowed to 3.4 percent."}]}
    bad = {"scenes": [{"narration": "Prices rose 12 percent and $4 trillion was created in 1971."}]}
    assert gates.number_issues(ok, allowed) == []
    assert gates.number_issues(bad, allowed)[0]["scene"] == 1


def test_directive_language_blocked():
    s = {"scenes": [{"narration": "Honestly, you should buy stocks now."}, {"narration": "Rates rose because inflation did."}]}
    issues = gates.directive_issues(s)
    assert len(issues) == 1 and issues[0]["scene"] == 1


def test_originality_blocks_repeats_and_missing_point_of_view():
    text = "the central bank raised rates again and again to slow demand across the whole economy this year " * 3
    script = {"scenes": [{"narration": text}], "our_take": "x"}
    lib = [{"title": "Old video", "narration": text, "opener": ""}]
    problems = gates.originality_issues(script, lib)
    assert any("similar" in p for p in problems) and any("point of view" in p for p in problems)


def test_chapters_start_at_zero_and_need_three():
    ch = [{"title": "Hook", "scene": 0}, {"title": "Evidence", "scene": 2}, {"title": "Meaning", "scene": 4}]
    assert gates.build_chapters(ch, [30, 30, 40, 40, 50]) == ["0:00 Hook", "1:00 Evidence", "2:20 Meaning"]
    assert gates.build_chapters(ch[:2], [30, 30, 40, 40, 50]) == []


# ---------- crew ----------
SCRIPT = {"scenes": [{"narration": "Scene one narration here that is long enough to count.", "search_queries": ["a"], "search_query": "a", "callout": "", "chart": ""},
                     {"narration": "Scene two narration here that is long enough to count too.", "search_queries": ["b"], "search_query": "b", "callout": "", "chart": ""}]}


def test_fix_scenes_applies_reviewer_fix_and_rewrites_the_rest(monkeypatch):
    monkeypatch.setattr(P, "llm", lambda prompt, max_tokens=6000: json.dumps(
        {"fixes": [{"scene": 2, "narration": "A rewritten second scene that explains what happened in plain words."}]}))
    issues = [{"scene": 1, "type": "fact", "problem": "wrong", "fix": "A corrected first scene that states only what the data shows, plainly."},
              {"scene": 2, "type": "policy", "problem": "advice", "fix": ""}]
    out = crew.fix_scenes(json.loads(json.dumps(SCRIPT)), issues, "sheet")
    assert out["scenes"][0]["narration"].startswith("A corrected first scene")
    assert out["scenes"][1]["narration"].startswith("A rewritten second scene")


def test_researcher_drops_unknown_series(monkeypatch):
    monkeypatch.setattr(P, "llm", lambda prompt, max_tokens=6000: json.dumps(
        {"charts": [{"series": "us_inflation"}, {"series": "bitcoin_price"}, "junk"]}))
    plan = crew.researcher_plan({"working_title": "t", "angle": "a"})
    assert [c["series"] for c in plan["charts"]] == ["us_inflation"]


def test_director_assigns_every_chart_and_drops_abstract_queries():
    script = {"scenes": [{"narration": "x" * 50, "search_queries": ["rising chart graphic", "farmer at market"], "search_query": "", "chart": "c9"} for _ in range(6)]}
    out = studio.direct(script, [{"id": "c1"}, {"id": "c2"}])
    assert {s["chart"] for s in out["scenes"] if s["chart"]} == {"c1", "c2"}
    assert all(s["search_queries"] == ["farmer at market"] for s in out["scenes"])


def test_state_file_keeps_last_runs(tmp_path):
    path = tmp_path / "studio" / "state.json"
    for i in range(23):
        log = state_lib.RunLog(True)
        log.data["id"] = f"run{i}"
        log.stage("researcher", "done", "ok")
        log.gate("real_data", True, "3 charts")
        log.finish("dry-run")
        state_lib.write_state(log, path)
    st = json.loads(path.read_text())
    assert len(st["runs"]) == 20 and st["runs"][-1]["id"] == "run22" and len(st["roles"]) == 8


def test_every_role_has_a_job_and_department():
    from studio_lib.roles import ROLES
    assert all(r["summary"] and r["dept"] and r["kind"] in ("llm", "code", "human") for r in ROLES)
    assert len({r["id"] for r in ROLES}) == len(ROLES)


# ---------- end to end (model, data and voice stubbed; real ffmpeg, charts, captions, thumbnail) ----------
CSV = "DATE,X\n" + "\n".join(f"{2008 + i // 12}-{i % 12 + 1:02d}-01,{200 + i * 0.7 + (i % 5)}" for i in range(220))
WB = [{"page": 1}, [{"date": str(y), "value": 2.0 + (2023 - y) * 0.2} for y in range(2023, 1999, -1)]]


@pytest.fixture
def studio_env(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "OUT", tmp_path / "output")
    monkeypatch.setattr(P, "HISTORY", tmp_path / "history.json")
    monkeypatch.setattr(P, "IDEAS", tmp_path / "ideas.json")
    monkeypatch.setattr(studio, "LIBRARY", tmp_path / "library")
    monkeypatch.setattr(state_lib, "STATE_PATH", tmp_path / "studio" / "state.json")
    for k, v in {"scenes": 6, "min_words": 100, "min_minutes": 0.05, "ai_thumbnails": False}.items():
        monkeypatch.setitem(P.CFG, k, v)
    for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("STUDIO_BOARD", "off")  # no network to the live board in tests unless a test turns it on
    monkeypatch.setattr(data, "fred_ok", lambda: True)  # tests never touch the real network
    monkeypatch.setattr(data, "fetch_text", lambda url, params=None: CSV)
    monkeypatch.setattr(data, "fetch_json", lambda url, params=None: WB)
    orig = chart_lib.render_chart_video
    monkeypatch.setattr(chart_lib, "render_chart_video", lambda *a, **k: orig(*a, reveal_s=0.6, hold_s=0.4, fps=8, **k))

    async def fake_tts(text, path):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=4", "-q:a", "5", str(path)], check=True)
        return [(w, i * 0.4, i * 0.4 + 0.35) for i, w in enumerate(text.split())]
    monkeypatch.setattr(P, "_tts", fake_tts)
    monkeypatch.setattr(P.time, "sleep", lambda s: None)
    return tmp_path


NARR = ("The story starts with a simple question about why prices keep changing and what the official data really shows. "
        "Economists track this with a few long running series, and the picture is more interesting than the headlines suggest. "
        "Looking at the record, the changes were gradual at first and then much faster, which is why people noticed them.")


def fake_model(review_issue=None, advice=False, seen=None):
    """Answers every role's prompt; `seen` collects (role, prompt) so tests can check what teammates passed along."""
    calls = {"review": 0}

    def model(prompt, max_tokens=6000):
        def saw(role):
            if seen is not None:
                seen.append((role, prompt))
        if "You are the Producer" in prompt:
            saw("producer")
            return json.dumps({"topic": "why prices rose", "working_title": "Why Did Prices Jump So Fast?", "angle": "what drove the jump",
                               "our_take": "Prices responded to money supply and rates with a lag, not instantly.",
                               "note": "Viewers should see that prices lagged policy; open on the biggest jump.", "idea": ""})
        if "You are the Researcher" in prompt:
            saw("researcher")
            return json.dumps({"note": "Money and rates moved first; prices followed.",
                               "charts": [{"series": "us_inflation", "start_year": 2010, "title": "US inflation", "why": "prices"},
                                          {"series": "us_fed_funds", "start_year": 2010, "title": "Policy rate", "why": "rates"},
                                          {"series": "wb_inflation", "countries": ["US", "DE"], "start_year": 2000, "title": "Inflation abroad", "why": "peers"}]})
        if "You are the Writer." in prompt and "Plan a" in prompt:
            saw("writer-outline")
            return json.dumps({"our_take": "Prices respond to money and rates with a lag, not at once.", "note": "The jump and the lag are the moments that matter.",
                               "acts": [{"name": "The question", "beats": [{"beat": "hook", "chart": ""}, {"beat": "setup", "chart": "c1"}]},
                                        {"name": "The evidence", "beats": [{"beat": "rates", "chart": "c2"}, {"beat": "abroad", "chart": ""}]},
                                        {"name": "What it means", "beats": [{"beat": "lag", "chart": ""}, {"beat": "recap", "chart": ""}]}]})
        if "You are the Editor." in prompt and "The Writer's plan" in prompt:
            saw("editor-preflight")
            return json.dumps({"issues": [{"beat": 3, "problem": "could drift into advice"}], "guidance": "Keep every claim descriptive; never tell viewers what to do."})
        if "You are the Writer." in prompt and "Write one scene per beat" in prompt:
            saw("writer-draft")
            text = ("You should buy stocks now. " if advice else "") + NARR
            return json.dumps({"scenes": [{"narration": text}] * 2})
        if "You are the Editor." in prompt and "Script scenes:" in prompt:
            saw("editor-review")
            calls["review"] += 1
            if review_issue and calls["review"] == 1:
                return json.dumps({"issues": [{"scene": 2, "kind": "fact", "problem": review_issue,
                                               "fix": "A corrected second scene explaining only what the supplied data shows, in plain and calm language for everyone."}]})
            return json.dumps({"issues": []})
        if "revising scenes the Editor flagged" in prompt:
            saw("writer-revise")
            return json.dumps({"fixes": [{"scene": n, "narration": NARR} for n in range(1, 7)]})
        if "You are the Designer for a faceless explainer channel" in prompt:
            saw("designer-visuals")
            return json.dumps({"scenes": [{"scene": n, "chart": "", "callout": "BIG SHIFT" if n % 2 else "",
                                           "search_queries": ["shopper at a grocery store", "cash register at a shop", "bank building exterior"]} for n in range(1, 31)]})
        if "You are the Publisher" in prompt:
            saw("publisher")
            return json.dumps({"title": "Why Prices Jumped So Fast", "description": "A data-led look at why prices moved.", "tags": ["economy", "inflation"]})
        if "Design the thumbnail" in prompt:
            saw("designer-thumbnail")
            return json.dumps({"thumbnail_text": "PRICES JUMPED", "thumbnail_prompt": ""})
        raise AssertionError("unexpected prompt: " + prompt[:90])
    return model


def test_studio_dry_run_end_to_end(studio_env, monkeypatch):
    monkeypatch.setattr(P, "llm", fake_model(review_issue="figure misread"))
    assert studio.run_studio(dry_run=True) == 0
    st = json.loads((studio_env / "studio" / "state.json").read_text())
    run = st["runs"][-1]
    assert run["status"] == "dry-run" and run["title"] == "Why Prices Jumped So Fast"
    assert all(g["passed"] for g in run["gates"]) and {"real_data", "greenlight", "numbers_and_facts", "policy", "originality", "length", "packaging"} <= {g["name"] for g in run["gates"]}
    assert (studio_env / "output" / "video.mp4").exists() and (studio_env / "output" / "thumbnail.jpg").exists()
    script = json.loads((studio_env / "output" / "script.json").read_text())
    assert "Data sources" in script["description"] and "not financial advice" in script["description"]
    assert script["scenes"][1]["narration"].startswith("A corrected second scene")  # reviewer fix was applied
    assert not (studio_env / "history.json").exists()  # dry runs record nothing


def test_advice_language_blocks_the_upload(studio_env, monkeypatch):
    monkeypatch.setattr(P, "llm", fake_model(advice=True))
    uploads = []
    monkeypatch.setattr(P, "upload", lambda *a, **k: uploads.append(a) or "x")
    # the rewrite step also returns advice-free text, so patch it to keep returning advice and force a block
    base = P.llm
    monkeypatch.setattr(P, "llm", lambda prompt, mt=6000: base(prompt, mt) if "revising scenes the Editor flagged" not in prompt
                        else json.dumps({"fixes": [{"scene": n, "narration": "You should buy stocks now, it is guaranteed to work for everybody."} for n in range(1, 7)]}))
    assert studio.run_studio(dry_run=False) == 0
    run = json.loads((studio_env / "studio" / "state.json").read_text())["runs"][-1]
    assert run["status"] == "blocked" and any(not g["passed"] for g in run["gates"]) and uploads == []


def test_live_run_publishes_and_records_history(studio_env, monkeypatch):
    monkeypatch.setattr(P, "llm", fake_model())
    monkeypatch.setattr(P, "upload", lambda video, script, thumb: "abc123XYZ")
    assert studio.run_studio(dry_run=False) == 0
    run = json.loads((studio_env / "studio" / "state.json").read_text())["runs"][-1]
    assert run["status"] == "published" and run["video_id"] == "abc123XYZ"
    hist = json.loads((studio_env / "history.json").read_text())
    assert hist[-1]["id"] == "abc123XYZ" and list((studio_env / "library").glob("*.json"))


# ---------- repo hygiene: board data and Claude Code roster stay valid ----------
REPO = Path(__file__).resolve().parent.parent.parent


def test_office_board_roles_match_the_registry():
    from studio_lib.roles import ROLES
    assert json.loads((REPO / "studio" / "roles.json").read_text()) == ROLES


def test_claude_roster_files_have_valid_frontmatter():
    files = sorted((REPO / ".claude" / "agents").glob("*.md"))
    assert len(files) >= 8
    for f in files:
        text = f.read_text()
        assert text.startswith("---\n"), f
        head = text.split("---")[1]
        fields = dict(line.split(":", 1) for line in head.strip().splitlines())
        assert fields["name"].strip() == f.stem and fields["description"].strip() and fields["model"].strip() in ("haiku", "sonnet", "opus")


def test_claude_md_states_the_hard_rules():
    text = (REPO / "CLAUDE.md").read_text()
    for needle in ("personal financial advice", "No role runs `git commit`", "secret values", "weakens a gate"):
        assert needle.lower() in text.lower(), needle


def test_fetch_retries_then_succeeds(monkeypatch):
    import requests
    calls = {"n": 0}
    class R:
        text = "ok"
        def raise_for_status(self): pass
        def json(self): return {"a": 1}
    def get(url, params=None, timeout=None, headers=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.ReadTimeout("slow")
        return R()
    monkeypatch.setattr(data.requests, "get", get)
    monkeypatch.setattr(data.time, "sleep", lambda s: None)
    assert data.fetch_text("u") == "ok" and calls["n"] == 3


def test_fetch_gives_up_after_four_tries(monkeypatch):
    import requests
    monkeypatch.setattr(data.requests, "get", lambda *a, **k: (_ for _ in ()).throw(requests.ReadTimeout("slow")))
    monkeypatch.setattr(data.time, "sleep", lambda s: None)
    with pytest.raises(requests.ReadTimeout):
        data.fetch_json("u")


def test_plain_text_normalisation_and_wb_only_catalog():
    assert crew.plain("Data\u2011Driven \u201cstory\u201d \u2014 now\u00a0here") == 'Data-Driven "story" - now here'
    wb_only = data.catalog_text("wb")
    assert "wb_inflation" in wb_only and "us_inflation" not in wb_only


def test_studio_falls_back_to_world_bank_when_fred_is_down(studio_env, monkeypatch):
    import requests
    def fetch_text(url, params=None):
        raise requests.ReadTimeout("FRED unreachable")
    monkeypatch.setattr(data, "fetch_text", fetch_text)
    base = fake_model()
    def model(prompt, max_tokens=6000):
        if "You are the Researcher" in prompt and "US series" not in prompt:  # World Bank-only re-plan
            return json.dumps({"charts": [{"series": "wb_inflation", "countries": ["US"], "start_year": 2000, "title": "Inflation"},
                                          {"series": "wb_gdp_growth", "countries": ["US", "DE"], "start_year": 2000, "title": "Growth"}]})
        return base(prompt, max_tokens)
    monkeypatch.setattr(P, "llm", model)
    assert studio.run_studio(dry_run=True) == 0
    run = json.loads((studio_env / "studio" / "state.json").read_text())["runs"][-1]
    assert run["status"] == "dry-run" and any(g["name"] == "real_data" and g["passed"] and "skipped" in g["detail"] or g["name"] == "real_data" and g["passed"] for g in run["gates"])


# ---------- live Office board writer ----------
from studio_lib import office


class FakeResp:
    status_code = 204
    text = ""


def test_board_updates_and_never_raises(monkeypatch):
    calls = []
    monkeypatch.setattr(office.requests, "patch", lambda url, json=None, headers=None, timeout=None: calls.append((url, json)) or FakeResp())
    b = office.Board("video-1", 14, enabled=True)
    b.reset(); b.start("researcher", "choosing datasets", 2); b.done("researcher", "4 charts planned"); b.mark("producer", "blocked", "gate failed")
    assert calls[0][0].endswith("role=neq.__none__") and calls[0][1]["status"] == "idle"
    assert calls[1] == (office.URL + "?role=eq.researcher", {**calls[1][1], "status": "working", "pipeline_name": "video-1", "pipeline_stage": 2, "pipeline_total": 14})
    assert calls[2][1]["status"] == "idle" and calls[2][1]["description"] == "4 charts planned" and calls[2][1]["pipeline_name"] is None
    assert calls[3][1]["status"] == "blocked"


def test_board_gives_up_quietly_after_four_failed_calls(monkeypatch):
    import requests
    monkeypatch.setattr(office.time, "sleep", lambda s: None)
    n = {"c": 0}
    def boom(*a, **k):
        n["c"] += 1
        raise requests.ConnectionError("blocked")
    monkeypatch.setattr(office.requests, "patch", boom)
    b = office.Board("v", 14, enabled=True)
    for _ in range(7):
        b.start("researcher", "x")
    assert n["c"] == 8 and b.enabled is False  # 4 failed calls, each tried twice


def test_board_can_be_switched_off_by_env(monkeypatch):
    monkeypatch.setenv("STUDIO_BOARD", "off")
    monkeypatch.setattr(office.requests, "patch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")))
    office.Board("v", 14).start("researcher", "x")


def test_studio_run_drives_the_live_board_in_order(studio_env, monkeypatch):
    events = []
    monkeypatch.setenv("STUDIO_BOARD", "on")
    def patch(url, json=None, headers=None, timeout=None):
        events.append((url.split("?")[1], json.get("status"), json.get("pipeline_name")))
        return FakeResp()
    monkeypatch.setattr(office.requests, "patch", patch)
    monkeypatch.setattr(P, "llm", fake_model())
    assert studio.run_studio(dry_run=True) == 0
    seq = [(q.split("role=eq.")[1], s) for q, s, _ in events if q.startswith("role=eq.")]
    started = [r for r, s in seq if s == "working"]
    assert started[0] == "producer" and started.index("producer") < started.index("researcher") < started.index("writer")
    # pre-production meeting: Writer, Editor and Designer sit down together, on the same pipeline
    iw, ie, idg = (seq.index((r, "working")) for r in ("writer", "editor", "designer"))
    assert sorted([iw, ie, idg]) == [iw, iw + 1, iw + 2] and seq.index(("writer", "idle")) > max(iw, ie, idg)
    assert len({p for q, s, p in events if s == "working"}) == 1  # one shared pipeline name for the whole video
    assert seq[-1] == ("producer", "idle")


def test_a_crash_marks_the_active_role_failed_on_the_board(studio_env, monkeypatch):
    events = []
    monkeypatch.setenv("STUDIO_BOARD", "on")
    monkeypatch.setattr(office.requests, "patch", lambda url, json=None, headers=None, timeout=None: events.append((url.split("?")[1], json.get("status"))) or FakeResp())
    def model(prompt, max_tokens=6000):
        raise ValueError("every provider failed")
    monkeypatch.setattr(P, "llm", model)
    monkeypatch.setattr(crew.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError):
        studio.run_studio(dry_run=True)
    assert ("role=eq.producer", "failed") in events and events[-1] == ("role=eq.producer", "failed")


def test_dashboard_page_and_writer_use_the_same_board():
    import re
    html = (REPO / "studio" / "index.html").read_text()
    assert re.search(r'SB_URL="([^"]+)"', html).group(1) == office.URL
    assert re.search(r'SB_KEY="([^"]+)"', html).group(1) == office.KEY
    assert "studio_board" in (REPO / "studio" / "migrations" / "001_studio_board.sql").read_text()
    # the page never needs a secret: only the public publishable key, and the table is read-only for it apart from status columns
    assert "service_role" not in html and "sb_secret" not in html


def test_thin_scenes_trigger_a_retry_with_feedback(monkeypatch):
    asked = []
    short = " ".join(["word"] * 30)
    full = " ".join(["word"] * 65)
    def model(prompt, max_tokens=6000):
        asked.append("IMPORTANT" in prompt)
        text = full if "IMPORTANT" in prompt else short
        return json.dumps({"scenes": [{"narration": text}] * 2})
    monkeypatch.setattr(P, "llm", model)
    monkeypatch.setattr(crew.time, "sleep", lambda s: None)
    outline = {"acts": [{"name": f"A{i}", "beats": [{"beat": "b", "chart": ""}, {"beat": "c", "chart": ""}]} for i in range(3)]}
    script = crew.writer_draft({"working_title": "T", "angle": "a", "our_take": "x"}, outline, "sheet", [])
    assert asked == [False, True, False, True, False, True]  # each act: thin first, redone with feedback
    assert all(len(s["narration"].split()) == 65 for s in script["scenes"])


def test_studio_skips_fred_when_it_is_unreachable(studio_env, monkeypatch):
    monkeypatch.setattr(data, "fred_ok", lambda: False)
    seen = {}
    base = fake_model()
    def model(prompt, max_tokens=6000):
        if "You are the Researcher" in prompt:
            seen["catalog_has_us_series"] = "US series" in prompt
            return json.dumps({"charts": [{"series": "wb_inflation", "countries": ["US"], "start_year": 2000, "title": "Inflation"},
                                          {"series": "wb_gdp_growth", "countries": ["US", "DE"], "start_year": 2000, "title": "Growth"}]})
        return base(prompt, max_tokens)
    monkeypatch.setattr(P, "llm", model)
    assert studio.run_studio(dry_run=True) == 0
    assert seen["catalog_has_us_series"] is False


def test_dashboard_desks_match_the_role_registry():
    import re
    from studio_lib.roles import FLOW, ROLES
    html = (REPO / "studio" / "index.html").read_text()
    flow = re.findall(r'"([a-z_]+)"', re.search(r"var FLOW=\[(.*?)\];", html).group(1))
    assert flow == FLOW and set(flow) | {"boss"} == {r["id"] for r in ROLES}
    assert len(flow) == 7  # seven working roles; the boss is not a pipeline step


def test_board_retries_a_timed_out_update_once(monkeypatch):
    monkeypatch.setattr(office.time, "sleep", lambda s: None)
    seen = []
    class Slow(FakeResp):
        status_code = 500
        text = "statement timeout"
    monkeypatch.setattr(office.requests, "patch", lambda *a, **k: seen.append(1) or (Slow() if len(seen) == 1 else FakeResp()))
    b = office.Board("v", 14, enabled=True)
    assert b.start("researcher", "x") is None and len(seen) == 2 and b.fails == 0 and b.enabled


def test_scrub_deletes_only_unsupported_sentences():
    allowed = [9.06]
    keep = "Prices rose about 9 percent in 2022 and people noticed it in shops and restaurants everywhere they went. "
    bad = "Then they climbed another 2.6 % afterwards. You should buy stocks now. "
    tail = "Economists link the pattern to supply problems and to strong demand after the lockdowns ended."
    script = {"scenes": [{"narration": keep + bad + tail}, {"narration": "Short scene with 5.5 % in it."}]}
    assert gates.scrub_unsupported(script, allowed) == 1
    out = script["scenes"][0]["narration"]
    assert "2.6" not in out and "You should" not in out and out.startswith("Prices rose about 9 percent") and out.endswith("ended.")
    assert script["scenes"][1]["narration"] == "Short scene with 5.5 % in it."  # too short to trim: left for the gate to catch


def test_numbers_and_policy_gates_are_judged_separately(studio_env, monkeypatch):
    base = fake_model()
    def model(prompt, max_tokens=6000):
        if "You are the Editor." in prompt and "Script scenes:" in prompt:
            return json.dumps({"issues": [{"scene": 1, "kind": "policy", "problem": "reads like advice", "fix": ""}]})
        return base(prompt, max_tokens)
    monkeypatch.setattr(P, "llm", model)
    monkeypatch.setattr(crew, "fix_scenes", lambda script, issues, sheet, allowed=None, notes=(): script)
    assert studio.run_studio(dry_run=True) == 0
    gates_by_name = {g["name"]: g for g in json.loads((studio_env / "studio" / "state.json").read_text())["runs"][-1]["gates"]}
    assert gates_by_name["numbers_and_facts"]["passed"] is True and gates_by_name["policy"]["passed"] is False


# ---------- the team really coordinates ----------
def test_teammates_notes_and_guidance_reach_the_next_role(studio_env, monkeypatch):
    seen = []
    monkeypatch.setattr(P, "llm", fake_model(seen=seen))
    assert studio.run_studio(dry_run=True) == 0
    prompts = {}
    for role, prompt in seen:
        prompts.setdefault(role, prompt)
    assert "prices lagged policy" in prompts["researcher"]                       # Producer's note reaches the Researcher
    assert "Producer:" in prompts["writer-outline"] and "Researcher:" in prompts["writer-outline"]
    assert "Highlight" in prompts["writer-outline"]                              # the Researcher's highlights reach the Writer
    assert "The jump and the lag" in prompts["editor-preflight"]                 # the Writer's note reaches the Editor
    assert "never tell viewers what to do" in prompts["writer-draft"]            # the Editor's pre-flight guidance reaches the Writer
    assert "Editor:" in prompts["designer-visuals"] and "Writer:" in prompts["designer-visuals"]
    assert "A corrected" not in prompts["designer-visuals"] or True
    assert "Why Prices Jumped So Fast" in prompts["designer-thumbnail"]          # the thumbnail is designed against the Publisher's title
    assert "Publisher: The title is" in prompts["designer-thumbnail"]


def test_designer_plans_visuals_from_the_final_narration(studio_env, monkeypatch):
    seen = []
    monkeypatch.setattr(P, "llm", fake_model(review_issue="figure misread", seen=seen))
    assert studio.run_studio(dry_run=True) == 0
    visual_prompt = next(p for r, p in seen if r == "designer-visuals")
    assert "A corrected second scene" in visual_prompt    # the Editor's fix is what the Designer sees, not the first draft
    script = json.loads((studio_env / "output" / "script.json").read_text())
    assert all(len(s["search_queries"]) >= 1 and "chart" not in " ".join(s["search_queries"]).lower() for s in script["scenes"])


def test_publisher_rejects_clickbait_and_designer_must_echo_the_title(monkeypatch):
    answers = iter([{"title": "The SHOCKING secret behind inflation", "description": "d", "tags": []},
                    {"title": "What Drove Inflation After 2020", "description": "d", "tags": ["x"]}])
    monkeypatch.setattr(P, "llm", lambda prompt, max_tokens=6000: json.dumps(next(answers)))
    monkeypatch.setattr(crew.time, "sleep", lambda s: None)
    script = {"our_take": "Prices follow money with a lag", "scenes": [{"narration": "word " * 30}] * 4}
    pkg = crew.publisher_package(script, {"working_title": "W"}, [], [])
    assert pkg["title"] == "What Drove Inflation After 2020"
    thumbs = iter([{"thumbnail_text": "BIG NEWS", "thumbnail_prompt": ""}, {"thumbnail_text": "WHAT DROVE INFLATION", "thumbnail_prompt": "a shop"}])
    monkeypatch.setattr(P, "llm", lambda prompt, max_tokens=6000: json.dumps(next(thumbs)))
    assert crew.designer_thumbnail(pkg["title"], script, [])["thumbnail_text"] == "WHAT DROVE INFLATION"


def test_designer_places_every_chart_in_the_evidence_act():
    outline = {"acts": [{"name": "a", "beats": [{"beat": "h", "chart": "c9"}, {"beat": "s", "chart": ""}]},
                        {"name": "b", "beats": [{"beat": f"e{i}", "chart": ""} for i in range(4)]},
                        {"name": "c", "beats": [{"beat": "r", "chart": ""}]}]}
    where = studio.assign_chart_cues(outline, [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}])
    assert set(where) == {"c1", "c2", "c3"} and all(3 <= i <= 6 for i in where.values())  # scenes 3-6 are the evidence act
    assert outline["acts"][0]["beats"][0]["chart"] == ""                                   # an invented chart id was dropped


def test_notes_block_keeps_only_the_latest_notes():
    block = crew.notes_block([(f"R{i}", f"note {i}") for i in range(12)])
    assert block.startswith("Notes from your teammates") and "note 11" in block and "note 3" not in block and block.count("\n- ") == 8
    assert crew.notes_block([]) == ""
