# Project 11 live report: the semantic model, report and row-level security (14–17 November 2026)

Project 11 put a Direct Lake semantic model (`sm_portfolio`) and a three-page Power BI report
(`rp_portfolio`) over project 9's gold tables, with row-level security by project. Both went to
dev through git sync and to prod through the deploy. In prod, the model matched gold as the owner
and as two test leads.

This follows the [live runbook](superpowers/plans/2026-10-25-p11-plan-2-live-run.md), failures
included. Times and dates are UTC unless marked as Brisbane time (UTC+10). Identifiers (tenant,
users, connections) are left out on purpose. The README's
[project 11 section](../README.md#semantic-model-report-and-row-level-security-project-11) has the
short version.

## The evidence

| Evidence | Result | Date and limits |
|---|---|---|
| The model matches gold | **Passed**, as the owner (cost per project, quality across models, cost per result, cost per gateway call, untagged cost) and as both leads (p3: 3 rows, p4: 1 row) | 16 Nov, 07:08–07:10, in prod; within 0.005; the gateway compared for the one session `p10-2026-11-15` |
| Row-level security | Each lead sees only their projects: the p4 lead p4's spend (9.70 AUD), no quality rows and the whole gateway page; the p3 lead p3's spend (4.55 AUD) and study (120 results), and no gateway rows | 17 Nov (Brisbane), "Test as role" in prod; [nine screenshots](live/p11/) |
| The owner's view | Spend 27.85 AUD across every project; 1,584 results across three studies; 107 gateway calls | 17 Nov (Brisbane), prod |
| A refresh after the daily load | A manual prod load ran the pipeline's new refresh step: a `DataFactory` refresh, Completed, inside the run | 16 Nov, 08:19–08:24 |
| Leads' access | Read and Build on the model only, no workspace role, no licence | 16 Nov, read back through the API |
| Capacity | About 353 CU-seconds across the whole build (9–17 Nov, Brisbane days); busiest day 317 (16 Nov), about 0.2% of an F2's day; none rejected | Capacity Metrics app; the report's rendering counts as the model's queries |

The spend total is above the README's 26.63 AUD answer because prod's gold has since loaded 14 and
15 November's costs; the answers stay as measured on 10 November.

## How it went

**14 Nov: a probe of the model in dev.** Before anything went live, a copy of the hand-written
model was created in dev through Fabric's create API, refreshed, queried and deleted. Fabric
accepted it as written: the OneLake expression, compatibility level 1604, the descriptive
relationship names, and relationships on date columns (so the planned integer-key fallback was not
needed). Every table framed with gold's row counts. Fabric stored the definition with formatting
changes only, and one value changed: `definition.pbism`'s version, 5.0 to 4.2.

**15 Nov: preflight.** The licence had 54 days left. Neither workspace had the model yet. Dev
follows a branch that parks the items git sync rejects, and that branch also left the model out.

**16 Nov, steps 2–5: users, hygiene, dev's model, access.**

- Two member users were created in Entra as test leads: no licence, no roles, neutral names.
  Their sign-in names live only in a git-ignored file, OneLake and Entra.
- The repository's hygiene secret gained the tenant's domain and both users' local parts. The
  hygiene check passed on `dev` before the secret was set.
- Dev's model got dev's workspace and lakehouse IDs in git. The parking branch took the model back,
  and dev's "Update from Git" created it with no conflict. It framed with the probe's figures, and
  Fabric wrote nothing back.
- `nb_access_load` loaded the access file into `security.user_project` (two rows) in dev.

**Step 7: the report, written as files.** The spec had the report authored in dev's web editor. On
the owner's choice it was written as PBIR files instead: 25 files, each validated against
Microsoft's published schemas. It was then created in dev through Fabric's API, and Fabric's stored
bytes went into git. All three pages rendered in dev. Two things didn't go as planned:

- **Export to image is off in the tenant.** Fabric's export-to-PNG API answered "Export report to
  image is disabled on tenant level". The setting was left alone, and the pages were checked in
  the browser.
- **One card showed scientific notation.** In dev, the shared-cost card showed its tiny value as
  9.52E-5. The card ignores precision settings for it. In prod, where the value is 0.03, it reads
  normally.

**Step 8: the fixed identity and the first prod deploy.**

- The model reads OneLake through a cloud connection whose credential is prod's workspace
  identity, with single sign-on off. It was created by API and shared with the deploy identity.
  Its ID became a prod environment secret, and the release went out.
- **The first prod refresh failed:** "one or multiple source tables either do not exist or access
  was denied". The workspace identity had no access to the lakehouse. It was given Read and ReadAll
  on the lakehouse, as an item, but refreshes still failed 19 minutes later.
- With the owner's yes, a Contributor workspace role was tried for the workspace identity. The
  refresh then completed. The role was removed straight away, and refreshes still completed five
  minutes later.
- So the role isn't needed, as Microsoft's documentation says. The lakehouse grant was enough; it
  took between 19 and about 88 minutes to reach Direct Lake framing. Re-running the deploy's failed
  jobs went green. Prod's report rendered all three pages.

**Step 9: the refresh after the daily load.**

- The model's "Keep your Direct Lake data up to date" setting was on. A manual prod load wrote new
  gold versions, but the model didn't reframe within 19 minutes. (The load added no new cost day,
  so the figures could not show a change either way.)
- So the pipeline now refreshes the model itself: a third step, Fabric's semantic model refresh
  activity, after the load. Neither route the plan first named works here. A pipeline the service
  principal deployed runs as that service principal. Semantic link's default token under it doesn't
  cover a refresh, and its notebook Power BI token doesn't reach semantic models.
- The activity uses a second cloud connection, again with the workspace identity as its credential:
  no sign-in, nothing expires. A refresh needs write access to the model, so the workspace identity
  now holds Contributor on prod's workspace.
- A dev probe proved the activity refreshes, and that a deactivated step skips cleanly. Git holds
  the step deactivated with empty IDs, and the prod deploy switches it on with prod's IDs
  ([#32](https://github.com/DmitryLyubaev/llm-cost-quality-fabric/pull/32), released in
  [#34](https://github.com/DmitryLyubaev/llm-cost-quality-fabric/pull/34)). A manual prod load then
  ran it: a `DataFactory` refresh completed inside the run.
- One process slip: `gh pr merge --auto` merged #32 at once, because `dev` has no required checks.
  CI then passed, so nothing was merged red. Merges are now done by hand after CI.

**Step 10: access in prod.** The access file was loaded into prod's `security.user_project` (two
rows, p3 and p4). Both leads got Read and Build on the model through the Power BI API, with no
workspace role and no email.

**Step 11: the model-versus-gold check.**

- **The first run failed for the leads only.** Every lead query errored
  (`AdomdErrorResponseException`); the owner's comparisons raised nothing. The cause: neither lead
  was a member of the `ProjectLead` role. The spec says the test users are its members, but no step
  added them, and members can't go in git.
- They were added on the model's Security page, and the re-run passed.
- Members live in the service, not the model's definition. The release's republish later kept them
  (the role still shows two members). Adding a lead is now a documented manual step.

**Step 12: screenshots and capacity.**

- The export API was still off, so the pages were captured from the browser: the report canvas
  only, with no address bar and no "viewing as" banner, which shows the viewer's sign-in name.
- The lead views came from "Test as role", with each sign-in name pasted from the private access
  file. The panel showed Read, Build and `ProjectLead` for both leads.
- Each image was checked for sign-in names, tenant, URLs and GUIDs before it left the private
  folder.
- Two quirks:
  - Collapsing the Pages pane while testing a role hides it with no way back.
  - Reloading that view loses the report. Re-entering from the Security page works.

## What stays open

- Whether a lead with a Free licence can open the report shared to them on the trial capacity:
  neither lead signed in.
- What Fabric writes on its own commit of the model or report: none was made. Git flowed one way,
  and Fabric wrote nothing back that differs.
- The 0.005 tolerance is wide for the smallest figures. They were compared by eye on 17 November
  and match the published figures.
- A lead's AUD estimate is converted with the rate from the cost lines they can see. The check
  compares that figure as the owner only.

## After the run

Nothing new bills. The model and report run on the trial capacity, and the two test users have no
licence. They stay until the owner decides otherwise. Deleting them would break only "Test as
role" for those names, not the screenshots. Both connections stay, because prod's model and
pipeline use them. Prod's daily schedule stays off, as project 9 left it.
