#!/usr/bin/env python3
"""Strategy tools driven by the channel config.

  python strategy.py plan     # STRATEGY.md: positioning, pillars, schedule, growth, monetization roadmap
  python strategy.py ideas    # fill ideas.json up to 100 ranked video ideas (pipeline picks the top unused one)
  python strategy.py review   # REVIEW.md: weekly analytics review of published videos
"""
import json, sys
import pipeline as P


def ask(prompt, tokens=6000):
    return P.llm(prompt, tokens)


def ctx():
    c = P.CFG
    return (f"Channel: faceless (AI voiceover + stock footage), niche: {c['niche']}, format: {c['format']}, "
            f"goal: reach YouTube Partner Program (1,000 subs + 4,000 watch hours) and then maximize ad RPM plus affiliate income. "
            f"Fully automated, so no on-camera work, filming or live video.")


def plan():
    out = ask(f"""Act as an expert YouTube strategist and monetization consultant. {ctx()}
Write STRATEGY.md in markdown covering: 1) niche evaluation (audience, competitors, how to stand out, risks incl. YouTube's
policy on mass-produced content), 2) channel positioning and identity, ideal viewer, 3) content pillars, 4) publishing schedule,
5) algorithm and growth plan (CTR, retention, suggested/search traffic, testing), 6) monetization roadmap with requirements,
limitations and ideal timing for YPP ads, affiliate links, digital products, newsletter, sponsorships, and 7) a practical weekly action plan
for the human owner (what to review, tweak, never skip). Be specific and honest about timelines and income expectations.""", 8000)
    (P.HERE / "STRATEGY.md").write_text(out)


def ideas():
    path = P.IDEAS
    have = json.loads(path.read_text()) if path.exists() else []
    seen = {i["title"] for i in have} | {h["title"] for h in P.load_history()}
    for _ in range(4):
        if len(have) >= 100:
            break
        raw = ask(f"""Act as a YouTube market researcher. {ctx()}
Generate 40 NEW explainer ideas people actively search for or click: 'why did X happen', 'how does X work', 'what actually happened to X', 'X vs Y explained', myths about the economy. Each must be answerable with public economic data. No personal-finance advice or how-to-get-rich ideas.
Avoid duplicates of: {sorted(seen)[-60:]}
Return ONLY a JSON array: [{{"title": "...", "angle": "one line on the hook and value", "score": 1-10 for search demand x click appeal x low competition}}]""")
        for i in json.loads(raw[raw.index("["): raw.rindex("]") + 1]):
            if i["title"] not in seen:
                seen.add(i["title"])
                have.append({"title": i["title"], "angle": i.get("angle", ""), "score": i.get("score", 5)})
    have.sort(key=lambda i: -i["score"])
    path.write_text(json.dumps(have, indent=2))
    print(len(have), "ideas,", sum(1 for i in have if not i.get("used")), "unused")


def review():
    hist = P.load_history()
    P.performance_notes(hist)  # refreshes view counts in place
    out = ask(f"""Act as an experienced YouTube data analyst. {ctx()}
Published videos with view counts (some may be missing data): {json.dumps(hist[-30:])}
Write REVIEW.md: what patterns in titles/topics/angles correlate with views, why the weak ones likely underperformed, and 5 specific,
concrete changes to make to the title style, hooks, topics and schedule. Note plainly if the data is too small to conclude anything.""", 3000)
    (P.HERE / "REVIEW.md").write_text(out)


if __name__ == "__main__":
    {"plan": plan, "ideas": ideas, "review": review}[sys.argv[1]]()
