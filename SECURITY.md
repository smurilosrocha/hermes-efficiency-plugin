# Security policy

## Reporting a vulnerability

Use GitHub's **Report a vulnerability** button in the *Security* tab of this repository. That opens a private
advisory visible only to the maintainer. Do not open a public issue for a security problem.

Please include: affected version or commit, what an attacker gains, and the smallest reproduction you can share.
Reports are handled on a best-effort basis; there is no paid bounty and no guaranteed response time.

## Scope

In scope:

- the plugin (`__init__.py`) and the report CLI (`tools/report.py`);
- handling of prompt material while computing fingerprints;
- file creation, permissions and path handling under `<hermes-root>/eficiencia/`;
- the HMAC key generation and storage.

Out of scope: vulnerabilities in Hermes Agent itself (report those upstream), and anything that already requires
the ability to read the ledger as the same local user who owns it.

## Supported versions

The latest release on the default branch. Older tags are kept for pinning but do not receive fixes.

## Design commitments

- The plugin never writes prompt, message, tool-argument or tool-output text to disk.
- The ledger is created `0600` inside a `0700` directory; writes refuse to follow a symbolic link.
- The observer is wrapped so that its own failure cannot fail the model call.
- No network access, no telemetry upload, no third-party dependency.
