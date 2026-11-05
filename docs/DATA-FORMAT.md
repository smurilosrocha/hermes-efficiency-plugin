# Ledger format

Three append-only JSONL files, one line per event, under `<hermes root>/eficiencia/`:

| file | written by | one line per |
|---|---|---|
| `calls/<YYYY-MM-DD>.jsonl` | `pre_api_request` + `post_api_request` / `api_request_error` | model call |
| `tools/<YYYY-MM-DD>.jsonl` | `post_tool_call` | tool call |
| `aux/<YYYY-MM-DD>.jsonl` | `post_auxiliary_call` | auxiliary LLM call |

A call record is written once, when it finishes: the `pre` hook keeps the request half in memory (at most 256
entries) and merges it with the response half. If the process dies in between, that call leaves no line. If the
in-memory queue overflows, the evicted request is written with `"incomplete": 1` instead of being dropped.

All files are created `0600` in a `0700` directory and are append-only from the plugin's side. Unknown fields
must be ignored by readers: new counters are added without a schema bump.

## Call record

| field | type | notes |
|---|---|---|
| `v`, `v2` | int | schema markers, currently `1` |
| `ts` | float | end of the call, Unix epoch seconds |
| `t0` | float | start of the call |
| `dur` | float | duration in seconds |
| `ttfb` | float \| null | time to first byte, when the provider streams |
| `profile`, `platform` | string | Hermes profile and platform that made the call |
| `session`, `task` | string | session id and Kanban task id, when present |
| `model` | string | model requested |
| `resp_model` | string | model that answered |
| `provider`, `api_mode` | string | provider id and wire mode |
| `call`, `turn`, `retry`, `bg` | int | call kind, turn number, retry flag, background flag |
| `n_msgs` | int | number of messages in the request |
| `bytes`, `req_chars` | int | serialised request size |
| `approx_tok` | int | rough token estimate for the request |
| `h_full`, `h_sys`, `h_last` | string | HMAC-SHA256 (hex) of the request, the system prompt, the last message |
| `hash_scope` | string | what the hashes cover |
| `sys_b`, `skills_b`, `memctx_b` | int | bytes of system prompt, skills index, injected memory context |
| `m_sys`, `m_user`, `m_asst`, `m_tool` | int | bytes of messages by role |
| `rules_n`, `tools_n` | int | house-rule copies and tool count |
| `usage` | object | provider counters: `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens`, `prompt_tokens`, `total_tokens`, `request_count` (whichever the provider reports) |
| `finish` | string | finish reason |
| `out_chars`, `tool_calls` | int | assistant text size and tool-call count |
| `err` | 0 \| 1 | failure flag |
| `err_type` | string | exception class, e.g. `RateLimitError` |
| `status` | int | HTTP status, when known |
| `incomplete` | 1 | present only when the response half never arrived |

## Tool record

`ts`, `profile`, `session`, `task`, `tool` (up to 80 chars), `ms`, `chars` (result size), `status`, and for
`skill_view` / `skill_manage` / `skills_list` also `skill` (the skill name). Tool arguments are read only to
extract that skill name; they are not stored.

## Auxiliary record

`ts`, `profile`, `session`, `task`, `aux_task` (up to 40 chars), `model`, `provider`, `dur`, `usage`, `err`.

## Fingerprints

`h_full`, `h_sys` and `h_last` are HMAC-SHA256 over canonical JSON with a random key of 32 bytes stored at
`eficiencia/hmac.key` (`0600`). They let a reader recognise repeated requests (cache analysis) without holding
the text. They are pseudonymous, not anonymous: with the key, a guessed request can be confirmed. Treat the key
as part of the data.
