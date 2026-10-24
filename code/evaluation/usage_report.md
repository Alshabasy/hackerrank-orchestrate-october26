# Usage report

## Full-dataset run summary

The final full-dataset run was executed with the deterministic local solver used to generate the root-level `output.csv` for the repository. No external API model calls were made, so the usage profile reflects a local, zero-cost execution.

## Model provider and model details

| Provider | Model name | Calls | Input tokens | Output tokens | Total tokens |
| --- | --- | ---: | ---: | ---: | ---: |
| Local deterministic solver | `N/A` | 0 | 0 | 0 | 0 |
| Overall | — | 0 | 0 | 0 | 0 |

## Cost summary

- Estimated total cost: $0.00
- Estimated cost per request: $0.00
- Average tokens per request: 0
- Total tokens: 0

## Notes

- This repository’s submission path was executed without any external LLM provider and therefore produced no model-call or token usage.
- The generated file was validated by the project’s deterministic output checker and includes 518 rows, one for each request in the dataset.
- The run is deterministic and repeatable because it relies on fixed parsing, timing, and serialization rules rather than stochastic model inference.
