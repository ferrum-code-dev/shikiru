# Security policy

## Reporting a vulnerability

Report suspected vulnerabilities privately through GitHub's private vulnerability reporting: in the
repository, open the **Security** tab and choose **Report a vulnerability**. Do not open a public
issue or pull request for it.

## Scope

In scope:

- Agents run with your privileges. Reports should show a boundary described in
  [Trust and implementation boundaries](docs/design.md#trust-and-implementation-boundaries) being
  crossed, not only that an agent can act as you.
- The Operator MCP surface served by `shikiru mcp`.
- State files under `.shikiru/` and the notification commands that Shikiru runs.

## Supported versions

Only the latest release receives security fixes.
