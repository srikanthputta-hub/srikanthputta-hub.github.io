# Money Blueprint studio (multi-channel): instructions for Claude Code sessions

One private repo runs four automated YouTube channels with one shared engine. `engine/` holds the pipeline, roles, gates and tests. `channels/<id>/` holds one channel's config, history, ideas, library, music, branding and state. Channels: `finance` (live), `facts`, `news`, `ai-updates` (scaffolded, source adapters not built yet; see `docs/ROADMAP.md`).

## Rules that hold for every channel
- Never personal financial advice, never market predictions, never an "expert" persona. Narration is AI-generated and disclosed. YouTube's July 2026 inauthentic-content policy targets mass-produced, templated channels: each channel must keep its own voice, format, visuals and original angle.
- Every claim traces to a sourced fact sheet. Nobody weakens a gate, the banned-phrase list, the number audit or the disclosure text to make a run pass. If a gate is wrong, bring it to the user.
- No role runs `git commit`, `git push`, merges, dispatches a workflow, or uploads to YouTube. Those stay with **boss** (the primary session) and need the user's say-so. No role reads or prints secret values (names only). Never commit `client_secret*.json`, tokens or `.env`.
- A channel's publish schedule stays off until the user says to turn it on after a clean dry run of that channel.
- Do not touch the Supabase `studio_board` or run migrations without the user's confirmation.

## Running a channel
`CHANNEL=<id> python engine/studio.py --dry-run` (default channel `finance`). In CI: workflow `studio.yml`, inputs `channel` and `dry_run`. Per-channel secrets are named `<CHANNEL>_YT_CLIENT_ID`, `<CHANNEL>_YT_CLIENT_SECRET`, `<CHANNEL>_YT_REFRESH_TOKEN` (for example `FINANCE_YT_REFRESH_TOKEN`); shared keys are `GROQ_API_KEY`, `GEMINI_API_KEY`, `PIXABAY_API_KEY`.

## Dispatch-first
For real work dispatch the matching subagent from `.claude/agents/` (product-manager -> architect -> coder -> reviewer -> compliance-auditor -> tester; devops for workflow failures). Pick the model tier by difficulty: haiku trivial, sonnet normal, opus design/policy/hardest debugging.

## Constraints (free tiers)
Groq free tier is about 8k tokens per minute, so prompts stay short. One run at a time (workflow `concurrency`). This repo is private: Actions minutes are shared with the owner's other private repos, so keep runs lean and watch monthly usage.

## Commands
`python -m pytest -q engine/tests` (about 2.5 minutes). Always validate workflow YAML after editing it.
