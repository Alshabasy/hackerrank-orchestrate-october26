# Usage report

## Final full-dataset run

The submitted prediction pipeline is a local deterministic Python program. Its prediction path makes no model-provider/API calls. The full output contains 518 requests; model-call totals below therefore refer to inference for that run, not AI tools used while developing the repository.

The final run was performed from the repository root on 2026-10-25 with `python3 code/main.py --draft-output /tmp/ghost-output-improved.csv`. The generated candidate passed both `python3 code/main.py --validate-output /tmp/ghost-output-improved.csv` and `python3 code/validate_output.py /tmp/ghost-output-improved.csv`, then was copied to root `output.csv`. The root output was validated again and contains 518 request rows. The accompanying test suite ran 34 tests successfully.

| Provider / execution | Model | Calls | Input tokens | Output tokens | Total tokens | Estimated cost |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Local Python solver | No model used | 0 | 0 | 0 | 0 | $0.00 |
| **Overall inference totals** | — | **0** | **0** | **0** | **0** | **$0.00** |

- Requests processed: 518
- Average model tokens per request: 0
- Estimated inference cost per request: $0.00
- Provider and model for inference: none; the solver does not invoke an LLM.
- Average total inference tokens per request: 0 / 518 = 0.
- Estimated inference cost per request: $0.00 / 518 = $0.00.

These figures describe model inference only. AI coding assistant usage during development is captured separately in the agent trace/chat transcript; this repository does not contain reliable per-session token or cost telemetry for those coding sessions, so no development-time token totals are asserted.

## Reproduction

From the repository root, run `python3 code/main.py` to regenerate `output.csv`. Run `python3 code/evaluation/main.py` to validate the output and reproduce the public sample metrics.
