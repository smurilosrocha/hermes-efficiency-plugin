# Threat model

## Assets

1. The content of prompts, messages and tool output, which must never land on disk.
2. The ledger, which reveals how much a machine uses each model, and when.
3. The HMAC key, which turns fingerprints from opaque digests into a confirmation oracle.

## Adversaries considered

| adversary | capability | goal |
|---|---|---|
| another local user | read/write in shared directories, cannot read files owned by the Hermes user | read the ledger, or redirect a write |
| a process running as the Hermes user | full read/write of the ledger | read everything, tamper, or forge lines |
| a remote party | network | receive data — there is no network path |
| a malicious dependency | code execution inside the plugin | exfiltrate prompt text |

## Mitigations in place

- **No prompt storage.** Only digests and byte counts are persisted. This is enforced by construction in the
  hooks, and covered by a test that feeds a request containing a marker string and asserts the string appears in
  no file the plugin writes.
- **No dependencies.** Standard library only, so there is no supply chain beyond Hermes itself.
- **No network.** Neither the plugin nor `tools/report.py` opens a socket.
- **File mode.** Directory `0700`, files `0600`, re-applied with `fchmod` on every write, so a file created by an
  older version with `0644` becomes private on the next call.
- **Symlink refusal.** `os.open(..., O_NOFOLLOW)`: a link planted at `calls/<day>.jsonl` cannot redirect the
  append to a file the attacker chose. Covered by a test.
- **Failure isolation.** Every hook body is wrapped; an observer bug cannot fail or alter a model call.
- **Bounded memory.** At most 256 request halves are held; on overflow the request is written with
  `"incomplete": 1` rather than lost silently.

## Residual risks

- **Same-user adversary.** Anyone running as the Hermes user can read, edit or delete the ledger. No local
  mechanism changes that; use separate Unix accounts.
- **Identifiers are clear.** Profile, session and task ids are written as-is. They are not personal data by
  themselves, but they correlate a ledger with other local records.
- **Fingerprints are pseudonymous.** With `hmac.key`, a party who can guess a request can confirm it. The key is
  `0600` for that reason; it is not a substitute for not storing the request.
- **In-process observation.** Hooks run inside the Hermes process, with the same privileges. A compromised plugin
  in the same process could read what the hooks read. The mitigation is to install few plugins and to pin them by
  commit.
- **Growth.** The ledger is append-only and never rotated; disk use grows with usage (roughly 0.4–1.2 kB per
  call). Rotation is left to the operator.
- **Time and model names.** Timestamps and model ids are exact. That is the point of a usage ledger, but it means
  the file is not safe to publish unfiltered.

## Reporting

See [SECURITY.md](../SECURITY.md).
