# Capstone, plan 1: the data agent and the analyst, offline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and test, with no Azure, Fabric, Foundry or GitHub calls, everything the capstone's
live run needs:
- the expected-answer machinery, the scorer and the freeze;
- the data agent's definition, its snapshot lakehouse and its notebooks;
- the deploy and publish command;
- the F2 and workspace Terraform;
- the direct-route runner and the report renderer;
- in project 3's repo, the portfolio analyst console.

**Architecture:**
- **This repo** owns the questions, the answers, the scoring, the freeze, the data agent (`fabric-capstone/`, never `fabric/`) and the report.
- **Project 3's repo** owns the analyst. It refuses to run off-freeze and writes records in the shared schema. Those records are copied here and scored by the same code.

**Tech Stack:**
- This repo: project 9's stack (PySpark 4.1.1, delta-spark 4.2.0, Python 3.13, fabric-cicd 1.4.0), plus Terraform `azurerm` (`azurerm_fabric_capacity`) and `microsoft/fabric`.
- Project 3: .NET 10, Azure.AI.Projects 2.0.1 (unchanged pin), xunit.v3.

**Spec:** `docs/superpowers/specs/2026-10-25-capstone-data-agent-design.md` (approved 25 Oct 2026).
**Facts:** `docs/research/2026-10-25-data-agent-facts.md`. **Plan 2** (written after this merges) is
the live run (spec §4.7).

## Global Constraints

- Project 9's rules hold:
  - commits as `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`;
  - no push without the owner's yes;
  - no Azure, Fabric, Foundry or GitHub calls in this plan;
  - Terraform `fmt` / `init -backend=false` / `validate` only, in WSL;
  - tests in WSL for this repo, `dotnet test` for project 3.
- The data agent and its lakehouse live in `fabric-capstone/`. Nothing capstone-related goes into `fabric/` or `deploy.yml`.
- The data agent's source is the lakehouse `lh_capstone` (a snapshot of gold, `gold` schema). Answers are capped at 25 rows.
- No hand-typed expected value. Every expected answer is an answer function plus an optional selector.
- Scoring is by rule, with no AI judge:
  - money: within 1% or 0.01 of the billing currency;
  - counts: exact;
  - rates: within 0.005;
  - labels: equal sets;
  - "unanswerable": passes only on a refusal or an explicit caveat;
  - an unreadable answer is wrong.
- The freeze hashes:
  - the questions, the expected answers, the rules and the snapshot time;
  - the data agent definition;
  - the analyst's instructions;
  - both models.

  The practice set is never scored and never overlaps the frozen 15.
- Every live step runs as `fabric@`. No secret and no new federated credential.
- No identifiers committed: no tenant, subscription or Entra ID, no UPN, no endpoint or MCP URL. Fabric workspace and item GUIDs are allowed.

## Review Focus

1. **Answers with formatted numbers:** `A$1,234.56`, `98.3%`, `1.2k`, `0.0047 USD`. The parser normalises them, a percent becomes a fraction for rate questions, and a stated currency that differs from the expected one is wrong. *(Task 2.)*
2. **A "shotgun" answer that lists every project's figure** for a single-value question. It's wrong when it contains more than 3 numbers of the scored kind, even if one of them matches. *(Task 2.)*
3. **A file edited after the freeze:** a question, an example query or the analyst's instructions. Verification fails and names the component that changed. *(Tasks 3 and 8.)*
4. **Raw tool output that holds a URL, a GUID or an email.** It is redacted before any record is written, in both repos. *(Tasks 7 and 8.)*
5. **An example query that drifts from gold.** Each one runs on the fixture lake and must equal its paired answer function. *(Task 4.)*

---

## File structure (new or changed)

```
src/lakehouse/capstone/answers.py, expected.py, score.py, freeze.py, snapshot.py, report.py  (Tasks 1–3, 4, 7)
capstone/questions.json, practice.json, record.schema.json        (Tasks 2, 7)
fabric-capstone/da_portfolio.DataAgent/, lh_capstone.Lakehouse/,
  nb_capstone_snapshot.Notebook/, nb_capstone_expected.Notebook/, parameter.yml   (Tasks 4–5)
.deploy/deploy.py (publish-capstone)                              (Task 5)
infra/capstone/, src/lakehouse/seeds/rg_rules.csv, gold PROJECTS (Task 6)
tools/ask_data_agent.py, tools/capstone_freeze.py, tools/capstone_report.py   (Tasks 3, 7)
foundry-workshop-agent: src/Portfolio.Analyst/, tests/Portfolio.Analyst.Tests/   (Task 8)
```

---

### Task 1: Capstone answer functions and the expected-answer builder

**Files:**
- Create `src/lakehouse/capstone/__init__.py`, `answers.py`, `expected.py` and `tests/test_capstone_expected.py`.

**Interfaces:**
- **`answers.py`**, each function `(spark, paths, **args) -> DataFrame`:
  - `spend_for_month(month: str)`: project and billing currency, total for one `yyyy-MM`;
  - `live_check_counts()`: verdict and count per project;
  - `eval_count_by_arm(study: str)`: arm, n scored;
  - `cost_by_project_between(start: str, end: str)`: a closed date range.

  Each one's docstring says the question it serves.
- **`Source(function: str, args: dict, select: dict | None, column: str | None)`**:
  - `function` is a dotted name, either `lakehouse.answers.*`, `lakehouse.gateway.answers.*` or `lakehouse.capstone.answers.*`;
  - `select` holds column-to-value equality filters;
  - `column` names the single cell to return. If it's None, the result is the set of the selected rows' first column.
- **`compute_expected(spark, paths, questions: list[dict]) -> dict[str, dict]`**:
  - returns `{question_id: {"value": number | list[str] | None, "currency": str | None, "rows": int}}`;
  - raises ValueError when a selector matches more than one row for a single-cell question, matches none, or yields more than 25 rows.

- [ ] **Step 1: Failing tests on `loaded_lake`:**
  - each new function's result on the fixture;
  - `compute_expected` for a cell question (`cost_per_project` → `untagged`/`AUD`/`total`) and for a set question;
  - a selector that matches none or two rows raises, and so does more than 25 rows;
  - an unknown function name raises;
  - only the three allowed modules are importable.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. **Step 5: Commit** `Add the capstone's answer functions and expected-answer builder`

### Task 2: The question set and the scorer

**Files:**
- Create `capstone/questions.json`, `capstone/practice.json`, `src/lakehouse/capstone/score.py`, `tests/test_capstone_score.py` and `tests/test_capstone_questions.py`.

**Interfaces:**
- **Question fields:** `{"id": "q01", "text": "...", "kind": "money|count|rate|labels|unanswerable", "source": Source-as-dict | null}`.
- **The frozen set:** 15 questions in `questions.json`, spread as spec §4.4 lists. Exactly one has the kind `unanswerable`, with no source.
- **The practice set:** 10 questions in `practice.json`, with no text overlapping the frozen set.
- **`parse_numbers(text: str) -> list[Number]`**, where `Number(value: float, percent: bool, currency: str | None)`:
  - handles `,` thousands separators, `k` / `M` suffixes, `%`, `A$`, `AU$`, `AUD`, `$`, `USD` and `US$`;
  - a bare `$` has currency `None`.
- **`score(answer_text: str, expected: dict, kind: str) -> Verdict`**, where `Verdict(correct: bool, reason: str)`:
  - money passes within 1% or 0.01;
  - counts must be exact;
  - rates pass within 0.005, with a percent read as a fraction;
  - labels must form an equal set, matched case-insensitively against the expected labels found in the text;
  - `unanswerable` passes on a refusal or caveat phrase from a fixed list (`not in the data`, `can't`, `cannot`, `no version`, `didn't change`, …);
  - more than 3 numbers of the scored kind is wrong (`shotgun`);
  - a stated currency other than the expected one is wrong;
  - no number at all is wrong (`unreadable`).

- [ ] **Step 1: Failing tests:**
  - each rule, pass and fail;
  - each Review Focus 1 format;
  - the shotgun rule (Review Focus 2);
  - the currency mismatch;
  - a refusal against a non-refusal for `unanswerable`;
  - `questions.json` has 15 entries with unique IDs and exactly one `unanswerable`;
  - `practice.json` has 10 entries and doesn't overlap the frozen set;
  - every source names an existing function, checked with `importlib`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement, and write the 15 and the 10 questions. **Step 4:** PASS.
  **Step 5: Commit** `Add the capstone question sets and the rule-based scorer`

### Task 3: The freeze

**Files:**
- Create `src/lakehouse/capstone/freeze.py`, `tools/capstone_freeze.py` and `tests/test_capstone_freeze.py`.

**Interfaces:**
- **`build_freeze(questions, expected, snapshot_time: str, data_agent_dir: Path, analyst_instructions: str, models: dict[str, str]) -> dict`** builds `{"components": {name: sha256}, "hash": sha256, "short": first 12}`. The components are `questions`, `expected`, `rules` (`score.py`'s source), `snapshot_time`, `data_agent`, `analyst_instructions` and `models`.
  - The `data_agent` component hashes every file under the folder: sorted relative paths, LF-normalised content.
- **`verify_freeze(freeze: dict, **same inputs) -> None`** raises `FreezeBroken(component)` on the first mismatch.
- **`tools/capstone_freeze.py freeze|verify`** arguments:
  - `--analyst-repo <path> --analyst-commit <sha>` reads `src/Portfolio.Analyst/instructions.md` at that commit with `git show`;
  - `--expected capstone/expected.json`;
  - `--snapshot-time`;
  - `--model-direct`, `--model-foundry`.

  It writes and checks `capstone/freeze.json`.

- [ ] **Step 1: Failing tests:**
  - the hash is deterministic regardless of file order and CRLF;
  - each component changed alone breaks verification and names it (Review Focus 3);
  - the CLI's `git show` path is exercised against a temporary git repo.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. **Step 5: Commit** `Add the capstone freeze`

### Task 4: The data agent definition, the snapshot lakehouse and the notebooks

**Files:**
- Create the data agent definition `fabric-capstone/da_portfolio.DataAgent/`, in its git form per the research file's section 3:
  - `.platform`;
  - `Files/Config/data_agent.json`;
  - `draft/stage_config.json`, holding the AI instructions;
  - `draft/lakehouse-tables-lh_capstone/datasource.json`, selecting the 9 gold tables, with the empty GUID for workspace and artifact;
  - `draft/lakehouse-tables-lh_capstone/fewshots.json`, holding about 20 examples.
- Create `fabric-capstone/lh_capstone.Lakehouse/`, with `.platform` and schemas enabled.
- Create the notebooks `fabric-capstone/nb_capstone_snapshot.Notebook/` and `nb_capstone_expected.Notebook/`.
- Create `src/lakehouse/capstone/snapshot.py`, `tests/test_capstone_agent.py` and `tests/test_capstone_snapshot.py`.

**Interfaces:**
- **`copy_gold(spark, source: LakePaths, target: LakePaths, tables: list[str]) -> dict`**:
  - overwrites each `target` gold table with the source's;
  - returns `{"snapshot_time": utc_iso, "rows": {table: n}}`;
  - writes the same dict to `Files/capstone/snapshot.json` in the target.
- **The snapshot notebook** takes the prod workspace ID and the lakehouse ID as parameters, and builds the source paths with `fabric_paths`. They are passed at run time, never committed.
- **The expected notebook** reads `/lakehouse/default/Files/capstone/questions.json` (uploaded), calls `compute_expected`, and writes `expected.json` beside it.
- **Each few-shot example** is `{"id", "question", "query"}`. The query uses `gold.`-qualified names, and the tests pair each example with a `Source` (`capstone/fewshots-sources.json`).

- [ ] **Step 1: Failing tests:**
  - **Snapshot:** `copy_gold` copies exactly the named tables and its counts match.
  - **Example queries:** each query runs on `loaded_lake` through Spark SQL, with a `gold` database registered over the fixture paths, and equals its paired Source's result (Review Focus 5).
  - **No overlap with the questions:** no example's question equals or near-copies a frozen question (normalised text, token overlap below 0.8).
  - **Instructions:** they name only real gold tables and columns.
  - **No identifiers:** `datasource.json` holds only the empty GUID, and there's no URL or `@` anywhere.
  - **Notebooks:** `%pip` comes first, then the parameters, then one call.
  - **Placement:** nothing under `fabric/` references the capstone.
- [ ] **Step 2:** FAIL. **Step 3:** Write the definition, with every unconfirmed format point in a new `fabric-capstone/VERIFY.md`, then the code. **Step 4:** PASS.
  **Step 5: Commit** `Add the capstone data agent, snapshot lakehouse and notebooks`

### Task 5: `publish-capstone`

**Files:**
- Modify `.deploy/deploy.py`, `tests/test_deploy.py` and `tests/test_workflows.py`.
- Create `fabric-capstone/parameter.yml`.

**Interfaces:**
- **`publish-capstone`**, with the environment variable `FABRIC_WORKSPACE_ID` (the capstone workspace), run by hand as `fabric@` through `AzureCliCredential`:
  - runs fabric-cicd with `repository_directory=fabric-capstone`, item types `Lakehouse`, `Notebook` and `DataAgent`, and environment `CAPSTONE`;
  - then looks up `da_portfolio` by name and calls the **Publish Data Agent** REST API (Learn's `publish-data-agent`, cited);
  - prints `publish-capstone: ok`.

  It follows the same error, redaction and no-ID rules as the other steps.
- **`fabric-capstone/parameter.yml`:** two regex `find_replace` rules on `**/datasource.json`, `item_type: DataAgent`. One sets `workspaceId` to `$workspace.$id`, the other sets `artifactId` to `$items.Lakehouse.lh_capstone.$id`, both under `CAPSTONE`.

- [ ] **Step 1: Failing tests:**
  - **Rule evaluation:** both rules are evaluated the way fabric-cicd 1.4.0 applies them, and each matches exactly once per file.
  - **The command:** publish runs after fabric-cicd, the lookup is paged, the publish URL and body are as Learn specifies, and failures print the status only.
  - **Isolation:** `deploy.yml` never mentions `publish-capstone` or `fabric-capstone`, and the existing `publish` scope is unchanged.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. **Step 5: Commit** `Add publish-capstone: deploy and publish the data agent by hand`

### Task 6: The F2 and workspace Terraform, and cost attribution

**Files:**
- Create `infra/capstone/` (`versions.tf`, `variables.tf`, `main.tf`, `outputs.tf`, which is empty, and `terraform.tfvars.example`).
- Modify `.github/workflows/ci.yml`, which also validates `infra/capstone`.
- Modify `src/lakehouse/seeds/rg_rules.csv` (`rg-p12-*,capstone,false,,,Capstone F2 and workspace (infra/capstone)`), `src/lakehouse/gold/build.py`'s `PROJECTS` (`("capstone", "Phase 4 capstone: data agent")`, before `releaselens-shared`), the grain text, and the tests `tests/test_infra.py`, `tests/test_gold.py` (dim_project count 13) and `tests/test_attribution.py`.

**Content:**
- **Resources:**
  - `azurerm_resource_group` `rg-p12-capstone`;
  - `azurerm_fabric_capacity`, SKU `F2`, region from a variable, with `administration_members` from a variable (`fabric@`'s UPN, kept in tfvars only);
  - `fabric_workspace` `capstone`, with `capacity_id` from the capacity.
- **Providers and IDs:** `azurerm` and `microsoft/fabric` are pinned like the other stacks. `subscription_id` and the admin UPN come from tfvars.

- [ ] **Step 1: Failing tests:**
  - **Shape:** in their own resource blocks, the SKU is `F2`, the resource group name is `rg-p12-capstone` and there are no outputs.
  - **Attribution:** `rg-p12-capstone` → `capstone`.
  - **Projects:** `dim_project` has 13 rows.
  - **CI:** `ci.yml` validates the new stack.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. `terraform fmt`/`init -backend=false`/`validate` pass in WSL. **Step 4:** PASS.
  **Step 5: Commit** `Add the capstone F2 and workspace stack, and attribute its cost`

### Task 7: The direct-route runner and the report

**Files:**
- Create `capstone/record.schema.json`, `tools/ask_data_agent.py`, `src/lakehouse/capstone/report.py`, `tools/capstone_report.py`, `tests/test_ask_data_agent.py` and `tests/test_capstone_report.py`.

**Interfaces:**
- **Record:** `{"route": "direct|foundry", "freeze": short, "question_id", "pass": 1..3, "answer_text", "raw_output", "tool_calls": int | null, "input_tokens": int | null, "output_tokens": int | null, "wall_ms": int, "started_utc"}`, one JSON object per line.
- **`ask_data_agent.py --passes 3 --out capstone/runs/direct`** reads the MCP endpoint from env `CAPSTONE_MCP_URL`, which is never printed. It:
  - takes a token for `https://api.fabric.microsoft.com/.default` from `AzureCliCredential`;
  - calls the data agent's MCP tool once per question, with an injectable transport;
  - redacts URLs, GUIDs and emails from `raw_output` before writing (Review Focus 4);
  - refuses to start unless `verify_freeze` passes.
- **`render(records: list[dict], questions, expected, freeze) -> str`** produces Markdown with:
  - the headline per route: mean correct per pass out of 15, correct in all 3, correct in none;
  - a per-question table of verdicts and reasons;
  - every wrong answer quoted;
  - tool calls and tokens where present;
  - wall time.

  CU figures are a manual input (`--cu-direct`, `--cu-foundry`).

- [ ] **Step 1: Failing tests:**
  - **Runner:** with a fake transport it writes valid records, redacts a URL, GUID and email, refuses on a broken freeze, and never prints the endpoint.
  - **Report:** a known set of records gives the expected headline numbers (hand-computed in the test), quotes a wrong answer, and fails on records from another freeze.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. **Step 5: Commit** `Add the direct-route runner and the capstone report`

### Task 8: The portfolio analyst (in `E:\Projects\foundry-workshop-agent`)

**Files:**
- Create `src/Portfolio.Analyst/` (`Portfolio.Analyst.csproj`, `Program.cs`, `instructions.md`, `AnalystFreeze.cs`, `AnalystRun.cs` and `RecordWriter.cs`) and `tests/Portfolio.Analyst.Tests/`.
- Add both to `FoundryWorkshopAgent.slnx`.

**Interfaces:**
- **Commands:**
  - `portfolio-analyst run --freeze <path> --questions <path> --passes 3 --out <dir> --model <name>`;
  - `portfolio-analyst verify --freeze <path> --questions <path> --model <name>`.
- **`AnalystFreeze.Verify(freezeJson, questionsJson, instructionsText, model)`** recomputes the `questions`, `analyst_instructions` and `models.foundry` component hashes, exactly as `freeze.py` computes them, and throws on the first mismatch, naming it (Review Focus 3). Cross-repo hash equality is pinned by a shared test vector, `tests/vectors/freeze-vector.json`, committed in both repos.
- **`AnalystRun`** provisions the prompt agent `portfolio-analyst`:
  - its instructions come from `instructions.md`;
  - its one tool is an MCP tool, labelled `portfolio-data`, with its server URL and project connection from env vars `ANALYST_MCP_URL` and `ANALYST_CONNECTION_ID`, both never printed;
  - it reuses `GptEngineFactory.ProjectClient`'s keyless setup (`AzureCliCredential`).

  It asks each question as a fresh conversation and records the answer text, the tool calls, the tokens and the wall time.
- **`RecordWriter`** writes the shared record schema with `route: "foundry"`, redacting URLs, GUIDs and emails with the existing `TranscriptRedaction` rules.
- **The SDK:** stay on Azure.AI.Projects 2.0.1. If its `McpTool` can't take a project connection, record that in `src/Portfolio.Analyst/VERIFY.md` for plan 2's spike. Don't upgrade.

- [ ] **Step 1: Failing tests (xunit.v3):**
  - freeze verification passes on the shared vector and fails naming each component;
  - the record writer redacts and matches the schema, validated against a copy of `record.schema.json`;
  - `run` refuses on drift;
  - with a scripted chat client (the existing `ScriptedChatClient` pattern), a 2-question, 1-pass run writes 2 records;
  - no URL or ID reaches stdout.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS (`dotnet test`). **Step 5: Commit** in
  project 3's repo, `feat(analyst): the portfolio analyst for the phase 4 capstone`

---

## After plan 1

- **Merge order:** project 3's PR (the analyst) first, on the owner's yes, then this repo's PR. Cancel this repo's deploy run.
- **Tracker:** add a line for this plan.
- **Plan 2** is the live run (spec §4.7). Its first step after the F2 exists is the MCP spike.
