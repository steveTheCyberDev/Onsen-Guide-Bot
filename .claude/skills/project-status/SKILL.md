---
name: project-status
description: Generate a project progress report for Onsen Guide Bot — Done, Pending/In-progress, Next, Priority, and Defects/Risks. Delegates to the project-progress-tracker agent. Use when the user asks "where are we?", "status report", "what's next?", "what's pending?", or starts a new session.
---

# Project Status Report

Produce a structured progress report for the Onsen Guide Bot. **Delegate the gathering to
the `project-progress-tracker` agent** (it already knows the project layout and history) — do
not re-derive the status inline.

## How to run

1. Launch the `project-progress-tracker` agent via the Agent tool. If one was already used
   this session, **resume it** (SendMessage) rather than spawning a cold one.

2. Instruct the agent to report against the **exact section format** below. The agent's
   default output is Done/Next; this skill extends it — tell the agent to also cover
   Pending/In-progress and Defects/Risks, and to keep each item one line with a concrete
   pointer (file, PR #, env flag, branch) so the report is actionable, not vague.

3. Tell the agent to ground the report in **live sources**, not just memory:
   - `git log` (recent commits) + `git branch` + working-tree status
   - open PRs / issues (`gh api repos/steveTheCyberDev/Onsen-Guide-Bot/pulls?state=open`
     — note: this repo's `gh` version errors on `gh pr` GraphQL calls, so prefer `gh api`)
   - `PROJECT_JOURNEY.md` (Status section + roadmap) and `docs/*-plan.md`
   - gated/in-flight features via env flags in `core/config.py` (e.g. `ASK_ENABLED`,
     `ANALYZE_ENABLED`, `CHAT_ENGINE`)
   - `TODO`/`FIXME`/`XXX` markers across `backend/` and `frontend/`
   - the "Honest limitations" section of `PROJECT_JOURNEY.md` for known gaps

4. **Relay** the agent's report to the user (agents don't talk to the user directly).
   Keep it tight; cut anything that isn't current.

## Required report format

```
# Onsen Guide Bot — Status (<date>)

## ✅ Done
1. <completed item> — <pointer: PR#/commit/file>
   …

## 🟡 Pending / In-progress
1. <started but not finished> — <open PR, gated flag, unmerged branch>
   …

## ⏭️ Next
1. <the agreed next build / immediate next step>
   …

## 🔺 Priority
- <what matters most right now, and why>

## 🐞 Defects / Risks
- <known bug, failing test, TODO/FIXME, or limitation> — <where / impact>
  (state "none known" if clean)
```

## Notes
- Convert any relative dates to absolute.
- If a memory note names a file/flag/branch, **verify it still exists** before reporting it
  (memories reflect a past moment).
- This is a read-only report — do not change code, merge, or push.
