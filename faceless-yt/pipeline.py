#!/usr/bin/env python3
"""Faceless YouTube pipeline: topic -> script -> voice -> stock footage -> captions -> render -> upload.

Usage:
  python pipeline.py --dry-run      # everything except upload; footage replaced by gradients if no PEXELS_API_KEY
  python pipeline.py                # full run, uploads to YouTube
"""
import argparse, asyncio, json, os, random, re, subprocess, sys, time, urllib.parse
from pathlib import Path

import requests

HERE = Path(__file__).parent
CFG = json.loads((HERE / "config.json").read_text())
OUT = HERE / "output"
HISTORY = HERE / "history.json"
MODEL = "claude-sonnet-5-5"
_GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
_gemini_cache = {}


def pick_gemini_model(models):
    """Newest plain 'gemini-X-flash' that supports generateContent; else any other text flash model."""
    ok = [m["name"].split("/")[-1] for m in models if "generateContent" in m.get("supportedGenerationMethods", [])]
    def version(n):
        m = re.fullmatch(r"gemini-(\d+(?:\.\d+)?)-flash", n)
        return float(m.group(1)) if m else None
    plain = sorted((n for n in ok if version(n) is not None), key=version, reverse=True)
    if plain:
        return plain[0]
    skip = ("image", "tts", "live", "audio", "embedding", "lite", "thinking", "robotics", "computer")
    flash = sorted(n for n in ok if "flash" in n and not any(s in n for s in skip))
    if flash:
        return flash[-1]
    raise RuntimeError(f"No usable Gemini flash model in this account's list: {ok[:15]}")


def gemini_model(key):
    if os.environ.get("GEMINI_MODEL"):  # explicit override wins
        return os.environ["GEMINI_MODEL"]
    if "m" not in _gemini_cache:
        r = requests.get(f"{_GEMINI_API}/models", headers={"x-goog-api-key": key}, params={"pageSize": 200}, timeout=30)
        r.raise_for_status()
        _gemini_cache["m"] = pick_gemini_model(r.json().get("models", []))
        print("Gemini model:", _gemini_cache["m"])
    return _gemini_cache["m"]


SIZE = (1080, 1920) if CFG["format"] == "short" else (1920, 1080)


def _gemini(prompt, max_tokens, key):
    r = requests.post(
        f"{_GEMINI_API}/models/{gemini_model(key)}:generateContent",
        headers={"x-goog-api-key": key}, timeout=180,
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.8}})
    if not r.ok:
        raise ValueError(f"Gemini {r.status_code} retry-after={r.headers.get('retry-after', '')}: {r.text[:600]}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def pick_groq_model(models):
    """Largest active text-chat model (any family); GROQ_MODEL overrides. Lists ids when nothing fits."""
    skip = ("whisper", "tts", "guard", "safeguard", "embed", "orpheus", "compound", "vision", "distil")
    sized, unsized = [], []
    for m in models:
        mid = m["id"]
        if m.get("active", True) is False or any(s in mid.lower() for s in skip):
            continue
        size = re.search(r"(?<![\d.])(\d+)b(?![a-z])", mid.lower())
        if size:
            sized.append((int(size.group(1)), "versatile" in mid or "instruct" in mid, mid))
        else:
            unsized.append(mid)
    if sized:
        return max(sized)[2]
    if unsized:
        return sorted(unsized)[-1]
    raise RuntimeError("No usable chat model on this Groq account; models seen: " + ", ".join(m["id"] for m in models))


def _groq(prompt, max_tokens, key):
    h = {"Authorization": f"Bearer {key}"}
    model = os.environ.get("GROQ_MODEL")
    if not model:
        r = requests.get("https://api.groq.com/openai/v1/models", headers=h, timeout=30)
        if not r.ok:
            raise ValueError(f"Groq {r.status_code}: {r.text[:300]}")
        model = pick_groq_model(r.json()["data"])
        print("Groq model:", model)
    r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=h, timeout=180,
                      json={"model": model, "max_tokens": min(max_tokens, 8000), "temperature": 0.8,
                            "messages": [{"role": "user", "content": prompt}]})
    if not r.ok:
        raise ValueError(f"Groq {r.status_code} retry-after={r.headers.get('retry-after', '')}: {r.text[:600]}")
    return r.json()["choices"][0]["message"]["content"]


def _claude(prompt, max_tokens, key):
    import anthropic
    m = anthropic.Anthropic(api_key=key).messages.create(model=MODEL, max_tokens=max_tokens,
                                                         messages=[{"role": "user", "content": prompt}])
    return m.content[0].text


_dead_providers = set()


def rate_limit_wait(msg):
    """Seconds to wait if this provider error is a 429 rate limit, else None."""
    if not re.match(r"\w+ 429", msg):
        return None
    ra = re.search(r"retry-after=([\d.]+)", msg)
    if ra:
        return min(float(ra.group(1)) + 1, 70)
    t = re.search(r"try again in (?:(\d+)m)?([\d.]+)(ms|s)", msg)
    if t:
        secs = float(t.group(2)) / (1000 if t.group(3) == "ms" else 1) + 60 * int(t.group(1) or 0)
        return min(secs + 1, 70)
    return 20


def llm(prompt, max_tokens=6000):
    """Tries every provider that has a key, free ones first: Gemini, Groq, then Claude (paid, optional).
    Rate limits (429) wait and retry the same provider; out-of-credit/auth errors skip it for the rest of the run."""
    providers = [(n, f, os.environ.get(k)) for n, f, k in
                 (("Gemini", _gemini, "GEMINI_API_KEY"), ("Groq", _groq, "GROQ_API_KEY"),
                  ("Claude", _claude, "ANTHROPIC_API_KEY")) if os.environ.get(k)]
    if not providers:
        raise RuntimeError("No LLM key found: set GEMINI_API_KEY or GROQ_API_KEY (both free), or ANTHROPIC_API_KEY (paid)")
    errors = []
    for name, fn, key in providers:
        if name in _dead_providers:
            continue
        for attempt in range(4):
            try:
                return fn(prompt, max_tokens, key)
            except Exception as e:
                msg = str(e)
                wait = rate_limit_wait(msg)
                if wait is not None and attempt < 3:
                    print(f"{name} rate limited; waiting {wait:.0f}s then retrying")
                    time.sleep(wait)
                    continue
                if re.match(r"\w+ (401|402|403)", msg):
                    _dead_providers.add(name)  # no credit / bad key: don't waste calls on it again this run
                print(f"{name} failed, trying next provider: {msg[:200]}")
                errors.append(f"{name}: {msg[:200]}")
                break
    raise ValueError("all LLM providers failed: " + (" | ".join(errors) or "every provider was skipped"))


def sh(*cmd):
    subprocess.run([str(c) for c in cmd], check=True, capture_output=True)


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


# ---------- 1. idea + script ----------
def load_history():
    h = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    return [x if isinstance(x, dict) else {"title": x} for x in h]


IDEAS = HERE / "ideas.json"


def next_idea():
    ideas = json.loads(IDEAS.read_text()) if IDEAS.exists() else []
    open_ = [i for i in ideas if not i.get("used")]
    return max(open_, key=lambda i: i.get("score", 0)) if open_ else None


def mark_used(title):
    ideas = json.loads(IDEAS.read_text())
    for i in ideas:
        if i["title"] == title:
            i["used"] = True
    IDEAS.write_text(json.dumps(ideas, indent=2))


def yt_client():
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
                        client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"],
                        token_uri="https://oauth2.googleapis.com/token")
    try:
        creds.refresh(Request())  # fail early, before any rendering work is wasted
    except RefreshError as e:
        raise RuntimeError(
            "YouTube refresh token rejected (expired or revoked). Apps in Google 'Testing' mode expire tokens "
            "every 7 days. Re-run get_refresh_token.py and update the YT_REFRESH_TOKEN secret. "
            f"Details: {e}")
    return build("youtube", "v3", credentials=creds)


def performance_notes(history):
    """Refresh view counts for published videos; return a prompt snippet on what's working."""
    ids = [h["id"] for h in history if h.get("id")][-50:]
    if not ids or "YT_REFRESH_TOKEN" not in os.environ:
        return ""
    try:
        items = yt_client().videos().list(part="statistics", id=",".join(ids)).execute()["items"]
        views = {i["id"]: int(i["statistics"].get("viewCount", 0)) for i in items}
    except Exception as e:  # never block a run on analytics
        print("analytics skipped:", e)
        return ""
    for h in history:
        if h.get("id") in views:
            h["views"] = views[h["id"]]
    ranked = sorted((h for h in history if "views" in h), key=lambda h: -h["views"])
    if len(ranked) < 3:
        return ""
    top = "; ".join(f"{h['title']} ({h['views']} views)" for h in ranked[:3])
    low = "; ".join(f"{h['title']} ({h['views']} views)" for h in ranked[-2:])
    return (f"\nPerformance so far. Best: {top}. Worst: {low}. "
            "Lean into the angles, topic types and title styles of the best; avoid those of the worst.")


def write_script(history, idea=None):
    assigned = (f"\nASSIGNED TOPIC (write this video; you may sharpen the title): {idea['title']} "
                f"- angle: {idea.get('angle', '')}") if idea else ""
    past = [h["title"] for h in history]
    perf = performance_notes(history)
    n = CFG["scenes"]
    length = "under 55 seconds total" if CFG["format"] == "short" else "about 6 minutes total"
    sent = "1-2 spoken sentences" if CFG["format"] == "short" else "3-4 spoken sentences"
    prompt = f"""You run a faceless YouTube channel about: {CFG['niche']}.
Audience: {CFG['audience']}.
Tone: {CFG['tone']}. Format: {CFG['format']} ({length}).
Already published (do not repeat): {past[-40:]}{perf}{assigned}{assigned}

Pick one fresh, specific, factually solid topic and write the video. Only state facts you are confident are true. Pick topics people actually search for (e.g. "how X works", "X vs Y", "mistakes to avoid"). Use rounded illustrative examples, never claim guaranteed returns, no specific stock/crypto picks. Title must promise a concrete payoff; description must start with the main keyword and end with "This video is educational, not financial advice."
Retention rules: scene 1 hooks within 5 seconds (a surprising number, a bold claim or a relatable problem; never 'in this video'), then opens a loop it pays off later. Every 2-3 scenes add a pattern interrupt (a question, a contrast, a concrete example). Deliver real value, with specific numbers and steps, not filler. The last scene gives a payoff recap and a one-line subscribe call to action.
Return ONLY JSON:
{{"title": "<=70 chars, curiosity-driven", "description": "2-3 sentences + hashtags", "tags": ["..."], "thumbnail_text": "2-4 punchy words, ALL CAPS, no punctuation", "thumbnail_prompt": "one vivid background image idea for the thumbnail: a simple concrete subject, strong colour contrast, no text, no faces",
 "scenes": [{{"narration": "{sent}", "search_queries": ["3 different stock-footage searches for this scene"], "callout": "on-screen text or empty string"}}]}}
search_queries rules: concrete, FILMABLE subjects only: real people, places, objects and actions (e.g. "woman counting cash at kitchen table", "city skyline at night", "hands typing on laptop"). Never abstract ideas, symbols, charts, graphics or animations.
callout rules: for about half the scenes, a punchy on-screen phrase of at most 5 words that carries the key number or term (e.g. "$200/MONTH = $250K", "RULE OF 72", "AVOID THIS TRAP"); otherwise "".
Exactly {n} scenes."""
    script = ask_json(prompt, CFG["scenes"])
    return fact_check(script)


def ask_json(prompt, n_scenes=None, tries=3):
    """Call the LLM for a JSON object; retry on malformed output or wrong scene count."""
    err = None
    for _ in range(tries):
        try:
            text = llm(prompt)
            data = json.loads(text[text.index("{"): text.rindex("}") + 1])
            scenes = data["scenes"]
            for s in scenes:
                qs = s.get("search_queries") or ([s["search_query"]] if s.get("search_query") else [])
                assert s.get("narration") and qs, "empty scene field"
                s["search_queries"] = [q for q in qs if q][:4]
                s["search_query"] = s["search_queries"][0]
            assert data.get("title") and data.get("description"), "missing title/description"
            if n_scenes:
                assert abs(len(scenes) - n_scenes) <= 2, f"got {len(scenes)} scenes"
            return data
        except RuntimeError as e:  # missing key: retrying can't help
            raise
        except Exception as e:
            err = e
            print("script attempt failed:", e)
            time.sleep(2)
    raise RuntimeError(f"no valid script after {tries} tries: {err}")


def fact_check(script):
    """Second pass: an editor reviews the script for factual errors and risky financial claims."""
    prompt = f"""You are a meticulous fact-checker and compliance editor for a personal-finance YouTube channel.
Review this video script. Fix any factual error, outdated figure, misleading simplification, guaranteed-return or
get-rich-quick claim, or specific investment recommendation. If you cannot be confident a statistic is correct,
replace it with a clearly illustrative round example ("say you invest $100 a month...") or cut it. Keep the same JSON structure,
scene count, tone and pacing. Return ONLY the corrected JSON.

{json.dumps(script)}"""
    try:
        fixed = ask_json(prompt, len(script["scenes"]), tries=2)
        fixed.setdefault("thumbnail_text", script.get("thumbnail_text"))
        return fixed
    except Exception as e:  # never publish less-checked content silently
        raise RuntimeError(f"fact-check failed, refusing to publish unchecked script: {e}")


# ---------- 2. voiceover ----------
async def _tts(text, path):
    """Synthesize speech; return [(word, start_s, end_s)] from the engine's WordBoundary events."""
    import edge_tts
    try:
        comm = edge_tts.Communicate(text, CFG["voice"], boundary="WordBoundary")
    except TypeError:  # older edge-tts emits word boundaries by default
        comm = edge_tts.Communicate(text, CFG["voice"])
    words = []
    with open(path, "wb") as f:
        async for ev in comm.stream():
            if ev["type"] == "audio":
                f.write(ev["data"])
            elif ev["type"] == "WordBoundary":
                s = ev["offset"] / 1e7
                words.append((ev["text"], s, s + ev["duration"] / 1e7))
    return words


# ---------- 3. footage ----------
USED_CLIPS = set()


def _search_pexels(query):
    r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": os.environ["PEXELS_API_KEY"]},
                     params={"query": query, "orientation": "portrait" if CFG["format"] == "short" else "landscape",
                             "per_page": 15}, timeout=30)
    r.raise_for_status()
    out = []
    for v in r.json().get("videos", []):
        if v["duration"] >= 3:
            best = min(v["video_files"], key=lambda f: abs(f["width"] - SIZE[0]))
            out.append(("pexels", v["id"], best["link"]))
    return out


def _search_pixabay(query):
    r = requests.get("https://pixabay.com/api/videos/", timeout=30,
                     params={"key": os.environ["PIXABAY_API_KEY"], "q": query, "per_page": 20, "safesearch": "true"})
    r.raise_for_status()
    out = []
    for h in r.json().get("hits", []):
        if h.get("duration", 0) >= 3:
            vid = h["videos"].get("large") or h["videos"].get("medium")
            if vid and vid.get("url"):
                out.append(("pixabay", h["id"], vid["url"]))
    return out


def fetch_clip(query, dest, seconds):
    """Stock footage from Pexels or Pixabay (whichever key is set); gradient fallback so a run always completes."""
    for env, search in (("PEXELS_API_KEY", _search_pexels), ("PIXABAY_API_KEY", _search_pixabay)):
        if not os.environ.get(env):
            continue
        try:
            fresh = [c for c in search(query) if c[:2] not in USED_CLIPS]
        except Exception as e:
            print(f"{env} search failed ({e}); trying next source")
            continue
        if not fresh:
            continue
        src, cid, url = random.choice(fresh[:8])
        USED_CLIPS.add((src, cid))
        raw = dest.with_suffix(".src.mp4")
        raw.write_bytes(requests.get(url, timeout=120).content)
        sh("ffmpeg", "-y", "-stream_loop", "-1", "-i", raw, "-t", seconds, "-an",
           "-vf", f"scale={SIZE[0]}:{SIZE[1]}:force_original_aspect_ratio=increase,"
                  f"crop={SIZE[0]}:{SIZE[1]},fps=30,setsar=1,eq=contrast=1.06:saturation=1.15",
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", dest)
        return
    # fallback: animated gradient so the pipeline always completes
    sh("ffmpeg", "-y", "-f", "lavfi", "-i",
       f"gradients=s={SIZE[0]}x{SIZE[1]}:d={seconds}:speed=0.02:r=30", "-c:v", "libx264",
       "-preset", "veryfast", "-pix_fmt", "yuv420p", dest)


_font_cache = {}


def find_font():
    """Heaviest Montserrat if installed (workflow installs it), else DejaVu Sans Bold."""
    if "f" not in _font_cache:
        path = None
        for q in ("Montserrat:style=ExtraBold", "Montserrat:bold", "DejaVu Sans:bold"):
            try:
                out = subprocess.run(["fc-match", "-f", "%{file}", q], capture_output=True, text=True).stdout.strip()
            except FileNotFoundError:
                break
            if out and os.path.exists(out):
                path = out
                break
        _font_cache["f"] = path or ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
                                    if os.path.exists("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf") else None)
    return _font_cache["f"]


def build_scene_clip(sc, i, d, work):
    """One scene = several short cuts (~5s) from different searches, optional animated on-screen callout."""
    queries = sc.get("search_queries") or [sc["search_query"]]
    seg = 5.0 if CFG["format"] == "long" else 3.5
    n = max(1, round(d / seg))
    parts, left = [], d
    for k in range(n):
        length = left / (n - k)
        left -= length
        part = work / f"v{i}_{k}.mp4"
        fetch_clip(queries[k % len(queries)], part, length)
        parts.append(part)
    listing = work / f"p{i}.txt"
    listing.write_text("".join(f"file '{q.resolve()}'\n" for q in parts))
    scene = work / f"v{i}.mp4"
    callout = (sc.get("callout") or "").strip()
    base = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing]
    if not callout:
        sh(*base, "-c", "copy", scene)
        return scene
    tf = work / f"c{i}.txt"
    tf.write_text(callout.upper())
    show = min(4.5, d)
    fs = SIZE[1] // (22 if CFG["format"] == "short" else 13)
    font = f"fontfile={find_font()}:" if find_font() else ""
    dt = (f"drawtext={font}textfile={tf}:fontsize={fs}:fontcolor=0xF5B82E:box=1:boxcolor=0x0B1F3A@0.80:boxborderw={fs // 3}:"
          f"x=(w-text_w)/2:y=h*0.13:alpha='if(lt(t,0.3),t/0.3,if(lt(t,{show - 0.5:.2f}),1,max(0,({show:.2f}-t)/0.5)))'")
    sh(*base, "-vf", dt, "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", scene)
    return scene


# ---------- 4. captions ----------
def ts(t):
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h:02}:{m:02}:{s:06.3f}".replace(".", ",")


def build_srt(timings, durs):
    """timings: per-scene [(word, start, end)]; groups 4 words per caption at their true times."""
    lines, offset, i = [], 0.0, 1
    for words, d in zip(timings, durs):
        for j in range(0, len(words), 4):
            g = words[j:j + 4]
            end = words[j + 4][1] if j + 4 < len(words) else g[-1][2] + 0.2
            lines.append(f"{i}\n{ts(offset + g[0][1])} --> {ts(offset + end)}\n{' '.join(w[0] for w in g)}\n")
            i += 1
        offset += d
    return "\n".join(lines)


# ---------- 5. render ----------
def render(script):
    OUT.mkdir(exist_ok=True)
    work = OUT / "work"
    work.mkdir(exist_ok=True)
    clips, audios, durs, timings = [], [], [], []
    for i, sc in enumerate(script["scenes"]):
        a = work / f"a{i}.mp3"
        timings.append(asyncio.run(_tts(sc["narration"], a)))
        d = duration(a) + 0.25
        v = build_scene_clip(sc, i, d, work)
        audios.append(a); clips.append(v); durs.append(d)
        print(f"scene {i + 1}: {d:.1f}s  {sc['search_queries']}" + (f"  callout: {sc['callout']}" if sc.get("callout") else ""))
    (work / "v.txt").write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    (work / "a.txt").write_text("".join(f"file '{a.resolve()}'\n" for a in audios))
    srt = work / "captions.srt"
    srt.write_text(build_srt(timings, durs))
    final = OUT / "video.mp4"
    size = 14 if CFG["format"] == "short" else 12
    style = (f"FontName=Montserrat,Bold=1,FontSize={size * 2},PrimaryColour=&H00FFFFFF,"
             f"OutlineColour=&H00000000,BorderStyle=1,Outline=3,Alignment=2,MarginV=" +
             ("260" if CFG["format"] == "short" else "60"))
    tracks = sorted((HERE / "music").glob("*.mp3")) if (HERE / "music").exists() else []
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", work / "v.txt",
           "-f", "concat", "-safe", "0", "-i", work / "a.txt"]
    vf = f"subtitles={srt}:force_style='{style}'"
    if tracks:  # royalty-free tracks in faceless-yt/music/, mixed quietly under the voice
        cmd += ["-stream_loop", "-1", "-i", random.choice(tracks)]
        af = ["-filter_complex", f"[0:v]{vf}[v];[2:a]volume=0.08[m];[1:a][m]amix=inputs=2:duration=first:normalize=0[a]",
              "-map", "[v]", "-map", "[a]"]
    else:
        af = ["-vf", vf]
    sh(*cmd, *af, "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-b:a", "160k", "-shortest", final)
    return final


# ---------- 5b. thumbnail ----------
def ai_background(prompt):
    """Optional free AI-generated background (Pollinations); None on any problem so stock frames are used instead."""
    if not prompt or not CFG.get("ai_thumbnails", True):
        return None
    from PIL import Image
    try:
        url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt + ", cinematic lighting, vibrant colors, no text, no watermark")
               + "?width=1280&height=720&nologo=true")
        r = requests.get(url, timeout=90)
        if r.ok and r.headers.get("content-type", "").startswith("image") and len(r.content) > 20000:
            path = OUT / "ai_bg.jpg"
            path.write_bytes(r.content)
            Image.open(path).verify()
            return path
    except Exception as e:
        print("AI background skipped:", e)
    return None


def best_frame():
    """Pick the most colourful, highest-contrast frame among the raw (caption-free) scene clips."""
    from PIL import Image, ImageStat
    best, best_score = None, -1
    for clip in sorted((OUT / "work").glob("v*_0.mp4")) or sorted((OUT / "work").glob("v[0-9]*.mp4")):
        f = OUT / f"cand_{clip.stem}.jpg"
        sh("ffmpeg", "-y", "-ss", "1", "-i", clip, "-frames:v", "1", f)
        img = Image.open(f).convert("RGB").resize((160, 90))
        stat = ImageStat.Stat(img)
        r, g, b = stat.mean
        score = sum(stat.stddev) + 0.5 * (abs(r - g) + abs(g - b) + abs(b - r)) - (60 if sum(stat.mean) / 3 < 45 else 0)
        if score > best_score:
            best, best_score = f, score
    return best


def layout_text(text, font_path, max_w=720, max_lines=3):
    from PIL import ImageFont
    words = text.upper().split()
    for size in range(200, 80, -10):
        font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
        lines, cur = [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if font.getlength(t) <= max_w or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        if len(lines) <= max_lines and max(font.getlength(l) for l in lines) <= max_w:
            break
    return font, size, lines


def make_thumbnail(video, text, prompt=None):
    from PIL import Image, ImageDraw, ImageEnhance, ImageOps
    src = ai_background(prompt) or best_frame()
    W, H = 1280, 720
    img = Image.open(src).convert("RGB")
    scale = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1))
    img = img.crop(((img.width - W) // 2, (img.height - H) // 2, (img.width + W) // 2, (img.height + H) // 2))
    img = ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(1.35)).enhance(1.12)
    # dark ramp on the left so text pops, subject stays visible on the right
    ramp = ImageOps.invert(Image.linear_gradient("L").rotate(90).resize((W, H)))
    alpha = ramp.point(lambda v: int(max(0.0, min(1.0, (v / 255 - 0.30) / 0.70)) * 0.92 * 255))
    img = Image.composite(Image.new("RGB", (W, H), (6, 14, 30)), img, alpha)
    d = ImageDraw.Draw(img)
    font, size, lines = layout_text(text, find_font())
    gap = int(size * 0.12)
    total = len(lines) * size + (len(lines) - 1) * gap
    y = (H - total) // 2 - 10
    hot = [i for i, l in enumerate(lines) if any(c.isdigit() or c in "$%" for c in l)] or [len(lines) - 1]
    for i, l in enumerate(lines):
        x, yy = 60, y + i * (size + gap)
        d.text((x + 6, yy + 7), l, font=font, fill=(0, 0, 0), stroke_width=9, stroke_fill=(0, 0, 0))
        d.text((x, yy), l, font=font, fill="#FFD400" if i in hot else "white", stroke_width=9, stroke_fill="black")
    d.rounded_rectangle([60, y + total + 26, 60 + 260, y + total + 40], radius=7, fill="#FFD400")
    logo_path = HERE / "branding" / "logo.png"
    if logo_path.exists():  # channel logo, bottom-right, circular
        logo = Image.open(logo_path).convert("RGB").resize((150, 150))
        mask = Image.new("L", (150, 150), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, 149, 149], fill=255)
        img.paste(logo, (W - 190, H - 190), mask)
    out = OUT / "thumbnail.jpg"
    img.save(out, quality=92)
    return out


# ---------- 6. upload ----------
def upload(video, script, thumb):
    from googleapiclient.http import MediaFileUpload
    yt = yt_client()
    title = script["title"] + (" #Shorts" if CFG["format"] == "short" and "#Shorts" not in script["title"] else "")
    links = "\n".join(f"{l['label']}: {l['url']}" for l in CFG.get("affiliate_links", []))
    desc = script["description"] + (f"\n\n{links}\n(Some links may be affiliate links; I may earn a commission at no cost to you.)" if links else "")
    body = {"snippet": {"title": title[:100], "description": desc,
                        "tags": script.get("tags", []), "categoryId": CFG["category_id"]},
            "status": {"privacyStatus": CFG["privacy"], "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": True}}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(video), chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    print("uploaded: https://youtu.be/" + resp["id"])
    try:  # custom thumbnails require a phone-verified channel
        yt.thumbnails().set(videoId=resp["id"], media_body=MediaFileUpload(str(thumb))).execute()
    except Exception as e:
        print("thumbnail not set:", e)
    return resp["id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    history = load_history()
    idea = next_idea()
    script = write_script(history, idea)
    print("Title:", script["title"])
    video = render(script)
    thumb = make_thumbnail(video, script.get("thumbnail_text") or script["title"][:24], script.get("thumbnail_prompt"))
    (OUT / "script.json").write_text(json.dumps(script, indent=2))
    if args.dry_run:
        print("dry run complete:", video, thumb)
        return
    vid = upload(video, script, thumb)
    if idea:
        mark_used(idea["title"])
    HISTORY.write_text(json.dumps(history + [{"title": script["title"], "id": vid}], indent=2))


if __name__ == "__main__":
    sys.exit(main())
