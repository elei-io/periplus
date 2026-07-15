# Crawl profile trials

Atlas trials compare the profile selected by a real CrawlPolicy with the next more expensive
trial-eligible CrawlProfile. Trials collect evidence; they never change active routing without an
explicit Apply action.

## Control-plane model

`crawl_profiles` contains reusable acquisition behaviour: stable slug, display name, description,
transport, validated transport configuration, integer `cost_rank`, and trial eligibility.
Deployment setup seeds exactly five profiles in ascending cost order: `direct`, `rendered`,
`settled`, `full-page`, and `interactive`. Provider profiles are explicit user-created profiles.

`crawl_policies` contains URL selection and courtesy limits: scheme, host, path prefix, exact or
prefix path mode, profile reference, enabled state, and per-site concurrency. There is no separate
URL-match table, policy priority, domain group, template registry, or policy revision counter.

## Selection and freezing

Runtime resolves the most specific enabled policy: exact scheme, exact host, exact path, then
longest path prefix. The queued request freezes the complete policy and profile plus the next
trial-eligible profile by `cost_rank`, if one exists. Later edits cannot change queued work.

The deterministic sampler uses `ATLAS_POLICY_TRIAL_SAMPLE_SHARE` and
`ATLAS_POLICY_TRIAL_SAMPLER_VERSION`. A selected sample acquires the same URL with the candidate
profile and a forced fresh-cache setting. It does not increase graph request counts, emit
navigation, evaluate graph edges, or gate completion. `ATLAS_POLICY_TRIAL_MAX_IN_FLIGHT` bounds
concurrent samples.

## Durable evidence

Both arms are immutable crawl observations joined by `trial_id`. Relevant `crawls` columns are:

```text
purpose                              use | sample
trial_id                             UUID nullable
crawl_profile_id                     UUID nullable
crawl_profile_slug                   VARCHAR
config_json                          JSON
config_hash                          VARCHAR
trial_sampler_version                INTEGER nullable
trial_sample_rate                    DOUBLE nullable
trial_candidate_strategy             VARCHAR nullable
trial_candidate_profile_id           UUID nullable
trial_candidate_profile_slug         VARCHAR nullable
trial_candidate_profile_config_hash  VARCHAR nullable
```

Full frozen configuration plus its hash is the reproducibility boundary. The report groups pairs
by origin and profile pair and compares failures, documents, HTML bytes, visible text, elements,
quality flags, distinct-document ratios, variability, and duration. Incomplete pairs remain
visible.

## Applying a result

Apply creates or updates the origin-wide CrawlPolicy and points it at the candidate profile.
More-specific path policies continue to win. The trial-only cache refresh is not copied into the
profile. Applying requires durable evidence for a completed pair; verdicts never mutate routing
automatically.
