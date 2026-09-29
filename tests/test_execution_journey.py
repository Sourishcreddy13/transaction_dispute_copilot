from src.execution.journey import ExecutionJourney


def test_execution_journey_marks_unvisited_nodes_skipped():
    journey = ExecutionJourney("RUN-TEST")
    journey.start("case_authorize", "authorization context")
    journey.complete("case_authorize", "Case authorization accepted")
    journey.start("classification_agent", "masked customer statement")
    journey.complete("classification_agent", "Intent: unauthorized; confidence: 0.99")

    result = journey.to_dict()

    statuses = {node["id"]: node["status"] for node in result["nodes"]}
    assert statuses["case_authorize"] == "executed"
    assert statuses["classification_agent"] == "executed"
    assert statuses["fraud_scoring_agent"] == "skipped"

    assert result["executed_path"] == [
        "case_authorize",
        "classification_agent",
    ]
    assert result["selected_edges"] == [
        {"source": "case_authorize", "target": "classification_agent"}
    ]


def test_execution_journey_records_selected_branch():
    journey = ExecutionJourney("RUN-TEST")
    journey.start("decision_engine", "decision inputs")
    journey.complete("decision_engine", "Recommendation: provisional_credit; human review: no")
    journey.start("finalize", "release controls")
    journey.complete("finalize", "Case state: RESOLVED; release gate passed")

    result = journey.to_dict()
    assert {"source": "decision_engine", "target": "finalize"} in result["selected_edges"]
