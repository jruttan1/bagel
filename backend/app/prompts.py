PROFILE_INSTRUCTIONS = """
Convert onboarding answers and portfolio characteristics into durable internal decision context. Separate
financial constraints from preferences. Infer conservatively. Do not preserve colorful wording, uncertain
ticker transcription, or unsupported market claims as facts. Do not treat a market opinion as permanent.
Return only the requested structured result; the summary is an internal note and must not address the person.
""".strip()


QUESTION_INSTRUCTIONS = """
Write one natural onboarding question that is answerable in one sentence and gathers the requested category.
If holdings are supplied, use only a meaningful non-obvious pattern. Do not praise, validate, or challenge the
person. Return only the question with no label, preamble, explanation, or example.
""".strip()
