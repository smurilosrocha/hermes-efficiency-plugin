# Changelog

## [0.4.0] - 2026-11-05

First public release. MIT licensed.

### Added

- `tools/report.py`: reads the ledger and reports calls, tokens (input, cache read, cache write, output),
  latency p50/p95 and errors by type, grouped by day, model and provider; `--json` for machine consumption.
- `post_auxiliary_call` collection: `aux/<day>.jsonl` with auxiliary task, model, provider, tokens, duration.
- Unit tests for the five hooks and for the report (17 tests, standard library only).
- CI on Python 3.10 and 3.14, plus a secret scan of the full history.
- `docs/DATA-FORMAT.md`, `docs/PRIVACY.md`, `docs/THREAT-MODEL.md`, `SECURITY.md`.

### Fixed

- Error classification: the hook passes `error={"type", "message"}` and no `error_type`, so every failure was
  recorded as `err_type="dict"`. The observer now reads `error["type"]` (for example `RateLimitError`).
- Ledger writes refuse to follow a symbolic link (`O_NOFOLLOW`) and re-apply `0600` with `fchmod`, so an existing
  `0644` file no longer stays world-readable.
- Queue overflow: an evicted request is written with `"incomplete": 1` instead of being dropped silently.

### Removed

- The internal reference dashboard and the automation that rewrote local configuration. The public repository
  ships the observer and a reference CLI only; the ledger format is documented so any consumer can be written.

### Notes

- `requires_hermes: >=0.21.7`, the first stable tag that fires `post_auxiliary_call`. Tested on `0.22.5`.
