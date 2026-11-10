# Did routing shift spend away from expensive models?

Measured 2026-11-09 from my local Claude Code and Codex usage logs. The router went live on 2026-09-20 (first logged decision 2026-09-20 00:19 UTC).

All costs are **list-price equivalents**. I pay flat subscriptions (a Claude plan and a ChatGPT/Codex plan), so none of this is money actually spent or saved.

## Windows and terms

- **Before:** 2026-08-24 to 2026-09-19 (27 calendar days, the first stretch where Codex logs carry full token counts).
- **After:** 2026-09-20 to 2026-11-09 (51 calendar days, 47 with activity, last day partial).
- **Prompt:** a prompt I typed (or a slash command) in a top-level Claude Code session: 1,322 before, 2,316 after.
- **Excluding batch jobs:** leaving out Codex sessions with 3 or fewer API calls, almost all one-shot jobs on the cheapest model: 10,974 sessions before, 2,174 after.
- **Tiers** follow list input price per million tokens: premium is $4 or more, mid is $1 to $3, economy (the cheap tier) is under $1.

## Headlines

1. **Volume was similar:** 49 prompts per calendar day before, 45 after.
2. **The cheapest model took on most of the new build work.** Excluding batch jobs, it went from writing 7.5% of all output tokens to 41%, at $0.91 a day at list price after. The same tokens at Claude Opus 5.5 rates would list at about $26 a day.
3. **Build work listed far below Opus rates.** Build (the cheap model plus two mid-priced ones) listed at $220 after, for tokens that would list at about $1,627 on Opus 5.5.
4. **Review is a deliberate spend.** Review's share of list-price cost rose from 5.1% to 10.9%, and review spend per day went from $7.80 to $14.50. The cross-family reviewer ran 430 reviews after the router went live; about 7 in 10 raised at least one issue (keyword labels, checked by hand on a random sample).
5. **Overall,** the after period listed $6,803, about 14% below a counterfactual of about $7,877 where every Codex token is billed at Opus 5.5 rates. Against Sonnet 5.5 rates the gap is about $180.
   - This assumes the same tokens, token for token. If Claude would have needed fewer tokens, the 14% is overstated.
   - It leaves out Claude cache writes, which understates the Opus side.
   - It has no rework allowance, because no data supports one.
   - Codex work already existed before the router, so not every after-window Codex token is work the router moved.
6. **List cost per prompt fell 6%,** from $3.14 to $2.94. New models were cheaper per token over the same weeks, so this cannot be attributed to the router alone.
7. **The router is nearly free to run:** 1,419 routing calls to Jev cost $0.14 at list price, and 1,486 review-gate calls cost $0.39.

## Table

| Metric | Before | After |
|---|---|---|
| Calendar days (days with activity) | 27 (27) | 51 (47) |
| Typed prompts | 1,322 | 2,316 |
| Prompts per calendar day | 49.0 | 45.4 |
| List-price cost, Claude + Codex | $4,152 | $6,803 |
| List cost per calendar day | $154 | $133 |
| List cost per prompt | $3.14 | $2.94 |
| Cost per prompt, after repriced at predecessor prices | $3.14 | $5.02 |
| Premium share of list cost | 96.0% | 94.2% |
| *Excluding batch jobs:* | | |
| Cheap-tier share of API calls | 9.5% | 29.8% |
| Cheap-tier API calls per calendar day | 72 | 437 |
| Cheap-tier share of non-cache-read tokens | 8.9% | 24.1% |
| Premium share of non-cache-read tokens | 82.5% | 62.3% |
| Premium share of output tokens | 85.5% | 50.6% |
| Premium non-cache-read tokens per calendar day | 5.3M | 6.4M |
| Tokens per prompt (incl. cache reads) | 3.54M | 7.77M |

## Confounders

- **New models and prices arrived mid-comparison,** most at lower list prices.
- **Tier membership moved with the models.** One model family was premium-priced before and mid-priced after; tiers follow list input price, not model family.
- **Longer contexts.** Cache-read tokens per prompt roughly doubled, tracking session length and a larger default context window. This inflates the repriced figure.
- **The repriced view is one-sided.** Only the after window is repriced.
- **A model-use policy change** the day before the router went live moved work off the most expensive model.
- **Delegation predates the router.** Cheap workers were already in use from late July, so "before" is not a no-delegation baseline.
- **Batch jobs.** About 11,000 one-shot cheap-model sessions in the before window (2,174 after) dominate raw call counts. With them included, the cheap tier's share of calls reads 41% before and 32% after, which reflects those jobs rather than routing. Their list cost is small: $55 before, $12 after.
- **Project mix** changed from week to week and is not controlled.
- **No quality measure.** Nothing here says whether the work came out better or worse.

## Data gaps

- Direct Codex prompts are not in the prompt count (only Claude prompts are), though Codex cost is.
- Prompts sent by one agent session to another cannot be told apart from typed prompts.
- A few internal models have no public price (under 0.2% of tokens).
- Premium rates (fast mode, regional inference, long-context pricing) are ignored; everything is priced at standard short-context rates.
- If a forked Codex session copies earlier token counts into a new file, those tokens are counted twice; deduplication runs only within a file.

## Method

- **Extraction:** every Claude Code transcript (assistant messages deduplicated by message id, taking the max of each usage counter) and every Codex session log (each token-count event's last usage, skipping repeats). No message text was written out.
- **Prompts:** user records in top-level Claude sessions that are not meta, sidechain, compact summaries, tool results or injected notices, unique by id; then only typed prompts and slash commands.
- **Pricing:** each row priced at the public list prices from [Anthropic](https://platform.claude.com/docs/en/about-claude/pricing) and [OpenAI](https://developers.openai.com/api/docs/pricing), fetched 2026-11-09.
- **Router facts:** from the router's own call log (cost per call) and outcome log.
- **Review:** a separate reviewer, given the files and the task but not the author's reasoning, checked the report before publication, and its corrections are applied above.
