import uuid
from typing import Any

from backend.db.models import ChapterReview


REVIEW_DIMENSION_KEYS = (
    'outline_adherence',
    'instruction_adherence',
    'continuity_consistency',
    'character_consistency',
    'writing_quality',
)


def build_review(
    attempt_id: uuid.UUID,
    payload: dict[str, Any],
    normalized_payload: dict[str, Any] | None = None,
) -> ChapterReview:
    normalized = normalized_payload or normalize_review_payload(payload)
    dimensions = normalized['dimensions']
    return ChapterReview(
        attempt_id=attempt_id,
        overall_score=normalized['overall_score'],
        passed=normalized['passed'],
        outline_score=dimensions['outline_adherence']['score'],
        instruction_score=dimensions['instruction_adherence']['score'],
        continuity_score=dimensions['continuity_consistency']['score'],
        character_score=dimensions['character_consistency']['score'],
        writing_score=dimensions['writing_quality']['score'],
        blocking_issues=normalized.get('blocking_issues', []),
        uncovered_outline_points=normalized.get('uncovered_outline_points', []),
        violated_instructions=normalized.get('violated_instructions', []),
        improvement_suggestions=normalized.get('improvement_suggestions', []),
        non_scoring_notes=normalized.get('non_scoring_notes', []),
        raw_json=payload,
    )


def is_review_passed(
    payload: dict[str, Any],
    *,
    runtime_settings: dict[str, Any] | None = None,
    hard_gates_enabled: bool = True,
) -> bool:
    normalized = normalize_review_payload(
        payload,
        runtime_settings=runtime_settings,
        hard_gates_enabled=hard_gates_enabled,
    )
    dimensions = normalized['dimensions']
    thresholds = _resolve_review_thresholds(runtime_settings)
    if not hard_gates_enabled:
        return bool(normalized['passed'])
    return (
        normalized['passed']
        and normalized['overall_score'] >= thresholds['review_overall_score_threshold']
        and dimensions['outline_adherence']['score'] >= thresholds['review_outline_score_threshold']
        and dimensions['instruction_adherence']['score'] >= thresholds['review_instruction_score_threshold']
        and not normalized.get('blocking_issues')
    )


def normalize_review_payload(
    payload: Any,
    *,
    runtime_settings: dict[str, Any] | None = None,
    hard_gates_enabled: bool = True,
) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    thresholds = _resolve_review_thresholds(runtime_settings)
    raw_dimensions = source.get('dimensions')
    dimensions: dict[str, Any] = raw_dimensions if isinstance(raw_dimensions, dict) else {}
    raw_scores = source.get('scores')
    scores: dict[str, Any] = raw_scores if isinstance(raw_scores, dict) else {}
    explicit_passed = source.get('passed') if isinstance(source.get('passed'), bool) else None
    overall_score = _coerce_score(source.get('overall_score'))
    fallback_score = (
        overall_score
        if overall_score is not None
        else (thresholds['review_overall_score_threshold'] if explicit_passed is True else 0.0)
    )

    normalized_dimensions: dict[str, dict[str, Any]] = {}
    for key in REVIEW_DIMENSION_KEYS:
        raw_dimension = dimensions.get(key)
        if raw_dimension is None:
            raw_dimension = scores.get(key)
        if raw_dimension is None:
            raw_dimension = source.get(key)
        comment = ''
        if isinstance(raw_dimension, dict):
            comment = _coerce_text(raw_dimension.get('comment') or raw_dimension.get('reason'))
        normalized_dimensions[key] = {
            'score': _extract_dimension_score(raw_dimension, fallback_score),
            'comment': comment,
        }

    if overall_score is None:
        computed_scores = [item['score'] for item in normalized_dimensions.values()]
        overall_score = round(sum(computed_scores) / len(computed_scores), 2) if computed_scores else fallback_score

    blocking_issues = _normalize_text_list(source.get('blocking_issues'))
    uncovered_outline_points = _normalize_text_list(source.get('uncovered_outline_points'))
    violated_instructions = _normalize_text_list(source.get('violated_instructions'))
    improvement_suggestions = _normalize_text_list(source.get('improvement_suggestions'))
    major_issues = _normalize_text_list(source.get('major_issues'))
    minor_issues = _normalize_text_list(source.get('minor_issues'))
    failed_reasons = _normalize_text_list(source.get('failed_reasons'))

    if (
        hard_gates_enabled
        and not blocking_issues
        and normalized_dimensions['outline_adherence']['score'] < thresholds['review_outline_score_threshold']
    ):
        outline_feedback = _dedupe_texts(
            major_issues
            + [normalized_dimensions['outline_adherence']['comment']]
            + failed_reasons
        )
        blocking_issues.extend(outline_feedback[:2])

    if (
        hard_gates_enabled
        and not violated_instructions
        and normalized_dimensions['instruction_adherence']['score'] < thresholds['review_instruction_score_threshold']
    ):
        instruction_feedback = _dedupe_texts(
            [normalized_dimensions['instruction_adherence']['comment']]
            + minor_issues
            + failed_reasons
        )
        violated_instructions.extend(instruction_feedback[:2])

    if not improvement_suggestions:
        improvement_suggestions = _dedupe_texts(
            major_issues
            + minor_issues
            + failed_reasons
            + [source.get('summary')]
        )[:4]

    passed = explicit_passed
    if passed is None:
        if hard_gates_enabled:
            passed = (
                overall_score >= thresholds['review_overall_score_threshold']
                and normalized_dimensions['outline_adherence']['score'] >= thresholds['review_outline_score_threshold']
                and normalized_dimensions['instruction_adherence']['score'] >= thresholds['review_instruction_score_threshold']
                and not blocking_issues
            )
        else:
            passed = overall_score >= thresholds['review_overall_score_threshold']

    return {
        'overall_score': overall_score,
        'passed': passed,
        'dimensions': normalized_dimensions,
        'blocking_issues': _dedupe_texts(blocking_issues),
        'uncovered_outline_points': _dedupe_texts(uncovered_outline_points),
        'violated_instructions': _dedupe_texts(violated_instructions),
        'improvement_suggestions': _dedupe_texts(improvement_suggestions),
        'non_scoring_notes': _dedupe_texts(_normalize_text_list(source.get('non_scoring_notes'))),
    }


def _resolve_review_thresholds(runtime_settings: dict[str, Any] | None) -> dict[str, Any]:
    if runtime_settings is not None:
        return runtime_settings
    return {
        'review_overall_score_threshold': 8.0,
        'review_outline_score_threshold': 8.0,
        'review_instruction_score_threshold': 8.0,
    }


def _extract_dimension_score(raw_dimension: Any, fallback_score: float) -> float:
    if isinstance(raw_dimension, dict):
        score = _coerce_score(raw_dimension.get('score') or raw_dimension.get('value'))
        if score is not None:
            return score
    else:
        score = _coerce_score(raw_dimension)
        if score is not None:
            return score
    return fallback_score


def _normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, list):
        return []

    normalized: list[str] = []
    for item in value:
        text = _coerce_text(item)
        if text:
            normalized.append(text)
    return normalized


def _coerce_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        primary_text = ''
        for key in ('issue', 'title', 'description', 'comment', 'reason', 'text', 'summary'):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                primary_text = candidate.strip()
                break
        if primary_text:
            details = value.get('details')
            if isinstance(details, str) and details.strip():
                return f'{primary_text}：{details.strip()}'
            return primary_text
        parts: list[str] = []
        for key in ('details', 'impact', 'evidence'):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                parts.append(candidate.strip())
        return '；'.join(parts)
    return ''


def _dedupe_texts(items: list[Any]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = _coerce_text(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _coerce_score(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0.0, min(10.0, float(value)))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return max(0.0, min(10.0, float(text)))
        except ValueError:
            return None
    return None
