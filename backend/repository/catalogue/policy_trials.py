"""Read models for crawl-policy trial evidence stored in DuckLake."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from repository.catalogue.client import Catalogue

PolicyTrialVerdict = Literal[
    "awaiting_sample",
    "insufficient_evidence",
    "promising",
    "no_clear_gain",
    "regressed",
    "inconclusive",
]


@dataclass(frozen=True)
class PolicyTrialSummary:
    use_crawls: int
    selected_trials: int
    sample_crawls: int
    completed_pairs: int
    awaiting_samples: int
    pairs_with_failure: int
    observed_sample_rate: float
    last_trial_at: datetime | None


@dataclass(frozen=True)
class PolicyTrialComparison:
    scheme: str
    host: str
    port: int
    registrable_domain: str
    use_template: str
    candidate_template: str
    selected_trials: int
    completed_pairs: int
    recovered_crawls: int
    sample_failures: int
    identical_documents: int
    median_html_delta_percent: float | None
    median_visible_text_delta_percent: float | None
    median_element_delta_percent: float | None
    mean_use_visible_text_chars: float | None
    mean_sample_visible_text_chars: float | None
    use_visible_text_stddev: float | None
    sample_visible_text_stddev: float | None
    use_visible_text_cv: float | None
    sample_visible_text_cv: float | None
    use_distinct_document_ratio: float | None
    sample_distinct_document_ratio: float | None
    median_use_quality_flag_count: float | None
    median_sample_quality_flag_count: float | None
    use_acquisition_failure_count: int
    sample_acquisition_failure_count: int
    median_duration_delta_ms: float | None
    last_trial_at: datetime
    verdict: PolicyTrialVerdict
    verdict_reason: str


@dataclass(frozen=True)
class PolicyTrialReport:
    summary: PolicyTrialSummary
    comparisons: tuple[PolicyTrialComparison, ...]
    total_comparisons: int


def get_policy_trial_application_domain(
    catalogue: Catalogue,
    *,
    scheme: str,
    host: str,
    port: int,
    template_name: str,
) -> str | None:
    """Return the observed registrable domain when a completed trial supports this action."""

    crawls = _table(catalogue, "crawls")
    documents = _table(catalogue, "documents")
    base = _paired_trials_cte(crawls, documents)
    row = catalogue.connection.execute(
        f"""
        WITH {base}
        SELECT registrable_domain
        FROM pairs
        WHERE scheme = ? AND host = ? AND port = ?
          AND sample_template = ?
          AND sample_crawl_id IS NOT NULL
        ORDER BY use_captured_at DESC
        LIMIT 1
        """,
        [scheme, host, port, template_name],
    ).fetchone()
    return None if row is None else str(row[0])


def get_policy_trial_report(
    catalogue: Catalogue, *, limit: int, offset: int
) -> PolicyTrialReport:
    """Summarize paired trial evidence and assign a conservative verdict."""

    crawls = _table(catalogue, "crawls")
    documents = _table(catalogue, "documents")
    base = _paired_trials_cte(crawls, documents)

    summary_row = catalogue.connection.execute(
        f"""
        WITH crawl_counts AS (
            SELECT count(*) FILTER (WHERE purpose = 'use') AS use_crawls,
                   count(*) FILTER (
                       WHERE purpose = 'use' AND trial_id IS NOT NULL
                   ) AS selected_trials,
                   count(*) FILTER (WHERE purpose = 'sample') AS sample_crawls
            FROM {crawls}
        ),
        {base}
        SELECT cc.use_crawls,
               cc.selected_trials,
               cc.sample_crawls,
               count(*) FILTER (WHERE sample_crawl_id IS NOT NULL) AS completed_pairs,
               count(*) FILTER (
                   WHERE trial_id IS NOT NULL AND sample_crawl_id IS NULL
               ) AS awaiting_samples,
               count(*) FILTER (
                   WHERE sample_crawl_id IS NOT NULL
                     AND (use_document_id IS NULL OR sample_document_id IS NULL)
               ) AS pairs_with_failure,
               CASE WHEN cc.use_crawls = 0 THEN 0.0
                    ELSE cc.selected_trials::DOUBLE / cc.use_crawls
               END AS observed_sample_rate,
               max(use_captured_at) AS last_trial_at
        FROM crawl_counts AS cc
        LEFT JOIN pairs ON true
        GROUP BY cc.use_crawls, cc.selected_trials, cc.sample_crawls
        """
    ).fetchone()
    if summary_row is None:
        raise RuntimeError("policy trial summary query returned no row")

    total_comparisons = int(
        catalogue.connection.execute(
            f"""
            WITH {base}
            SELECT count(*)
            FROM (
                SELECT scheme, host, port, use_template, sample_template
                FROM pairs
                GROUP BY scheme, host, port, use_template, sample_template
            ) AS comparison_cohorts
            """
        ).fetchone()[0]
    )
    comparison_rows = catalogue.connection.execute(
        f"""
        WITH {base}
        SELECT scheme, host, port, registrable_domain,
               use_template,
               sample_template,
               count(*) AS selected_trials,
               count(*) FILTER (WHERE sample_crawl_id IS NOT NULL) AS completed_pairs,
               count(*) FILTER (
                   WHERE use_document_id IS NULL AND sample_document_id IS NOT NULL
               ) AS recovered_crawls,
               count(*) FILTER (
                   WHERE use_document_id IS NOT NULL AND sample_crawl_id IS NOT NULL
                     AND sample_document_id IS NULL
               ) AS sample_failures,
               count(*) FILTER (
                   WHERE use_document_id IS NOT NULL
                     AND use_document_id = sample_document_id
               ) AS identical_documents,
               median(
                   100.0 * (sample_html_size_bytes - use_html_size_bytes)
                   / nullif(use_html_size_bytes, 0)
               ) AS median_html_delta_percent,
               median(
                   100.0 * (sample_visible_text_chars - use_visible_text_chars)
                   / nullif(use_visible_text_chars, 0)
               ) AS median_visible_text_delta_percent,
               median(
                   100.0 * (sample_element_count - use_element_count)
                   / nullif(use_element_count, 0)
               ) AS median_element_delta_percent,
               avg(use_visible_text_chars) FILTER (
                   WHERE sample_crawl_id IS NOT NULL
                     AND use_visible_text_chars IS NOT NULL
               ) AS mean_use_visible_text_chars,
               avg(sample_visible_text_chars) AS mean_sample_visible_text_chars,
               stddev_samp(use_visible_text_chars) FILTER (
                   WHERE sample_crawl_id IS NOT NULL
               ) AS use_visible_text_stddev,
               stddev_samp(sample_visible_text_chars) AS sample_visible_text_stddev,
               CASE
                   WHEN count(use_visible_text_chars) FILTER (
                       WHERE sample_crawl_id IS NOT NULL
                   ) < 2
                     OR avg(use_visible_text_chars) FILTER (
                       WHERE sample_crawl_id IS NOT NULL
                   ) = 0
                   THEN NULL
                   ELSE stddev_samp(use_visible_text_chars) FILTER (
                       WHERE sample_crawl_id IS NOT NULL
                   ) / avg(use_visible_text_chars) FILTER (
                       WHERE sample_crawl_id IS NOT NULL
                   )
               END AS use_visible_text_cv,
               CASE
                   WHEN count(sample_visible_text_chars) < 2
                     OR avg(sample_visible_text_chars) = 0
                   THEN NULL
                   ELSE stddev_samp(sample_visible_text_chars)
                        / avg(sample_visible_text_chars)
               END AS sample_visible_text_cv,
               least(
                   count(DISTINCT use_document_id) FILTER (
                       WHERE sample_crawl_id IS NOT NULL
                   ),
                   count(DISTINCT requested_url) FILTER (
                       WHERE sample_crawl_id IS NOT NULL
                         AND use_document_id IS NOT NULL
                   )
               )::DOUBLE
                   / nullif(count(DISTINCT requested_url) FILTER (
                       WHERE sample_crawl_id IS NOT NULL
                         AND use_document_id IS NOT NULL
                   ), 0) AS use_distinct_document_ratio,
               least(
                   count(DISTINCT sample_document_id),
                   count(DISTINCT requested_url) FILTER (
                       WHERE sample_document_id IS NOT NULL
                   )
               )::DOUBLE
                   / nullif(count(DISTINCT requested_url) FILTER (
                       WHERE sample_document_id IS NOT NULL
                   ), 0) AS sample_distinct_document_ratio,
               median(use_quality_flag_count) FILTER (
                   WHERE sample_crawl_id IS NOT NULL
               ) AS median_use_quality_flag_count,
               median(sample_quality_flag_count) AS median_sample_quality_flag_count,
               count(*) FILTER (
                   WHERE sample_crawl_id IS NOT NULL AND use_failure_count > 0
               ) AS use_acquisition_failure_count,
               count(*) FILTER (
                   WHERE sample_crawl_id IS NOT NULL AND sample_failure_count > 0
               ) AS sample_acquisition_failure_count,
               median(sample_duration_ms - use_duration_ms) AS median_duration_delta_ms,
               max(use_captured_at) AS last_trial_at
        FROM pairs
        GROUP BY scheme, host, port, registrable_domain,
                 use_template, sample_template
        ORDER BY last_trial_at DESC, scheme, host, port
        LIMIT ? OFFSET ?
        """,
        [limit, offset],
    ).fetchall()

    comparisons = tuple(
        _comparison_with_verdict(
            scheme=str(row[0]),
            host=str(row[1]),
            port=int(row[2]),
            registrable_domain=str(row[3]),
            use_template=str(row[4]),
            candidate_template=str(row[5]),
            selected_trials=int(row[6]),
            completed_pairs=int(row[7]),
            recovered_crawls=int(row[8]),
            sample_failures=int(row[9]),
            identical_documents=int(row[10]),
            median_html_delta_percent=_optional_float(row[11]),
            median_visible_text_delta_percent=_optional_float(row[12]),
            median_element_delta_percent=_optional_float(row[13]),
            mean_use_visible_text_chars=_optional_float(row[14]),
            mean_sample_visible_text_chars=_optional_float(row[15]),
            use_visible_text_stddev=_optional_float(row[16]),
            sample_visible_text_stddev=_optional_float(row[17]),
            use_visible_text_cv=_optional_float(row[18]),
            sample_visible_text_cv=_optional_float(row[19]),
            use_distinct_document_ratio=_optional_float(row[20]),
            sample_distinct_document_ratio=_optional_float(row[21]),
            median_use_quality_flag_count=_optional_float(row[22]),
            median_sample_quality_flag_count=_optional_float(row[23]),
            use_acquisition_failure_count=int(row[24]),
            sample_acquisition_failure_count=int(row[25]),
            median_duration_delta_ms=_optional_float(row[26]),
            last_trial_at=row[27],
        )
        for row in comparison_rows
    )
    return PolicyTrialReport(
        summary=PolicyTrialSummary(
            use_crawls=int(summary_row[0]),
            selected_trials=int(summary_row[1]),
            sample_crawls=int(summary_row[2]),
            completed_pairs=int(summary_row[3]),
            awaiting_samples=int(summary_row[4]),
            pairs_with_failure=int(summary_row[5]),
            observed_sample_rate=float(summary_row[6]),
            last_trial_at=summary_row[7],
        ),
        comparisons=comparisons,
        total_comparisons=total_comparisons,
    )


def _paired_trials_cte(crawls: str, documents: str) -> str:
    return f"""
        ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY trial_id, purpose
                ORDER BY captured_at DESC, crawl_id DESC
            ) AS trial_arm_rank
            FROM {crawls}
            WHERE trial_id IS NOT NULL
        ),
        use_arm AS (
            SELECT * FROM ranked
            WHERE purpose = 'use' AND trial_arm_rank = 1
        ),
        sample_arm AS (
            SELECT * FROM ranked
            WHERE purpose = 'sample' AND trial_arm_rank = 1
        ),
        pairs AS (
            SELECT CAST(u.trial_id AS VARCHAR) AS trial_id,
                   u.url_scheme AS scheme,
                   u.url_host AS host,
                   u.url_port AS port,
                   u.url_registrable_domain AS registrable_domain,
                   u.requested_url,
                   u.template AS use_template,
                   coalesce(u.trial_candidate_template, s.template, 'unknown')
                       AS sample_template,
                   u.crawl_id AS use_crawl_id,
                   u.captured_at AS use_captured_at,
                   u.status_code AS use_status_code,
                   u.duration_ms AS use_duration_ms,
                   u.document_id AS use_document_id,
                   ud.html_size_bytes AS use_html_size_bytes,
                   ud.visible_text_chars AS use_visible_text_chars,
                   ud.element_count AS use_element_count,
                   CAST(coalesce(json_array_length(ud.quality_flags_json), 0) AS BIGINT)
                       AS use_quality_flag_count,
                   CASE WHEN u.failure_code IS NULL THEN 0 ELSE 1 END
                       AS use_failure_count,
                   s.crawl_id AS sample_crawl_id,
                   s.captured_at AS sample_captured_at,
                   s.status_code AS sample_status_code,
                   s.duration_ms AS sample_duration_ms,
                   s.document_id AS sample_document_id,
                   sd.html_size_bytes AS sample_html_size_bytes,
                   sd.visible_text_chars AS sample_visible_text_chars,
                   sd.element_count AS sample_element_count,
                   CAST(coalesce(json_array_length(sd.quality_flags_json), 0) AS BIGINT)
                       AS sample_quality_flag_count,
                   CASE WHEN s.failure_code IS NULL THEN 0 ELSE 1 END
                       AS sample_failure_count
            FROM use_arm AS u
            LEFT JOIN sample_arm AS s USING (trial_id)
            LEFT JOIN {documents} AS ud ON ud.document_id = u.document_id
            LEFT JOIN {documents} AS sd ON sd.document_id = s.document_id
        )
    """


def _table(catalogue: Catalogue, name: str) -> str:
    return ".".join(
        '"' + value.replace('"', '""') + '"'
        for value in (catalogue.config.alias, catalogue.config.schema, name)
    )


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)


def _comparison_with_verdict(**values: object) -> PolicyTrialComparison:
    verdict, reason = _trial_verdict(
        completed_pairs=int(values["completed_pairs"]),
        recovered_crawls=int(values["recovered_crawls"]),
        sample_failures=int(values["sample_failures"]),
        identical_documents=int(values["identical_documents"]),
        median_visible_text_delta_percent=_as_optional_float(
            values["median_visible_text_delta_percent"]
        ),
        median_element_delta_percent=_as_optional_float(
            values["median_element_delta_percent"]
        ),
        use_visible_text_cv=_as_optional_float(values["use_visible_text_cv"]),
        sample_visible_text_cv=_as_optional_float(values["sample_visible_text_cv"]),
        use_distinct_document_ratio=_as_optional_float(
            values["use_distinct_document_ratio"]
        ),
        sample_distinct_document_ratio=_as_optional_float(
            values["sample_distinct_document_ratio"]
        ),
        median_use_quality_flag_count=_as_optional_float(
            values["median_use_quality_flag_count"]
        ),
        median_sample_quality_flag_count=_as_optional_float(
            values["median_sample_quality_flag_count"]
        ),
        use_acquisition_failure_count=int(values["use_acquisition_failure_count"]),
        sample_acquisition_failure_count=int(
            values["sample_acquisition_failure_count"]
        ),
    )
    return PolicyTrialComparison(**values, verdict=verdict, verdict_reason=reason)  # type: ignore[arg-type]


def _trial_verdict(
    *,
    completed_pairs: int,
    recovered_crawls: int,
    sample_failures: int,
    identical_documents: int,
    median_visible_text_delta_percent: float | None,
    median_element_delta_percent: float | None,
    use_visible_text_cv: float | None,
    sample_visible_text_cv: float | None,
    use_distinct_document_ratio: float | None,
    sample_distinct_document_ratio: float | None,
    median_use_quality_flag_count: float | None,
    median_sample_quality_flag_count: float | None,
    use_acquisition_failure_count: int,
    sample_acquisition_failure_count: int,
) -> tuple[PolicyTrialVerdict, str]:
    """Assign a conservative, explainable verdict from query-time evidence."""

    if completed_pairs == 0:
        return "awaiting_sample", "The sampled acquisition has not completed yet."
    if sample_acquisition_failure_count > use_acquisition_failure_count:
        return "regressed", "The trial policy caused more acquisition failures."
    if sample_failures > 0 and recovered_crawls == 0:
        return "regressed", "The trial policy lost content captured by the current policy."
    if recovered_crawls > 0:
        return "promising", "The trial policy recovered acquisitions the current policy missed."
    if completed_pairs < 3:
        return (
            "insufficient_evidence",
            f"Only {completed_pairs} completed pair{'s' if completed_pairs != 1 else ''}; at least 3 are needed for a verdict.",
        )

    use_flags = median_use_quality_flag_count
    sample_flags = median_sample_quality_flag_count
    if use_flags is not None and sample_flags is not None and sample_flags < use_flags:
        return "promising", "The trial policy reduced median quality flags."

    diversity_gain = _difference(
        sample_distinct_document_ratio, use_distinct_document_ratio
    )
    variation_gain = _difference(sample_visible_text_cv, use_visible_text_cv)
    if diversity_gain is not None and diversity_gain >= 0.10:
        return "promising", "The trial policy produced more distinct documents across URLs."
    if variation_gain is not None and variation_gain >= 0.10:
        return "promising", "Visible-text variation increased across distinct URLs."
    if (
        median_visible_text_delta_percent is not None
        and median_visible_text_delta_percent >= 10.0
    ):
        return "promising", "The trial policy captured materially more visible text."
    if median_element_delta_percent is not None and median_element_delta_percent >= 10.0:
        return "promising", "The trial policy captured materially more elements."

    if (
        median_visible_text_delta_percent is not None
        and median_visible_text_delta_percent <= -10.0
        and median_element_delta_percent is not None
        and median_element_delta_percent <= -10.0
    ):
        return "regressed", "The trial policy captured materially less page evidence."

    comparable_pairs = completed_pairs - recovered_crawls - sample_failures
    identical_share = (
        identical_documents / comparable_pairs if comparable_pairs > 0 else None
    )
    if identical_share is not None and identical_share >= 0.80:
        return "no_clear_gain", "At least 80% of comparable pairs produced the same document."
    return "inconclusive", "The observed changes are too small or mixed for a recommendation."


def _difference(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else left - right


def _as_optional_float(value: object) -> float | None:
    return None if value is None else float(value)
