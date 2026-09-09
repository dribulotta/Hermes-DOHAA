"""Run public reference controls. This demo does not run or compare LLM agents."""

import argparse
import json
from pathlib import Path

from tools.document_stream import DocumentEvent, StreamStore
from tools.document_stream_scoring import ReferenceFact, score_report


def report(value, revision):
    return {"facts": [{"key": "delivery_day", "value": value,
                       "evidence": [{"source_id": "notice", "revision": revision}]}]}


def run_demo(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    first = DocumentEvent("delivery-1", "notice", 1, 1, "Delivery will be on Tuesday.")
    replacement = DocumentEvent("delivery-2", "notice", 2, 2, "The delivery moves to Friday.")
    noise = DocumentEvent("advert-1", "advert", 1, 2, "Our public brochure is available.", expires_at=4)
    late = DocumentEvent("delivery-3", "notice", 1, 3, first.text)
    withdrawn = DocumentEvent("delivery-4", "notice", 3, 4, "", retracted=True)
    temporary = DocumentEvent("delivery-5", "notice", 4, 5, "Temporary schedule: Thursday.", expires_at=6)
    path = output / "stream.sqlite3"
    with StreamStore(path, run_id="public-development-demo") as stream:
        stream.append([first, replacement, noise])
        before = stream.observation(2)
    # A fresh connection reconstructs exactly the committed observations.
    with StreamStore(path, run_id="public-development-demo") as stream:
        reopened = stream.observation(2) == before
        duplicate_insertions = stream.append([replacement])
        stream.append([late, withdrawn, temporary])
        # Manually specified controls, independent of the temporal reducer.
        answers = {1: ("Tuesday", 1), 2: ("Friday", 2), 3: ("Friday", 2),
                   4: None, 5: ("Thursday", 4), 6: None}
        checkpoints = []
        for tick, answer in answers.items():
            refs = [] if answer is None else [ReferenceFact(
                "delivery_day", answer[0], ((("notice", answer[1]),),))]
            proposal = {"facts": []} if answer is None else report(*answer)
            score = score_report(proposal, refs, stream.active_sources(tick))
            checkpoints.append({"tick": tick, **score})
        stale = score_report(report("Tuesday", 1),
                             [ReferenceFact("delivery_day", "Friday", ((("notice", 2),),))],
                             stream.active_sources(3))
        expired = score_report(report("Thursday", 4), [], stream.active_sources(6))
        result = {
            "schema_version": "hermes-document-stream-demo/1.0",
            "scope": "public_development_harness_self_check",
            "native_model_calls": 0,
            "architectural_comparison": False,
            "restart_observation_identical": reopened,
            "exact_redelivery_insertions": duplicate_insertions,
            "unique_delivery_records": len(stream.observation(6)["events"]),
            "reference_controls": checkpoints,
            "negative_controls_rejected": sum(not x["task_success"] for x in (stale, expired)),
        }
    result["passed"] = (reopened and duplicate_insertions == 0
                        and all(x["task_success"] for x in checkpoints)
                        and result["negative_controls_rejected"] == 2)
    with (output / "summary.json").open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write("\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new output directory")
    args = parser.parse_args()
    result = run_demo(args.output)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
