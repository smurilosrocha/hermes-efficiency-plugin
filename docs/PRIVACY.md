# Privacy

The plugin is built so that a usage ledger can exist without a transcript existing.

## What stays out of the ledger

Prompt text, system-prompt text, message text, tool arguments, tool output, model output, card text, file
content, API keys and credentials. The observer touches prompt material only long enough to compute an
HMAC-SHA256 digest and a byte count, then drops the reference. Nothing is sent anywhere: there is no network
code in the plugin or in `tools/report.py`.

## What is written in clear

Numbers (tokens, bytes, durations, counts), model and provider names, and local identifiers: the Hermes profile
name, the session id, the Kanban task id and the platform. These are pseudonymous only in the sense that they
are local names; they are **not** anonymised. A ledger that says "profile X spent 4.2M output tokens" is meant
to be readable.

## Where it lives

`<hermes root>/eficiencia/` — shared by every profile on the machine, so a single ledger covers all agents:

```
eficiencia/
  calls/<day>.jsonl   model calls, 0600
  tools/<day>.jsonl   tool calls, 0600
  aux/<day>.jsonl     auxiliary calls, 0600
  hmac.key            0600, generated on first use
```

The directory is created `0700`; every write re-applies `0600` and refuses to follow a symbolic link.

## Who can read it

Anyone who can read files as the user running Hermes. The plugin does not encrypt the ledger: encryption with a
key next to the data adds no protection against that threat and would make the files unreadable after a restore.
If the host is shared, rely on separate Unix accounts, not on the plugin.

## Retention

There is no automatic rotation or deletion. Deleting the `eficiencia/` directory removes everything; deleting a
day file removes that day. If you copy the ledger elsewhere, copy `hmac.key` with it or the fingerprints become
useless for comparison.

## Disabling

- One process: `EFICIENCIA_OFF=1`.
- Permanently: `hermes plugins disable eficiencia`.

Both stop collection; neither deletes what was already written.
