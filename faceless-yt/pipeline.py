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
SIZE = (1080, 1920) if CFG["format"] == "short" else (1920, 1080)


def sh(*cmd):
    subprocess.run([str(c) for c in cmd], check=True, capture_output=True)


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


# ---------- 1. idea + script ----------
def write_script():
    import anthropic
    past = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    n = CFG["scenes"]
    length = "under 55 seconds total" if CFG["format"] == "short" else "about 5 minutes total"
    prompt = f"""You run a faceless YouTube channel about: {CFG['niche']}.
Tone: {CFG['tone']}. Format: {CFG['format']} ({length}).
Already published (do not repeat): {past[-40:]}

Pick one fresh, specific, factually solid topic and write the video. Only state facts you are confident are true.
Scene 1 must open with a hook. The last scene ends with a one-line call to action to subscribe.
Return ONLY JSON:
{{"title": "<=70 chars, curiosity-driven", "description": "2-3 sentences + hashtags", "tags": ["..."],
 "scenes": [{{"narration": "1-2 spoken sentences", "search_query": "2-4 word stock-footage search, concrete visuals"}}]}}
Exactly {n} scenes."""
    msg = anthropic.Anthropic().messages.create(
        model=MODEL, max_tokens=3000, messages=[{"role": "user", "content": prompt}])
    text = msg.content[0].text
    return json.loads(text[text.index("{"): text.rindex("}") + 1])


# ---------- 2. voiceover ----------
async def _tts(text, path):
    import edge_tts
    await edge_tts.Communicate(text, CFG["voice"]).save(str(path))


# ---------- 3. footage ----------
def fetch_clip(query, dest, seconds):
    key = os.environ.get("PEXELS_API_KEY")
    if key:
        orient = "portrait" if CFG["format"] == "short" else "landscape"
        r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": key},
                         params={"query": query, "orientation": orient, "per_page": 15}, timeout=30)
        vids = [v for v in r.json().get("videos", []) if v["duration"] >= 3]
        if vids:
            v = random.choice(vids[:8])
            files = sorted(v["video_files"], key=lambda f: abs(f["width"] - SIZE[0]))
            data = requests.get(files[0]["link"], timeout=120).content
            raw = dest.with_suffix(".src.mp4")
            raw.write_bytes(data)
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


def build_srt(scenes, durs):
    lines, t, i = [], 0.0, 1
    for sc, d in zip(scenes, durs):
        words = sc["narration"].split()
        chunks = [" ".join(words[j:j + 4]) for j in range(0, len(words), 4)]
        total = sum(len(c) for c in chunks)
        for c in chunks:
            seg = d * len(c) / total
            lines.append(f"{i}\n{ts(t)} --> {ts(t + seg)}\n{c}\n")
            t += seg
            i += 1
    return "\n".join(lines)


# ---------- 5. render ----------
def render(script):
    OUT.mkdir(exist_ok=True)
    work = OUT / "work"
    work.mkdir(exist_ok=True)
    clips, audios, durs = [], [], []
    for i, sc in enumerate(script["scenes"]):
        a = work / f"a{i}.mp3"
        asyncio.run(_tts(sc["narration"], a))
        d = duration(a) + 0.25
        v = work / f"v{i}.mp4"
        fetch_clip(sc["search_query"], v, d)
        audios.append(a); clips.append(v); durs.append(d)
        print(f"scene {i + 1}: {d:.1f}s  [{sc['search_query']}]")
    (work / "v.txt").write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    (work / "a.txt").write_text("".join(f"file '{a.resolve()}'\n" for a in audios))
    srt = work / "captions.srt"
    srt.write_text(build_srt(script["scenes"], durs))
    final = OUT / "video.mp4"
    size = 14 if CFG["format"] == "short" else 12
    style = (f"FontName=DejaVu Sans,Bold=1,FontSize={size * 2},PrimaryColour=&H00FFFFFF,"
             f"OutlineColour=&H00000000,BorderStyle=1,Outline=3,Alignment=2,MarginV=" +
             ("260" if CFG["format"] == "short" else "60"))
    sh("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", work / "v.txt",
       "-f", "concat", "-safe", "0", "-i", work / "a.txt",
       "-vf", f"subtitles={srt}:force_style='{style}'",
       "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-b:a", "160k", "-shortest", final)
    return final


# ---------- 6. upload ----------
def upload(video, script):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
                        client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"],
                        token_uri="https://oauth2.googleapis.com/token")
    yt = build("youtube", "v3", credentials=creds)
    title = script["title"] + (" #Shorts" if CFG["format"] == "short" and "#Shorts" not in script["title"] else "")
    body = {"snippet": {"title": title[:100], "description": script["description"],
                        "tags": script.get("tags", []), "categoryId": CFG["category_id"]},
            "status": {"privacyStatus": CFG["privacy"], "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": True}}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(video), chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    print("uploaded: https://youtu.be/" + resp["id"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    script = write_script()
    print("Title:", script["title"])
    video = render(script)
    (OUT / "script.json").write_text(json.dumps(script, indent=2))
    if args.dry_run:
        print("dry run complete:", video)
        return
    upload(video, script)
    past = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    HISTORY.write_text(json.dumps(past + [script["title"]], indent=2))


if __name__ == "__main__":
    sys.exit(main())
