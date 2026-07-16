"""The eval judge: the strict 0-10 x 4 validator (clamping), verdict paths, breaker, env config, stub."""

from __future__ import annotations

import json

from toolscout_eval.judge import (
    PROMPT_VERSION,
    EvalJudgeConfig,
    make_eval_judge,
    parse_eval_json,
    stub_judge,
)

GOOD = json.dumps({"scores": {"TF": 7, "TA": 5, "TG": 6, "PA": 8}, "notes": "fine"})

INPUTS = {"task": "t", "reference": "r", "execution_summary": "s", "final_solution": "42",
          "total_rounds": 1}


def test_validator_parses_good_json():
    v = parse_eval_json(GOOD)
    assert v.ok
    assert v.scores == {"TF": 7.0, "TA": 5.0, "TG": 6.0, "PA": 8.0}
    assert v.notes == "fine"


def test_validator_clamps_out_of_range_to_0_10():
    v = parse_eval_json(json.dumps({"scores": {"TF": 15, "TA": -3, "TG": 5, "PA": 10.5}}))
    assert v.ok
    assert v.scores == {"TF": 10.0, "TA": 0.0, "TG": 5.0, "PA": 10.0}


def test_validator_rejects_missing_category():
    v = parse_eval_json(json.dumps({"scores": {"TF": 5, "TA": 5, "TG": 5}}))
    assert not v.ok and any("PA" in e for e in v.errors)


def test_validator_rejects_non_numeric_and_bool_scores():
    assert not parse_eval_json(json.dumps({"scores": {"TF": "high", "TA": 5, "TG": 5, "PA": 5}})).ok
    assert not parse_eval_json(json.dumps({"scores": {"TF": True, "TA": 5, "TG": 5, "PA": 5}})).ok


def test_validator_rejects_non_json():
    assert not parse_eval_json("the run was fine, 8/10").ok
    assert not parse_eval_json("").ok


def test_validator_tolerates_fences_and_extra_fields():
    fenced = "```json\n" + json.dumps({
        "scores": {"TF": 4, "TA": 4, "TG": 4, "PA": 4, "dependency_awareness": 9},
        "notes": "n", "parallelism_and_efficiency": 2}) + "\n```"
    v = parse_eval_json(fenced)
    assert v.ok and set(v.scores) == {"TF", "TA", "TG", "PA"}


def test_make_eval_judge_scores_via_injected_chat_fn():
    judge = make_eval_judge(EvalJudgeConfig(), chat_fn=lambda prompt: GOOD)
    verdict = judge(INPUTS)
    assert verdict.ok
    assert verdict.score is not None and verdict.score.TF == 7.0 and verdict.score.notes == "fine"


def test_judge_prompt_carries_the_reconstructed_inputs():
    seen = {}

    def chat(prompt: str) -> str:
        seen["prompt"] = prompt
        return GOOD

    make_eval_judge(EvalJudgeConfig(), chat_fn=chat)(dict(INPUTS, task="upper-case 'ok'"))
    prompt = seen["prompt"]
    assert "upper-case 'ok'" in prompt          # the fuzzy task
    assert "UNTRUSTED" in prompt                # the trajectory is data to assess, not instructions
    assert "TOTAL ROUNDS: 1" in prompt
    assert "do NOT penalize output format" in prompt


def test_endpoint_error_yields_unscored_verdict_never_a_fake_zero():
    def chat(prompt: str) -> str:
        raise RuntimeError("boom")

    judge = make_eval_judge(EvalJudgeConfig(transient_retries=0), chat_fn=chat)
    verdict = judge(INPUTS)
    assert not verdict.ok and verdict.score is None and "endpoint" in verdict.reason


def test_circuit_breaker_short_circuits_a_hopeless_judge():
    calls = {"n": 0}

    def chat(prompt: str) -> str:
        calls["n"] += 1
        return "not json"

    judge = make_eval_judge(EvalJudgeConfig(max_consecutive_invalid=2), chat_fn=chat)
    assert not judge(INPUTS).ok
    assert not judge(INPUTS).ok
    third = judge(INPUTS)
    assert not third.ok and "circuit" in third.reason
    assert calls["n"] == 2                      # the third call never reached the model


def test_stub_judge_is_deterministic_and_offline():
    a, b = stub_judge(INPUTS), stub_judge(dict(INPUTS, task="an entirely different task"))
    assert a.ok and b.ok and a.score == b.score
    assert "stub" in a.score.notes              # the notes say it is not a model verdict


def test_config_reads_tseval_env(monkeypatch):
    monkeypatch.setenv("TSEVAL_MODEL", "judge-model-x")
    monkeypatch.setenv("TSEVAL_BASE_URL", "http://localhost:9")
    monkeypatch.setenv("TSEVAL_API_KEY", "k")
    monkeypatch.setenv("TSEVAL_TIMEOUT", "12.5")
    c = EvalJudgeConfig.from_env()
    assert (c.model, c.base_url, c.api_key, c.timeout) == ("judge-model-x", "http://localhost:9", "k", 12.5)


def test_prompt_version_is_pinned():
    assert PROMPT_VERSION == "atlas-eval-v1"
