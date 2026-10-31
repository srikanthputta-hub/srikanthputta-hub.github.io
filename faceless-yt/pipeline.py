#!/usr/bin/env python3
"""Faceless YouTube pipeline: topic -> script -> voice -> stock footage -> captions -> render -> upload.

Usage:
  python pipeline.py --dry-run      # everything except upload; footage replaced by gradients if no PEXELS_API_KEY
  python pipeline.py                # full run, uploads to YouTube
"""
import argparse, asyncio, json, os, random, re, subprocess, sys, time
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
        raise ValueError(f"Gemini {r.status_code}: {r.text[:300]}")
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
        raise ValueError(f"Groq {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"]


def _claude(prompt, max_tokens, key):
    import anthropic
    m = anthropic.Anthropic(api_key=key).messages.create(model=MODEL, max_tokens=max_tokens,
                                                         messages=[{"role": "user", "content": prompt}])
    return m.content[0].text


def llm(prompt, max_tokens=6000):
    """Tries every provider that has a key, free ones first: Gemini, Groq, then Claude (paid, optional)."""
    providers = [(n, f, os.environ.get(k)) for n, f, k in
                 (("Gemini", _gemini, "GEMINI_API_KEY"), ("Groq", _groq, "GROQ_API_KEY"),
                  ("Claude", _claude, "ANTHROPIC_API_KEY")) if os.environ.get(k)]
    if not providers:
        raise RuntimeError("No LLM key found: set GEMINI_API_KEY or GROQ_API_KEY (both free), or ANTHROPIC_API_KEY (paid)")
    errors = []
    for name, fn, key in providers:
        try:
            return fn(prompt, max_tokens, key)
        except Exception as e:
            print(f"{name} failed, trying next provider: {str(e)[:200]}")
            errors.append(f"{name}: {str(e)[:200]}")
    raise ValueError("all LLM providers failed: " + " | ".join(errors))


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
{{"title": "<=70 chars, curiosity-driven", "description": "2-3 sentences + hashtags", "tags": ["..."], "thumbnail_text": "2-4 punchy words, ALL CAPS, no punctuation",
 "scenes": [{{"narration": "{sent}", "search_query": "2-4 word stock-footage search, concrete visuals"}}]}}
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
            assert all(s.get("narration") and s.get("search_query") for s in scenes), "empty scene field"
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
                  f"crop={SIZE[0]}:{SIZE[1]},fps=30,setsar=1",
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", dest)
        return
    # fallback: animated gradient so the pipeline always completes
    sh("ffmpeg", "-y", "-f", "lavfi", "-i",
       f"gradients=s={SIZE[0]}x{SIZE[1]}:d={seconds}:speed=0.02:r=30", "-c:v", "libx264",
       "-preset", "veryfast", "-pix_fmt", "yuv420p", dest)


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
        v = work / f"v{i}.mp4"
        fetch_clip(sc["search_query"], v, d)
        audios.append(a); clips.append(v); durs.append(d)
        print(f"scene {i + 1}: {d:.1f}s  [{sc['search_query']}]")
    (work / "v.txt").write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    (work / "a.txt").write_text("".join(f"file '{a.resolve()}'\n" for a in audios))
    srt = work / "captions.srt"
    srt.write_text(build_srt(timings, durs))
    final = OUT / "video.mp4"
    size = 14 if CFG["format"] == "short" else 12
    style = (f"FontName=DejaVu Sans,Bold=1,FontSize={size * 2},PrimaryColour=&H00FFFFFF,"
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
def make_thumbnail(video, text):
    from PIL import Image, ImageDraw, ImageEnhance, ImageFont
    frame = OUT / "frame.jpg"
    sh("ffmpeg", "-y", "-ss", "1", "-i", OUT / "work" / "v0.mp4", "-frames:v", "1", frame)  # raw clip: no burned-in captions
    img = Image.open(frame).convert("RGB")
    W, H = 1280, 720
    scale = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * scale), int(img.height * scale)))
    img = img.crop(((img.width - W) // 2, (img.height - H) // 2, (img.width + W) // 2, (img.height + H) // 2))
    img = ImageEnhance.Brightness(ImageEnhance.Color(img).enhance(1.4)).enhance(0.55)
    d = ImageDraw.Draw(img)
    font_path = next((f for f in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"] if os.path.exists(f)), None)
    words = text.upper().split()
    lines = [" ".join(words[:2]), " ".join(words[2:])] if len(words) > 2 else [" ".join(words)]
    lines = [l for l in lines if l]
    size = 190 if max(len(l) for l in lines) <= 9 else 140
    font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
    y = (H - len(lines) * (size + 20)) // 2
    for i, l in enumerate(lines):
        w = d.textlength(l, font=font)
        color = "#FFD400" if i == len(lines) - 1 else "white"
        d.text(((W - w) / 2, y + i * (size + 20)), l, font=font, fill=color, stroke_width=10, stroke_fill="black")
    out = OUT / "thumbnail.jpg"
    img.save(out, quality=90)
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
    thumb = make_thumbnail(video, script.get("thumbnail_text") or script["title"][:24])
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
