# Faceless YouTube automation

`pipeline.py`: Claude picks a fresh topic and writes the script → edge-tts voiceover → Pexels stock footage → burned-in captions (ffmpeg) → YouTube upload. `.github/workflows/faceless-yt.yml` runs it daily.

## Setup
1. Edit `config.json` (niche is preset to beginner personal finance, long-form).
2. Keys: Anthropic (console.anthropic.com), Pexels (free, pexels.com/api).
3. YouTube: Google Cloud → enable YouTube Data API v3 → OAuth client (Desktop) → download `client_secret.json` → `python get_refresh_token.py client_secret.json`.
4. GitHub repo → Settings → Secrets → Actions: `ANTHROPIC_API_KEY`, `PEXELS_API_KEY`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`.
5. Run the workflow manually with dry_run on, check the video artifact, then let the schedule run (manual runs default to dry-run; scheduled runs upload).

Start with `"privacy": "private"` until you trust the output. Unverified Google API projects can only upload private videos until audited.
Drop royalty-free .mp3 files in `faceless-yt/music/` and one is mixed in quietly under the voice.
