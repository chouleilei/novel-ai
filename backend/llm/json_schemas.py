from backend.llm.base import JSONValue


CRITIC_REVIEW_SCHEMA: dict[str, JSONValue] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "overall_score",
        "passed",
        "dimensions",
        "blocking_issues",
        "uncovered_outline_points",
        "violated_instructions",
        "improvement_suggestions",
        "non_scoring_notes",
    ],
    "properties": {
        "overall_score": {"type": ["number", "string", "null"]},
        "passed": {"type": ["boolean", "null"]},
        "dimensions": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": [
                "outline_adherence",
                "instruction_adherence",
                "continuity_consistency",
                "character_consistency",
                "writing_quality",
            ],
            "properties": {
                "outline_adherence": {
                    "type": ["object", "number", "string", "null"],
                    "additionalProperties": False,
                    "required": ["score", "comment", "reason"],
                    "properties": {
                        "score": {"type": ["number", "string", "null"]},
                        "comment": {"type": ["string", "null"]},
                        "reason": {"type": ["string", "null"]},
                    },
                },
                "instruction_adherence": {
                    "type": ["object", "number", "string", "null"],
                    "additionalProperties": False,
                    "required": ["score", "comment", "reason"],
                    "properties": {
                        "score": {"type": ["number", "string", "null"]},
                        "comment": {"type": ["string", "null"]},
                        "reason": {"type": ["string", "null"]},
                    },
                },
                "continuity_consistency": {
                    "type": ["object", "number", "string", "null"],
                    "additionalProperties": False,
                    "required": ["score", "comment", "reason"],
                    "properties": {
                        "score": {"type": ["number", "string", "null"]},
                        "comment": {"type": ["string", "null"]},
                        "reason": {"type": ["string", "null"]},
                    },
                },
                "character_consistency": {
                    "type": ["object", "number", "string", "null"],
                    "additionalProperties": False,
                    "required": ["score", "comment", "reason"],
                    "properties": {
                        "score": {"type": ["number", "string", "null"]},
                        "comment": {"type": ["string", "null"]},
                        "reason": {"type": ["string", "null"]},
                    },
                },
                "writing_quality": {
                    "type": ["object", "number", "string", "null"],
                    "additionalProperties": False,
                    "required": ["score", "comment", "reason"],
                    "properties": {
                        "score": {"type": ["number", "string", "null"]},
                        "comment": {"type": ["string", "null"]},
                        "reason": {"type": ["string", "null"]},
                    },
                },
            },
        },
        "blocking_issues": {
            "type": ["array", "string", "null"],
            "items": {
                "type": ["string", "object"],
                "additionalProperties": False,
                "properties": {},
                "required": [],
            },
        },
        "uncovered_outline_points": {
            "type": ["array", "string", "null"],
            "items": {
                "type": ["string", "object"],
                "additionalProperties": False,
                "properties": {},
                "required": [],
            },
        },
        "violated_instructions": {
            "type": ["array", "string", "null"],
            "items": {
                "type": ["string", "object"],
                "additionalProperties": False,
                "properties": {},
                "required": [],
            },
        },
        "improvement_suggestions": {
            "type": ["array", "string", "null"],
            "items": {
                "type": ["string", "object"],
                "additionalProperties": False,
                "properties": {},
                "required": [],
            },
        },
        "non_scoring_notes": {
            "type": ["array", "string", "null"],
            "items": {
                "type": ["string", "object"],
                "additionalProperties": False,
                "properties": {},
                "required": [],
            },
        },
    },
}
