# Contributing

Small, tested changes. The plugin is an observer inside every model call, so a bug here is felt everywhere.

## Setup

```bash
git clone https://github.com/smurilosrocha/hermes-efficiency-plugin
cd hermes-efficiency-plugin
python3 -m unittest discover -s tests -v
```

No dependencies: the plugin and the report use the standard library. If a change needs a third-party package,
open an issue first — the no-dependency rule is a security property, not a preference.

## Rules

- **Never store text.** New fields may hold numbers, counts, names and digests. A field that carries prompt,
  message, tool or response text will be refused.
- **Never break the call.** Every hook body stays wrapped in `try/except`; the observer must not raise into the
  caller.
- **Guard new logic with a test.** A guard (permission, symlink, overflow, error classification) needs a test that
  fails when the guard is removed. Say in the PR which test fails when you delete the guard.
- **Keep the schema additive.** Readers ignore unknown fields; do not rename or repurpose an existing one without a
  `v`/`v2` bump and a note in `CHANGELOG.md`.
- **Two-space indentation, `ruff`-clean, no formatter churn.** Run `python3 -m compileall -q .` before pushing.

## Commits and PRs

Imperative subject under ~72 characters, body explaining *why*. One concern per PR. Include the test output in the
description; CI runs the same suite on Python 3.10 and 3.14.

Security problems do not go through issues — see [SECURITY.md](SECURITY.md).
