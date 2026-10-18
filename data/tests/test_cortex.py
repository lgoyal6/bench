import json

import pytest


def test_cortex_does_not_call_model_without_real_rows(sql_api, monkeypatch):
    statements = []

    def execute(statement, values=()):
        statements.append(statement)
        return []

    monkeypatch.setattr(sql_api, "execute", execute)
    try:
        with pytest.raises(ValueError, match="No actual"):
            sql_api.answer(task_id="L1-test", question="Why did it fail?", model="account-model")
        assert len(statements) == 1 and "AI_COMPLETE" not in statements[0]
    finally:
        sql_api.close()


@pytest.mark.parametrize("citation", ["unit-run", "invented-run"])
def test_cortex_citations_are_checked_against_bounded_context(sql_api, monkeypatch, citation):
    calls = []

    def execute(statement, values=()):
        calls.append((statement, values))
        if len(calls) == 1:
            return [[json.dumps({"run_id": "unit-run", "verdict": "fail"})]]
        return [[json.dumps({"answer": "A failure was recorded.", "cited_run_ids": [citation]})]]

    monkeypatch.setattr(sql_api, "execute", execute)
    try:
        if citation == "invented-run":
            with pytest.raises(ValueError, match="citations"):
                sql_api.answer(task_id="L1-test", question="Outcomes?", model="account-model")
        else:
            answer = sql_api.answer(task_id="L1-test", question="Outcomes?", model="account-model")
            assert answer["context_run_ids"] == ["unit-run"]
            assert answer["context_limit"] == 20
        assert calls[0][1] == ("L1-test",)
        assert calls[1][1][0] == "account-model"
    finally:
        sql_api.close()
