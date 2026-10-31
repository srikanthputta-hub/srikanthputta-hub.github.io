"""Studio roster: eight desks, grouped by the aspect of the work they own.

kind: "llm" = leans on a language model; "code" = deterministic code; "human" = you and the Claude Code session.
Handoff order: Producer -> Researcher -> Writer <-> Editor -> Designer -> Post-Production -> Publisher -> Producer (final call).
"""
ROLES = [
    {"id": "producer", "title": "Producer", "dept": "Newsroom", "kind": "llm",
     "summary": "Picks each topic and angle, convenes the team, and makes the final go / no-go: every gate must pass or nothing ships."},
    {"id": "researcher", "title": "Researcher", "dept": "Newsroom", "kind": "llm",
     "summary": "Finds the real data that answers the question, downloads it, and hands the team a fact sheet with the biggest moves highlighted."},
    {"id": "writer", "title": "Writer", "dept": "Newsroom", "kind": "llm",
     "summary": "Outlines the video, writes the script act by act from the fact sheet, and revises it with the Editor's notes."},
    {"id": "editor", "title": "Editor", "dept": "Standards", "kind": "llm",
     "summary": "The quality gate: pre-flights the outline, fact-checks every figure, enforces YouTube's rules (no advice, persona or hype) and blocks repeats."},
    {"id": "designer", "title": "Designer", "dept": "Production", "kind": "llm",
     "summary": "Plans the visuals from the finished script (animated charts, concrete footage, callouts) and designs a thumbnail that matches the title."},
    {"id": "post_production", "title": "Post-Production", "dept": "Production", "kind": "code",
     "summary": "Records the narration and cuts voice, charts, footage, captions and music into the final video."},
    {"id": "publisher", "title": "Publisher", "dept": "Publishing & Growth", "kind": "llm",
     "summary": "Writes the title, description and chapters, uploads, and reviews how the channel is doing."},
    {"id": "boss", "title": "Boss (you + Claude Code)", "dept": "Operations", "kind": "human",
     "summary": "Sets priorities, owns secrets and approvals; dispatches the Claude Code roster to build and maintain the studio."},
]

ROLE_IDS = [r["id"] for r in ROLES]
FLOW = ["producer", "researcher", "writer", "editor", "designer", "post_production", "publisher"]  # desks around the board, boss sits at the bottom
