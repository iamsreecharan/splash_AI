"""Run the free sample workflow against a running local Splash instance."""

import argparse
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
import urllib.parse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default=".splash/smoke-results.json")
    parser.add_argument("--verify-deletion", action="store_true",
                        help="Delete the sample repository after checking its data lifecycle")
    args = parser.parse_args()

    def request(path, body=None, method=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            args.url.rstrip("/") + path, data=data,
            headers={"Content-Type": "application/json"},
            method=method or ("POST" if data is not None else "GET"),
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, json.load(response)

    def check(condition, description):
        if not condition:
            raise AssertionError(description)
        print("PASS", description, flush=True)
        results.append(description)

    def await_run(job_id):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            _, run = request(f"/api/runs/{job_id}")
            if run["status"] == "failed":
                raise RuntimeError(f"Analysis failed: {run.get('error')}")
            if run["status"] == "completed":
                return run
            time.sleep(0.5)
        raise TimeoutError(f"Run {job_id} did not finish")

    results = []
    _, config = request("/api/public/config")
    check(config.get("demo_mode") is True, "Explicit local demo mode is enabled")
    _, health = request("/health")
    check(bool(health), "API readiness endpoint responds successfully")
    _, seeded = request("/api/demo/seed", {})
    repository_id = seeded["repository_id"]
    _, repositories = request("/api/repositories")
    repository = next(item for item in repositories["items"] if item["id"] == repository_id)
    check(repository["status"] == "ready" and repository["indexed_chunks"] > 0,
          "Sample repository is indexed in persistent storage")

    status, queued = request("/api/demo/analyze", {"scenario": "rename"})
    check(status == 202, "Breaking-change analysis is accepted as a background job")
    run = await_run(queued["job_id"])
    check(len(run["stages"]) == 4 and run["alert_count"] > 0,
          "All four agent stages finish and generate an impact alert")
    _, alerts = request(f"/api/alerts?repository_id={repository_id}")
    alert = next(item for item in alerts["items"] if item["pr_number"] == run["pr_number"])
    check(alert["evidence_status"] == "verified" and alert["severity"] == "high",
          "API field rename has a verified high-severity finding")
    check(any("MachineDashboard" in item["path"] and "machine.temperature" in item["snippet"]
              for item in alert["evidence"]), "Finding cites the actual frontend consumer")
    check(all(item["commit_sha"] and item["start_line"] >= 1 for item in alert["evidence"]),
          "Source evidence includes commit references and valid line numbers")
    citations_match = True
    for evidence in alert["evidence"]:
        query = urllib.parse.urlencode({"sha": evidence["commit_sha"], "path": evidence["path"]})
        _, source = request(f"/api/repositories/{repository_id}/source?{query}")
        excerpt = "\n".join(source["content"].splitlines()[evidence["start_line"] - 1:evidence["end_line"]])
        citations_match &= excerpt == evidence["snippet"] and source["commit_sha"] == evidence["commit_sha"]
    check(citations_match, "Every cited excerpt matches the stored source snapshot exactly")
    _, feedback = request(f"/api/alerts/{alert['id']}/feedback", {"kind": "useful"})
    check(feedback["feedback"] == "useful", "Useful feedback persists")
    _, feedback = request(f"/api/alerts/{alert['id']}/feedback", {"kind": "resolved"})
    check(feedback["status"] == "resolved", "Resolution feedback changes alert state")

    _, queued = request("/api/demo/analyze", {"scenario": "safe"})
    safe_run = await_run(queued["job_id"])
    check(safe_run["alert_count"] == 0 and len(safe_run["stages"]) == 4,
          "Harmless field addition finishes with zero alerts")
    try:
        request("/api/webhooks/github", {"forged": True})
    except urllib.error.HTTPError as exc:
        check(exc.code in {401, 403, 503}, "Unsigned GitHub webhook is rejected")
    else:
        raise AssertionError("Unsigned webhook was accepted")

    if args.verify_deletion:
        request(f"/api/repositories/{repository_id}", method="DELETE")
        for path in (f"/api/repositories/{repository_id}/status", f"/api/alerts/{alert['id']}",
                     f"/api/runs/{run['id']}", f"/api/repositories/{repository_id}/source?{query}"):
            try:
                request(path)
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
            else:
                raise AssertionError(f"Deleted data remains accessible: {path}")
        _, remaining = request("/api/repositories")
        check(not any(item["id"] == repository_id for item in remaining["items"]),
              "Removing the sample erases its source, alerts, and analysis history")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"passed": len(results), "checks": results,
                                  "breaking_run": run, "safe_run": safe_run,
                                  "deletion_verified": args.verify_deletion}, indent=2))
    print(f"{len(results)} checks passed; saved {output}", flush=True)


if __name__ == "__main__":
    main()
