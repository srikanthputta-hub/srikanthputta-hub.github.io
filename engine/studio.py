#!/usr/bin/env python3
"""The Money Blueprint studio. Runs the whole production line, role by role, behind QA gates.

  python studio.py --dry-run   # everything except the upload
  python studio.py             # publishes only if every gate passes; otherwise nothing ships

Eight desks, one shared set of team notes, and three meetings:
  Producer -> Researcher -> [pre-production meeting: Writer outlines, Editor pre-flights, Designer places the charts]
  -> Writer drafts <-> Editor reviews -> Designer plans visuals from the finished narration -> Post-Production cuts the video
  -> [packaging meeting: Publisher writes the title, Designer makes the thumbnail match it] -> Producer's final go / no-go.
"""
import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone

import pipeline
from studio_lib import charts as chart_lib
from studio_lib import crew, data, gates, office
from studio_lib import state as state_lib

LIBRARY = pipeline.HERE / "library"
STAGES = 9
ABSTRACT = ("graph", "chart", "infographic", "animation", "illustration", "concept", "symbol", "abstract", "icon", "diagram")
FALLBACK_QUERIES = ["people walking through a busy city street", "close-up of hands writing in a notebook",
                    "empty office desk with a laptop", "aerial view of a city at dusk"]


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


def load_library():
    out = []
    for f in sorted(LIBRARY.glob("*.json")) if LIBRARY.exists() else []:
        try:
            j = json.loads(f.read_text())
            out.append({"title": j["title"], "narration": j["narration"], "opener": j.get("opener", "")})
        except Exception:
            continue
    return out


def assign_chart_cues(outline, charts):
    """Designer, in the pre-production meeting: every chart gets a beat, spread across the evidence act. Returns a note."""
    valid = {c["id"] for c in charts}
    beats = [b for a in outline["acts"] for b in a["beats"]]
    for b in beats:
        if b.get("chart") not in valid:
            b["chart"] = ""
    used = {b["chart"] for b in beats if b["chart"]}
    evidence = outline["acts"][1]["beats"] if len(outline["acts"]) > 1 else beats
    free = [b for b in evidence if not b["chart"]] or [b for b in beats[1:] if not b["chart"]]
    for c in charts:
        if c["id"] not in used and free:
            b = free.pop(len(free) // 2)
            b["chart"] = c["id"]
            used.add(c["id"])
    where = {b["chart"]: i + 1 for i, b in enumerate(beats) if b["chart"]}
    return where


def direct(script, charts):
    """Designer's last check: valid chart cues, every chart used, concrete b-roll queries only."""
    valid = {c["id"] for c in charts}
    for s in script["scenes"]:
        if s.get("chart") not in valid:
            s["chart"] = ""
        qs = [q for q in s["search_queries"] if not any(w in q.lower() for w in ABSTRACT)]
        s["search_queries"] = qs or FALLBACK_QUERIES[:3]
        s["search_query"] = s["search_queries"][0]
    used = {s["chart"] for s in script["scenes"] if s["chart"]}
    free = [i for i, s in enumerate(script["scenes"]) if not s["chart"] and i > 0]
    for c in charts:
        if c["id"] not in used and free:
            idx = free.pop(len(free) // 2)
            script["scenes"][idx]["chart"] = c["id"]
    return script


def build_description(script, charts, durs):
    parts = [script["description"].strip()]
    chap = gates.build_chapters(script.get("chapters", []), durs)
    if chap:
        parts.append("Chapters\n" + "\n".join(chap))
    src = sorted({f"{c['source']}, series {c['series_code']}" for c in charts})
    parts.append("Data sources\n" + "\n".join(f"- {s}" for s in src))
    parts.append("About this channel: educational explainers about how money and the economy work, built from public data. "
                 "Narration is AI-generated and the research and script are produced by an automated studio with human oversight. "
                 "This video is for education only and is not financial advice.")
    links = "\n".join(f"{l['label']}: {l['url']}" for l in pipeline.CFG.get("affiliate_links", []))
    return "\n\n".join(parts) + (f"\n\n{links}\n(Some links may be affiliate links.)" if links else "")


def block(log, reason):
    log.stage("producer", "blocked", reason)
    log.finish("blocked", reason)
    return 0


def run_studio(dry_run):
    cfg = pipeline.CFG
    if str(cfg.get("status", "")).startswith("scaffold"):
        raise SystemExit(f"Channel '{pipeline.CHANNEL}' is scaffolded only: its source adapters are not built yet (see docs/ROADMAP.md).")
    log = state_lib.RunLog(dry_run)
    log.board = office.Board(pipeline=f"video-{log.data['id']}", total=STAGES)
    log.board.reset()
    try:
        code = _run(log, dry_run, cfg)
    except Exception as e:
        log.board.fail_active(f"{type(e).__name__}: {e}"[:200])
        log.stage("producer", "failed", f"{type(e).__name__}: {e}")
        log.finish("failed", f"{type(e).__name__}: {e}")
        raise
    finally:
        pipeline.OUT.mkdir(exist_ok=True)
        (pipeline.OUT / "report.md").write_text(state_lib.report_text(log))
        state_lib.write_state(log)
    return code


def _run(log, dry_run, cfg):
    shutil.rmtree(pipeline.OUT / "work", ignore_errors=True)
    history = pipeline.load_history()
    titles = [h["title"] for h in history]
    ideas = [i for i in (json.loads(pipeline.IDEAS.read_text()) if pipeline.IDEAS.exists() else []) if not i.get("used")]
    perf = pipeline.performance_notes(history)
    notes = []  # the team's running notes: every role reads them and adds to them

    # 1. Producer: topic, angle and the point of view
    log.begin("producer", "choosing the next topic and angle", 1)
    brief = crew.producer_kickoff(titles, sorted(ideas, key=lambda i: -i.get("score", 0)), perf)
    notes.append(("Producer", brief["note"]))
    log.set(topic=brief["topic"])
    log.stage("producer", "done", brief["working_title"])

    # 2. Researcher: real data and the fact sheet
    log.begin("researcher", "choosing datasets and downloading the data", 2)
    use_fred = data.fred_ok()
    if not use_fred:
        print("FRED is unreachable from this runner; using World Bank data only")
    plan = crew.researcher_plan(brief, notes, only=None if use_fred else "wb")
    charts, skipped = [], []

    def collect(specs):
        for spec in specs:
            if len(charts) >= 4:
                break
            try:
                charts.append(data.load_chart(spec, f"c{len(charts) + 1}"))
            except Exception as e:
                skipped.append(f"{spec.get('series')}: {str(e)[:80]}")

    collect(plan["charts"][:4])
    if len(charts) < 2:  # FRED can be unreachable from cloud runners: ask for World Bank-only charts instead
        print(f"only {len(charts)} chart(s) loaded; re-planning with World Bank data")
        collect(crew.researcher_plan(brief, notes, only="wb")["charts"][:4])
    ok = len(charts) >= 2
    log.gate("real_data", ok, f"{len(charts)} chart(s) from real data" + (f"; skipped {skipped}" if skipped else ""))
    log.stage("researcher", "done" if ok else "blocked", ", ".join(c["title"] for c in charts) or "no data")
    if not ok:
        return block(log, "not enough real data for a data-driven video")
    sheet, allowed = data.fact_sheet(charts)
    if plan.get("note"):
        notes.append(("Researcher", plan["note"]))
    notes.append(("Researcher", "The fact sheet has Highlight lines, the biggest move in each series: build the story around the strongest one."))

    # 3. Pre-production meeting: Writer outlines, Editor pre-flights the plan, Designer places the charts
    log.begin("writer", "outlining the video with the team", 3)
    log.begin("editor", "pre-flight check of the outline", 3)
    log.begin("designer", "placing the charts across the story", 3)
    outline, err = None, None
    for _ in range(3):
        try:
            outline = crew.writer_outline(brief, sheet, cfg["scenes"], notes)
            break
        except RuntimeError as e:
            err = e
            print("outline retry:", e)
    if outline is None:
        raise err
    if outline["note"]:
        notes.append(("Writer", outline["note"]))
    pre = crew.editor_preflight(outline, titles, notes)
    if pre["guidance"]:
        notes.append(("Editor", pre["guidance"]))
    where = assign_chart_cues(outline, charts)
    n_beats = sum(len(a["beats"]) for a in outline["acts"])
    log.stage("writer", "done", f"outline: {n_beats} scenes in {len(outline['acts'])} acts")
    log.stage("editor", "done", f"pre-flight: {len(pre['issues'])} concern(s) passed to the Writer")
    log.stage("designer", "done", "charts placed: " + ", ".join(f"{c} at scene {i}" for c, i in sorted(where.items())))
    log.gate("greenlight", len(where) == len(charts), f"outline approved: every chart has a scene; {len(pre['issues'])} pre-flight concern(s) handed to the Writer"
             if len(where) == len(charts) else "some charts have no scene")

    # 4. Writer drafts, act by act, with the Editor's guidance and the whole team's notes
    log.begin("writer", "writing the script act by act", 4)
    script = crew.writer_draft(brief, outline, sheet, charts, pre["guidance"], notes)
    words = gates.word_count(script)
    log.stage("writer", "done", f"{len(script['scenes'])} scenes, {words} words")
    problems = gates.structure_issues(script, cfg)
    log.gate("structure", not problems, "; ".join(problems) or f"{len(script['scenes'])} scenes, {words} words")
    if problems:
        return block(log, "script too thin: " + "; ".join(problems))

    # 5. Editor reviews, Writer revises (up to two rounds), then a last-resort scrub that only deletes
    issues = []
    for rnd in range(3):
        if rnd == 2:
            n = gates.scrub_unsupported(script, allowed)
            if n:
                print(f"scrubbed unsupported sentences from {n} scene(s)")
        auto = gates.number_issues(script, allowed) + gates.directive_issues(script)
        log.begin("editor", f"review round {rnd + 1}: figures, facts and policy", 5)
        review = crew.editor_review(script, sheet, notes)
        log.stage("editor", "done", f"round {rnd + 1}: {len(review)} issue(s) from the read, {len(auto)} automatic flag(s)")
        issues = auto + review
        if not issues:
            break
        if rnd < 2:
            log.begin("writer", f"revising {len(issues)} flagged note(s) from the Editor", 5)
            script = crew.fix_scenes(script, issues, sheet, allowed, notes)
            log.stage("writer", "done", "scenes revised")
    bad_facts = [i for i in issues if i["type"] in ("number", "fact")]
    bad_policy = [i for i in issues if i["type"] == "policy"]
    log.gate("numbers_and_facts", not bad_facts, "all figures trace to the fact sheet and the Editor found nothing" if not bad_facts
             else f"{len(bad_facts)} unresolved: " + "; ".join(i["problem"][:70] for i in bad_facts[:3]))
    log.gate("policy", not bad_policy, "no advice, expert persona or hype" if not bad_policy
             else f"{len(bad_policy)} unresolved: " + "; ".join(i["problem"][:70] for i in bad_policy[:3]))
    if bad_facts or bad_policy:
        return block(log, "script did not pass the Editor's review after 2 revisions")
    problems = gates.structure_issues(script, cfg)  # the review may have trimmed sentences: re-check the length
    log.gate("structure_after_review", not problems, "; ".join(problems) or f"{gates.word_count(script)} words after review")
    if problems:
        return block(log, "script too thin after review: " + "; ".join(problems))
    log.begin("editor", "comparing with published scripts", 5)
    problems = gates.originality_issues(script, load_library())
    log.gate("originality", not problems, "; ".join(problems) or "distinct from published scripts and has its own point of view")
    log.stage("editor", "done" if not problems else "blocked", "; ".join(problems) or "originality ok")
    if problems:
        return block(log, "not original enough: " + "; ".join(problems))
    notes.append(("Editor", "The script is cleared. Designer: keep visuals literal and concrete; no chart or graphic stock searches."))

    # 6. Designer plans the visuals from the finished narration, then animates the charts
    log.begin("designer", "planning visuals from the finished script", 6)
    script = crew.designer_visuals(script, charts, notes)
    script = direct(script, charts)
    work = pipeline.OUT / "work"
    work.mkdir(parents=True, exist_ok=True)
    rendered, by_id = 0, {c["id"]: c for c in charts}
    for cid in sorted({s["chart"] for s in script["scenes"] if s["chart"]}):
        try:
            vid = chart_lib.render_chart_video(by_id[cid], work / f"chart_{cid}.mp4", work, font_path=pipeline.find_font())
            for s in script["scenes"]:
                if s["chart"] == cid:
                    s["chart_video"] = str(vid)
            rendered += 1
        except Exception as e:
            print("chart render failed:", cid, str(e)[:120])
    log.stage("designer", "done" if rendered >= 2 else "blocked", f"{sum(1 for s in script['scenes'] if s['chart'])} chart scene(s), {rendered} animated chart(s)")
    if rendered < 2:
        log.gate("charts_rendered", False, f"only {rendered} chart(s) rendered")
        return block(log, "charts failed to render")

    # 7. Post-Production records and cuts the video
    log.begin("post_production", "recording the narration and cutting the video", 7)
    video = pipeline.render(script)
    durs = json.loads((work / "durs.json").read_text())
    minutes = sum(durs) / 60
    log.stage("post_production", "done", f"{len(durs)} scenes voiced and cut into {minutes:.1f} minutes")
    log.gate("length", minutes >= cfg.get("min_minutes", 6), f"{minutes:.1f} min (minimum {cfg.get('min_minutes', 6)})")
    if minutes < cfg.get("min_minutes", 6):
        return block(log, f"video too short ({minutes:.1f} min)")

    # 8. Packaging meeting: the Publisher writes the title, the Designer makes the thumbnail match it
    log.begin("publisher", "writing the title, description and chapters", 8)
    log.begin("designer", "designing the thumbnail to match the title", 8)
    pkg = crew.publisher_package(script, brief, titles, notes)
    notes.append(("Publisher", f"The title is: {pkg['title']}"))
    thumb_plan = crew.designer_thumbnail(pkg["title"], script, notes)
    script.update(title=pkg["title"], description=pkg["description"], tags=pkg["tags"], **thumb_plan)
    thumb = pipeline.make_thumbnail(video, script["thumbnail_text"], script["thumbnail_prompt"])
    problems = crew.title_issues(script["title"])
    if not (crew.keywords(script["thumbnail_text"]) & crew.keywords(script["title"])):
        problems.append("thumbnail text does not echo the title")
    log.gate("packaging", not problems, "; ".join(problems) or f"title and thumbnail make the same promise: {script['title']} / {script['thumbnail_text']}")
    script["description"] = build_description(script, charts, durs)
    log.stage("publisher", "done", f"title: {script['title']}")
    log.stage("designer", "done", f"thumbnail: {script['thumbnail_text']}")
    (pipeline.OUT / "script.json").write_text(json.dumps(script, indent=2))
    log.set(title=script["title"])
    if problems:
        return block(log, "packaging did not pass: " + "; ".join(problems))

    # 9. Producer's final call, then the Publisher uploads
    log.begin("producer", "final go / no-go" + (" (dry run)" if dry_run else ""), 9)
    if dry_run:
        log.stage("producer", "done", "every gate passed; dry run, nothing uploaded")
        log.finish("dry-run", "all gates passed; would publish")
        return 0
    log.stage("producer", "done", "every gate passed: go")
    log.begin("publisher", "uploading to YouTube", 9)
    vid = pipeline.upload(video, script, thumb)
    log.set(video_id=vid)
    log.stage("publisher", "done", f"published https://youtu.be/{vid}")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    LIBRARY.mkdir(exist_ok=True)
    narration = " ".join(s["narration"] for s in script["scenes"])
    (LIBRARY / f"{stamp}-{slug(script['title'])}.json").write_text(json.dumps(
        {"title": script["title"], "id": vid, "narration": narration, "opener": " ".join(script["scenes"][0]["narration"].lower().split()[:8])}, indent=2))
    pipeline.HISTORY.write_text(json.dumps(history + [{"title": script["title"], "id": vid, "date": stamp, "topic": brief["topic"]}], indent=2))
    if brief.get("idea"):
        try:
            pipeline.mark_used(brief["idea"])
        except Exception:
            pass
    log.finish("published", f"https://youtu.be/{vid}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    sys.exit(run_studio(ap.parse_args().dry_run))


if __name__ == "__main__":
    main()
