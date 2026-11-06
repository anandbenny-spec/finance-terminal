# prompts

One folder per AI task: `<task>/v<n>.md` (the prompt) and `<task>/schema.json` (structured-output tasks).
The version is recorded on every output (`core.ai_output.prompt_version`). Written with the module 1 spec.

A version may carry its own `<version>.schema.json`, which `load_schema` prefers to `schema.json`. `call_summary` v6 (2B-11c, #301)
is the first prompt of a task that changed route: v1 to v5 take the Citations API, v6 and later the structured JSON route
(`ft/ai/call_summary.py`, `route_of`), and the earlier versions stay here because outputs under them stay published.
