# Quartermaster

Quartermaster is a router and a quality layer for AI coding agents.

It balances cost and quality: cheap models take the build work, and a reviewer from a different model family checks what comes back.

**Seven weeks of agent work listed about 14% (about $1,070) under a bill with every Codex token priced at Opus rates, while paying for more independent review than before.** That prices the same tokens at Claude Opus 5.5 rates, token for token, with no allowance for redone work.

Comparing the 27 days before the router went live on 2026-09-20 with the 51 days after, and leaving out one-shot batch jobs:

- **Where it came from.** Cheap models took the build work. The cheapest, GPT-6 Luna, listed at $46 for tokens that would list at $1,330 on Opus 5.5, about 3.5% of the price. All build work listed at $220 against about $1,627. Luna went from writing 7.5% of the output tokens to 41%.
- **What it paid for.** 430 pieces of work went to an independent, higher-priced reviewer from a different model family, and about 7 in 10 came back with at least one issue (labels checked by hand on a sample). Review's share of list-price cost rose from 5% to 11%, on purpose.
- **What review caught,** before it went live: a new tournament clock that could overwrite a running game, venue TVs that could stay on an old blind level, an unverified answer that would have been marked as checked (caught before it shipped), and an admin check that accepted the wrong sign-in type.
- **The overhead.** The router itself: 1,419 routing decisions cost $0.14, and 1,486 review-gate checks cost $0.39.

*How it's counted: list-price equivalents from my own usage logs, because I pay flat subscriptions. New models and prices arrived during the comparison, so these numbers describe what happened rather than isolate the router's effect. Method, tables and caveats: [docs/spend-shift-2026-11.md](docs/spend-shift-2026-11.md).*

Every task becomes a short statement of plain facts, and a low-cost classifier decides who does it: the expensive main model, a cheaper model, or a dispatched worker, so the scarce budget lasts. Then checks inspect each brief before it goes out and each piece of work when it comes back.

The name: a quartermaster hands each job the right resources and inspects what comes back.

I design the system and direct the AI coding agents that build it ([how I work](#how-i-work)).

## The router

`prompt -> digest of facts -> eight questions -> policy -> worker, effort and after-steps`

On each working turn, `hooks/route_hook.py` asks the main agent to write a digest before `scripts/route_plan.py` routes the work. The digest names `goal`, `files`, `change`, `done_check`, `depends_on_conversation`, `out_of_scope`, `audience` and `reversibility`, with optional `pieces` for independent work. It carries the task behind a conversational prompt in terms a worker could use. `validate()` in `jev/plan_router.py` rejects judgement words such as "easy" and "risky", and any field naming an expected destination. The agent supplies facts; the classifier and the code choose the route.

Jev, a low-cost typed-decision model from [TypeSafe](https://typesafe.ai), answers eight questions about that digest through `jev/client.py`: the work type, how much reasoning it needs, whether it needs long context, whether a worker new to the conversation could use the digest, whether dependencies are portable, whether the done-check proves completion, the stakes and whether pieces split cleanly. I use its numbers as scores to rank by, not as calibrated odds.

Code in `route()` applies the policy. Design and investigation stay with the main model; design with outside stakes gets a consult first. Work that cannot be briefed from the digest stays in the main session. Long input goes to a long-context worker; work above the complexity band stays with the main model. The remaining portable work goes to a cheaper worker at the selected effort level. Research can go to a worker when workers have network access. Code checks whether a worker can write the named paths and keeps unreachable work in session. `routes.example.json` maps these roles to models.

The route also sets what happens afterward. When the done-check proves completion, the main agent runs it and its result decides; when it does not, the route calls for a review. Higher stakes call for a reviewer from another model family. Independent pieces can fan out when the router judges that they split cleanly. `hooks/plan_gate.py` enforces an assigned worker route at edit time, and `scripts/route_veto.py` requires a written reason of at least 20 characters for an override and logs it.

Each routing decision costs about a hundredth of a cent, with a median of half a second. In a replay of the decisions logged in an earlier, shorter window (2026-09-20 to 2026-10-22) through today's policy, the router keeps about three quarters of tasks with the main model, sends about a quarter to workers, and calls for a cross-model review on 179 of them. The router picks by fit, not by volume, so keeping most work with the main model is the router choosing well. Each task the router sends to a cheaper worker keeps the main model's allowance for the work only it should do.

## The quality layer

- **Brief lint:** `hooks/brief_lint.py` checks whether a worker brief names its target, gives a checkable completion condition and bounds its scope. A research brief needs a stated answer shape. Its advice does not block dispatch.
- **Edit gate:** `hooks/plan_gate.py` denies an inline edit to a file assigned to a worker. `scripts/route_veto.py` logs an override with its written reason.
- **Review gate:** `hooks/review_gate_hook.py` and `jev/review_gate.py` judge the shape of returned work: whether a check actually ran and passed, whether the change spans files or reaches people outside, and whether it drifts from the task. The gate recommends another read when that shape warrants one, and stays silent when a passing check ran and nothing drifted. It does not grade correctness. The gate judges completed work; an agent launched in the background is recorded as pending and judged when it finishes.
- **Dispatch and research source check:** `scripts/dispatch.py` sends a file-backed brief, reports only files the worker changed, and runs the returned-work checks where the result is visible. For research, it fetches up to 10 cited sources and 30 claims, screens pages for instructions aimed at an agent, and checks whether each page supports the claims citing it. It reads every cited link, in the report files the worker wrote as well as its final message, including links listed on their own. A flag calls for inspection; it never certifies a report.
- **Review fallback:** `jev/codex_usage.py` reads the rate-limit readings Codex writes to its session logs. When the five-hour window has 25% or less left (`review_fallback_left_percent` in `routes.example.json`), or Codex reports a limit reached, the router and the review gate name a fresh-context reviewer in place of the cross-family reviewer, and cheap-worker implementation continues. In that state dispatch declines the configured cross-family reviewer model unless `--force-reviewer` is passed, and it prints the usage reading after every run. A missing or stale reading leaves the fallback off.

In those same logs, 662 brief checks cost $0.02 and 1,137 review-gate checks cost $0.26. Along with routing, that is about 2,800 decisions for about 40 cents, from one person's use.

## Design rules worth copying

- The agent states facts, a model judges, and code decides (`jev/plan_router.py`).
- The author of a request must not answer their own question: judgement words and a named destination are rejected before anything is sent (`validate` in `jev/plan_router.py`).
- Ask about the shape of returned work, not whether it is correct (`jev/review_gate.py`).
- Prefer a check that ran to a judgement: when the record shows one ran and passed and nothing drifted, there is no review (`assess_output`).
- Ask for the presence of evidence, never its absence (`was_verified`).
- Advisory checks remain nonblocking and distinguish a service interruption from a check error (`scripts/dispatch.py`). The blocking edit gate needs a written, logged reason to override (`hooks/plan_gate.py`, `scripts/route_veto.py`).
- Label every threshold as derived or guessed, in one ledger (`jev/thresholds.py`; the source check's screening cuts are in `jev/judge.py`).
- Pin the wording of every model question. A threshold chosen against one wording does not carry over to another (`tests/test_question_wording.py`).
- Test the parsing as well as the decisions, so checks read their inputs as intended (`tests/test_parsing.py`).

## Scope

Quartermaster guides a cooperating agent on one machine; it is not a security boundary. I have used it as one person on Windows. Live judgements need an API key.

## Run it

Python 3.10 or later and Git are required. The code uses only the Python standard library. Run offline tests with `python tests/run_all.py`; no API key or network is needed.

I run this with Claude Code as the main agent and OpenAI's Codex CLI for dispatched workers; `routes.example.json` maps roles to models.

Copy `routes.example.json` to `routes.json` and edit the local role mapping; anything you leave out keeps the example's value. The local file is ignored by Git. `AGENT_CHECKS_CONFIG` can point to another JSON file. See [the hook settings example](examples/claude-settings.example.json), replace `/path/to/quartermaster` with the checkout path, and add its `hooks` object to your settings.

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

I still work that way. Now my computer science partners are AI agents, Claude Code and Codex. This layer took about two weeks, from mid-September to the start of October 2026: research, a written specification, then building and testing. The agents write most of the code. I own the design, the specifications and what counts as done. [DESIGN-LOG.md](DESIGN-LOG.md) lists those decisions in order.

This repository is a cleaned public copy of the private version that runs in my own setup. Its history starts at publication.

See [References](REFERENCES.md) and [Third-party notices](THIRD_PARTY_NOTICES.md).
