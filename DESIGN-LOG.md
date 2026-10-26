# Design log

These are dated decisions from the private working version of Quartermaster, in order. I condensed them from that repository's commit messages. The entries describe the choices and how the checks work now.

## 2026-09-18: Research before code

I researched the decision model and published routers before writing the checks. I treat the model's numbers as scores to rank by, always offer an "other" option, and leave arithmetic, counting and date math to code.

## 2026-09-20: First client and router

- **Pin the model version.** Thresholds are tied to the model whose scores informed them.
- **Ask each choice with both option orders.** Reversing the options gives the client a way to account for order effects.
- **Route on how well the work is specified, not on how hard it sounds.** Targets and a completion condition can be read from a brief; difficulty cannot, so the router does not ask for it.
- **Ask the review gate about returned work's shape.** Its questions concern evidence and reach, rather than a verdict on correctness.
- **Measure API limits directly.** The client uses observed limits rather than assuming what the documentation leaves unspecified.
- **Ask about the presence of evidence.** The review gate asks whether a check ran and passed; it does not ask a second question about absence.

## 2026-09-20: Route a digest of facts, never the raw prompt

A prompt within a continuing conversation can be shorthand whose task lives in earlier turns. The main agent therefore writes a digest with the goal, named files, change, done-check, dependencies, scope, audience and reversibility. Jev judges that digest, and code decides the route. `validate()` rejects judgement words and a field naming an expected destination, so the digest author supplies facts without answering the routing question.

## 2026-09-20: Enforce routed work

The route has an edit gate. An inline edit to a file assigned to a worker is denied by `hooks/plan_gate.py`. An override goes through `scripts/route_veto.py`, requires a written reason of at least 20 characters, and is logged with the worker and files. The override is one script, so the authority to override stays with me.

## 2026-09-20: Reserve the long-context worker for long-context recall

The long-context role is for work that needs recall across a long document, transcript or many source files. `route()` uses its long-context question for that choice, while the separate complexity question decides whether work stays with the main model.

## 2026-09-20: Label every threshold

`jev/thresholds.py` keeps the cuts in one ledger and labels each as derived or guessed. A derived cut records its argument; it is not presented as a measured outcome. The source check's screening cuts in `jev/judge.py` are labelled too.

## 2026-09-20: The routing decision belongs to the router

The digest may not name an expected destination. Jev answers the routing questions and `route()` applies policy to those answers. An override needs a specific reason the router could not have known; disagreement alone does not change the route.

## 2026-09-20: Watch outcomes, do not score the router

I watch the split of work across roles, cost and how often work returns for another pass. These need nobody's opinion. Scoring the router against the orchestrating agent's own predictions would carry that agent's view straight back in, so the router is not scored that way.

## 2026-09-20: Make the documentation describe what actually runs

I keep the README tied to the current hooks, router, dispatch wrapper and checks. The public copy names the files that implement each behavior so readers can inspect the running policy.

## 2026-09-28: Move to a new worker model family

The role mapping can move workers to a new model family through local configuration. The router's printed reasons state where evidence is still needed, so an older model comparison is not presented as a measurement of a new role.

## 2026-09-28: Decide reachability in code, not with a question

Whether a worker can write a named path is a path fact. `jev/plan_router.py` compares paths against configured protected directories and keeps unreachable work in the main session. Tests cover names that merely contain a protected directory's name and paths with mixed case.

## 2026-10-01: Run checks inside the dispatch wrapper

`scripts/dispatch.py` runs the returned-work checks after the worker completes, where a background job's result is visible. It closes stdin, passes a file-backed brief to the worker, and reports only files changed during that dispatch. For research, it fetches cited sources, screens pages for instructions aimed at an agent, and checks whether the pages support the cited claims.

## 2026-10-01: Keep concurrent sessions apart

Turn markers and other state files are keyed by session. An offline test interleaves two sessions to check that routing and edit-gate state stay associated with the right session.

## 2026-10-19: Preparing this public copy

This copy includes the router's fact digest and policy, the brief and edit checks, the dispatch wrapper, the review gate and the research source check. Tests cover command and report parsing, the source check's no-key path, the wording of model questions and the labelled threshold ledger.

## 2026-10-25: Judge completed work, not launches

The review gate judges completed work: background agent launches are recorded as pending, then assessed from the agent's final result when a completion notification arrives. Pending and final verdicts are appended to `logs/review-gate-verdicts.jsonl`; read-only dispatches tell the gate no changes were expected, so it skips the diff question. Hook responses use ASCII-escaped JSON, and the route hook keeps its routing behavior when the review gate module cannot be imported.
