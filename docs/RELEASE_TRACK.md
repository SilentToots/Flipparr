# SonicBoom production release track

Decision date: 2026-08-29

Status: approved direction for the first user release

## Decision

SonicBoom is a pre-release product being built for users, not a POC or a
prototype. The first supported release is a self-hosted, single-user Docker
application intended to run on a NAS-connected host with the application data
on a local Docker volume and one or more mounted comic-library roots.

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
- cataloging issues and collected editions with evidence-backed coverage;
- discovery, following, request, Prowlarr search, SABnzbd download, validation,
  post-processing, import, replacement, and recovery;
- provider degradation without losing local inventory or accepted corrections;
- desktop and mobile-responsive operation for core workflows.

Multi-user approvals, public Internet exposure, distributed workers,
high-availability clustering, microservices, PostgreSQL, Redis, and a wholesale
Tailwind rewrite are not initial-release requirements. They require a measured
constraint or approved product requirement before adoption.

## Architecture rules for release work

- **One source of truth:** Catalog Core v2 owns canonical identity and accepted
  coverage; replaceable reference data cannot mutate locked user state.
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
- **Reusable product UI:** design tokens and responsive components are the
  default. Loading, empty, success, degraded, and error states are shared
  patterns and must explain whether the user needs to act.

## Release definition of done

A capability may be merged before every product gate passes, but it must not be
called release-ready until its applicable gates pass. The first user release
requires all of the following:

### Catalog and data integrity

- A clean intake and a repeated intake of the golden library produce the same
  canonical entities, assignments, coverage, and ownership totals.
- Files cannot be actively assigned to two editions; provider IDs cannot map to
  multiple canonical entities; duplicate runs fail closed.
- Issue, volume, omnibus, special, and multi-run collected-edition cases pass the
  documented golden-library expectations.
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

### Gate 2 — Catalog correctness

Catalog Core v2, reference packs, evidence resolution, collected-edition
coverage, clean intake, golden-library tests, and deterministic shadow results
pass without manual database repair.

Implementation status: the canonical user-state schema, replaceable
reference-catalog schema, immutable snapshot activation rules, typed provider
relationships, resumable provider-import coordinator, recorded provider fixture,
versioned GCD local-export adapter, redacted progress telemetry, scheduler
wake-up contract, and executable golden-manifest comparison contract are
complete and locally tested. The bounded, versioned GCD extractor is implemented
and verified against a current-schema contract fixture. A fail-closed,
redacted real-snapshot evidence harness is also implemented, but has not yet
been executed against an authenticated real GCD dump. Gate 2 remains open:
real-snapshot GCD evidence, authenticated Metron/Comic Vine adapters,
production scheduling, evidence resolution, clean inventory, real fixture
projections, and the NAS shadow results are not yet complete. See
`docs/GATE_2_CATALOG_CONTRACT.md` and `docs/PROVIDER_IMPORT_V2.md`.

### Gate 3 — Fulfillment correctness

Prowlarr through SABnzbd through validated import and request reconciliation is
restart-safe, idempotent, observable, and proven for issues and collected works.

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
