#!/usr/bin/env python3
"""The Money Blueprint studio. Runs the whole production line, role by role, behind QA gates.

  python studio.py --dry-run   # everything except the upload
  python studio.py             # publishes only if every gate passes; otherwise nothing ships

Roles and handoffs: Head of Content -> Researcher -> Data Analyst -> Scriptwriter -> Fact-checker / Policy Editor /
Originality Editor -> Director -> Chart Designer -> Voice Actor / Video Editor -> Thumbnail Designer -> Publisher -> Producer.
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


def direct(script, charts):
    """Director: valid chart cues, every chart used, concrete b-roll queries only."""
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
    log = state_lib.RunLog(dry_run)
    log.board = office.Board(pipeline=f"video-{log.data['id']}", total=14)
    log.board.reset()
    log.begin("ops", f"watching run {log.data['id']} ({log.data['mode']})")
    try:
        code = _run(log, dry_run, cfg)
        log.stage("ops", "done", f"run {log.data['status']}: {log.data['title'] or log.data['note'] or 'no title'}"[:200])
    except Exception as e:
        log.board.fail_active(f"{type(e).__name__}: {e}"[:200])
        log.stage("ops", "failed", f"{type(e).__name__}: {e}")
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
    ideas = [i for i in (json.loads(pipeline.IDEAS.read_text()) if pipeline.IDEAS.exists() else []) if not i.get("used")]
    perf = pipeline.performance_notes(history)

    log.begin("head_of_content", "choosing the next topic and angle", 1)
    brief = crew.head_of_content([h["title"] for h in history], sorted(ideas, key=lambda i: -i.get("score", 0)), perf)
    log.set(topic=brief["topic"])
    log.stage("head_of_content", "done", brief["working_title"])

    log.begin("researcher", "choosing real datasets to chart", 2)
    use_fred = data.fred_ok()
    if not use_fred:
        print("FRED is unreachable from this runner; using World Bank data only")
    plan = crew.researcher(brief, only=None if use_fred else "wb")
    log.stage("researcher", "done", f"{len(plan['charts'])} chart(s) planned")

    log.begin("data_analyst", "downloading FRED and World Bank data", 3)
    charts, notes = [], []

    def collect(specs):
        for spec in specs:
            if len(charts) >= 4:
                break
            try:
                charts.append(data.load_chart(spec, f"c{len(charts) + 1}"))
            except Exception as e:
                notes.append(f"{spec.get('series')}: {str(e)[:80]}")

    collect(plan["charts"][:4])
    if len(charts) < 2:  # FRED can be unreachable from cloud runners: ask for World Bank-only charts instead
        log.stage("researcher", "done", f"only {len(charts)} chart(s) loaded; re-planning with World Bank data")
        collect(crew.researcher(brief, only="wb")["charts"][:4])
    ok = len(charts) >= 2
    log.gate("real_data", ok, f"{len(charts)} chart(s) from real data" + (f"; skipped {notes}" if notes else ""))
    log.stage("data_analyst", "done" if ok else "blocked", ", ".join(c["title"] for c in charts) or "no data")
    if not ok:
        return block(log, "not enough real data for a data-driven video")
    sheet, allowed = data.fact_sheet(charts)

    log.begin("scriptwriter", "writing the outline and the script, act by act", 4)
    script = crew.write_script(brief, sheet, charts, cfg["scenes"])
    words = gates.word_count(script)
    log.stage("scriptwriter", "done", f"{len(script['scenes'])} scenes, {words} words")
    problems = gates.structure_issues(script, cfg)
    log.gate("structure", not problems, "; ".join(problems) or f"{len(script['scenes'])} scenes, {words} words")
    if problems:
        return block(log, "script too thin: " + "; ".join(problems))

    clean = False
    for rnd in range(3):
        issues = gates.number_issues(script, allowed) + gates.directive_issues(script)
        log.begin("fact_checker", f"review round {rnd + 1}: checking every figure and claim", 5)
        log.begin("policy_editor", f"review round {rnd + 1}: advice, persona and hype check", 6)
        fc, pe = crew.fact_check(script, sheet), crew.policy_review(script)
        log.stage("fact_checker", "done", f"round {rnd + 1}: {len(fc)} issue(s)")
        log.stage("policy_editor", "done", f"round {rnd + 1}: {len(pe)} issue(s), {len(issues)} automatic flag(s)")
        issues += fc + pe
        if not issues:
            clean = True
            break
        if rnd < 2:
            script = crew.fix_scenes(script, issues, sheet)
    log.gate("numbers_and_facts", clean, "all figures trace to the fact sheet and reviewers found nothing" if clean else f"{len(issues)} unresolved issue(s): " + "; ".join(i["problem"][:70] for i in issues[:3]))
    log.gate("policy", clean, "no advice, expert persona or hype" if clean else "unresolved policy issues")
    if not clean:
        return block(log, "script did not pass fact-check and policy review after 2 revisions")

    log.begin("originality_editor", "comparing with published scripts", 7)
    problems = gates.originality_issues(script, load_library())
    log.gate("originality", not problems, "; ".join(problems) or "distinct from published scripts and has its own point of view")
    log.stage("originality_editor", "done" if not problems else "blocked", "; ".join(problems) or "ok")
    if problems:
        return block(log, "not original enough: " + "; ".join(problems))

    log.begin("director", "assigning charts and b-roll", 8)
    script = direct(script, charts)
    log.stage("director", "done", f"{sum(1 for s in script['scenes'] if s['chart'])} chart scene(s)")

    work = pipeline.OUT / "work"
    work.mkdir(parents=True, exist_ok=True)
    log.begin("chart_designer", "animating the real data", 9)
    rendered = 0
    by_id = {c["id"]: c for c in charts}
    for cid in sorted({s["chart"] for s in script["scenes"] if s["chart"]}):
        try:
            vid = chart_lib.render_chart_video(by_id[cid], work / f"chart_{cid}.mp4", work, font_path=pipeline.find_font())
            for s in script["scenes"]:
                if s["chart"] == cid:
                    s["chart_video"] = str(vid)
            rendered += 1
        except Exception as e:
            print("chart render failed:", cid, str(e)[:120])
    log.stage("chart_designer", "done" if rendered >= 2 else "blocked", f"{rendered} animated chart(s)")
    if rendered < 2:
        log.gate("charts_rendered", False, f"only {rendered} chart(s) rendered")
        return block(log, "charts failed to render")

    log.begin("voice_actor", "recording the narration", 10)
    log.begin("video_editor", "cutting video, charts, captions and music", 11)
    video = pipeline.render(script)
    durs = json.loads((work / "durs.json").read_text())
    minutes = sum(durs) / 60
    log.stage("voice_actor", "done", f"{len(durs)} scenes voiced")
    log.stage("video_editor", "done", f"{minutes:.1f} minutes")
    log.gate("length", minutes >= cfg.get("min_minutes", 6), f"{minutes:.1f} min (minimum {cfg.get('min_minutes', 6)})")
    if minutes < cfg.get("min_minutes", 6):
        return block(log, f"video too short ({minutes:.1f} min)")

    log.begin("thumbnail_designer", "designing the thumbnail", 12)
    thumb = pipeline.make_thumbnail(video, script["thumbnail_text"], script.get("thumbnail_prompt"))
    log.stage("thumbnail_designer", "done", script["thumbnail_text"])
    log.begin("publisher", "adding chapters, sources and disclosure", 13)
    script["description"] = build_description(script, charts, durs)
    log.stage("publisher", "done", "chapters, sources and disclosure added")
    (pipeline.OUT / "script.json").write_text(json.dumps(script, indent=2))

    log.set(title=script["title"])
    if dry_run:
        log.begin("producer", "final go / no-go (dry run)", 14)
        log.stage("producer", "done", "all gates passed; dry run, nothing uploaded")
        log.finish("dry-run", "all gates passed; would publish")
        return 0

    log.begin("producer", "all gates passed: publishing", 14)
    vid = pipeline.upload(video, script, thumb)
    log.set(video_id=vid)
    log.stage("producer", "done", "all gates passed; published")
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
