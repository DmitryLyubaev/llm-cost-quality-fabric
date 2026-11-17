# Capstone report: the data agent, direct and through Foundry

- Freeze: `963eebbaf65d`
- Fabric commit: `c26b664`
- Snapshot: 2026-11-16T05:44:19Z
- Freezes: 1
- Scored: 15 questions are scored, of 15 in the frozen set.
- Passes: 3 per route. Scoring is by rule (`lakehouse.capstone.score`), with no AI judge.

## Headline

| Route | Mean correct per pass | Per pass | Correct in all 3 | Correct in none |
|---|---|---|---|---|
| Direct | 14.00 of 15 | 14, 14, 14 | 14 | 1 |
| Through Foundry | 13.33 of 15 | 14, 14, 12 | 12 | 1 |

## Capacity units

Read by hand from the Capacity Metrics app, over each run's own time window (CU-seconds).

- Direct: 15,961.0 CU-seconds
- Through Foundry: 13,303.8 CU-seconds

## By question

| Question | Kind | Direct (right of 3) | Through Foundry (right of 3) |
|---|---|---|---|
| q01 | money | 3 | 3 |
| q02 | money | 3 | 3 |
| q03 | money | 3 | 3 |
| q04 | labels | 3 | 3 |
| q05 | rate | 3 | 3 |
| q06 | rate | 3 | 3 |
| q07 | count | 3 | 2 |
| q08 | money | 3 | 3 |
| q09 | count | 3 | 3 |
| q10 | count | 3 | 3 |
| q11 | labels | 3 | 2 |
| q12 | count | 3 | 3 |
| q13 | count | 0 | 0 |
| q14 | money | 3 | 3 |
| q15 | unanswerable | 3 | 3 |

## Verdicts

### Direct

- Tool calls: 45 in all, 1.0 per question.
- Tokens: not recorded.
- Wall time: 431.3 s in all, 9.6 s per question.
- First question asked 2026-11-17T01:02:44.377Z, last 2026-11-17T01:09:53.357Z.

| Question | Pass | Verdict | Reason | Tool calls | Tokens in | Tokens out | Wall (s) |
|---|---|---|---|---|---|---|---|
| q01 | 1 | right | correct: within 0.0243317 of 2.4331703255217607 | 1 | – | – | 14.4 |
| q01 | 2 | right | correct: within 0.0243317 of 2.4331703255217607 | 1 | – | – | 6.8 |
| q01 | 3 | right | correct: within 0.0243317 of 2.4331703255217607 | 1 | – | – | 9.0 |
| q02 | 1 | right | correct: within 0.01 of 0.0 | 1 | – | – | 6.9 |
| q02 | 2 | right | correct: within 0.01 of 0.0 | 1 | – | – | 6.4 |
| q02 | 3 | right | correct: within 0.01 of 0.0 | 1 | – | – | 6.1 |
| q03 | 1 | right | correct: within 0.0455284 of 4.5528433653465346 | 1 | – | – | 8.2 |
| q03 | 2 | right | correct: within 0.0455284 of 4.5528433653465346 | 1 | – | – | 13.2 |
| q03 | 3 | right | correct: within 0.0455284 of 4.5528433653465346 | 1 | – | – | 6.6 |
| q04 | 1 | right | correct: ['p1'] | 1 | – | – | 9.6 |
| q04 | 2 | right | correct: ['p1'] | 1 | – | – | 8.0 |
| q04 | 3 | right | correct: ['p1'] | 1 | – | – | 6.5 |
| q05 | 1 | right | correct: within 0.005 of 0.9833333333333333 | 1 | – | – | 19.0 |
| q05 | 2 | right | correct: within 0.005 of 0.9833333333333333 | 1 | – | – | 7.7 |
| q05 | 3 | right | correct: within 0.005 of 0.9833333333333333 | 1 | – | – | 12.8 |
| q06 | 1 | right | correct: within 0.005 of 0.6462882096069869 | 1 | – | – | 15.4 |
| q06 | 2 | right | correct: within 0.005 of 0.6462882096069869 | 1 | – | – | 7.7 |
| q06 | 3 | right | correct: within 0.005 of 0.6462882096069869 | 1 | – | – | 16.1 |
| q07 | 1 | right | correct: exactly 60 | 1 | – | – | 8.8 |
| q07 | 2 | right | correct: exactly 60 | 1 | – | – | 6.4 |
| q07 | 3 | right | correct: exactly 60 | 1 | – | – | 18.4 |
| q08 | 1 | right | correct: within 0.01 of 0.0013023638407161245 | 1 | – | – | 36.7 |
| q08 | 2 | right | correct: within 0.01 of 0.0013023638407161245 | 1 | – | – | 10.0 |
| q08 | 3 | right | correct: within 0.01 of 0.0013023638407161245 | 1 | – | – | 12.5 |
| q09 | 1 | right | correct: exactly 6 | 1 | – | – | 7.0 |
| q09 | 2 | right | correct: exactly 6 | 1 | – | – | 6.8 |
| q09 | 3 | right | correct: exactly 6 | 1 | – | – | 11.2 |
| q10 | 1 | right | correct: exactly 8 | 1 | – | – | 12.2 |
| q10 | 2 | right | correct: exactly 8 | 1 | – | – | 7.1 |
| q10 | 3 | right | correct: exactly 8 | 1 | – | – | 9.0 |
| q11 | 1 | right | correct: ['p4', 'p6', 'p7', 'p8'] | 1 | – | – | 7.9 |
| q11 | 2 | right | correct: ['p4', 'p6', 'p7', 'p8'] | 1 | – | – | 6.8 |
| q11 | 3 | right | correct: ['p4', 'p6', 'p7', 'p8'] | 1 | – | – | 7.4 |
| q12 | 1 | right | correct: exactly 107 | 1 | – | – | 7.0 |
| q12 | 2 | right | correct: exactly 107 | 1 | – | – | 7.6 |
| q12 | 3 | right | correct: exactly 107 | 1 | – | – | 10.8 |
| q13 | 1 | wrong | mismatch: [0.0] is not exactly 229 | 1 | – | – | 8.9 |
| q13 | 2 | wrong | mismatch: [0.0] is not exactly 229 | 1 | – | – | 6.6 |
| q13 | 3 | wrong | mismatch: [0.0] is not exactly 229 | 1 | – | – | 9.4 |
| q14 | 1 | right | correct: within 0.01 of 0.6254278509865374 | 1 | – | – | 12.1 |
| q14 | 2 | right | correct: within 0.01 of 0.6254278509865374 | 1 | – | – | 7.1 |
| q14 | 3 | right | correct: within 0.01 of 0.6254278509865374 | 1 | – | – | 5.5 |
| q15 | 1 | right | correct: refusal ('did not change') | 1 | – | – | 3.0 |
| q15 | 2 | right | correct: refusal ('did not change') | 1 | – | – | 2.2 |
| q15 | 3 | right | correct: refusal ('did not change') | 1 | – | – | 2.4 |

### Through Foundry

- Tool calls: 48 in all, 1.1 per question.
- Tokens: 57,599 in, 5,481 out.
- Wall time: 742.8 s in all, 16.5 s per question.
- First question asked 2026-11-17T03:12:05.864Z, last 2026-11-17T03:23:25.597Z.

| Question | Pass | Verdict | Reason | Tool calls | Tokens in | Tokens out | Wall (s) |
|---|---|---|---|---|---|---|---|
| q01 | 1 | right | correct: within 0.0243317 of 2.4331703255217607 | 1 | 1,228 | 69 | 15.1 |
| q01 | 2 | right | correct: within 0.0243317 of 2.4331703255217607 | 1 | 1,226 | 63 | 14.6 |
| q01 | 3 | right | correct: within 0.0243317 of 2.4331703255217607 | 1 | 1,230 | 69 | 13.3 |
| q02 | 1 | right | correct: within 0.01 of 0.0 | 1 | 1,232 | 64 | 13.8 |
| q02 | 2 | right | correct: within 0.01 of 0.0 | 1 | 1,227 | 64 | 17.1 |
| q02 | 3 | right | correct: within 0.01 of 0.0 | 1 | 1,226 | 69 | 14.4 |
| q03 | 1 | right | correct: within 0.0455284 of 4.5528433653465346 | 1 | 1,234 | 66 | 13.5 |
| q03 | 2 | right | correct: within 0.0455284 of 4.5528433653465346 | 1 | 1,221 | 65 | 13.7 |
| q03 | 3 | right | correct: within 0.0455284 of 4.5528433653465346 | 1 | 1,205 | 129 | 11.8 |
| q04 | 1 | right | correct: ['p1'] | 1 | 1,201 | 497 | 16.1 |
| q04 | 2 | right | correct: ['p1'] | 1 | 1,283 | 97 | 13.8 |
| q04 | 3 | right | correct: ['p1'] | 1 | 1,187 | 398 | 19.1 |
| q05 | 1 | right | correct: within 0.005 of 0.9833333333333333 | 1 | 1,213 | 66 | 13.1 |
| q05 | 2 | right | correct: within 0.005 of 0.9833333333333333 | 2 | 1,940 | 159 | 38.5 |
| q05 | 3 | right | correct: within 0.005 of 0.9833333333333333 | 1 | 1,224 | 66 | 13.7 |
| q06 | 1 | right | correct: within 0.005 of 0.6462882096069869 | 2 | 2,002 | 164 | 25.7 |
| q06 | 2 | right | correct: within 0.005 of 0.6462882096069869 | 1 | 1,233 | 68 | 11.3 |
| q06 | 3 | right | correct: within 0.005 of 0.6462882096069869 | 2 | 2,018 | 225 | 27.6 |
| q07 | 1 | right | correct: exactly 60 | 1 | 1,180 | 60 | 14.9 |
| q07 | 2 | right | correct: exactly 60 | 1 | 1,189 | 61 | 10.7 |
| q07 | 3 | wrong | mismatch: [0.0] is not exactly 60 | 1 | 1,224 | 61 | 16.5 |
| q08 | 1 | right | correct: within 0.01 of 0.0013023638407161245 | 1 | 1,260 | 92 | 20.1 |
| q08 | 2 | right | correct: within 0.01 of 0.0013023638407161245 | 1 | 1,241 | 77 | 16.1 |
| q08 | 3 | right | correct: within 0.01 of 0.0013023638407161245 | 1 | 1,257 | 88 | 15.7 |
| q09 | 1 | right | correct: exactly 6 | 1 | 1,192 | 58 | 14.2 |
| q09 | 2 | right | correct: exactly 6 | 1 | 1,192 | 58 | 11.6 |
| q09 | 3 | right | correct: exactly 6 | 1 | 1,195 | 58 | 10.4 |
| q10 | 1 | right | correct: exactly 8 | 1 | 1,202 | 59 | 12.1 |
| q10 | 2 | right | correct: exactly 8 | 1 | 1,204 | 58 | 12.7 |
| q10 | 3 | right | correct: exactly 8 | 1 | 1,188 | 59 | 10.2 |
| q11 | 1 | right | correct: ['p4', 'p6', 'p7', 'p8'] | 1 | 1,195 | 74 | 11.6 |
| q11 | 2 | right | correct: ['p4', 'p6', 'p7', 'p8'] | 1 | 1,210 | 82 | 14.2 |
| q11 | 3 | wrong | mismatch: extra ['p2'] | 1 | 1,328 | 121 | 17.8 |
| q12 | 1 | right | correct: exactly 107 | 1 | 1,218 | 69 | 10.8 |
| q12 | 2 | right | correct: exactly 107 | 1 | 1,218 | 85 | 13.8 |
| q12 | 3 | right | correct: exactly 107 | 1 | 1,233 | 65 | 15.1 |
| q13 | 1 | wrong | mismatch: [0.0] is not exactly 229 | 1 | 1,217 | 65 | 10.9 |
| q13 | 2 | wrong | mismatch: [0.0] is not exactly 229 | 1 | 1,238 | 65 | 14.1 |
| q13 | 3 | wrong | mismatch: [0.0] is not exactly 229 | 1 | 1,257 | 65 | 14.0 |
| q14 | 1 | right | correct: within 0.01 of 0.6254278509865374 | 1 | 1,287 | 96 | 14.6 |
| q14 | 2 | right | correct: within 0.01 of 0.6254278509865374 | 1 | 1,284 | 92 | 14.8 |
| q14 | 3 | right | correct: within 0.01 of 0.6254278509865374 | 1 | 1,268 | 71 | 13.9 |
| q15 | 1 | right | correct: caveat ('not in the data') | 1 | 1,276 | 524 | 20.3 |
| q15 | 2 | right | correct: caveat ('not in the data') | 1 | 1,256 | 460 | 22.4 |
| q15 | 3 | right | correct: caveat ('not in the data') | 1 | 1,260 | 360 | 63.2 |

## Unanswerable answers

Every answer to an unanswerable question, right or wrong, with its tool calls.

#### Direct: q15, pass 1: right, 1 tool call

> The version did not change between measured runs of gpt-5.6-luna, so the data holds no comparison of its success rate from one model version to the next.

Reason: correct: refusal ('did not change')

#### Direct: q15, pass 2: right, 1 tool call

> The model version did not change between measured runs, so the data holds no comparison of success rate from one version of gpt-5.6-luna to the next.

Reason: correct: refusal ('did not change')

#### Direct: q15, pass 3: right, 1 tool call

> The model version did not change between measured runs, so the data holds no comparison of success rate from one model version of gpt-5.6-luna to the next.

Reason: correct: refusal ('did not change')

#### Through Foundry: q15, pass 1: right, 1 tool call

> Not in the data: gpt-5.6-luna has scored results on only a single model version, so there is no next version to compare. 

Reason: correct: caveat ('not in the data')

#### Through Foundry: q15, pass 2: right, 1 tool call

> Not in the data: it does not record distinct model versions for gpt-5.6-luna measured runs, so the requested success-rate view is unavailable.

Reason: correct: caveat ('not in the data')

#### Through Foundry: q15, pass 3: right, 1 tool call

> Not in the data: it does not record a separate model version for gpt-5.6-luna across measured runs.

Reason: correct: caveat ('not in the data')

## Wrong answers

#### Direct: q13, pass 1

> 0 scored results used the text-embedding-3-large model in the p2-retrieval study, based on counting scored attempts filtered to that study and model.

Reason: mismatch: [0.0] is not exactly 229

#### Direct: q13, pass 2

> 0 scored results used the text-embedding-3-large model in the p2-retrieval study, based on counting scored attempts for that model within that study.

Reason: mismatch: [0.0] is not exactly 229

#### Direct: q13, pass 3

> 0 scored results used the text-embedding-3-large model in the p2-retrieval study, counting rows in gold.fact_eval_attempt with attempt_kind = 'scored', model = 'text-embedding-3-large', and study = 'p2-retrieval'.

Reason: mismatch: [0.0] is not exactly 229

#### Through Foundry: q07, pass 3

> 0 scored results.

Reason: mismatch: [0.0] is not exactly 60

#### Through Foundry: q11, pass 3

> - p4 (API Management as the AI gateway)
> - p6 (App Service front end)
> - p7 (Short-lived build agents on Container Apps jobs)
> - p8 (Azure Pipelines delivery for project 2)
>
> These had at least 1 live check with verdict = fail.

Reason: mismatch: extra ['p2']

#### Through Foundry: q13, pass 1

> 0 scored results.

Reason: mismatch: [0.0] is not exactly 229

#### Through Foundry: q13, pass 2

> 0 scored results.

Reason: mismatch: [0.0] is not exactly 229

#### Through Foundry: q13, pass 3

> 0 scored results.

Reason: mismatch: [0.0] is not exactly 229
