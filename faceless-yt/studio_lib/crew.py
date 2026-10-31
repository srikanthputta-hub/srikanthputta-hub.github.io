"""The studio's language-model roles: Producer, Researcher, Writer, Editor, Designer, Publisher.

Each role is a small, single-purpose call with a strict JSON answer, and every call receives the team's running notes so
roles build on each other's work instead of working from scratch. Prompts stay short on purpose: the free Groq tier
allows about 8k tokens per minute per request.
"""
import json
import re
import time

import pipeline
from . import data

MIN_SCENE_WORDS = 50
CLICKBAIT = ("shocking", "you won't believe", "you wont believe", "secret", "insane", "destroy", "exposed", "crash is coming",
             "get rich", "millionaire", "guaranteed", "this one trick")
STOP = {"the", "and", "for", "why", "how", "what", "did", "does", "with", "that", "this", "from", "into", "your", "are", "was", "has", "have"}

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
    table = {"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": " - ", "‘": "'", "’": "'",
             "“": '"', "”": '"', " ": " ", " ": " ", "…": "..."}
    return " ".join("".join(table.get(ch, ch) for ch in str(text)).split())


def notes_block(notes):
    """The team's running notes, newest last, injected into every role's prompt so each builds on the others' work."""
    if not notes:
        return ""
    return "Notes from your teammates (use them):\n" + "\n".join(f"- {who}: {text}" for who, text in notes[-8:]) + "\n"


def normalize_scene(s, charts_ids=()):
    qs = [q for q in (s.get("search_queries") or []) if isinstance(q, str) and q.strip()][:4]
    if not qs and s.get("search_query"):
        qs = [s["search_query"]]
    if not qs:
        qs = ["people walking through a busy city street", "close-up of hands writing in a notebook"]
    chart = s.get("chart") if s.get("chart") in charts_ids else ""
    return {"narration": plain(s.get("narration", "")), "search_queries": [plain(q) for q in qs], "search_query": plain(qs[0]),
            "callout": plain(s.get("callout") or "")[:40], "chart": chart}


# ---------------------------------------------------------------- Producer
def producer_kickoff(past_titles, ideas, perf):
    ideas_txt = "; ".join(i["title"] for i in ideas[:8]) or "none yet"
    prompt = f"""You are the Producer of the channel. {POSITIONING}
Audience: {pipeline.CFG['audience']}
Choose the next video: a 'why did this happen / how does this work' explainer that real public data can answer. Public data is annual and runs to about 2023, so pick stories that data up to then can tell.
Already published (do not repeat or closely echo): {past_titles[-30:]}
Backlog ideas (use one if it fits): {ideas_txt}{perf}
Return ONLY JSON: {{"topic": "...", "working_title": "curiosity-driven, under 70 chars, no clickbait lies", "angle": "the specific question this video answers", "our_take": "the original insight or point of view viewers get only here (one or two sentences)", "note": "one sentence for the team: what the viewer should understand by the end and the hook to open with", "idea": "backlog idea title used or empty"}}"""
    b = json_call(prompt, 1200)
    for k in ("topic", "working_title", "angle", "our_take"):
        if not str(b.get(k, "")).strip():
            raise RuntimeError(f"Producer returned no {k}")
        b[k] = plain(b[k])
    b["note"] = plain(b.get("note") or b["angle"])
    return b


# ---------------------------------------------------------------- Researcher
def researcher_plan(brief, notes=(), only=None):
    prompt = f"""You are the Researcher. Video: "{brief['working_title']}" - angle: {brief['angle']}.
{notes_block(notes)}Pick 3 or 4 charts from ONLY these datasets (they are real; do not invent others):
{data.catalog_text(only)}
Return ONLY JSON: {{"charts": [{{"series": "catalog id", "countries": ["US"], "start_year": 2000, "title": "short chart title", "why": "what this chart shows, under 12 words"}}], "note": "one sentence for the Writer: the story these datasets tell (no numbers)"}}
Use countries only for World Bank series. Choose start years that show the story (long enough for 15+ points). World Bank data is annual and runs to about 2023, so frame the story as history."""
    plan = json_call(prompt, 1200)
    plan["charts"] = [dict(c, title=plain(c.get("title", "")), why=plain(c.get("why", ""))) for c in plan.get("charts", [])
                      if isinstance(c, dict) and c.get("series") in data.CATALOG]
    plan["note"] = plain(plan.get("note", ""))
    return plan


# ---------------------------------------------------------------- Writer + Editor: the pre-production meeting
def writer_outline(brief, sheet, n_scenes, notes=()):
    prompt = f"""You are the Writer. {POSITIONING}
Video: "{brief['working_title']}". Angle: {brief['angle']}. Our take: {brief['our_take']}.
{notes_block(notes)}FACT SHEET (the only numbers you may quote; the Highlight lines are the biggest moves, use them):
{sheet}
Plan a {n_scenes}-scene video in 3 acts (hook and setup, the evidence, what it means). Beat 1 must hook with a surprising fact from the sheet. Spread the charts across the evidence act. The last beat is a neutral recap and a one-line invitation to subscribe.
Return ONLY JSON: {{"our_take": "restated, one or two sentences", "note": "one sentence for the Editor and Designer: the moments that matter most", "acts": [{{"name": "short chapter title", "beats": [{{"beat": "what this scene explains", "chart": "c1 or empty"}}]}}]}}"""
    o = json_call(prompt, 2500)
    acts = [a for a in o.get("acts", []) if a.get("beats")]
    total = sum(len(a["beats"]) for a in acts)
    if len(acts) < 3 or abs(total - n_scenes) > 4:
        raise RuntimeError(f"outline has {len(acts)} acts / {total} beats")
    o["acts"] = acts
    o["note"] = plain(o.get("note", ""))
    return o


def editor_preflight(outline, past_titles, notes=()):
    """The Editor reads the plan before the expensive script is written and steers the Writer away from trouble."""
    beats = "\n".join(f"{i + 1}. {b['beat']}" for i, b in enumerate(b for a in outline["acts"] for b in a["beats"]))
    prompt = f"""You are the Editor. {POSITIONING}
YouTube will not monetize advice, expert personas, hype, or repetitive templated videos.
{notes_block(notes)}Earlier videos on this channel: {past_titles[-20:]}
The Writer's plan (one beat per scene):
{beats}
Flag only beats that drift into advice, predictions, hype, or retell an earlier video. Then give the Writer one short instruction to keep the script safe and distinctive.
Return ONLY JSON: {{"issues": [{{"beat": 4, "problem": "..."}}], "guidance": "one or two sentences for the Writer"}}. Use an empty issues list when the plan is fine."""
    res = json_call(prompt, 1200)
    return {"issues": [i for i in res.get("issues", []) if isinstance(i, dict)], "guidance": plain(res.get("guidance", ""))}


def writer_draft(brief, outline, sheet, charts, guidance="", notes=()):
    ids = {c["id"] for c in charts}
    scenes, chapters, prev = [], [], ""
    for a_i, act in enumerate(outline["acts"]):
        beats = json.dumps([b["beat"] for b in act["beats"]])
        prompt = f"""You are the Writer. {POSITIONING}
Video: "{brief['working_title']}". Act {a_i + 1} of {len(outline['acts'])}: "{act['name']}".
{notes_block(notes)}{('Editor guidance: ' + guidance + chr(10)) if guidance else ''}FACT SHEET (only numbers you may quote; quote them exactly, rounding to a whole number is fine):
{sheet}
Story so far (last lines): {prev or 'this is the first act'}
Write one scene per beat, in order. Beats: {beats}
Rules: each scene is 4 spoken sentences, 60 to 80 words (count them; short scenes are rejected). Calm and curious, plain words. Explain what happened and why; never tell viewers what to do, never claim expertise, no hype, no predictions. When a beat is about a chart, say what the chart shows with exact numbers from the sheet.
Return ONLY JSON: {{"scenes": [{{"narration": "..."}}]}}"""
        got, best_avg, ask = None, -1, prompt
        for _ in range(3):
            res = json_call(ask, 4500)
            cand = [s for s in res.get("scenes", []) if str(s.get("narration", "")).strip()]
            if len(cand) < len(act["beats"]) - 1:
                continue
            avg = sum(len(str(s["narration"]).split()) for s in cand) / len(cand)
            if avg > best_avg:
                got, best_avg = cand, avg
            if avg >= MIN_SCENE_WORDS:
                break
            ask = prompt + f"\nIMPORTANT: your last attempt averaged only {avg:.0f} words per scene. Write 4 full sentences and 60 to 80 words in every scene."
        if not got:
            raise RuntimeError(f"Writer returned no scenes for act {a_i + 1}")
        chapters.append({"title": plain(act["name"]), "scene": len(scenes)})
        for s, beat in zip(got, act["beats"]):
            scenes.append(normalize_scene({"narration": s["narration"], "chart": beat.get("chart")}, ids))
        prev = " ".join(scenes[-1]["narration"].split()[-30:])
    return {"title": plain(brief["working_title"])[:95], "our_take": plain(outline.get("our_take") or brief["our_take"]),
            "scenes": scenes, "chapters": chapters}


# ---------------------------------------------------------------- Editor: review, then the Writer revises
def _numbered(script):
    return "\n".join(f"{i + 1}. {s['narration']}" for i, s in enumerate(script["scenes"]))


def editor_review(script, sheet, notes=()):
    prompt = f"""You are the Editor. {POSITIONING}
YouTube will not monetize videos that give personal financial advice, present the narrator as an expert or adviser, rely on hype or emotional manipulation, or are templated filler.
{notes_block(notes)}The FACT SHEET is the only numeric ground truth:
{sheet}
Script scenes:
{_numbered(script)}
Flag a scene only for a real problem. kind "fact": a number that is not in the fact sheet or is misread, a claim you believe is false, or a cause stated as certain when economists dispute it. kind "policy": telling viewers what to do with money, advice dressed as explanation, claiming expertise, a market prediction stated as fact, a guarantee, hype, or one-sided political framing. Do not nitpick style.
Return ONLY JSON: {{"issues": [{{"scene": 3, "kind": "fact" or "policy", "problem": "...", "fix": "complete replacement narration for that scene"}}]}}. Use an empty issues list when the script is clean."""
    res = json_call(prompt, 3000)
    out = []
    for it in res.get("issues", []) or []:
        try:
            kind = "policy" if str(it.get("kind", "fact")).lower().startswith("pol") else "fact"
            out.append({"scene": int(it["scene"]), "type": kind, "problem": str(it.get("problem", ""))[:300], "fix": str(it.get("fix", "")).strip()})
        except (KeyError, ValueError, TypeError):
            continue
    return out


def fix_scenes(script, issues, sheet, allowed=None, notes=()):
    """The Writer revises: apply the Editor's complete fixes directly; rewrite scenes whose issues came without one."""
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
        figures = ("Figures you may quote, and nothing else: " + ", ".join(f"{a:,.1f}" for a in (allowed or [])[:60]) + "\n") if allowed else ""
        flagged = "\n".join(f"{n}. PROBLEMS: {' | '.join(p)}\n   CURRENT: {script['scenes'][n - 1]['narration']}" for n, p in need.items())
        prompt = f"""You are the Writer revising scenes the Editor flagged. {POSITIONING}
{notes_block(notes)}FACT SHEET (only numbers you may quote):
{sheet}
Rewrite each flagged scene to remove its problems. Keep 4 sentences (60 to 80 words), calm and plain, explanatory, no advice.
{figures}If a scene contains any other figure, delete the figure and say it in words instead (for example "a small share").
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


# ---------------------------------------------------------------- Designer: visuals from the finished narration
def designer_visuals(script, charts, notes=(), chunk=9):
    ids = {c["id"] for c in charts}
    chart_txt = "; ".join(f"{c['id']} = {c['title']} ({c['why'] or c['unit']})" for c in charts)
    for start in range(0, len(script["scenes"]), chunk):
        part = script["scenes"][start:start + chunk]
        lines = "\n".join(f"{start + i + 1}. [outline suggests chart: {s['chart'] or 'none'}] {' '.join(s['narration'].split()[:42])}" for i, s in enumerate(part))
        prompt = f"""You are the Designer for a faceless explainer channel. You plan the visuals from the finished narration so the picture always matches what is being said.
{notes_block(notes)}Animated charts available: {chart_txt}
Scenes (first words of each narration):
{lines}
For each scene return: chart (a chart id only when the narration is about that data, otherwise empty; use every chart once), search_queries (3 concrete, filmable stock-footage searches: real people, places, objects and actions that illustrate what is said; never charts, graphics or abstract ideas), callout (5 words or fewer carrying the key number or term, or empty; about half the scenes).
Return ONLY JSON: {{"scenes": [{{"scene": 1, "chart": "c1 or empty", "search_queries": ["...", "...", "..."], "callout": ""}}]}}"""
        res = json_call(prompt, 2800)
        for v in res.get("scenes", []):
            try:
                i = int(v["scene"]) - 1
            except (KeyError, ValueError, TypeError):
                continue
            if 0 <= i < len(script["scenes"]):
                sc = script["scenes"][i]
                new = normalize_scene({"narration": sc["narration"], "search_queries": v.get("search_queries"), "callout": v.get("callout"),
                                       "chart": v.get("chart") or sc.get("chart")}, ids)
                sc.update({k: new[k] for k in ("search_queries", "search_query", "callout", "chart")})
    return script


# ---------------------------------------------------------------- Publisher then Designer: the packaging meeting
def title_issues(title):
    t = title.lower()
    problems = []
    if len(title) > 70:
        problems.append("longer than 70 characters")
    if title.isupper() and len(title) > 12:
        problems.append("all capitals")
    for w in CLICKBAIT:
        if w in t:
            problems.append(f"clickbait wording ({w})")
    return problems


def publisher_package(script, brief, past_titles, notes=()):
    summary = " ".join(script["scenes"][0]["narration"].split()[:40]) + " ... " + " ".join(script["scenes"][len(script["scenes"]) // 2]["narration"].split()[:40])
    ask, last = "", None
    for _ in range(2):
        prompt = f"""You are the Publisher of a faceless explainer channel. Package the finished video so it earns clicks honestly and is found in search.
{notes_block(notes)}Working title: "{brief['working_title']}". Our take: {script['our_take']}
What the video says: {summary}
Earlier titles (be distinct): {past_titles[-15:]}
Title rules: under 70 characters, curiosity-driven but true to the video, include the main topic words people search for, no clickbait, not all capitals.{ask}
Return ONLY JSON: {{"title": "...", "description": "2 neutral sentences, no advice", "tags": ["8 search tags"]}}"""
        last = json_call(prompt, 900)
        title = plain(last.get("title", ""))
        problems = title_issues(title)
        if title and not problems:
            break
        ask = f"\nYour last title was rejected: {', '.join(problems) or 'empty'}. Write a better one."
    title = plain(last.get("title", "")) or plain(brief["working_title"])
    if title_issues(title):
        title = plain(brief["working_title"])[:70]
    return {"title": title[:95], "description": plain(last.get("description", "")) or script["our_take"],
            "tags": [plain(t) for t in (last.get("tags") or [])[:12]]}


def keywords(text):
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in STOP}


def designer_thumbnail(title, script, notes=()):
    """The Designer builds the thumbnail to make the same promise as the Publisher's title."""
    ask = ""
    for _ in range(2):
        prompt = f"""You are the Designer. Design the thumbnail for a video titled "{title}". Its point: {script['our_take']}
{notes_block(notes)}The thumbnail text and the title must make the same promise: the text has to repeat a key word from the title.{ask}
Return ONLY JSON: {{"thumbnail_text": "2-4 words ALL CAPS, no punctuation", "thumbnail_prompt": "one concrete background image idea, no text, no faces"}}"""
        res = json_call(prompt, 600)
        text = plain(res.get("thumbnail_text", "")).upper()
        if 1 <= len(text.split()) <= 5 and keywords(text) & keywords(title):
            return {"thumbnail_text": text, "thumbnail_prompt": plain(res.get("thumbnail_prompt", ""))}
        ask = f"\nYour last text ({text or 'empty'}) did not repeat a key word from the title. Try again."
    words = [w for w in re.findall(r"[A-Za-z]{4,}", title) if w.lower() not in STOP][:3]
    return {"thumbnail_text": " ".join(words).upper() or "WHAT HAPPENED", "thumbnail_prompt": plain(res.get("thumbnail_prompt", ""))}
