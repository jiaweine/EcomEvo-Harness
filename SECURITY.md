# Security Policy

## Supported code

Until versioned releases are published, security fixes are maintained on the latest `main` branch only.

## Reporting a vulnerability

Please do not disclose suspected vulnerabilities in a public issue, pull request, discussion, screenshot, or log attachment.

1. Open the repository **Security** tab and use **Report a vulnerability** when that option is available.
2. If private vulnerability reporting is unavailable, contact the maintainer privately through the contact method published on the maintainer's GitHub profile.
3. Include the affected commit, impact, prerequisites, a minimal reproduction, and any known mitigation. Remove customer data, access tokens, provider keys, MCP credentials, and internal endpoints.

You should receive an acknowledgement within five business days. The maintainer will validate the report, agree on a disclosure timeline, prepare a regression test and fix, and credit the reporter unless anonymity is requested.

## Security boundaries

- Never submit real commerce credentials or customer evidence to repository tests.
- High-impact business actions must retain approval, idempotency, audit, and uncertain-result handling.
- Runtime learning may change cognitive routing only; it must not change RBAC, Sandbox, Verifier, or action permissions.
- Production deployments still require a trusted identity gateway, secret management, network isolation, backups, and downstream authorization.
