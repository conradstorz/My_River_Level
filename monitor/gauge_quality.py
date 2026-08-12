"""
Judge how good each NOAA gauge is at predicting floods.

A gauge having flood-stage thresholds only means NOAA *could* say when
flooding starts; it says nothing about whether the forecasts are any good.
This module answers the real question — "does this gauge make reasonable
flood predictions?" — by comparing forecasts we archived earlier against the
levels that were actually observed, and turning the result into a letter
grade with a plain-English explanation.

Grading rubric (checked in this order; the first match wins):

1. No flood-stage thresholds        -> "F"        Not usable for flood warning
2. No forecast published            -> "D"        Observation only
3. No observation in the last 24 h  -> "F"        Not reporting
4. Fewer than MIN_SAMPLES matched   -> "Unrated"  Collecting accuracy data
   forecast/observation pairs
5. Otherwise, by the mean absolute error of its 24-hour forecasts:
       < 0.5 ft -> "A"  Reliable flood predictions
       < 1.0 ft -> "B"  Good
       < 2.0 ft -> "C"  Fair
       else     -> "D"  Unreliable

"Unrated" is deliberately not a letter. The forecast archive starts empty, so
a freshly added gauge has nothing to score and must say so rather than imply
a verdict it has not earned. That is the normal state for a new gauge, not a
fault.

A matched pair is an archived forecast point issued ~24 h (or 48/72 h) before
the moment it describes, together with the observation nearest that moment;
both windows are +/- `tolerance_minutes`. Points with no nearby observation are
skipped, so the sample count is honest about what was actually verifiable.

`set_gauge_quality` has one text column for the explanation, so the headline
and the detail are stored joined by HEADLINE_SEPARATOR; `split_quality_detail`
takes them apart again for display.
"""

import logging
from datetime import datetime, timedelta, timezone

from db.models import (get_all_noaa_gauges, get_forecast_points,
                       get_noaa_observations, set_gauge_quality)

logger = logging.getLogger(__name__)

# Matched forecast/observation pairs needed before a letter grade is honest.
MIN_SAMPLES = 10

# Mean-absolute-error boundaries in feet for the 24-hour forecast, and the
# grade plus plain-English headline each one earns.
GRADE_THRESHOLDS = (
    (0.5, "A", "Reliable flood predictions"),
    (1.0, "B", "Good"),
    (2.0, "C", "Fair"),
)
WORST_GRADE = ("D", "Unreliable")

# A gauge silent this long is treated as not reporting.
REPORTING_WINDOW_HOURS = 24

# Horizons scored, longest last; the 24-hour figure decides the grade.
HORIZONS = (24, 48, 72)

HEADLINE_SEPARATOR = " — "


def split_quality_detail(stored_detail):
    """Split a stored explanation back into (headline, detail)."""
    if not stored_detail:
        return "", ""
    parts = stored_detail.split(HEADLINE_SEPARATOR, 1)
    if len(parts) == 1:
        return "", parts[0]
    return parts[0], parts[1]


def _aware(value):
    """Coerce a datetime to UTC-aware so archive comparisons are valid."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def forecast_error_samples(lid, horizon_hours, tolerance_minutes=90, db_path=None):
    """
    Absolute forecast error in feet at roughly `horizon_hours` of lead time.

    Each archived forecast point whose lead time (valid_at - issued_at) is
    within +/- `tolerance_minutes` of `horizon_hours` is paired with the
    observation closest to its valid_at, also within that tolerance. Points
    with no nearby observation are skipped.
    """
    points = get_forecast_points(lid, db_path)
    if not points:
        return []
    observations = [
        (_aware(o["observed_at"]), o["stage"])
        for o in get_noaa_observations(lid, db_path)
        if o.get("observed_at") is not None and o.get("stage") is not None
    ]
    if not observations:
        return []

    tolerance = timedelta(minutes=tolerance_minutes)
    target = timedelta(hours=horizon_hours)
    samples = []
    for point in points:
        issued_at = _aware(point.get("issued_at"))
        valid_at = _aware(point.get("valid_at"))
        predicted = point.get("predicted_stage")
        if issued_at is None or valid_at is None or predicted is None:
            continue
        lead = valid_at - issued_at
        if abs(lead - target) > tolerance:
            continue
        observed_at, observed = min(observations,
                                    key=lambda o: abs(o[0] - valid_at))
        if abs(observed_at - valid_at) > tolerance:
            continue
        samples.append(abs(predicted - observed))
    return samples


def _mean(values):
    """Mean of `values`, or None when there is nothing to average."""
    if not values:
        return None
    return sum(values) / len(values)


def _has_flood_categories(gauge):
    """True when NOAA publishes at least one flood-stage threshold."""
    return any(gauge.get(key) is not None for key in (
        "action_stage", "minor_flood_stage",
        "moderate_flood_stage", "major_flood_stage",
    ))


def _is_reporting(observations, now=None):
    """True when the gauge produced a reading inside the reporting window."""
    if not observations:
        return False
    latest = _aware(observations[-1].get("observed_at"))
    if latest is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - latest) <= timedelta(hours=REPORTING_WINDOW_HOURS)


def _accuracy_detail(mae_24h, mae_48h, mae_72h, samples):
    """Plain-English sentence(s) stating the measured forecast error."""
    detail = (f"24-hour forecasts are off by {mae_24h:.2f} ft on average "
              f"({samples} forecasts checked).")
    if mae_48h is not None:
        detail += f" 48-hour forecasts are off by {mae_48h:.2f} ft."
    if mae_72h is not None:
        detail += f" 72-hour forecasts are off by {mae_72h:.2f} ft."
    return detail


def score_gauge(gauge, db_path=None):
    """
    Grade one gauge's flood-prediction quality. See the module docstring
    for the rubric. `gauge` is a row from `get_all_noaa_gauges()`.

    Returns a dict with grade, headline, detail, mae_24h/48h/72h, samples,
    has_flood_categories, publishes_forecast, and reporting.
    """
    lid = gauge["lid"]
    errors = {h: forecast_error_samples(lid, h, db_path=db_path)
              for h in HORIZONS}
    mae = {h: _mean(errors[h]) for h in HORIZONS}
    samples = len(errors[24])

    has_flood_categories = _has_flood_categories(gauge)
    publishes_forecast = bool(get_forecast_points(lid, db_path))
    observations = get_noaa_observations(lid, db_path)
    reporting = _is_reporting(observations)

    result = {
        "mae_24h": mae[24],
        "mae_48h": mae[48],
        "mae_72h": mae[72],
        "samples": samples,
        "has_flood_categories": has_flood_categories,
        "publishes_forecast": publishes_forecast,
        "reporting": reporting,
    }

    if not has_flood_categories:
        result.update(
            grade="F",
            headline="Not usable for flood warning",
            detail=("NOAA publishes no flood-stage thresholds for this gauge, "
                    "so it cannot tell you when flooding starts."),
        )
    elif not publishes_forecast and gauge.get("has_forecast") is False:
        result.update(
            grade="D",
            headline="Observation only",
            detail=("This gauge reports current levels but NOAA publishes no "
                    "forecast for it, so you get no advance warning."),
        )
    elif not publishes_forecast:
        # We have no forecast on file but NOAA has never told us there isn't
        # one — the gauge was just added, or the last check failed. Saying
        # "publishes no forecast" here would be a confident false statement.
        result.update(
            grade="Unrated",
            headline="Not yet assessed",
            detail=("We have not yet retrieved a forecast for this gauge. "
                    "Check back after the next forecast update."),
        )
    elif not reporting:
        result.update(
            grade="F",
            headline="Not reporting",
            detail="No readings in the last 24 hours.",
        )
    elif samples < MIN_SAMPLES:
        result.update(
            grade="Unrated",
            headline="Collecting accuracy data",
            detail=("Has flood thresholds and publishes forecasts. Accuracy "
                    f"grade needs {MIN_SAMPLES} matched forecasts; "
                    f"{samples} so far."),
        )
    else:
        grade, headline = WORST_GRADE
        for limit, letter, words in GRADE_THRESHOLDS:
            if mae[24] < limit:
                grade, headline = letter, words
                break
        result.update(
            grade=grade,
            headline=headline,
            detail=_accuracy_detail(mae[24], mae[48], mae[72], samples),
        )
    return result


def score_all_gauges(db_path=None):
    """Grade every NOAA gauge, persist the verdicts, and return how many."""
    scored = 0
    for gauge in get_all_noaa_gauges(db_path):
        try:
            result = score_gauge(gauge, db_path)
        except Exception:
            logger.exception("Error scoring NOAA gauge %s", gauge.get("lid"))
            continue
        set_gauge_quality(
            gauge["lid"],
            result["grade"],
            f"{result['headline']}{HEADLINE_SEPARATOR}{result['detail']}",
            db_path,
        )
        scored += 1
        logger.info("Gauge %s graded %s (%s)", gauge["lid"],
                    result["grade"], result["headline"])
    return scored
