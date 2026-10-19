# Design log

Dated decisions from the private working version of these checks, in order. Each entry says what changed and why. They are condensed from that repository's commit messages, which record who caught what: some calls were mine, some came from the orchestrating agent, and some came from an independent check. Model names, prices and project names are left out; the reasoning is not.

## 2026-09-18: Research before code

Before writing anything, I researched the decision model and the routers other people had published. The findings set the first rules: treat the model's numbers as scores to rank by, not as probabilities; always offer an "other" option; never let it do arithmetic, counting or date math.

## 2026-09-20: First client and router

- **Pinned the model version.** A moving "latest" version would silently invalidate every threshold.
- **Asked every choice twice, with the options reversed.** A documented example from the vendor answered differently when its options were reversed, in every run we tried.
- **Routed on how well the work is specified, not how hard it is.** Difficulty is not in the request, so asking for it gets a confident guess.
- **Made the review gate ask about the shape of returned work, never whether it is correct.**
- **Measured the API limits the documentation left out** instead of assuming them.
- **Traced three bugs that day to badly worded questions, not to the model.** One asked about the presence of evidence and then about its absence, and the two answers contradicted each other on the same record. Since then: ask about presence, never absence.

## 2026-09-20: Route a digest of facts, never the raw prompt

The first router forwarded my prompts as typed. They were conversational shorthand ("do it", "fix the three gaps"), so the model saw an empty box and delegated nothing on 0 of 11 real prompts. My redesign: the orchestrating agent states the facts of the work, the model judges, and code decides. A digest that contains judgement words ("simple", "risky") is rejected before any call, because its author must not answer their own question.

## 2026-09-20: Enforce routed work

Advisory routing was overridden every time, because following the route cost effort and ignoring it cost nothing. So I inverted the costs:

- An inline edit to a file routed to a worker is now denied.
- Overriding the denial needs a written reason of at least 20 characters, logged with the worker and the files.
- The override is one script. Deleting it removes overriding entirely, so the authority stays with me.

The change was checked independently of the worker that built it, because that worker wrote both the code and its test.

## 2026-09-20: Reserve the long-context worker for long-context recall

One routing band rested on a recommendation copied into a number, never checked against the cost-per-finished-task data in the same document, which contradicted it. The band was removed. The long-context worker is now chosen only when the work needs recall across a long input.

## 2026-09-20: Label every threshold

Prompted by that band, every threshold was checked against the evidence behind it. Four of seven turned out to be guesses, and each is now labelled as a guess instead of passing for a measured number. No values changed: a derived number is an argument, not a measurement.

## 2026-09-20: The routing decision belongs to the router

Left to its own judgement, the orchestrating agent delegated nothing across an entire session, then delegated straight away once delegation became an instruction. Two changes followed:

- A digest may no longer name an expected destination.
- Disagreeing with a route is not grounds for overriding it. An override is reserved for something specific the router could not have known.

## 2026-09-20: Watch outcomes, do not score the router

The orchestrating agent twice proposed scoring the router against its own predictions. I ruled that out: any scoring designed by the party the router replaces carries that party's bias straight back in. What gets watched instead needs nobody's opinion: the split of work across workers, the cost, and how often work comes back for a second pass.

## 2026-09-20: Make the documentation describe what actually runs

The README had drifted from the code. It claimed four hooks when seven ran, and listed routing as unbuilt when it was built and enforced. Rewritten against the live setup.

## 2026-09-28: Move to a new worker model family

A new model family shipped after the research, and the router was still dispatching to superseded models. I caught one of the two stale choices. The router's printed reasons now say where evidence is missing, instead of reusing figures measured on the old models.

## 2026-09-28: Decide reachability in code, not with a question

A routine edit to a configuration file was routed to a worker that could not write there, and needed the first logged override. Whether a worker can reach a path is a fact, not a judgement, so code now decides it. The tests include near misses: a file whose name merely contains a protected folder's name, and a protected path in mixed case.

## 2026-10-01: Fix hooks that had gone blind

The dispatch command changed on 2026-09-28. The brief linter and the review gate still parsed the old command, so for three days neither saw a single dispatch, and nothing reported it. The changes:

- **The checks moved into the dispatch wrapper.** They now run when the worker finishes, which is the only place a background job's result is visible.
- **Stdin is closed.** An open stdin had hung a worker.
- **Windows briefs reach the worker intact.** Windows cuts a batch file's arguments at the first newline, so the wrapper bypasses the npm shim.
- **The diff covers only the worker's changes**, even in a tree other agents are editing.
- **Research reports get a source check:** every cited page is fetched, screened for instructions aimed at an agent, and checked against the claims that cite it.

## 2026-10-01: Keep concurrent sessions apart

Two sessions running at once overwrote each other's turn state, and the edit gate falsely reported an unrouted turn. Every state file is now keyed by session, with an offline test that interleaves two sessions.

## 2026-10-19: Prepare this public copy

While this public copy was prepared, two agent edits broke checks without failing a single test:

- **A text clean-up turned every `?` into `:`** in five files. That broke the regular expressions that read commands, npm shims and research reports. A diff against the private version caught it.
- **A trimmed file lost the import the research check needed.** The check fails open, so it would simply have gone quiet. An independent review caught it.

What changed in response:

- Tests now cover that parsing and the full no-key path of the source check.
- A crash in a check is reported as a bug, not as an outage.
- The wording of every model question is pinned by a test.
- Every threshold the checks use now lives in one labelled ledger.
