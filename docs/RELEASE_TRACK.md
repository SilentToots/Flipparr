# SonicBoom production release track

Decision date: 2026-08-29

Status: approved direction for the first user release

Scope amended 2026-09-02: [one run with independent Issues and collected editions](INDEPENDENT_FORMATS_SCOPE_V2.md).
Both formats remain first-class local-library intake and ownership targets;
automatic monitoring, provider search and downloader acquisition are Issue-only
for the first release. Automatic issue-to-volume contents mapping, missing-Volume
catalogs, Volume acquisition, cross-format ownership credit and fulfillment are
out of release scope. This decision supersedes earlier contents-coverage and
live-Volume-acquisition targets below. Catalog implementation and Gate 2
validation are complete; Issue fulfillment and release-candidate validation
remain open under Gates 3 and 4.

## Decision

SonicBoom is a pre-release product being built for public distribution, not a
POC, experiment, or prototype. The first supported release is a self-hosted,
single-user Docker application that anyone may install for their own comic
library. It is intended to run on a NAS-connected host with the application
data on a local Docker volume and one or more mounted comic-library roots.

Public distribution is a design constraint from the start. Release artifacts
must contain only code, assets, dependencies, and reference data that SonicBoom
is permitted to redistribute, with required attribution and license notices.
Authenticated provider dumps and user credentials are never release assets;
where redistribution is not permitted, users configure or import the source
locally through a documented adapter.

The V2 transition will use an incremental replacement strategy:

1. Freeze V1 except for security fixes, data-loss risks, and V2 validation
   blockers.
2. Build new significant functionality directly in the V2 target foundation.
3. Keep stable UI and integration behavior available through explicit adapters
   while individual capabilities move to V2.
4. Re-index source comic files into a clean V2 database rather than migrating
   semantically dirty canonical rows.
5. Shadow-test the new path, compare deterministic results, and retain a bounded
   configuration-level rollback to V1 until release gates pass.

This avoids two expensive failure modes: polishing architecture that is already
scheduled for removal, and replacing the entire application in one unvalidated
cutover. The temporary compatibility layer is transition infrastructure and
must be removed after the final capability moves to V2.

## Why this is the best-fit approach

Established modernization guidance recommends incrementally replacing bounded
capabilities while old and new systems coexist, preserving rollback until the
replacement is validated. It also warns that the compatibility façade creates
temporary cost and can become a bottleneck if allowed to become permanent.
That tradeoff fits SonicBoom because catalog identity and coverage need a clean
model, while the React UI and parts of the provider, Prowlarr, SABnzbd, archive,
cover, and import behavior already provide useful regression evidence.

Repository inspection also found that the V1 HTTP runtime and broad store/handler
classes are prototype-era foundations. Python documents `http.server` as not
recommended for production. Therefore, new release work must not deepen those
dependencies. The V2 target will use a production ASGI runtime, versioned
migrations, durable job boundaries, and testable service interfaces.

This repository-specific conclusion is an engineering inference from the V1
coupling and the recurring identity, duplicate-run, provider, and restart issues;
it is not a claim that every application should be rewritten.

## Initial supported release

The initial user release supports:

- one self-hosted SonicBoom instance for one trusted user or household;
- Docker deployment on a single host;
- local persistent application/database storage;
- one or more mounted comic library roots;
- cataloging issues and collected editions under the same run, with
  evidence-backed identity and independent ownership/monitoring for each format;
- discovery, following, request, Prowlarr search, SABnzbd download, validation,
  post-processing, import, replacement, and recovery;
- provider degradation without losing local inventory or accepted corrections;
- desktop and mobile-responsive operation for core workflows.

Multi-user approvals, public Internet exposure, distributed workers,
high-availability clustering, microservices, PostgreSQL, and Redis are not
initial-release requirements. They require a measured constraint or approved
product requirement before adoption.

The V2 production interface will use React, TypeScript, Vite, and Tailwind CSS
as its implementation foundation. This is a V2 replacement decision, not a
request to retrofit the maintenance-only V1 interface. Tailwind adoption starts
when the V2 presentation capability is built and must follow
[the V2 UI architecture](V2_UI_ARCHITECTURE.md); it does not block the current
catalog, acquisition, import, or recovery gates.

## Architecture rules for release work

- **End-to-end progress:** follow the prioritization principles in
  [AGENTS.md](../AGENTS.md#end-to-end-progress-and-prioritization). Component
  improvements are valuable foundations, but need bounded checkpoints against
  the complete relevant workflow and release risks. Report engineering gains
  separately from measured user outcomes; do not let narrow optimization
  displace integrated validation and progress toward the full release toolset.
- **Outcome-first UX:** design every capability as part of the user's complete
  task, from trigger through success or recovery. Prefer safe automation and
  strong defaults, ask only for decisions the system cannot make confidently,
  expose product language rather than internal provider/job/schema concepts,
  and never leave a user at a status without a clear explanation and next
  action. Follow [the V2 UI and UX architecture](V2_UI_ARCHITECTURE.md).
- **One source of truth:** Catalog Core v2 owns canonical identity and
  format-specific ownership; replaceable reference data cannot mutate locked
  user state. Shared run grouping does not imply cross-format fulfillment.
- **Idempotent boundaries:** scan, enrichment, request, download reconciliation,
  and import can be repeated after interruption without duplicating entities or
  losing state.
- **Durable background work:** work survives process and container restarts and
  records state, attempts, next retry, and actionable failure details.
- **Provider-neutral behavior:** no provider-specific identity is required for a
  canonical entity. Provider outages and rate limits degrade enrichment, not
  local library availability.
- **Versioned change:** database and configuration changes have explicit versions
  and tested forward migration; rollback uses verified backup/restore or the
  documented previous-version path.
- **Safe files:** SonicBoom never deletes or replaces an original until the new
  file is validated and the destination is verified; failures remain visible
  and recoverable.
- **Bounded modules:** inventory, identity, reference data, coverage, acquisition,
  import, and presentation communicate through explicit interfaces rather than
  direct cross-module table mutation.
- **Reusable product UI:** the V2 interface uses the tokenized, mobile-first
  Tailwind component system defined in
  [the V2 UI architecture](V2_UI_ARCHITECTURE.md). Loading, empty, success,
  degraded, and error states are shared component contracts and must explain
  whether the user needs to act. Motion respects reduced-motion preferences,
  and core workflows must reflow without lost information or functionality.

## Release definition of done

A capability may be merged before every product gate passes, but it must not be
called release-ready until its applicable gates pass. The first user release
requires all of the following:

### Catalog and data integrity

- A clean intake and a repeated intake of the golden library produce the same
  canonical entities, assignments, and separate issue/volume ownership totals.
- Files cannot be actively assigned to two editions; provider IDs cannot map to
  multiple canonical entities; duplicate runs fail closed.
- Issue, volume, omnibus, special, and multi-run collected-edition cases pass the
  documented identity/placement expectations. Unknown contents cannot block an
  otherwise identified volume; ambiguous run placement cannot silently merge runs.
- Acquiring or owning one format cannot fulfill, suppress or create requests for
  the other. Both formats can coexist and be monitored under one run.
- Provider refreshes cannot overwrite locked corrections or silently move files
  and accepted coverage.
- Database foreign-key and integrity checks pass automatically.

### Workflow reliability

- Scan, metadata, search, download, validation, import, replacement, and retry
  work resumes safely after a container restart.
- Provider rate limits, provider outages, Prowlarr failures, SABnzbd failures,
  malformed archives, permission errors, and insufficient storage have tested,
  visible recovery paths.
- Completed downloads are validated, safely normalized, placed in the intended
  library root, rescanned, and reconciled with the request.
- Every queued or failed item exposes current state, relevant history, the next
  automatic action, and any user action that is actually required.

### Installation and operations

- A documented Docker clean install succeeds on the supported NAS-style setup
  using only release artifacts and documented configuration.
- Persistent paths, permissions, UID/GID behavior, secrets, library mounts, and
  completed-download mounts are validated during setup.
- Backup, restore, upgrade from the previous release, and rollback are exercised
  against representative data.
- Health/readiness endpoints, structured logs with correlation IDs, durable job
  inspection, and support-safe diagnostics exist.
- The image is reproducibly built and tested in CI, uses a maintained pinned
  base, runs without unnecessary privileges, and excludes development/secrets
  from the build context.

### Security, UX, and supportability

- The [credential and API-key handling contract](SECRET_HANDLING_V2.md) passes:
  mounted secrets are absent from source, images, environment configuration,
  databases, caches, responses, logs and exception chains; rotation and exposure
  response are documented. Any secret disclosure is a release blocker.
- The applicable OWASP ASVS controls are selected and verified for the supported
  trusted-network deployment; the product does not imply safe public exposure
  without authentication and deployment guidance.
- Core workflows are keyboard operable, have visible focus, usable labels,
  adequate contrast, and responsive layouts at supported breakpoints.
- Loading, long-running, degraded, success, and failure states use shared
  components and clear product language.
- Setup, provider configuration, library import, backup/restore, troubleshooting,
  and known limitations are documented.
- A versioned image, release notes, migration notes, and a rollback note are
  published for the release candidate.

## Delivery gates

### Gate 1 — Release foundation

Production ASGI runtime, typed configuration, migrations, CI, health checks,
structured logs, durable-job contract, and backup/rollback skeleton are working.

Implementation status: the isolated V2 application, local Python 3.13 tests,
migration safeguards, durable leases, health endpoints, structured logging, and
verified online-backup skeleton are complete. CI and the isolated V2 Dockerfile
are defined. CI now includes a clean-install smoke test covering readiness,
non-root execution, repeatable migrations, verified backup creation, and state
survival after container replacement. The complete pull-request workflow passed
on August 30, 2026 with Python 3.13, 188 repository tests, the image build, and
the container smoke test. PR #1 was merged and the same workflow passed on
`main` in run 33315695668, closing Gate 1. V2 is still isolated and has not been
deployed over the live V1 installation. See `docs/V2_RELEASE_FOUNDATION.md`.

### Gate 2 — Catalog correctness (closed 2026-09-02)

2026-08-31: [format-specific acquisition intent](ACQUISITION_INTENT_V2.md)
adds schema 8/contract 1, followed by schema 9 recoverable replacement swaps.
Issue and Volume targets are immutable and independent
under one run; downloader attempts are durable and secret-free; imported status
requires an accepted current-fingerprint assignment to the exact target.
Injected Prowlarr/SABnzbd ports now enforce bounded exact-format candidate
selection without persisting credentials, and completed downloads can be copied
non-destructively into an intent/attempt-specific staging root before normal V2
archive intake. Runtime polling and final promotion are now connected as
described below. Replacement quarantine/rollback is now implemented as described
below; request UI and live mounted-volume validation remain open. This does not
improve the existing 842-file NAS organization
measurement. Gates 2 and 3 remain open; no deployment or live acquisition
occurred.

The same checkpoint now includes a bounded SAB queue/history reconciler with
deterministic post-submit crash recovery, atomic completed-storage persistence,
and a staging reconciler that waits for mount visibility and enters validation
only after normal archive intake confirms the staged hash and health. Transient
SAB outages remain retryable; terminal SAB and archive failures are typed on the
same format-specific intent. The opt-in runtime now reads a mounted secret file,
requires separate real completed/staging roots and schedules bounded status,
staging and tracked-target binding passes. Issue binding creates a direct edition
for only the requested Issue; Volume binding uses only its requested edition.
Staging assignments remain proposed and cannot fulfill ownership. Atomic
promotion is now connected after target binding: validated bytes are copied
through a durable partial file into the configured library, re-inventoried,
assigned only to the tracked target and then accepted/imported in one catalog
transaction. Issue naming preserves the V1 series/year/number/title convention;
Volumes receive an independent `Vol N` destination and no issue credit.
Identical replay recovers; conflicts and low space stop in review while SAB and
staging sources remain. The credential gate follows
[SECRET_HANDLING_V2.md](SECRET_HANDLING_V2.md). Schema 9 now journals an exact
replacement file, original hash/catalog snapshot and deterministic quarantine
path before mutation. The original is atomically quarantined before new bytes
take its path; exact catalog acceptance commits the swap, while a failed
acceptance restores the original and retains the rejected new copy. Restart
replay, changed-original rejection and both Issue and Volume boundaries are
tested. **827 warning-as-error tests pass.** Request UI and Docker/NAS-mounted
failure injection remain open; no live services or NAS files were touched.

The same checkpoint now includes a loopback-only, non-cacheable request
projection for the future V2 UI. It distinguishes download completion from
validated acquisition, preserves the exact Issue/Volume target, translates
typed failures and replacement recovery into product language, and excludes
operational IDs, filesystem paths, provider URLs and credentials. Unsupported
actions were explicitly unavailable pending a durable command/restart contract.
**831 warning-as-error tests passed.** This was an API/read-model gain,
not a completed request UI, live acquisition result or NAS deployment.

Schema 10 now supplies that durable contract for `retry_search` and
`retry_import`. A token-protected request queues one idempotent action for the
exact intent revision; the bounded runtime claims it with an expiring lease,
survives process restart and avoids repeating work when catalog state already
advanced. An interrupted, unconfirmed SAB submission is checked three times by
deterministic intent/attempt label and then surfaced for review without blind
resubmission. Pending state is per request, so one retry does not animate sibling
requests. Queued actions survive verified backup/restore and contain no provider
URL, storage path, external queue ID or credential. **838 warning-as-error tests
pass.** Guided candidate selection, SAB review UI, responsive React/Tailwind UI,
mounted-volume failure injection and live acquisition remain open.

Schema 11 now completes the guided candidate-selection backend. Review results
are capped at 10 rows, expire after one hour and persist only display evidence
plus an opaque SHA-256 key. A selected row becomes a leased action; after restart
the worker re-runs Prowlarr and requires the same current key before fetching or
submitting anything. Provider download paths, GUIDs, NZBs, credentials and SAB
IDs are absent from the snapshot and public projection. A human choice may
override title/year uncertainty but never the exact Issue/Volume number, format,
comic category, Usenet protocol or import validator. Changed/disappeared results
fail safely, and selection plus its candidate evidence survive verified backup.
**841 warning-as-error repository tests pass.** The first React/TypeScript/
Tailwind Requests slice is now production-built and covered by four component
interaction tests. Its isolated responsive fixture passes browser checks at
320, 408, 768 and 1440 CSS pixels without page-level horizontal overflow;
guided release review becomes a full-height mobile dialog and centered desktop
dialog. A separate SAB confirmation dialog explains an unconfirmed submission,
shows only the safe release title and can requeue the existing queue/history
check without authorizing search or resubmission. Its loader is request-scoped.
The fixture uses a temporary database and no provider credentials, downloader,
library path or NAS access. Live isolated acquisition and mounted-volume failure
injection remain open; no provider, downloader, NAS or deployment state was
changed.

2026-08-31: [NAS-wide audit and V1 parity recovery](NAS_INTAKE_BASELINE_2026-08-31.md)
reconcile 962 active files and nine managed exclusions. Filename/number clues
agree with V1 for 941 files; this is not independent accuracy. Restored V2
filename discovery raises query-ready metadata inputs from 468 to 842 without
changing identity/ownership safeguards; 765 tests pass. No fresh provider fetch,
matched-ownership gain or deployment is claimed. V1's useful intake, grouping,
naming and folder behavior is now an explicit compatibility requirement. Restore
that behavior on clean NAS intake before treating Gate 2 as complete.

2026-08-31: the [first independent-format intake slice](INDEPENDENT_FORMATS_IMPLEMENTATION_V2.md)
adds policy 18/schema 7, volume acceptance without contents and separate issue/
volume ownership. Clean and upgrade replays preserve prior data and accept one
additional difficult volume; 747 tests pass. This selected cohort is not the
library success measure. The user requested the actual NAS folder as the primary
baseline and separation of usable organization from exact-edition enrichment.
Gate 2 stays open; the running pilot and NAS were not upgraded.

Current acceptance contract: clean, repeatable intake correctly identifies and
groups issues and collected editions within runs, exposes independent ownership,
preserves user corrections and prevents duplicate runs or cross-format credits.
Missing volume contents are not a blocker. Rebaseline the real-file cohort against
this contract and measure accuracy separately; the historical 2/12 contents-based
result below is not a measured edition-only acceptance rate. Gate 2 remains open.
The active implementation sequence is in [the revised scope](INDEPENDENT_FORMATS_SCOPE_V2.md#delivery-sequence-and-acceptance).

The dated checkpoints below record the previous contents-linked implementation.

2026-08-31: [discovery recovery](DISCOVERY_RECOVERY_V2.md) adds shared literal-title
prioritization, search-only book clues and validated CV catalog-addition
compatibility. Three more successful GETs and cached replay add 51 reference
issues and one collection record, but real-file acceptance is still 2/12
collections; title placement remains 6/12. All 14 source hashes and four accepted
mappings are unchanged. 734 warning-as-error tests pass. One CV lookup is deferred
by the existing local pilot budget, not an upstream 429. Fresh file-level evidence
and collection-context interpretation remain the next integrated work; Gate 2
is still open and no NAS deployment occurred.

2026-08-31: [live evidence discovery](LIVE_EVIDENCE_DISCOVERY_V2.md) now connects
Metron and Comic Vine to isolated intake, with durable caching, pacing, quotas,
candidate validation, additive reference publication and background healing.
The bounded 14-file pass made 33 successful GETs with no rate-limit responses,
but **no additional collections resolved**: coverage remains 2/12 and title
placement 6/12. Nine reference issues in four runs were added, not new collected
editions. All source hashes and accepted mappings were preserved. 726 local
warning-as-error tests pass. Fresh book clues, candidate narrowing, collection
interpretation/conflict reconciliation and untouched accuracy validation are
the next integrated checkpoint. Gate 2 remains open; no NAS deployment.

2026-08-31: the [isolated clean-intake runtime](INTAKE_PILOT_V2.md) now connects
scan, combined-reference resolution, retained book evidence, durable background
work and a small results/correction screen. The real 14-file scan yields four
accepted files, four provisional titles, four unmatched/conflicting collections
and two empty archives. Collection coverage remains 2/12. Placement is 6/12,
down from 7/12 because attaching previously skipped EPUB evidence exposes a
title conflict; the earlier result must not be reported as current coverage.
Source hashes and canonical mappings are unchanged. All 701 warning-as-error
tests pass. Live evidence discovery, untouched accuracy validation, Docker/remote
CI validation and production deployment remain open. Gate 2 is not complete.

2026-08-31: [provisional title attribution](PROVISIONAL_TITLE_ATTRIBUTION_V2.md)
separates useful reversible organization from issue ownership. Same-cohort yield
is 7/12 title placements, including five provisional files; full coverage remains
2/12. User decisions survive healing, and no run is chosen from a title match.
All 682 warning-as-error tests pass. UI/runtime integration, independent validation
and the release coverage target remain open. Nothing is deployed.

EPUB evidence checkpoint (2026-08-31): worker 6 adds bounded image-backed EPUB
sampling through the shared OCR service. The real publication page is readable,
but interpretation and missing-edition fallback still block ownership; collection
acceptance remains 2/12. All 662 warning-as-error tests, 21 native smoke checks and
cohort recovery checks pass. No deployment or Gate 2 sign-off. See
[EPUB book evidence](EPUB_BOOK_EVIDENCE_V2.md).

Candidate-verification checkpoint (2026-08-31): policy 15 adds provider
corroboration for strong digital publication declarations without borrowing print
IDs or bypassing local contents. One regression volume advances from identity to
contents verification; acceptance remains 2/12. All 645 warning-as-error tests and
real-file recovery checks pass. This is intermediate evidence, not production
coverage or deployment. See [collection candidate verification](COLLECTION_CANDIDATE_VERIFICATION_V2.md).

Integrated intake checkpoint (2026-08-31): policy 14 shares canonical ownership
between exact-ID singles and volumes, enables complete Comic Vine original
catalogs in the collection path, and preserves unclassified candidates. Single-issue
controls improve from 0/2 to 2/2; collection acceptance stays 2/12. All 626
warning-as-error tests and real-file recovery checks pass. Candidate-to-local-edition
verification remains the coverage bottleneck; this is not representative accuracy,
unattended production intake or deployment. See
[shared reference intake](UNIFIED_REFERENCE_INTAKE_V2.md).

Combined-source checkpoint (2026-08-31): 32 successful bounded Metron/Comic Vine
requests added 228 isolated reference records without increasing the frozen
batch's 2-of-12 collection acceptance. All 14 outcomes and baseline records are
preserved. This identifies integration work across candidate normalization,
edition verification, typed original-issue handling and provider-neutral catalog
completeness; it is not a reason to continue OCR/GCD-only tuning. No resolver or
live-library changes were made. See
[combined-source intake audit](COMBINED_SOURCE_INTAKE_AUDIT_2026-08-31.md).

Historical Gate 2 requirement (superseded 2026-08-31): Catalog Core v2, reference
packs, evidence resolution, collected-edition coverage, clean intake,
golden-library tests, and deterministic shadow results were required to pass
without manual database repair. This was a requirement, not a gate-pass claim.

Retired product target: **75–85% automatic collected-edition coverage** on representative
clean intake across providers, embedded metadata and bounded local OCR, with
accepted-mapping correctness measured separately. OCR's default inclusion requires
real-file accuracy and NAS resource-cost validation, not a desktop-only demo. Missing
identity/evidence remains in the denominator; an issue-only pivot is a last
resort requiring explicit user agreement. See
`docs/COLLECTION_COVERAGE_TARGET_V2.md` for the measurement contract.
The OCR boundary and cost gate are in `docs/LOCAL_OCR_EVIDENCE_V2.md`.
An opt-in worker/cache/benchmark has been tested on seven read-only NAS copies
on macOS (368 regression tests and seven native smoke checks pass). Four of
five readable volumes expose useful publication text, but exact automatic
mapping, held-out accuracy, NAS resource isolation and foreground-load impact
remain unverified. This does not close Gate 2 or prove the coverage target.
See `docs/LOCAL_OCR_BENCHMARK_2026-08-30.md`.

Follow-up: versioned indicia extraction and read-only exact-reference
corroboration are implemented; 406 local tests and seven native smoke checks
pass. Cached NAS replay opens no comics, calls no providers and writes no
catalog state. Real-file acceptance still awaits identifier-poor edition
identity, coverage-evidence reconciliation and low-quality region treatment.
See `docs/OCR_INTERPRETATION_V2.md`; Gate 2 remains open.

Coverage reconciliation follow-up: resolver policy 7 distinguishes unqualified
native story/issue membership from explicit partial/exclusion evidence. On the
same three captured snapshots and 268 generated candidates, accepted editions
increase from 8 to 12 (82 to 106 unique issues), with all prior mappings unchanged.
430 local warning-as-error tests and seven macOS native smoke checks pass.
The same seven real-file OCR samples still yield no newly accepted/corroborated
mapping; edition identity, OCR scope/quality and representative NAS validation
remain open. See `docs/COVERAGE_RECONCILIATION_V2.md`. No deployment or Gate 2
closure is claimed.

Bounded OCR follow-up: worker 4 / parser 3 with reference verifier 2 handles native
publisher-label differences within exact-linked runs and opt-in region retries.
A same-file paired run produces the first real-file corroboration candidate
(Saga volume 1, #1–6), with zero catalog acceptance or writes. No new ISBNs were
recovered. Median wall time rises from 2.1473 to 2.4494 seconds and peak observed
macOS RSS from 357 to 441 MiB; retries remain off by default. 449 warning-as-error
tests and ten native checks pass; separate-process cache replay is deterministic.
Independent accuracy, identifier-poor edition identity, representative coverage
and enforced NAS operating limits remain open. No remote CI/deployment or Gate 2
sign-off. See `docs/OCR_REGION_RECOVERY_V2.md` for paired evidence and next work.

Identifier-poor digital follow-up: schema 4 / policy 8 persists file-bound local
evidence and replay signatures; parser 4 separates geometrically distant domain
footers while retaining all conflict checks. The real Alex + Ada volume 1 now
imports into a fresh temporary QA catalog as one local digital edition, one 2013
run and five owned issues, without a borrowed print ISBN or manually entered range.
Its own declaration/contents and a same-title/volume/month/publisher provider
contents plan must agree. Full re-observation, restart and backup/restore are
stable. 475 warning-as-error tests and ten native checks pass. External edition
identity remains provisional; the seven-file convenience sample is not the
75–85% measurement corpus. Held-out accuracy, broader coverage, Linux/NAS costs
and runtime integration remain open; no deployment. See `docs/LOCAL_DIGITAL_EDITION_V2.md`.

Intake-effectiveness checkpoint (2026-08-31): a reusable no-edit batch evaluator
now compares embedded/cached-reference resolution with retained OCR across all
14 existing real files, including EPUB. Twelve collection candidates yield zero
then two accepted files; the remaining first blockers are seven identity, one
reference and two empty archives. Contents diagnostics show identity fixes alone
will not resolve every remaining book. All 605 tests pass; full re-observation,
restart, backup/restore and independent CLI replay agree. Policy 13 is unchanged,
no new volume is accepted in this pass, and prior inputs/results stay frozen.
Next checkpoints measure batch coverage and actual intervention, not test count.
Cached replay excludes historical evidence preparation and does not validate
production discovery/healing or the 75–85% target. Gate 2 remains open. See
`docs/INTAKE_EFFECTIVENESS_V2.md`.

Print-source intake follow-up (2026-08-31): policy 13 / proof 1 recognizes a
file-local reproduction of a verified print source using repeated ISBN evidence,
strong first-printing identity and matching full contents. Source ISBN/provider
edition IDs stay in provenance, not file product identity. Saga volume 1 now
automatically covers the 2012 run's #1–6 in clean QA; Alex + Ada retains #1–5.
588 warning-as-error tests pass, including legacy digital-assignment compatibility;
13-file re-observation/restart/backup and frozen parser outputs remain stable.
One extra development volume is not representative coverage or independent
accuracy validation. No new native OCR/provider calls or deployment; Gate 2 stays
open. See `docs/PRINT_SOURCE_INTAKE_V2.md`.

Book-first contents follow-up (2026-08-31): policy 12 accepts complete local
contents anchored by an explicit original-run year without requiring a duplicate
provider collection description. Original issue identities/catalog completeness,
local conflicts, exact-ID routing and user locks remain enforced. All 568 tests
pass, including 20 new regressions. Thirteen clean CBZ inputs retain stable
rescan/restart/backup results, but none has an explicit run-year claim; no extra
real volume is accepted. This is a secondary path, not proof of the coverage
target. Its subsequent Saga ISBN/edition-role investigation is documented in the
print-source follow-up above. No deployment; Gate 2 remains open. See `docs/BOOK_FIRST_CONTENTS_V2.md`.

Declaration-layout follow-up (2026-08-31): digital declaration 2 / policy 11 reads
bounded same-page, same-observation fields across OCR blocks. The real Curse Words
file now has an unverified edition candidate and a specific missing-confirmation
receipt; no additional ownership is accepted. Four clean-file intakes preserve
rescan/restart/backup results, including Alex + Ada's five owned issues. All 548
warning-as-error tests pass. Book-first source priority is documented separately
from OCR confidence; current corroboration/conflict guards remain. No native OCR,
provider calls or deployment. Gate 2 stays open; see `docs/OCR_DECLARATION_RECOVERY_V2.md`.

Deskew follow-up (2026-08-31): OCR worker 5 / parser 6 / policy 10 adds one opt-in,
geometry-selected retry for sampled no-text pages within the existing two-retry
budget. The real Curse Words page now yields literal #11–15 text; its edition and
qualified contents remain unresolved. Four paired files preserve original readings
and clean intake/restart/backup behavior, including Alex + Ada's five owned issues.
528 warning-as-error tests and 16 native Mac checks pass. No additional challenge
volume is accepted, no Linux/NAS performance or coverage claim, no deployment.
See `docs/OCR_DESKEW_RECOVERY_V2.md` for measured overhead and remaining work.

OCR wording follow-up: parser 5 / indicia 2 recognizes shared `Collecting` and
explicit book/volume-subject declarations without relaxing confidence, identity,
qualification or conflict checks. Policy 9 replays old waiting receipts offline.
517 warning-as-error tests pass; 56 historical parser output hashes are unchanged.
Thirteen CBZ inputs retain stable fresh-intake/restart/backup results: Alex + Ada
still owns five issues; no additional challenge volume is accepted. Skewed and
garbled indicia remain blockers, not grammar successes. Gate 2 remains open; see
`docs/OCR_WORDING_RECOVERY_V2.md`. No deployment or new native OCR/provider calls.

Large-archive follow-up: archive policy 2 is shared across CBZ/EPUB intake and OCR.
The deluxe regression passes full CRC validation of 482 members and bounded OCR
of eight pages. Typed resource failures remain unvalidated, not corrupt; OCR
cache keys include the policy so old size failures are retried. The expanded cap
is now 4 GiB with independent member/ratio/count and cooperative time bounds.
506 warning-as-error tests and twelve native Mac smoke checks pass. Edition
identity still waits, with zero ownership gained. Linux/NAS operation and runtime
integration remain open; no deployment. See `docs/LARGE_ARCHIVE_INTAKE_V2.md`.

EPUB intake follow-up: shared ZIP validation now supports bounded single-package
EPUB 2/3 metadata and reading-order observations. The private Locke & Key sample
now validates its exact eISBN and 172 spine entries, but still waits for a matching
reference edition; no extra owned issues or coverage percentage are claimed.
Mixed-format duplicate handling, reference-arrival healing, replay, restart and
backup/restore are covered by 494 passing warning-as-error tests. The original
challenge expectations remain frozen. No live changes or deployment; Gate 2 is
open. See `docs/EPUB_INTAKE_V2.md` for supported limits and remaining work.

Unseen-file challenge follow-up: seven new NAS files (six collection candidates
and a Batman single-issue control) were evaluated against frozen policy 8/parser 4
and the existing three-provider QA reference snapshot. No collection was accepted.
The pass reproduced unused EPUB identifiers, a large-book archive ceiling, missing
contents/declaration variants, skewed-page OCR failure and reference gaps. Visual
QA also found publisher-versus-digital-book contents disagreement. All candidates
remain counted; no misleading accuracy or 75–85% coverage claim. Rescan, restart,
backup/restore and 475 warning-as-error tests pass; live state is untouched.
Next work is shared format/evidence ingestion, not additional product features.
See `docs/HELD_OUT_COLLECTION_CHALLENGE_V2.md` for sources, limits and acceptance work.

Implementation status: the canonical user-state schema, replaceable
reference-catalog schema, immutable snapshot activation rules, typed provider
relationships, resumable provider-import coordinator, recorded provider fixture,
versioned GCD local-export adapter, redacted progress telemetry, scheduler
wake-up contract, and executable golden-manifest comparison contract are
complete and locally tested. The bounded, versioned GCD extractor is implemented
and verified against a current-schema contract fixture. A fail-closed,
redacted real-snapshot evidence harness passed on August 30 against the
authenticated August 29 GCD SQLite dump for Alex + Ada and Birthright.
Deterministic extraction and checkpoint resume/replay also passed. Gate 2
remains open. Format/extractor v2 fixes bounded unlinked-edition discovery,
contents-note preservation, and unsafe full-reprint classification. Its real
snapshot resolves explicit contents claims for two of eleven GCD-labeled
collections, leaving nine unresolved; 207 local repository tests pass with
warnings as errors. A bounded authenticated Metron/Comic Vine audit then found
contents evidence for all nine remaining sample editions and reproduced native
response-shape/omitted-field gaps in V1. This does not accept canonical matches
or ownership; six Birthright candidates still need edition-identity resolution.
See `docs/PROVIDER_COVERAGE_AUDIT_2026-08-30.md`. Cross-provider coverage,
authenticated Metron/Comic Vine
adapters, production scheduling, evidence resolution, clean inventory, real
fixture projections, and the NAS shadow results are not yet complete. See
`docs/COLLECTED_EDITION_COVERAGE.md`,
`docs/GCD_REAL_SNAPSHOT_VALIDATION.md`,
`docs/GATE_2_CATALOG_CONTRACT.md` and `docs/PROVIDER_IMPORT_V2.md`.

The confirmed native Metron parser loss and omitted Comic Vine descriptions are
now fixed through shared pure V2 evidence normalization and maintenance call-site
changes. Cached real responses retain all 15 Metron links and 14 checked Comic
Vine edition descriptions; 222 warning-as-error tests pass. This is not a
canonical-resolution, deployment, or image-build sign-off. See
`docs/PROVIDER_EVIDENCE_FIXES.md`.

The next isolated slice now passes clean CBZ intake through exact edition identity
and verified contents to canonical ownership. Captured Metron plus GCD evidence
for Alex + Ada yields one original run, three editions, and 15 unique owned
issues, unchanged after rescan/restart/duplicate files. The public regression
suite now passes 250 tests with warnings as errors. Schema 3 adds durable local
resolution receipts; an offline healing pass is implemented, not a live
scheduler. The acceptance archives contain generated metadata/test pixels, not
the user's real comics. General Comic Vine contents acceptance, filename-only
identity, broader collected structures, runtime integration, representative file
QA, and Docker validation remain open. Gate 2 is not closed and nothing was
deployed. See `docs/LOCAL_INTAKE_V2.md`.

The follow-up Comic Vine slice now validates scoped native HTML contents without
inventing original issue IDs. Alex + Ada's three trades plus hardcover produce
four editions in one run, still 15 unique owned issues. Birthright volumes 2–10
now have scoped contents statements, but missing run/edition cross-identifiers
still prevent automatic acceptance; no title-only joins were introduced. The
suite passes 278 warning-as-error tests. This remains isolated captured-evidence
QA, not real-file validation or deployment. Gate 2 remains open. Details and
aggregate evidence: `docs/COMIC_VINE_CONTENTS_V2.md`.

The identity follow-up recovers validated ISBNs from native Bookland barcode
fields and derives missing run cross-IDs from two explicit original-issue
anchors, without title-based joining. It also recognizes Metron's native
`Single Issue` original type. Clean Birthright CV intake now yields nine accepted
editions in one run, 45 unique owned issues and one honestly unresolved volume.
The separate GCD-to-CV edition links remain open. GCD extractor 3 preserves
identifier provenance; resolver policy 3 rechecks older receipts. 298 local
warning-as-error tests pass. No deployment, real-file or Gate 2 sign-off.
See `docs/IDENTITY_RECOVERY_V2.md`.

The reciprocal-index follow-up reuses exact native links in original-run
collection lists, with strict whole-item parsing, singleton-container checks
and proof revalidation. Clean CV-identified Birthright intake now accepts ten
editions in one run and 50 unique issues; Alex + Ada remains four editions and
15 unique issues. Pack/proof version 2 preserves version-1 readability; resolver
policy 4 allows healing without rescanning. 321 warning-as-error tests pass.
No new provider calls, title-specific mappings, deployment or live-library
changes. Separate GCD-to-CV edition identity gaps and broader/real-file intake
validation remain open; Gate 2 is not closed. See
`docs/RECIPROCAL_COLLECTION_INDEX_V2.md`.

A broader preselected eight-run GCD cohort now measures all discovered candidates
under native-ID, ISBN-only and title-only profiles with fresh databases. It
exposed two shared defects: unrecognized `Collected Series` labels and a resolver
comparison that incorrectly required per-issue reprint IDs to match. Extractor
4 and resolver policy 5 fix these with legacy compatibility. Discovery increases
from 257 to 268 candidates; one of 134 recognized English collections now resolves
41 issues. This confirms GCD-only evidence remains insufficient, not a coverage
gate pass. All 335 local warning-as-error tests pass, including existing CV
replays. No live-library changes, new provider requests or deployment. Explicit
publisher/year prose support, broader provider contribution and real-file/runtime
validation remain next. See `docs/BROAD_INTAKE_QA_V2.md`.

The scoped-notation follow-up adds parser 2, GCD extractor 5 and resolver policy
6 while preserving old snapshot grammar. On the unchanged 268-candidate cohort,
GCD-only acceptance increases to two editions/46 issues; adding exact Saga
Metron/CV evidence accepts eight editions/82 issues. This includes six additional
Saga trades, not a general 75–80% coverage result. The 134 English collection
candidates include older-run negative controls, so they are not the final
real-intake denominator. All 341 warning-as-error tests pass. Native provider
discovery and the remaining evidence-model/parser gaps are documented in
`docs/COLLECTION_COVERAGE_PROGRESS_2026-08-30.md`. Gate 2 remains open; no deployment.

### Deferred after end-to-end functional validation — reference footprint

Do not interrupt issue/volume intake, run attribution, acquisition and validated
import work to optimize the local GCD footprint. Once those workflows are
running end to end, measure and implement a distributable reference-data
strategy before the release-candidate gate.

The raw authenticated 2026-08-29 GCD download is approximately 1.74 GiB
compressed and 6.23 GiB unpacked. It must not be bundled into the core container
image or silently downloaded by every installation. Evaluate a compact,
versioned SonicBoom reference index containing only required identity and catalog
fields; publish it as an independently updateable optional artifact only if
redistribution, attribution and licensing requirements are satisfied. Record its
compressed/unpacked size, update cost, coverage on the representative NAS cohort
and rollback behavior, then set an explicit release storage budget. Retain a
provider-cache path for incremental enrichment so the full source snapshot is
not a runtime prerequisite.

### Latest Gate 2 checkpoint — independent volume organization

Policy 25 restores safe ISBN-bearing EPUB edition metadata and closes an
order-dependent provider/local duplicate-run defect. On a fresh isolated replay
of all 962 the NAS observations with the enriched reference cache preloaded,
833 files are accepted, 128 are health-blocked and one explicit volume/issue
format contradiction requires review. The result has 60 runs, zero duplicate
normalized-title/year groups, 682 directly owned issues, 148 unique owned volume
slots, clean database integrity and stable restart projection. All 14 ISBN EPUBs
gain clean edition title/publisher metadata; only 2/14 have exact GCD/Open Library
edition identity. This is useful volume intake, not proof of broad exact provider
coverage. Open Library exact-ISBN discovery is now connected to the production V2
durable scheduler and cache without adding credentials or run-selection authority.
The schema-5 upgrade preserves existing provider requests and retains the deployed
migration-3/4 checksums. A fresh zero-network the NAS replay of the same cohort
reproduced the outcome exactly, including clean integrity, stable restart and no
duplicate normalized-title/year groups. This closes the scheduler wiring item but
does not improve the 2/14 exact-ISBN ceiling. Gate 2 remains open for truth-sampled
title/publisher/era run attribution; repeating exact ISBN lookups is not an
accepted next step. Evidence and constraints are recorded in
`docs/LIVE_EVIDENCE_DISCOVERY_V2.md`, `docs/INTAKE_EFFECTIVENESS_V2.md` and
`docs/evidence/nas-20260902-open-library-scheduler-p26b.json`.

The subsequent 12-volume feasibility pass made 20 successful, correctly paced
Metron/Comic Vine calls with no operational failures and no attribution gain.
Eleven volumes still lacked a cached run candidate. The verified private GCD
snapshot does contain relevant original-run and collection candidates omitted by
the bounded reference export. Gate 2's next bounded step is therefore a compact
GCD run-candidate index and truth-sampled typed attribution—not relaxed matching
and not another identical provider request loop. See
`docs/evidence/nas-20260902-isbn-title-provider-p27.json`.

The compact-index feasibility checkpoint is now complete. A versioned SQLite
artifact containing only GCD publication-run candidate facts reduced the verified
private source from 6,694,060,032 bytes to 84,774,912 bytes (1.266%) while
retaining all 229,414 non-deleted comic-publication series. On the same 14 ISBN
volumes, strict local-title lookup finds candidates for 11 files; the remaining
three are present under bounded, observed title aliases. This closes candidate
availability for the sample but does not establish accepted attribution. Locke &
Key still requires explicit multi-run family handling, and publisher/type/era or
independent provider evidence must resolve same-title candidates before canonical
assignment. The artifact/manifest validator, immutable activation state and
rollback contract pass on the NAS. Public automatic updates remain blocked on a
signed metadata channel and final GCD distribution/attribution review; the raw
private snapshot and generated artifact remain outside the repository and image.
See `docs/GCD_RUN_INDEX_V2.md` and
`docs/evidence/gcd-run-index-20260902.json`.

The follow-on conservative attribution policy passes exact-ID, explicit-era,
same-title negative, publisher-conflict, alias-precedence and multi-run-family
tests. Its honest provider-verification result is only 1/14 ISBN files; nine files
are safely retained in the Locke & Key family and four retain plausible but
unverified GCD candidates. Four bounded Metron lookups by those exact GCD series
IDs returned zero results with no operational failure, so that hypothesis is
stopped. Gate 2 must not turn optional provider attribution into mandatory intake
intervention. The next integrated behavior is accepted local organization with
an explicit enrichment-pending state, background retry only when new evidence or
a new source version exists, and a correction path that never silently moves a
run. See `docs/evidence/gcd-run-attribution-20260902.json`.

The intake projection now implements that product consequence without a schema
migration. Accepted local placement is reported as `In library`; optional metadata
verification has its own verified/checking/pending/unavailable state and never
becomes required intervention. The existing durable receipt signatures gate
offline healing, and provider responses remain behind the provider client's cache
and researched cooldown rules. The production React/Tailwind intake screen now
consumes that contract and reuses the shared status chip and loader. Six UI tests
and its production build pass; the NAS passes the 24-test intake suite, 109
adjacent identity/discovery/index regressions with warnings as errors, and the
updated image clean-install/restart smoke. The responsive browser matrix remains
open because the app browser blocked the temporary NAS/localhost fixture; it is
not represented as passing. No deployment occurred.

The following recovery checkpoint adds an explicit, locked run-correction seam
for accepted issues and volumes placed in duplicate provisional runs. It previews
only compatible existing title/era runs, requires a fresh optimistic state token,
and applies one audited transaction without changing source files or cross-format
ownership. Provider-confirmed identities and acquisition history fail closed and
require separate recovery work. the NAS passes the focused correction tests, the
25-test intake/API suite and 182 adjacent identity regressions; the React/Tailwind
dialog passes eight UI tests and production build. See
`docs/RUN_CORRECTION_V2.md`. Whole-run consolidation, explicit unmatch and the
responsive visual matrix remain open, so Gate 2 remains open.

The consolidated checkpoint was committed as `d128d09` after 908 tests passed in
the authoritative the NAS Docker runtime with warnings treated as errors, along
with clean source and image-history credential scans. Replaying that exact code
from the quiescent 962-observation inventory reproduced 833 accepted files, 128
health blocks, one review, 60 runs, 682 issues and 148 unique volumes with clean
integrity and stable restart projection. A read-only V1 comparison matched all
834 healthy paths, all 819 comparable numbers and 831/833 formats. Both format
differences are confirmed V1 issue/edition errors. The three V1-to-V2 run merges
were reviewed as legacy duplicate/subtitle rows or one publisher-supported
two-volume series; no unsafe V2 merge was demonstrated. This is strong behavior
parity evidence but not independent correctness for the unchanged agreements.
Gate 2 therefore remains open for a separated-label review of the full 60-run
population. See
`docs/evidence/nas-20260902-current-replay-parity-d128d09.json`.

The separated-label review is now complete. The original 60-run packet measured
52 useful groups (86.7%), 27 exact title-and-era attributions (45%), one unsafe
merge and one uncertain boundary. The unsafe merge exposed a generic
folder-cohort defect: unnumbered subtitle publications could inherit the main
collection title solely because numbered volume siblings existed.

The corrected rule requires the current file itself to declare volume format and
an explicit volume number before folder-title inheritance. A fresh isolated
replay of the same 962 read-only NAS observations preserved 833 accepted files,
682 issues and 148 unique volume slots while safely expanding the affected one
group into a numbered six-volume main run and three separately named
publications. The new 63-run review measured 56 useful groups (88.9%), zero
unsafe merges, one uncertain boundary and zero unreviewed groups. All 105
independently reviewed representative formats and numbers are correct; three
remain unmeasured. Exact run attribution is 27/63 (42.9%), primarily because era
evidence is missing or uncertain, and remains progressive enrichment rather than
an intake blocker. The complete 916-test warnings-as-errors suite passes on
the NAS. See `docs/GROUPING_REVIEW_V2.md` and
`docs/evidence/nas-20260902-grouping-review-p29.json`.

This satisfies the agreed useful-grouping floor with no demonstrated unsafe merge
in the frozen cohort. A final read-only exit audit found zero duplicate normalized
title/era groups, provider IDs, edition identifiers, file assignments, or
multiple-primary-run editions. Every accepted or locked file has exactly one
primary run; integrity and foreign keys pass. Explicit regressions cover
same-title/different-era separation, independent Issue/Volume ownership, locked
correction preservation and provider ambiguity. Gate 2 is closed. Optional exact
era/provider enrichment continues in the background without blocking ownership.
End-to-end acquisition/import recovery moves to Gate 3, while responsive,
accessibility, deployment and operations validation remain Gate 4 work.

### Gate 3 — Fulfillment correctness

Prowlarr through SABnzbd through validated import and request reconciliation is
restart-safe, idempotent, observable, and proven for Issues. Issues and collected
editions share run grouping and independent ownership, while collected editions
enter through local-library intake. Import/replacement of one cannot fulfill or
mutate the other. Test this boundary through failures, retries and restart, not
only successful downloads.

The first Gate 3 preflight found and fixed a recovery-feedback mismatch: library
destination, write and replacement-promotion failures now explain the mount or
permission problem, confirm retention of the download, and expose the durable
request-scoped `Retry import` action that the backend already supports. Ten
focused and 75 adjacent warnings-as-errors tests pass on the NAS. The next
checkpoint is an isolated non-root Docker run with an explicitly read-only
completed-download bind and separate writable staging/library binds, followed by
permission/storage failure injection. A real Prowlarr/SAB submission remains an
explicitly authorized later smoke; nothing was submitted or deployed here. See
`docs/ACQUISITION_INTENT_V2.md`.

The non-root mounted-volume checkpoint now passes four isolated the NAS Docker
scenarios. A completed Volume import survives a restart between copy and catalog
acceptance without duplication or Issue credit. Read-only-library and injected
low-space failures retain both completed and staged bytes, leave ownership
unchanged and expose `Retry import`. A same-format Volume replacement imports,
quarantines the original and creates no Issue credit. Completed storage and code
were mounted read-only; state, staging and library were separate binds. No NAS
comic, credential, provider, downloader, live catalog or deployment was touched.
The mounted-volume checkpoint is closed; an explicitly authorized live
Prowlarr/SAB smoke remains the Gate 3 exit item. See
`docs/evidence/acquisition-mount-20260902-p30b.json`.

The authorized live the NAS smoke now proves exact candidate selection,
credential-safe Prowlarr grabbing, confirmed SAB submission and successful
download completion. It also exposed the remaining import blocker: the selected
job contained one valid RAR4 CBR, while V2 currently fully validates only CBZ and
EPUB. The isolated library/staging remained empty, ownership remained unchanged,
and integrity/foreign keys pass. V1's RAR-header-only check is not sufficient for
V2's validated-import guarantee. Gate 3 therefore remains open pending a
resource-bounded CBR decoder boundary plus successful real Issue and Volume
imports and restart reconciliation. Ninety-four adjacent warnings-as-errors
tests pass on the NAS. See
`docs/evidence/live-acquisition-20260902-p31f.json` and
`docs/ACQUISITION_INTENT_V2.md`.

The retained real RAR4 download now also passes an isolated CBR conversion and
import boundary. The main process durably publishes a hash-bound job and waits;
a pinned no-network decoder container with no credentials, catalog, completed
storage or library mount produces a bounded CBZ; and the main process fully
revalidates it before exact Issue binding and promotion. The workflow resumed
across three separate container runs, retained the SAB source, produced one
accepted assignment in the isolated library, and left integrity/foreign keys
clean. Twenty focused and 174 adjacent warnings-as-errors tests pass. This
closes the real Issue import proof, not Gate 3: a real Volume import and deployed
Compose/restart exercise remain. The hardened two-service definition is now in
`compose.v2.yaml`. See `docs/CBR_DECODER_BOUNDARY_V2.md` and
`docs/evidence/cbr-staging-20260902-p33.json`.

A representative read-only NAS Volume, Alex + Ada Volume 1, now also passes
normal validation, exact Volume binding and isolated promotion with one accepted
assignment and zero Issue dependencies. Its compact `alexandada_vol1` filename
exposed and fixed a general V1-compatibility case: compact punctuation/spacing is
accepted only when the exact format and number still agree with the immutable
target. This proves real-file Volume import, not live provider/downloader Volume
acquisition. Gate 3 remains open for that authorized live request and the
deployed Compose/restart exercise. See
`docs/evidence/real-volume-import-20260902-p35.json`.

The authorized live Volume checkpoint then found and fixed a general search
regression: V2 generated catalog fallback queries but sent only the first to
Prowlarr. Searches now use the catalog contract (currently one query for Issues
and up to two for Volumes) under one deduplicated 100-candidate aggregate budget
and stop when an exact strong match appears. The configured indexers still
returned no safe release: Saga Volume 10
produced only two wrong-Volume-1 results, and the one bounded alternate,
Absolute Batman Volume 1, returned none. The exact title/format/number gate
rejected both; no NZB was fetched or submitted and no live state changed. Gate 3
therefore remains open because of demonstrated configured-indexer Volume
coverage, not importer correctness. Twenty-two focused and all 938 backend tests
pass with warnings treated as errors on the NAS. Review indexer capabilities and
comic-category configuration before another live attempt; do not cycle titles or
weaken identity rules. See
`docs/evidence/live-volume-acquisition-20260902-p38.json`.

The follow-up read-only audit ruled out that basic configuration hypothesis: all
six configured indexers are enabled Usenet sources, map Comics category `7030`,
and have no current failure or disabled flags. A single Mylar-style `Saga v10`
probe returned three unrelated Thor Epic Collection results and no exact target.
The stop criterion is met. Further title/query cycling is not justified; the next
product decision is whether to add a demonstrably stronger comic-release source
or defer the live-Volume proof while keeping Gate 3 open.

A subsequent torrent feasibility pilot tested that alternative before building
it. the NAS's TrackerA (`7030`) and TrackerB (`7000` Books) indexers were
healthy and FlareSolverr-tagged, but returned zero candidates for both missing
Volume targets across three established naming forms. TrackerF and TrackerG lack useful
Books/Comics categories and were excluded. No torrent or magnet was submitted.
This two-target sample does not justify a qBittorrent adapter: it adds no measured
coverage benefit while introducing seeding and stalled-swarm lifecycle work.
Keep the transport seam, and reconsider only after a representative read-only
cohort demonstrates material exact-match gain. See
`docs/evidence/torrent-coverage-20260902-p39.json`.

Product decision 2026-09-02: the two bounded live-Volume source checks establish
that automatic collected-edition acquisition cannot be a credible first-release
promise. Volume and collection files remain supported for local intake, grouping,
metadata, health and manual import/replacement, including purchased bundles.
Production Requests exposes automatic search and downloader actions for Issues
only. Historical Volume intents and the generic tested contract remain preserved;
the runtime blocks new Volume search/submission before provider traffic. Gate 3
therefore no longer requires a live Volume release. Its remaining release proof
is the deployed Compose/restart exercise for the supported Issue acquisition path.

The first V2 QA Compose deployment on 2026-09-02 exposed and fixed two integrated
gaps that isolated image tests did not cover. The base stack did not enable the
intake/UI runtime, and Docker bridge NAT presented the exact bridge gateway—not
loopback—as the ASGI client. A tracked QA override now enables the interface
without making the public base stack depend on the NAS's private reference data.
Docker Engine 29's loopback host bind is combined with one exact private gateway
allowlist entry plus the existing Host, Origin and CSRF checks; a neighboring
container remains denied with 403. Both hardened services start and restart,
schema 11 and empty acquisition state persist, health/readiness stay green, the
Requests contract advertises Issue acquisition and rejects Volume acquisition,
and the 408px/1440px browser smoke passes. Seventy-two adjacent backend tests and
ten UI component assertions pass on the NAS. This closes idle Compose/restart and
interface reachability, not an in-flight deployed Issue acquisition/restart proof;
Gate 3 remains open for that bounded lifecycle exercise.

The same deployed interface then completed one read-only-source intake of the
established 962-file NAS cohort. It accepted all 834 healthy files without
matching intervention: 682 Issues and 152 collected editions across 63
provisional runs. The remaining 128 rows are explicit file-health or unsupported-
format blocks (5 unreadable archives, 70 empty archives, 48 unsupported formats,
3 archives without comic pages and 2 missing ZIP directories), not unresolved
healthy matches. The result contains zero exact duplicate normalized title/year/
publisher run keys and reproduces the prior local-first baseline. Live provider
discovery was disabled, so all 63 runs remain provisional and no provider-
verification gain is claimed. See
`docs/evidence/deployed-v2-qa-20260902-p41.json`.

The deployed Issue acquisition checkpoint then exercised the remaining Gate 3
path. A Fables #8 request survived an in-flight Compose restart without duplicate
submission, rejected a genuinely corrupt RAR4 payload, and stopped safely when
ten remaining results could not satisfy the immutable target. The checkpoint
fixed shared recovery-policy drift, repeated cosmetic variants of rejected
releases, root-owned non-root-worker scratch, stale prior-attempt staging and
non-terminal rejected attempts.

A separate provider-confirmed, released and previously unowned Fables #141
request completed the full live path. Its first exact release was a real ZIP but
failed image CRC validation and was retained as a failed attempt. Retry excluded
that release and selected a different exact result, which downloaded, validated,
bound only to Issue #141 and atomically promoted as one accepted healthy CBZ. The
unsupported original CBR remains present, completed and staged sources remain
recoverable, both services are healthy with zero application error lines, and
SQLite integrity and foreign keys pass. The deployed backup CLI also now performs
and verifies an online schema-11 backup instead of exiting as a no-op. See
`docs/evidence/gate3-live-issue-20260902-p42.json`.

The final clean-source the NAS run passes all 947 backend tests with warnings
treated as errors. Gate 3 is closed. Gate 4 remains open for the clean-install,
upgrade, rollback, provider-outage, realistic-performance,
responsive/accessibility, security and operations release-candidate matrix.

Gate 4 storage preflight found that the Debian Bookworm release image provides
SQLite 3.40.1, which predates SQLite's documented WAL-reset race fix. The user
and reference catalogs now default to the rollback journal and reject an
explicit WAL configuration unless the runtime contains an upstream fixed line.
the NAS passes 950 warnings-as-errors tests. A clean 962-file replay completed
in 115.805 seconds with the established 833 accepted, 128 health-blocked and one
review outcome, stable restart projection, clean integrity/foreign keys and no
resource warning. This supports SQLite for the single-user/single-host release
without accepting an avoidable WAL risk. Clean-install smoke has passed; Gate 4
remains open for implemented restore/rollback, upgrade, interruption, outage,
responsive/accessibility, security and operations checks. See
`docs/evidence/gate4-sqlite-runtime-20260903-p43.json`.

The supported offline restore path now verifies the selected backup's bound
manifest, checksum, exact schema, integrity and foreign keys and rejects journal
sidecars. The application holds an exclusive process-lifetime database lock and
restore must acquire the same lock, so a running app is refused between as well
as during SQL transactions. Restore preserves the state being replaced as
another verified backup, then stages, fsyncs and atomically installs the selected
state. A disposable the NAS Docker volume proved clean install, backup,
post-backup mutation, stopped-service restore, restart, selected-state recovery
and preservation of the newer state in the safety backup. Forty-four focused
and 957 repository warnings-as-errors tests pass. A subsequent manifest-race
safeguard binds the staged copy to the initially verified checksum; fourteen
focused tests and the final disposable-volume smoke pass after that bounded
change. Commit `f43dd87` is deployed on the NAS with both services healthy from
one immutable release directory, clean database integrity/foreign keys, mode-0600
runtime lock and zero service error lines. A live restore invocation was refused
by that lock before replacement; no live catalog was restored. See
`docs/V2_BACKUP_RESTORE_RUNBOOK.md` and
`docs/evidence/gate4-offline-restore-20260903-p44.json`. Gate 4 remains open for
cross-version upgrade/code rollback, interruption, provider-outage,
responsive/accessibility, security and operations checks.

The next operations checkpoint exposed a backup-permission regression in two
private grouping-review trees. Ad-hoc checkpoint containers had written one
complete tree and three later reports as UID 0 with private `0700`/`0600`
modes, so the deliberately unprivileged standard rsync sender returned code 23.
The security boundary behaved correctly. The 17-node, no-symlink scope was
enumerated before repair; only those two trees were normalized to the NAS's
SonicBoom service identity (`1000:10`) while retaining `0700` directories and
`0600` files. No broad `/srv/docker` permission or backup-reader privilege
was added. The grouping-review CLI now refuses artifact-writing commands as UID
0 or with a UID/GID that differs from the managed output parent, and directs
Docker callers to select the configured service UID/GID. Ten
focused tests and all 960 repository tests pass with warnings treated as errors
in the NAS Docker; a real root invocation exits 2 without creating output. The
installed backup job then completed both synchronization passes, created and
verified the local and iCloud Restic repositories, resumed containers, delivered
Kuma `up`, and exited 0. See
`docs/evidence/gate4-checkpoint-backup-permissions-20260903-p47.json`.

The first-release upgrade/rollback checkpoint then used a fresh verified online
copy of the deployed realistic catalog as disposable the NAS state. V2 has no
earlier public release and does not migrate V1 rows, so the supported proof is a
stop-first replacement between the previous and current schema-compatible
candidate images, not an invented promise to upgrade historical internal
snapshots. All baseline counts across series, runs, editions, files,
assignments, acquisition intents and jobs survived the upgrade; user schema 11,
reference schema 5, integrity, foreign keys and a pre-upgrade marker remained
valid. The prior image then reopened the post-upgrade state directly. A separate
offline rollback restored the verified pre-upgrade backup, retained the newer
state in a verified safety backup, restarted the prior image and removed only
the post-upgrade marker. An invalid candidate exited 3 with the database checksum
unchanged. Live state was never a test target and both deployed services remained
running. This closes the first-release schema-compatible candidate
upgrade/rollback proof. Future persistent-schema changes remain blocked until
they add paired user/reference rollback support and their own the NAS proof.
Public packaging must also select immutable versioned image digests. See
`docs/V2_UPGRADE_ROLLBACK_RUNBOOK.md` and
`docs/evidence/gate4-upgrade-rollback-20260903-p48.json`.

The abrupt-interruption checkpoint then used another fresh disposable copy of
the same realistic catalog. A non-root candidate container was killed with
`SIGKILL` while 256 large rows were uncommitted; Docker recorded exit 137 without
an out-of-memory event and left a private 44,544-byte rollback journal. The next
database open removed the hot journal, retained zero uncommitted rows and passed
integrity and foreign-key checks. Claimed resolution and acquisition rows were
not available before lease expiry, were reclaimed as the same rows at attempt
two, and refused completion by their stale owners. A running intake batch moved
back through queued work and completed against an unchanged read-only synthetic
comic. The production app then started ready on recovered state with no error
lines. Fifty-two focused the NAS tests pass with warnings treated as errors.
This closes the release candidate's application-process interruption proof;
physical media/controller failure remains an infrastructure and backup concern,
not something this container test claims to simulate. See
`docs/V2_INTERRUPTION_RECOVERY_RUNBOOK.md` and
`docs/evidence/gate4-interruption-recovery-20260903-p50.json`.

The provider-outage checkpoint then ran the production transports with Docker
networking disabled and unique fake credentials. Metron, Comic Vine and Open
Library each retained one waiting request with a 60-second initial cooldown; a
restarted client refused an immediate duplicate attempt. A Prowlarr outage saved
the Issue request, exposed a credential-safe service explanation and retained a
retry-search action. A SABnzbd status outage returned retry-later while leaving
the submitted intent and exact attempt unchanged. Canonical state had an
identical before/after signature, integrity and foreign keys passed, injected
credentials were absent from both databases, and the corrected checkpoint
writer produced private `0700`/`0600` artifacts. Ninety-four focused the NAS
tests pass with warnings treated as errors. The final run used commit `c2eff05`
in candidate image `sonicboom-v2:p53` without source overlays; its exported
source bundle also passed the secret-hygiene regression, bringing the focused
total to 97. The V2 intake interface now presents provider availability,
cooldown timing, connection failures and the bounded retry action through a
shared mobile-first component with polite status updates and scoped loading
feedback. All 12 frontend tests and the type-checked production build pass in
the NAS Node 22 Docker. This completes provider-outage persistence and visible
recovery behavior; the broader full-page responsive/accessibility matrix remains
part of Gate 4. See
`docs/V2_PROVIDER_OUTAGE_RUNBOOK.md` and
`docs/evidence/gate4-provider-outage-20260903-p54.json`.

### Gate 4 — Release candidate

Clean install, upgrade, rollback, realistic-library performance, provider
outages, responsive/accessibility, security, operations, and documentation pass
the release checklist. Only then is SonicBoom presented as a user release.

## Verification and primary references

- Microsoft documents the Strangler Fig pattern as an incremental migration that
  keeps the interface stable, minimizes disruption, and treats the façade as
  temporary transition architecture:
  https://learn.microsoft.com/en-us/azure/architecture/patterns/strangler-fig
- AWS describes transform, coexist, and eliminate phases and explicitly calls
  for a rollback plan for each replaced service:
  https://docs.aws.amazon.com/prescriptive-guidance/latest/modernization-decomposing-monoliths/strangler-fig.html
- Python warns that `http.server` is not recommended for production because it
  implements only basic security checks:
  https://docs.python.org/3.13/library/http.server.html
- FastAPI documents the operational simplicity and bounded memory behavior of a
  single Uvicorn process per container:
  https://fastapi.tiangolo.com/deployment/docker/
- GitHub documents CI workflows that build and test changes and report failures
  before merge:
  https://docs.github.com/en/actions/get-started/continuous-integration
- Docker's build guidance covers CI builds, minimal/pinned images, build-context
  exclusions, ephemeral containers, and non-root execution:
  https://docs.docker.com/build/building/best-practices/
- OWASP ASVS provides a requirements and verification basis for web application
  security controls:
  https://owasp.org/www-project-application-security-verification-standard/

## Decision review triggers

Revisit this decision only if evidence shows that the compatibility boundary is
more costly or risky than a direct cutover, or if the supported product target
changes materially. Revisit SQLite, local workers, or the single-container
deployment only when measured concurrency, reliability, or multi-user
requirements exceed their documented operating constraints.
