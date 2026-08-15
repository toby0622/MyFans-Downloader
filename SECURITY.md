# Security Policy

## Supported versions

Security fixes are provided for the latest `1.x` release.

## Reporting a vulnerability

Please use the repository's private **Report a vulnerability** form when it is
available. If private reporting is unavailable, open a public issue requesting
a private contact channel, but do not include exploit details, credentials,
signed media URLs, personal data, or private files in that issue.

## Protecting local data

- Never commit or upload `config.ini`, `.runtime/`, `config/`, `downloads/`, or
  application log files.
- Do not attach an application log to an issue without reviewing and redacting
  it first.
- If an authorization token is ever exposed, revoke or rotate it immediately.
- Release executables should be downloaded only from this repository's GitHub
  Releases page and verified against the accompanying SHA-256 checksum.
