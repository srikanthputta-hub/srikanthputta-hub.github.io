"""Programmatic QA gates: numbers, advice language, structure, originality, chapters. No model involved."""
import re

NUM_RE = re.compile(
    r"(?P<cur>[$£€])?\s?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?"
    r"(?P<unit>percentage points?|per cent|percent|%|trillion|billion|million|thousand)?", re.I)

# Phrases that turn an explainer into advice or an "AI expert" - exactly what YouTube says it won't monetize.
DIRECTIVE_PATTERNS = [
    r"\byou should\b", r"\byou must\b", r"\byou need to\b", r"\byou ought to\b", r"\byou('ll| will) want to\b",
    r"\bi (recommend|suggest|advise)\b", r"\bwe (recommend|suggest|advise)\b", r"\bmy advice\b", r"\bour advice\b",
    r"\bas an? (financial|investment|tax|money) (advis[eo]r|expert|planner|professional)\b", r"\bas an expert\b",
    r"\bguarantee[sd]?\b", r"\bget rich\b", r"\bfinancial freedom\b", r"\bnever lose\b", r"\brisk[- ]free\b",
    r"\b(buy|sell) (stocks?|shares|bitcoin|crypto|gold|bonds)\b", r"\binvest (in|your)\b", r"\bhere'?s what to do\b",
    r"\bwill (definitely|certainly) (rise|fall|crash|soar)\b", r"\bsecret (trick|method|formula)\b",
]
_DIRECTIVE = [re.compile(p, re.I) for p in DIRECTIVE_PATTERNS]


def audited_numbers(text):
    """Numbers that must trace to the fact sheet: currency amounts, percentages, scale words, and values >= 100 (not years)."""
    out = []
    for m in NUM_RE.finditer(text):
        raw = m.group("num")
        val = float(raw.replace(",", ""))
        unit, cur = (m.group("unit") or ""), m.group("cur")
        if not cur and not unit and "," not in raw and "." not in raw and 1800 <= val <= 2100:
            continue  # a year
        if cur or unit or val >= 100:
            out.append((m.group(0).strip(), val))
    return out


def number_ok(val, allowed):
    for a in allowed:
        for cand in (a, a * 1000, a / 1000):
            tol = max(0.5, 0.03 * abs(cand)) if abs(cand) >= 10 else max(0.15, 0.03 * abs(cand))
            if abs(val - cand) <= tol:
                return True
    return False


def number_issues(script, allowed):
    issues = []
    for i, sc in enumerate(script["scenes"]):
        bad = [tok for tok, val in audited_numbers(sc["narration"]) if not number_ok(val, allowed)]
        if bad:
            issues.append({"scene": i + 1, "type": "number",
                           "problem": f"figure(s) not in the fact sheet: {', '.join(bad)}. Quote only fact-sheet numbers or drop the figure."})
    return issues


def directive_issues(script):
    issues = []
    for i, sc in enumerate(script["scenes"]):
        hits = sorted({m.group(0).lower() for rx in _DIRECTIVE for m in rx.finditer(sc["narration"])})
        if hits:
            issues.append({"scene": i + 1, "type": "policy",
                           "problem": f"advice / expert-persona / hype language: {', '.join(hits)}. Rewrite as neutral explanation of what happened and why."})
    return issues


def word_count(script):
    return sum(len(sc["narration"].split()) for sc in script["scenes"])


def structure_issues(script, cfg):
    problems = []
    n, want = len(script["scenes"]), cfg["scenes"]
    if n < want - 3:
        problems.append(f"only {n} scenes (need about {want})")
    if word_count(script) < cfg.get("min_words", 1000):
        problems.append(f"only {word_count(script)} words (need at least {cfg.get('min_words', 1000)} for a video long enough to carry mid-roll ads)")
    if len(script["scenes"][0]["narration"].split()) < 20:
        problems.append("opening scene is too thin to hook viewers")
    return problems


def _ngrams(text, n=3):
    w = re.findall(r"[a-z0-9']+", text.lower())
    return {tuple(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def similarity(a, b):
    ga, gb = _ngrams(a), _ngrams(b)
    return len(ga & gb) / len(ga | gb) if ga and gb else 0.0


def originality_issues(script, library, threshold=0.20):
    """library: list of {"title","narration","opener"} from published videos."""
    problems = []
    text = " ".join(sc["narration"] for sc in script["scenes"])
    opener = " ".join(script["scenes"][0]["narration"].lower().split()[:8])
    for item in library:
        sim = similarity(text, item["narration"])
        if sim >= threshold:
            problems.append(f"too similar ({sim:.0%}) to '{item['title']}'")
        if opener and opener == item.get("opener"):
            problems.append(f"same opening line as '{item['title']}'")
    if len((script.get("our_take") or "").split()) < 8:
        problems.append("no clear original point of view (our_take is missing or too thin)")
    return problems


def clock(seconds):
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def build_chapters(chapters, durs, min_gap=12.0):
    """chapters: [{"title","scene"}] (scene = 0-based index of the chapter's first scene); durs: seconds per scene."""
    starts, t = [], 0.0
    for d in durs:
        starts.append(t)
        t += d
    out, last = [], -1e9
    for ch in chapters:
        i = ch.get("scene", 0)
        if i >= len(starts):
            continue
        if not out:
            out.append((0.0, ch["title"]))  # YouTube requires the first chapter at 0:00
            last = 0.0
        elif starts[i] - last >= min_gap:
            out.append((starts[i], ch["title"]))
            last = starts[i]
    return [f"{clock(s)} {title}" for s, title in out] if len(out) >= 3 else []
