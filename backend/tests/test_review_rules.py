import uuid

from backend.services.review_rules import build_review, is_review_passed, normalize_review_payload


def test_normalize_review_payload_preserves_structured_issue_lists():
    payload = {
        'overall_score': 7.6,
        'dimensions': {
            'outline_adherence': {'score': 7, 'comment': '关键落点不足'},
            'instruction_adherence': {'score': 8, 'comment': '指令基本满足'},
            'continuity_consistency': {'score': 8, 'comment': '连续性稳定'},
            'character_consistency': {'score': 8, 'comment': '人物一致'},
            'writing_quality': {'score': 8, 'comment': '文笔稳定'},
        },
        'blocking_issues': [
            {'issue': '缺少主角亲手触发总控的动作', 'impact': '关键大纲点未落地'},
        ],
        'uncovered_outline_points': [
            {'title': '展示“官方抹去”的直接证据'},
        ],
        'violated_instructions': [
            {'reason': '收尾没有停在明确悬念上'},
        ],
    }

    normalized = normalize_review_payload(payload)

    assert normalized['blocking_issues'] == ['缺少主角亲手触发总控的动作']
    assert normalized['uncovered_outline_points'] == ['展示“官方抹去”的直接证据']
    assert normalized['violated_instructions'] == ['收尾没有停在明确悬念上']


def test_is_review_passed_honors_runtime_thresholds():
    payload = {
        'overall_score': 7.6,
        'dimensions': {
            'outline_adherence': {'score': 7.1},
            'instruction_adherence': {'score': 6.6},
            'continuity_consistency': {'score': 8.5},
            'character_consistency': {'score': 8.4},
            'writing_quality': {'score': 8.3},
        },
        'blocking_issues': [],
    }
    runtime_settings = {
        'review_overall_score_threshold': 7.5,
        'review_outline_score_threshold': 7.0,
        'review_instruction_score_threshold': 6.5,
    }

    assert is_review_passed(payload, runtime_settings=runtime_settings) is True


def test_is_review_passed_can_disable_hard_gates():
    payload = {
        'overall_score': 6.2,
        'passed': True,
        'dimensions': {
            'outline_adherence': {'score': 5.5},
            'instruction_adherence': {'score': 5.8},
            'continuity_consistency': {'score': 7.2},
            'character_consistency': {'score': 7.0},
            'writing_quality': {'score': 7.1},
        },
        'blocking_issues': ['结尾张力不足'],
    }

    assert is_review_passed(payload, hard_gates_enabled=False) is True


def test_normalize_review_payload_does_not_infer_hard_gate_failures_when_disabled():
    payload = {
        'overall_score': 7.6,
        'passed': True,
        'scores': {
            'outline_adherence': {'score': 7.1, 'reason': '大纲覆盖不足'},
            'instruction_adherence': {'score': 6.2, 'reason': '指令执行偏弱'},
            'continuity_consistency': {'score': 8.0},
            'character_consistency': {'score': 8.0},
            'writing_quality': {'score': 8.0},
        },
    }

    normalized = normalize_review_payload(payload, hard_gates_enabled=False)

    assert normalized['passed'] is True
    assert normalized['blocking_issues'] == []
    assert normalized['violated_instructions'] == []


def test_build_review_fills_missing_dimensions_from_overall_score():
    payload = {
        'overall_score': 8.6,
        'passed': True,
        'improvement_suggestions': '延续当前线索推进',
    }

    review = build_review(uuid.uuid4(), payload)

    assert review.overall_score == 8.6
    assert review.passed is True
    assert review.outline_score == 8.6
    assert review.instruction_score == 8.6
    assert review.continuity_score == 8.6
    assert review.character_score == 8.6
    assert review.writing_score == 8.6
    assert review.improvement_suggestions == ['延续当前线索推进']
    assert review.raw_json == payload
