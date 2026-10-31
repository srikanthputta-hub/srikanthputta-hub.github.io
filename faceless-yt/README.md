# Faceless YouTube automation

`pipeline.py`: Claude picks a fresh topic and writes the script → edge-tts voiceover → Pexels stock footage → burned-in captions (ffmpeg) → YouTube upload. `.github/workflows/faceless-yt.yml` runs it daily.

## Setup
1. Edit `config.json` (niche is preset to beginner personal finance, long-form).
2. Keys (all free): an LLM key, Gemini (aistudio.google.com/apikey, no card needed) and/or Groq (console.groq.com/keys, no card needed; used automatically if Gemini fails) and Pexels (pexels.com/api; new Pexels keys are currently paused) or Pixabay (pixabay.com/api/docs, free). Claude (`ANTHROPIC_API_KEY`) is an optional paid alternative; Gemini is used if both are set.
3. YouTube: Google Cloud → enable YouTube Data API v3 → OAuth client (Desktop) → download `client_secret.json` → `python get_refresh_token.py client_secret.json`.
4. GitHub repo → Settings → Secrets → Actions: `GEMINI_API_KEY` and/or `GROQ_API_KEY`, `PEXELS_API_KEY` or `PIXABAY_API_KEY`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`.
5. Run the workflow manually with dry_run on, check the video artifact, then let the schedule run (manual runs default to dry-run; scheduled runs upload).

Start with `"privacy": "private"` until you trust the output. Unverified Google API projects can only upload private videos until audited.
Drop royalty-free .mp3 files in `faceless-yt/music/` and one is mixed in quietly under the voice.

## Strategy automation (`strategy.py`)
- `python strategy.py plan` → `STRATEGY.md` (positioning, content pillars, schedule, growth, monetization roadmap). Run once now.
- `python strategy.py ideas` → `ideas.json`, 100 ranked ideas. The pipeline publishes the top unused one each run.
- `python strategy.py review` → `REVIEW.md`, weekly analytics review.
The weekly workflow refills ideas and writes the review every Monday. Run it manually once (choose `plan`, then `ideas-and-review`) before the first daily upload.

## Quality gates
- Every script gets a second Claude pass as fact-checker/compliance editor. If that pass fails the run aborts rather than publishing unchecked finance content.
- Add affiliate links under `affiliate_links` in `config.json` as `[{"label": "...", "url": "..."}]`; they are appended to descriptions with a disclosure line. Only use programs you have actually joined.
- `python -m pytest faceless-yt/tests` runs the offline tests (also run in CI).

## Visuals
- Each scene cuts between 3 different concrete stock searches (~5s per cut) instead of looping one clip, with a light colour grade and an animated on-screen callout (key number/term) on about half the scenes.
- Thumbnails: best frame of the footage, or a free AI-generated background (Pollinations, `ai_thumbnails` in `config.json`; falls back to footage if unavailable), left-aligned bold text with the number highlighted, channel logo.
