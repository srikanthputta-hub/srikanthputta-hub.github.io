"""The studio's language-model roles. Each is a small, single-purpose call with a strict JSON answer.

Prompts stay short on purpose: the free Groq tier allows about 8k tokens per minute per request.
"""
import json
import time

import pipeline
from . import data

POSITIONING = (
    "'The Money Blueprint' is a faceless YouTube channel that explains how money and the economy work using real public "
    "data (inflation, interest rates, money supply, debt, jobs, housing). It is educational storytelling. It NEVER gives "
    "personal financial advice, never tells viewers what to do with their money, never presents the narrator as an expert "
    "or adviser, and never predicts markets."
)


def json_call(prompt, max_tokens=3500, tries=3):
    err = None
    for _ in range(tries):
        try:
            text = pipeline.llm(prompt, max_tokens)
            return json.loads(text[text.index("{"): text.rindex("}") + 1])
        except RuntimeError:
            raise  # no key configured: retrying cannot help
        except Exception as e:
            err = e
            print("role call failed:", str(e)[:160])
            time.sleep(2)
    raise RuntimeError(f"role call failed after {tries} tries: {err}")


def plain(text):
    """Straight quotes and hyphens, normal spaces: keeps odd glyphs out of titles, callouts and speech."""
    table = {"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": " - ", "\u2018": "'", "\u2019": "'",
             "\u201c": '"', "\u201d": '"', "\u00a0": " ", "\u202f": " ", "\u2026": "..."}
    return " ".join("".join(table.get(ch, ch) for ch in str(text)).split())


def normalize_scene(s, charts_ids=()):
    qs = [q for q in (s.get("search_queries") or []) if isinstance(q, str) and q.strip()][:4]
    if not qs and s.get("search_query"):
        qs = [s["search_query"]]
    if not qs:
        qs = ["people walking through a busy city street", "close-up of hands writing in a notebook"]
    chart = s.get("chart") if s.get("chart") in charts_ids else ""
    return {"narration": plain(s.get("narration", "")), "search_queries": [plain(q) for q in qs], "search_query": plain(qs[0]),
            "callout": plain(s.get("callout") or "")[:40], "chart": chart}


def head_of_content(past_titles, ideas, perf):
    ideas_txt = "; ".join(i["title"] for i in ideas[:8]) or "none yet"
    prompt = f"""You are the Head of Content. {POSITIONING}
Audience: {pipeline.CFG['audience']}
Choose the next video: a 'why did this happen / how does this work' explainer that real public data can answer.
Already published (do not repeat or closely echo): {past_titles[-30:]}
Backlog ideas (use one if it fits): {ideas_txt}{perf}
Return ONLY JSON: {{"topic": "...", "working_title": "curiosity-driven, under 70 chars, no clickbait lies", "angle": "the specific question this video answers", "our_take": "the original insight or point of view viewers get only here (one or two sentences)", "idea": "backlog idea title used or empty"}}"""
    b = json_call(prompt, 1200)
    for k in ("topic", "working_title", "angle", "our_take"):
        if not str(b.get(k, "")).strip():
            raise RuntimeError(f"Head of Content returned no {k}")
        b[k] = plain(b[k])
    return b


def researcher(brief, only=None):
    prompt = f"""You are the Researcher. Video: "{brief['working_title']}" - angle: {brief['angle']}.
Pick 3 or 4 charts from ONLY these datasets (they are real; do not invent others):
{data.catalog_text(only)}
Return ONLY JSON: {{"charts": [{{"series": "catalog id", "countries": ["US"], "start_year": 2000, "title": "short chart title", "why": "what this chart shows, under 12 words"}}]}}
Use countries only for World Bank series. Choose start years that show the story (long enough for 15+ points)."""
    plan = json_call(prompt, 1200)
    plan["charts"] = [c for c in plan.get("charts", []) if isinstance(c, dict) and c.get("series") in data.CATALOG]
    return plan


def _outline(brief, sheet, n_scenes):
    prompt = f"""You are the Scriptwriter. {POSITIONING}
Video: "{brief['working_title']}". Angle: {brief['angle']}. Our take: {brief['our_take']}.
FACT SHEET (the only numbers you may quote):
{sheet}
Plan a {n_scenes}-scene video in 3 acts (hook and setup, the evidence, what it means). Beat 1 must hook with a surprising fact from the sheet. Spread the charts across the evidence act. The last beat is a neutral recap and a one-line invitation to subscribe.
Return ONLY JSON: {{"title": "under 70 chars", "description": "2 neutral sentences, no advice", "tags": ["8 tags"], "thumbnail_text": "2-4 words ALL CAPS", "thumbnail_prompt": "one concrete background image idea, no text, no faces", "our_take": "restated, one or two sentences", "acts": [{{"name": "short chapter title", "beats": [{{"beat": "what this scene explains", "chart": "c1 or empty"}}]}}]}}"""
    o = json_call(prompt, 2500)
    acts = [a for a in o.get("acts", []) if a.get("beats")]
    total = sum(len(a["beats"]) for a in acts)
    if len(acts) < 3 or abs(total - n_scenes) > 4:
        raise RuntimeError(f"outline has {len(acts)} acts / {total} beats")
    o["acts"] = acts
    return o


def write_script(brief, sheet, charts, n_scenes):
    ids = {c["id"] for c in charts}
    outline = None
    for attempt in range(3):
        try:
            outline = _outline(brief, sheet, n_scenes)
            break
        except RuntimeError as e:
            print("outline retry:", e)
            if attempt == 2:
                raise
    scenes, chapters, prev = [], [], ""
    for a_i, act in enumerate(outline["acts"]):
        beats = json.dumps(act["beats"])
        prompt = f"""You are the Scriptwriter. {POSITIONING}
Video: "{outline['title']}". Act {a_i + 1} of {len(outline['acts'])}: "{act['name']}".
FACT SHEET (only numbers you may quote; quote them exactly, rounding to a whole number is fine):
{sheet}
Story so far (last lines): {prev or 'this is the first act'}
Write one scene per beat, in order. Beats: {beats}
Rules: 3-4 spoken sentences per scene (about 70 words), calm and curious, plain words. Explain what happened and why; never tell viewers what to do, never claim expertise, no hype, no predictions. If a beat has a chart, say what the chart shows with exact numbers from the sheet.
search_queries: 3 concrete, filmable stock-footage searches (people, places, objects, actions). Never charts or graphics. callout: 5 words or fewer with a key number or term, or empty.
Return ONLY JSON: {{"scenes": [{{"narration": "...", "search_queries": ["...", "...", "..."], "callout": "", "chart": "c1 or empty"}}]}}"""
        got = None
        for _ in range(3):
            res = json_call(prompt, 4500)
            got = [s for s in res.get("scenes", []) if str(s.get("narration", "")).strip()]
            if len(got) >= len(act["beats"]) - 1:
                break
        if not got:
            raise RuntimeError(f"Scriptwriter returned no scenes for act {a_i + 1}")
        chapters.append({"title": plain(act["name"]), "scene": len(scenes)})
        scenes += [normalize_scene(s, ids) for s in got[:len(act["beats"])]]
        prev = " ".join(scenes[-1]["narration"].split()[-30:])
    return {"title": plain(outline["title"])[:95], "description": plain(outline["description"]), "tags": [plain(t) for t in outline.get("tags", [])[:12]],
            "thumbnail_text": plain(outline.get("thumbnail_text") or brief["working_title"][:24]),
            "thumbnail_prompt": plain(outline.get("thumbnail_prompt", "")), "our_take": plain(outline.get("our_take") or brief["our_take"]),
            "scenes": scenes, "chapters": chapters}


def _numbered(script):
    return "\n".join(f"{i + 1}. {s['narration']}" for i, s in enumerate(script["scenes"]))


def _issues(res, kind):
    out = []
    for it in res.get("issues", []) or []:
        try:
            out.append({"scene": int(it["scene"]), "type": kind, "problem": str(it.get("problem", ""))[:300],
                        "fix": str(it.get("fix", "")).strip()})
        except (KeyError, ValueError, TypeError):
            continue
    return out


def fact_check(script, sheet):
    prompt = f"""You are the Fact-checker. The FACT SHEET is the only numeric ground truth:
{sheet}
Script scenes:
{_numbered(script)}
Flag a scene only for real problems: a number that is not in the fact sheet or is misread, a claim you believe is false, or a cause stated as certain when economists dispute it. Do not nitpick style.
Return ONLY JSON: {{"verdict": "pass" or "revise", "issues": [{{"scene": 3, "problem": "...", "fix": "complete replacement narration for that scene"}}]}}. Use an empty issues list when the script is clean."""
    return _issues(json_call(prompt, 2500), "fact")


def policy_review(script):
    prompt = f"""You are the Policy Editor for a YouTube channel. YouTube will not monetize videos that give viewers personal financial advice, present the narrator as an expert or adviser, rely on hype or emotional manipulation, or are templated filler.
Script scenes:
{_numbered(script)}
Flag a scene only for: telling viewers what to do with money, advice dressed as explanation, claiming expertise, market predictions stated as fact, guarantees, hype, or one-sided political framing.
Return ONLY JSON: {{"verdict": "pass" or "revise", "issues": [{{"scene": 3, "problem": "...", "fix": "complete replacement narration for that scene"}}]}}. Use an empty issues list when the script is clean."""
    return _issues(json_call(prompt, 2500), "policy")


def fix_scenes(script, issues, sheet):
    """Apply reviewer fixes directly; ask the Scriptwriter to rewrite scenes whose issues came without a fix."""
    by_scene = {}
    for it in issues:
        if 1 <= it["scene"] <= len(script["scenes"]):
            by_scene.setdefault(it["scene"], []).append(it)
    need = {}
    for n, its in by_scene.items():
        fixes = [i["fix"] for i in its if len(i.get("fix", "")) >= 40]
        if len(fixes) == len(its):
            script["scenes"][n - 1]["narration"] = " ".join(fixes[0].split())
        else:
            need[n] = [i["problem"] for i in its]
    if need:
        flagged = "\n".join(f"{n}. PROBLEMS: {' | '.join(p)}\n   CURRENT: {script['scenes'][n - 1]['narration']}" for n, p in need.items())
        prompt = f"""You are the Scriptwriter revising flagged scenes. {POSITIONING}
FACT SHEET (only numbers you may quote):
{sheet}
Rewrite each flagged scene to remove its problems. Keep 3-4 sentences, calm and plain, explanatory, no advice.
{flagged}
Return ONLY JSON: {{"fixes": [{{"scene": 3, "narration": "..."}}]}}"""
        res = json_call(prompt, 3500)
        for f in res.get("fixes", []):
            try:
                n = int(f["scene"])
                if 1 <= n <= len(script["scenes"]) and len(str(f["narration"]).strip()) >= 40:
                    script["scenes"][n - 1]["narration"] = " ".join(str(f["narration"]).split())
            except (KeyError, ValueError, TypeError):
                continue
    return script
