# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.x (main) | Yes |

## Reporting a vulnerability

Do not open a public issue. Use GitHub's private vulnerability reporting for this repository:
**Security** tab -> **Report a vulnerability**.

Include:

- Affected version or commit.
- A minimal reproduction with synthetic data.
- Impact: secret exposure, gate bypass, code execution, or data loss.
- Any known mitigation.

You will receive an acknowledgment within 7 days and a status update within 30 days. Please
allow time for a fix before public disclosure.

## Scope

In scope:

- Redaction bypasses that persist secrets in the store or reports.
- Report injection that can execute code or corrupt CI output.
- Gate bypasses that turn a failure into exit 0.
- Path traversal, SQL injection, or unsafe deserialization.
- Live-call budget or consent bypasses.

Out of scope:

- Vulnerabilities in third-party providers.
- A compromised host or terminal.
- Prompt injection that only changes model output without bypassing a gate.
- Missing redaction for an unconfigured, project-specific secret format.

## Safe harbor

Good-faith research on your own data and repositories is welcome. Do not access other users'
data, degrade service, or run live paid calls at scale.
