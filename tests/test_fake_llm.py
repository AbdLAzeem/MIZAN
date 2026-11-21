import pytest

from mizan.llm import LLM, FakeLLM, Message


def test_fake_llm_returns_scripted_replies_in_order() -> None:
    llm: LLM = FakeLLM(["first", "second"])
    msgs = [Message("user", "hi")]
    assert llm.complete(msgs) == "first"
    assert llm.complete(msgs) == "second"


def test_fake_llm_records_calls() -> None:
    llm = FakeLLM(["ok"])
    llm.complete([Message("user", "hello")])
    assert llm.calls == [[Message("user", "hello")]]


def test_fake_llm_fails_loudly_when_script_runs_out() -> None:
    llm = FakeLLM([])
    with pytest.raises(RuntimeError):
        llm.complete([Message("user", "hi")])
