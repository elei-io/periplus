# Promoted query baseline

Classification: compiler activation policy. At the user's explicit request, all
currently enabled experimental rules become the default in stable. Experimental
uses the same baseline and remains the place to add future candidate rules.

Compiler `public-query-v7:stable` and `public-query-v7:experimental` both apply:

- `capture_heading_content_scope_v1`: the conservative exact-URL capture/heading
  join from [the first experiments](../experimental-first-three/README.md).
- `prose_scalar_before_capture_v1`: compute a prose regex count before the capture
  join in the supported latest-page aggregate family, from
  [the prose investigation](../prose-scalar/README.md).

The rewrite implementations, eligibility, order, installed-definition checks,
original binding, namespace validation and resource bounds are unchanged. The
experimental mode gate is removed from these two promoted rules; no general
research content-scoping, section materialization or discovery candidate is
activated. Both modes retain independent admission and explicit mode reporting. The SQL
console no longer displays the internal compiler revision beside results: its
public schema remains `public_v1`. API and operator compiler metadata are retained.

## Evidence and acceptance

This is promotion of measured implementations, not a new optimization claim.
The linked frozen-snapshot comparisons establish the accepted result/performance
evidence: exact-URL heading pairs improved from 89–102 seconds to 5–7 seconds;
prose warm medians improved about 20% in both orders at a 1 GB diagnostic budget.
The prose rule still does not guarantee completion within the production 512 MB
budget. Promotion does not change that limit or eliminate broad corpus scans.

The configuration-locked service tests compare both modes against native execution
on the same immutable fixture, including results/types, activation in prep and
execution, catalogue-mismatch rejection and unchanged unsupported forms. Existing
adversarial and physical-work regressions continue to exercise the same rules.

Deployment verification must confirm both processes report v7, both prepare the
same rules, and unchanged representative SQL executes through stable. Live requests
can see different snapshots and are not a new frozen-snapshot speedup comparison.
Rollback is reverting the promotion commit; no catalogue rebuild is required.

Validation: `make check` passed (719 backend tests, 34 environment-dependent
skips; 5 SDK tests; shared package tests and both frontend checks/builds).
The accompanying SQL-console cleanup removes the internal compiler revision and
shortcut hint, and places Ask SQL at the right edge of the toolbar. The editor's
orphaned accessibility description reference is removed; keyboard shortcuts remain.
