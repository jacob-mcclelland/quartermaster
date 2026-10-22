# Routing and the cost of delegated work

## What this is

I compare the same tokens for work the router delegated from 2026-09-20 to 2026-10-22 (33 days), priced at Claude Opus 5.5 list rates. I assume Opus would have used the same tokens with the same cache-hit mix. This is API-equivalent: I pay flat subscriptions, so it is not dollars returned and does not measure subscription quota directly. It covers the delegated slice only, has no matched-quality evaluation, and does not price the main model's own work on the tasks it kept.

## Routing decisions

From the decision service's call log, I counted 1,047 routing decisions, first on 2026-09-20 and last on 2026-10-22, at $0.1037 in total with a median latency of 0.50 s. The log stores the classifier's answers, not the destination. Replaying the 1,038 decisions that used the current eight-question set through today's policy gives:

| Replayed destination | Decisions | Share |
|---|---:|---:|
| Kept with the main model | 783 | 75% |
| Cheap worker | 232 | 22% |
| Long-context worker | 20 | 2% |
| Consult then main model | 3 | 0.3% |

Of these, 179 also called for a cross-model review. The replay uses today's policy, not the policy in force on each date, and cannot apply the rule for files a worker cannot reach because file lists are not logged.

## Worker sessions

Token totals come from the Codex session logs, read for counts only: each session's launcher, model and final token totals (input, cached input, output). I dropped interactive sessions and Codex's automatic approval reviewer. I counted a session resumed into a second file once. A session counts as delegated work when its user messages contain the required delegation clause at least twice. The machine rules quote it once in every session, so a second occurrence usually comes from a task brief. This heuristic can err both ways. I classed sessions on a reviewer model as reviews.

| Category | Sessions | Uncached input | Cached input | Output | In the headline |
|---|---:|---:|---:|---:|---|
| Delegated work | 279 | 30.74M | 569.79M | 8.77M | Yes |
| Delegated cross-model reviews | 147 | 8.77M | 71.42M | 0.53M | No |
| Bulk pre-generation for another project | 111 | 19.37M | 463.33M | 7.72M | No |
| Codex Desktop native subagents | 86 | 16.14M | 342.22M | 4.05M | No |
| Pipeline and product runs without a brief | 2,283 | 12.77M | 120.91M | 1.60M | No |

## Prices

USD per 1M tokens, list prices as of 2026-10-22 unless noted:

| Model | Input | Cached input | Output | Source |
|---|---:|---:|---:|---|
| GPT-6 Luna | $0.10 | $0.01 | $0.50 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| GPT-6 Sol (priced with the GPT-6.1 Sol row as a proxy) | $2.00 | $0.10 | $10.00 | OpenAI pricing |
| GPT-6 Astra | $10.00 | $1.00 | $50.00 | OpenAI pricing |
| GPT-5.6 Luna (37 sessions) | $0.20 | $0.02 | $1.20 | OpenAI pricing as recorded on 2026-09-19 |
| GPT-5.6 Terra (2 sessions) | $2.00 | $0.20 | $12.00 | OpenAI pricing as recorded on 2026-09-19 |
| Claude Opus 5.5 | $4.00 | $0.20 (cache read) | $20.00 | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |

Cache writes are omitted on both sides because the Codex logs report no cache-write tokens. I do not model long-context surcharges.

## Cost

I apply this formula once at the worker's prices and once at Opus 5.5's prices:

`cost = (uncached input x input price + cached input x cached price + output x output price) / 1,000,000`

| Delegated work | Sessions | At worker list price | Same tokens at Opus 5.5 |
|---|---:|---:|---:|
| GPT-6 Luna | 218 | $10.21 | $326.53 |
| GPT-5.6 Luna | 37 | $4.13 | $58.22 |
| GPT-6 Sol | 22 | $13.31 | $26.63 |
| GPT-5.6 Terra | 2 | $0.64 | $0.95 |
| **Total** | **279** | **$28.31** | **$412.34** |

Worker cost is 6.86% of the Opus reference ($28.31 / $412.34), which the README rounds to about 7%. Rows are rounded individually, so they sum to a cent or two less than the totals. Cross-model reviews (147 sessions, $185.71 at list price) are a quality step and are not in the headline. If Sol's cached rate were $0.20 instead of the proxy's $0.10, worker cost would rise by $5.48.

## Limits of the comparison

I cannot tie worker sessions one to one to routing decisions (279 sessions against 252 replayed worker routes: a route can produce retries or several pieces, and some sessions were started at my direct request). Opus is not the alternative for every session. This comparison is not a measured causal saving.
