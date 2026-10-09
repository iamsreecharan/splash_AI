"""Run with `python -m evals.run --output evals/report.json` from backend."""
import argparse
import json
from pathlib import Path
from app.analysis.engine import analyze_change
from evals.cases import CASES

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


def evaluate(cases=CASES):
    true_positive = false_positive = false_negative = 0
    faithful = evidence_count = 0
    outcomes = []
    for case in cases:
        result = analyze_change(repository_name="eval/fixture", base_files=case.base, head_files=case.head,
                                base_sha=BASE_SHA, head_sha=HEAD_SHA, title=case.name, enable_ai=False)
        alerts = result["alerts"]
        correct = sum(alert["affected_component"] == case.consumer_path and
                      set(alert["affected_fields"]) == set(case.expected_fields)
                      for alert in alerts) if case.expected_alerts else 0
        true_positive += min(case.expected_alerts, correct)
        false_positive += len(alerts) - min(case.expected_alerts, correct)
        false_negative += case.expected_alerts - min(case.expected_alerts, correct)
        accurate_evidence = True
        for alert in alerts:
            roles = {e["role"] for e in alert["evidence"]}
            if not {"producer_before", "consumer_request"}.issubset(roles):
                accurate_evidence = False
            for evidence in alert["evidence"]:
                evidence_count += 1
                snapshot = case.base if evidence["snapshot"] == "base" else case.head
                sha = BASE_SHA if evidence["snapshot"] == "base" else HEAD_SHA
                lines = snapshot.get(evidence["path"], "").splitlines()
                expected = "\n".join(lines[evidence["start_line"] - 1:evidence["end_line"]])
                valid = bool(expected) and evidence["snippet"] == expected and evidence["commit_sha"] == sha and f"/blob/{sha}/" in evidence["source_url"]
                faithful += valid
                accurate_evidence &= valid
        passed = len(alerts) == case.expected_alerts and correct == case.expected_alerts and accurate_evidence
        outcomes.append({"name": case.name, "category": case.category, "passed": passed,
                         "expected_alerts": case.expected_alerts, "actual_alerts": len(alerts)})
    return {
        "dataset": "supported-static-http-contracts-v1", "cases": len(cases),
        "passed": sum(item["passed"] for item in outcomes),
        "precision": true_positive / (true_positive + false_positive) if true_positive + false_positive else None,
        "recall": true_positive / (true_positive + false_negative) if true_positive + false_negative else None,
        "evidence_faithfulness": faithful / evidence_count if evidence_count else None,
        "evidence_items_checked": evidence_count,
        "true_positive": true_positive, "false_positive": false_positive, "false_negative": false_negative,
        "limitations": "Synthetic cases cover explicit FastAPI literal routes and named fetch/json bindings. Metrics do not establish general repository or runtime accuracy.",
        "outcomes": outcomes,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate()
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(json.dumps({key: value for key, value in report.items() if key != "outcomes"}, indent=2))
    return 0 if report["passed"] == report["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
