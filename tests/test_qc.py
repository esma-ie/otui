"""Parsing TIMES's QA_CHECK.LOG and merging it with otui's graph checks."""

from otui.graph import Graph, Node
from otui.qc import parse_qa_log, report

LOG = """              ******          TIMES -- VERSION 4.9.7           ******
              **************   QUALITY ASSURANCE LOG   **************

 *** COM_FR does not sum to unity (T=first year)
 *01 WARNING       - Normalized to 1,   R=REG1         C=DRAP         (T=2005)
 *01 WARNING       - Normalized to 1,   R=REG2         C=DRAP         (T=2005)
 *** Process with no output
 *02 SEVERE ERROR  - R=REG1 P=BAD
"""


def test_parse():
    issues = parse_qa_log(LOG)
    assert len(issues) == 3
    assert issues[0].check == "COM_FR does not sum to unity (T=first year)"
    assert issues[0].severity == "WARNING"
    assert issues[0].message.startswith("Normalized to 1,")
    assert issues[2].severity == "SEVERE ERROR" and issues[2].check == "Process with no output"


def test_report_orders_by_severity():
    g = Graph(
        nodes={"c:X": Node(id="c:X", name="X", kind="commodity", flags={"no_supply"})}, links={}
    )
    r = report(g, LOG)
    assert r["times_log"] is True
    assert r["counts"] == {"SEVERE ERROR": 1, "ERROR": 1, "WARNING": 2}
    assert [i["severity"] for i in r["issues"]] == ["SEVERE ERROR", "ERROR", "WARNING", "WARNING"]
    assert r["issues"][1]["source"] == "otui" and r["issues"][1]["item"] == "c:X"


def test_report_without_run():
    r = report(None, None)
    assert r == {"times_log": False, "counts": {}, "issues": []}
