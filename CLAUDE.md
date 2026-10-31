# The Money Blueprint studio: instructions for Claude Code sessions

This repo holds (1) the owner's GitHub Pages site (`index.html`, leave it alone unless asked) and (2) an automated YouTube studio for **The Money Blueprint**: `faceless-yt/` (pipeline), `studio/` (public Office board at `/studio/`), `.github/workflows/`, `.claude/agents/` (this roster).

## What the channel is
Educational explainers on how money and the economy work, built from real public data (FRED, World Bank), with AI narration. **Never** personal financial advice, never market predictions, never an "expert" persona. Reason: YouTube's July 2026 clarification of its inauthentic-content policy says channels presenting an AI "human expert" advising on finances, plus generic/templated output, are not monetizable. Ad revenue also needs 1,000 subscribers plus qualified watch hours (rising to 8,000 hours from 1 Feb 2027), so retention on long videos matters.

## How the studio runs (daily dry-run-able, `python faceless-yt/studio.py --dry-run`)
Head of Content -> Researcher -> Data Analyst -> Scriptwriter -> Fact-checker + Policy Editor (up to 2 revision rounds) -> Originality Editor -> Director -> Chart Designer -> Voice Actor + Video Editor -> Thumbnail Designer -> Publisher -> Producer (every gate must pass or nothing ships). State is written to `studio/state.json` and shown on the Office board. Roles are defined in `faceless-yt/studio_lib/roles.py`; keep `studio/roles.json` in sync with it (a test checks).

## Dispatch-first (boss = the primary session)
For real work, dispatch the matching subagent from `.claude/agents/` instead of doing it in the main thread. Typical handoff chain: `product-manager` (what "done" means) -> `architect` (design note, for anything touching pipeline, gates or workflows) -> `coder` -> `reviewer` (reads the actual diff against the design) -> `compliance-auditor` (anything touching prompts, banned phrases, disclosure or gates) -> `tester`. `devops` handles workflow failures; `researcher` and `analyst` are read-mostly. Pick the model tier by difficulty: haiku for trivial checks, sonnet for normal work, opus for design, policy and the hardest debugging.

What stays with **boss**: priorities; git commit/push, PRs and merges; dispatching workflow runs; enabling the publish schedule; secrets; anything the user must approve.

## Access rules (apply to every dispatched role)
- No role runs `git commit`, `git push`, merges, dispatches a workflow, or uploads to YouTube.
- No role reads, prints or asks for secret values. Secret **names** only. Never commit `client_secret*.json`, tokens or `.env`.
- Nobody weakens a gate, the banned-phrase list, the fact-sheet number audit or the disclosure text to make a run pass. If a gate is wrong, bring it to the user.
- The publish schedule stays off unless the user says to turn it on after a clean dry run.

## Constraints to design around (all free tiers)
- LLM: Groq free tier is about 8k tokens per minute per request, so prompts stay short and the studio writes in acts. The Gemini key currently fails with a billing error; `llm()` falls back automatically. Only one run at a time (workflow `concurrency`).
- Footage: Pexels keys are paused; Pixabay is used. Voice: edge-tts (flaky, retried). Data: FRED and World Bank, keyless.
- Scheduled workflows only fire from the default branch; a dry run can be dispatched from any branch.

## Commands
- Tests: `python -m pytest -q faceless-yt/tests` (about a minute; includes an end-to-end studio run with the model, data and voice stubbed).
- Dry run in CI: dispatch `faceless-yt.yml` with `dry_run=true` (from a branch to test before merging).
- Always validate workflow YAML after editing it.
