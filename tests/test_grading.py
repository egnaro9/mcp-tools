"""Grading an answer against its sources — the tool that makes the stack useful.

The property that matters: a fabricated figure gets caught, and a grounded answer
isn't falsely accused. Both are pinned here, plus determinism — the whole reason
this is lexical rather than a model judgement.
"""
from mcptools.grading import format_report, grade_answer
from mcptools.server import handle

SOURCES = [
    "BM25 is a ranking function that scores documents using term frequency saturation "
    "and document length normalization.",
    "It was developed as part of the Okapi information retrieval system.",
]


def test_grounded_answer_is_fully_supported():
    r = grade_answer("BM25 is a ranking function. It uses term frequency saturation "
                     "and document length normalization.", SOURCES)
    assert r["faithfulness"] == 1.0
    assert r["unsupported"] == []
    assert r["verdict"] == "fully supported"


def test_fabricated_number_is_caught():
    """The strongest hallucination tell: a precise figure the sources never state."""
    r = grade_answer("BM25 is a ranking function. It improves accuracy by 47%.", SOURCES)
    assert r["faithfulness"] < 1.0
    bad = r["unsupported"][0]
    assert "47" in bad["unsupported_numbers"]
    assert "47" in bad["reason"]


def test_offtopic_claim_is_caught_even_without_numbers():
    r = grade_answer("BM25 is a ranking function. Penguins migrate across Antarctic ice shelves.",
                     SOURCES)
    assert [c["supported"] for c in r["claims"]] == [True, False]
    assert "content words" in r["unsupported"][0]["reason"]


def test_report_names_the_sentence_to_cut():
    r = grade_answer("It improves accuracy by 47%.", SOURCES)
    out = format_report(r)
    assert "47%" in out and "do not support" in out


def test_deterministic():
    a = "BM25 is a ranking function. It improves accuracy by 47%."
    assert grade_answer(a, SOURCES) == grade_answer(a, SOURCES)


def test_degenerate_inputs_dont_crash():
    assert grade_answer("", SOURCES)["verdict"] == "empty answer"
    assert "no sources" in grade_answer("Anything.", [])["verdict"]
    assert grade_answer("Yes.", SOURCES)["n_claims"] == 0   # no checkable content


def test_exposed_over_mcp():
    tools = {t["name"] for t in handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
    assert {"calc", "search", "grade_answer"} <= tools
    r = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "grade_answer",
        "arguments": {"answer": "It improves accuracy by 47%.", "sources": SOURCES}}})
    # a finding is not a tool failure
    assert r["result"]["isError"] is False
    assert "47" in r["result"]["content"][0]["text"]


def test_sources_may_be_a_bare_string():
    r = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "grade_answer",
        "arguments": {"answer": "BM25 is a ranking function.", "sources": SOURCES[0]}}})
    assert r["result"]["isError"] is False
