# Agent checks

Checks I put around AI coding agents, so work is routed on purpose, briefs are specific, and finished work is judged by something other than the agent's own claim.

I design the checks and direct the AI coding agents that build them ([how I work](#how-i-work)). The judgements come from Jev, a typed-decision model from [TypeSafe](https://typesafe.ai) that answers yes/no, choice and score questions with probabilities; `jev/client.py` calls its HTTP API. This repo treats those numbers as scores to rank by, not as calibrated odds.

## The problem

Agents sometimes report success they have not achieved. Delegation can become a habit rather than a decision about the work. A check that watches commands can go blind when the command changes, and a check that fails open can stop working without anyone noticing.

## The checks

`prompt -> route -> brief lint -> dispatch -> review gate`  (plan gate runs before each file edit)

### Per-turn route

When a new prompt arrives, `hooks/route_hook.py` asks for a factual digest. It tells the agent to state `goal`, `files`, `change`, `done_check`, dependencies, scope, audience, and reversibility, then run `scripts/route_plan.py`. The router asks, among other things, whether "a competent worker who has never seen the conversation could complete this work from the digest alone." Code applies the role policy. It rejects judgement words such as "easy" in the digest and does not route a raw conversational prompt.

### Brief lint

Before a worker receives a task, `hooks/brief_lint.py` asks whether "the task identifies precisely what it is about," whether completion is checkable, and whether scope is bounded. A research brief instead needs a stated shape for its answer. It gives advice and fails open if the decision service is unavailable. It never blocks a dispatch.

### Dispatch and source check

`scripts/dispatch.py` passes a file-backed brief to a worker, closes stdin, and reports only files changed during that dispatch. For research, it fetches the URLs the report cites (up to 10 sources and 30 claims), screens each page for instructions aimed at an agent, and checks whether the page supports each claim that cites it. A flag means inspect the claim; it never certifies the report. If a check cannot run, the output says whether that was an outage (no key, no network) or a bug in the check.

### Plan gate

Before a file edit, `hooks/plan_gate.py` checks the current turn's route marker. If the file was assigned to a worker, it denies the inline edit. `scripts/route_veto.py` accepts an override only with a written reason of at least 20 characters and logs it. If a turn has no route, the gate warns once. Damaged or absent state fails open.

### Review gate

After work returns, `hooks/review_gate_hook.py` and the dispatch wrapper ask whether the record shows a check actually ran and passed, and whether the change spans files, reaches people outside, or drifts from the task. The gate recommends another read when the work has that shape. It never grades correctness. When the record shows a passing check and nothing drifted, it stays silent. Low stakes need no review even when verification is missing; that is a deliberate policy choice.

## Design rules worth copying

- The agent states facts, a model judges, and code decides (`jev/plan_router.py`).
- The author of a request must not answer their own question: judgement words and a named destination are rejected before anything is sent (`validate` in `jev/plan_router.py`).
- Ask about the shape of returned work, not whether it is correct (`jev/review_gate.py`).
- Prefer a check that ran to a judgement: when the record shows one ran and passed and nothing drifted, there is no review (`assess_output`).
- Ask for the presence of evidence, never its absence (`was_verified`).
- Advisory checks fail open, but say whether they failed from an outage or a bug (`scripts/dispatch.py`). The one blocking check needs a written, logged reason to override (`hooks/plan_gate.py`, `scripts/route_veto.py`).
- Label every threshold as derived or guessed, in one ledger (`jev/thresholds.py`; the source check's screening cuts are in `jev/judge.py`).
- Pin the wording of every model question. A threshold chosen against one wording does not carry over to another (`tests/test_question_wording.py`).
- Test the parsing, not only the decisions. A check that cannot read its input goes quiet instead of failing (`tests/test_parsing.py`).

## What went wrong

These are observed in my own use.

- The first router sent raw prompts and delegated on 0 of 11 of them, while about 1.5M tokens of subagent work ran on the expensive tier in one session. The hook now asks for a digest of facts before the router decides.
- Hooks saw no dispatches from 2026-09-28 to 2026-10-01 after the command changed. The dispatch wrapper now runs the returned-work and research checks when a job finishes.
- The first good research brief failed the linter because it had no test command. The linter now checks the defined answer shape for research.
- On one returned record, the review gate gave 0.895 for evidence being present and 0.68 for evidence being absent. It now asks only for presence.
- An audit of the router's thresholds found four of seven were guesses with nothing behind them. The ledger now labels every cut the checks use as derived or a guess.
- While this public copy was being prepared, two agent edits broke checks without failing a single test. A text clean-up turned every `?` in five files into `:`, which broke the regular expressions that read commands, npm shims and research reports. A trimmed file lost the import the research check needed. Both checks fail open, so they would simply have gone quiet. A diff against the private version caught the first and an independent review caught the second. `tests/test_parsing.py` and a no-key run of the source check in `tests/test_dispatch.py` now cover both, and a crash in a check is now reported as a bug rather than an outage.

## Limits

These checks guide a cooperating agent on one machine; they are not a security boundary. Most checks fail open. Model scores rank options and are not calibrated probabilities. The threshold cuts still need measured outcomes. It has been used by one person, on Windows. Live judgements need an API key. The paper linked in [References](REFERENCES.md) reports that no judge configuration it tested exceeded AUROC 0.65 on one benchmark; that result is not a measurement of these checks.

## Run it

Python 3.10 or later and Git are required. The code uses only the Python standard library. Run offline tests with `python tests/run_all.py`; no API key or network is needed.

I run this with Claude Code as the main agent and OpenAI's Codex CLI for dispatched workers; `routes.example.json` maps roles to models.

Copy `routes.example.json` to `routes.json` and edit the local role mapping; anything you leave out keeps the example's value. The local file is ignored by Git. `AGENT_CHECKS_CONFIG` can point to another JSON file. See [the hook settings example](examples/claude-settings.example.json), replace `/path/to/agent-checks` with the checkout path, and add its `hooks` object to your settings.

Set `TYPESAFE_API_KEY` before live checks. In PowerShell, run `$env:TYPESAFE_API_KEY = "your-key"`; in a POSIX shell, run `export TYPESAFE_API_KEY=your-key`. The tests do not use it.

## Runtime records

All runtime state lives under `logs/`, which Git ignores. These are made-up rows showing each format, not collected user data.

- `logs/calls-YYYY-MM.jsonl`: `{"ts":"2026-10-19T12:00:00+00:00","label":"route_plan","model":"decision-model","questions":{},"answers":{},"input_tokens":120,"cost_usd":0.00000504,"seconds":0.21,"debiased":false}`
- `logs/vetoes.jsonl`: `{"ts":"2026-10-19T12:01:00+00:00","turn":"abc123","session_id":"demo","worker":"cheap_worker","files":["src/example.py"],"reason":"A small inline correction is needed now."}`
- `logs/.last-prompt-demo.json`: `{"prompt":"Update the example parser"}`
- `logs/.current-session.json`: `{"session_id":"demo"}`
- `logs/.route-plan-turn-demo.json`: `{"turn":"abc123","worker":"cheap_worker","files":["src/example.py"],"veto":false,"session_id":"demo","routed_at":"2026-10-19T12:00:00+00:00"}`
- `logs/.plan-gate-fired-demo.json`: `{"turn":"abc123"}`
- `logs/thresholds.json` (optional, written by hand): `{"values":{"delegate_at":0.5}}` overrides the ledger locally.

## How I work

I learned this way of working in graduate school. As a linguist I was often paired with a computer science student. I brought the broad view and most of the ideas, and knew enough computer science to show what needed doing and which tools fit; my partner wrote most of the code. We argued it back and forth until it worked.

I still work that way. Now my computer science partners are AI agents, Claude Code and Codex. This layer took about two weeks, from mid-September to the start of October 2026: research into how routers and model judges fail, a written specification, then many rounds of building, testing, catching what broke and redesigning. The agents write most of the code. I own the design, the specifications, what counts as done, and the corrections when a check fails in real use. [DESIGN-LOG.md](DESIGN-LOG.md) lists those decisions in order; the stories under "What went wrong" are the ones that taught the most.

This repository is a cleaned public copy of the private version that runs in my own setup. Its history starts at publication.

See [References](REFERENCES.md) and [Third-party notices](THIRD_PARTY_NOTICES.md).
