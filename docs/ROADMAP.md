# Roadmap: what you need to provide, and what Claude builds

## A. One-time, shared (you)
1. Create the empty private GitHub repo `money-blueprint-studio` in your account (the integration cannot create repos). Then add it to the Claude session.
2. Apply the Supabase SQL (`002`, `003` in the Pages repo's `studio/migrations`) in the SQL editor of project `qxyvmvtecnnwqeqaouzh`.
3. Shared secrets in the new repo (Settings, Secrets, Actions): `GROQ_API_KEY`, `GEMINI_API_KEY` (optional), `PIXABAY_API_KEY`. Copy them from the old repo.
4. Tell Claude your time zone and the evening upload time for news, and whether news is world or one region.

## B. Per channel (you), repeat for facts, news, ai-updates
Each channel needs its own Google account (2-step verification on) so channels stay unlinked.
1. Create the Google account, then create the YouTube channel with the chosen name and handle (see `docs/NAMES.md`). Upload `channels/<id>/branding/logo.png` and `banner.png`.
2. Google Cloud: new project (one per channel, so the 10,000-unit daily upload quota is not shared). Enable "YouTube Data API v3".
3. OAuth consent screen: External, add yourself as a test user, then click "Publish app" so refresh tokens do not expire every 7 days.
4. Credentials: create an OAuth client (Desktop app). Download `client_secret.json`.
5. Run `engine/get_refresh_token.py` locally (or use the Cowork prompt from before) signed in as that channel's account. Keep the printed refresh token.
6. Add three repo secrets: `FACTS_YT_CLIENT_ID`, `FACTS_YT_CLIENT_SECRET`, `FACTS_YT_REFRESH_TOKEN` (news: `NEWS_...`, AI updates: `AI_...`). Finance keeps `FINANCE_...` (the old `YT_...` names also work).
7. Tell Claude when each channel is ready.

## C. Claude builds (in this order)
1. Move the engine and finance channel into the new repo, run the tests, dry-run finance there. (scaffold done locally)
2. Source adapters and fact sheets, one channel at a time, each with its own gates and tests:
   - facts: sourced fact lists (Wikipedia/Wikidata, NASA, NOAA, museum data), each fact with a named source.
   - ai-updates: official lab blog RSS, arXiv, Hugging Face papers; primary-source gate.
   - news: RSS + GDELT clustering; 2-independent-outlet gate; 24-hour window; evening schedule.
3. Channel dimension on the live board (new `channel` column) and a channel switcher on the dashboard.
4. A monthly Actions-minutes counter on the dashboard (repo is private).
5. Per channel: clean dry run, you review, then enable that channel's schedule. Planned cadence: finance Mon/Wed/Fri, facts 3-4 a week, news daily, AI updates daily.

## D. Rules
Do not turn on any schedule without a clean dry run and your say-so. Keep each channel's voice, format and visuals distinct (YouTube's inauthentic-content policy).
