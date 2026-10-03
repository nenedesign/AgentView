# AgentView Security Model

AgentView is a local-only tool. Its security posture reflects that: defense-in-depth
for a single-user environment where the primary threats are other processes on the
same machine, browser extensions, and accidental public exposure.

## Threat model

| Threat | How AgentView addresses it |
|---|---|
| Another process on the same machine reading dashboard data | Session token required on all API requests; not guessable without access to stderr at startup |
| Browser extension reading activity data | Same-origin policy + no CORS headers; extensions cannot cross-origin fetch 127.0.0.1 without the token |
| DNS rebinding (external site redirecting to 127.0.0.1) | 127.0.0.1 bind only; session token still required; Host header validation is a future hardening step |
| Clickjacking / iframe embedding | CSP: `frame-ancestors 'none'` |
| CSRF against the dashboard | Dashboard is read-only; no state-changing endpoints; no cookies |
| Accidental public exposure | Default bind is 127.0.0.1; public bind requires an explicit CLI flag with a red warning |
| Sensitive trace data cached by browser | `Cache-Control: no-store` on all responses |
| Trace data leaked via Referer | `Referrer-Policy: no-referrer` on all responses |
| Content-type sniffing attacks | `X-Content-Type-Options: nosniff` |
| Script injection via CSP bypass | `script-src 'self'`; no inline scripts |
| Exfiltration via connect-src | `connect-src 'self'`; no external fetch targets |

## Session token

AgentView generates a cryptographically random 32-byte URL-safe token when the
dashboard starts. The CLI prints the startup URL (token included) to stderr only.
The token is never written to a log file, never appears in a predictable path, and
is never sent to any external service.

Clients supply the token either as:
- A query parameter: `?token=<token>`
- A request header: `X-AgentView-Token: <token>`

Token comparison uses `secrets.compare_digest` to prevent timing attacks.

## Content Security Policy

```
default-src 'none';
script-src 'self';
style-src 'self' 'unsafe-inline';
img-src 'self' data:;
connect-src 'self';
font-src 'self';
base-uri 'none';
form-action 'none';
frame-ancestors 'none'
```

The `'unsafe-inline'` allowance on `style-src` is intentional: the dashboard uses
inline styles for dynamic state rendering. Script execution is restricted to
same-origin only; no inline `<script>` tags are used anywhere in the HTML shell.

## Browser extension threat note

Browser extensions with host permissions for `localhost` or `127.0.0.1` can read
HTTP responses from the dashboard, including activity data. AgentView's session
token reduces (but does not eliminate) this surface: an extension with host
permissions already has ambient access to the tab.

Mitigation options for sensitive environments:
- Disable browser extensions while using the dashboard.
- Use a browser profile with no extensions installed for the dashboard session.
- Restrict host permissions for installed extensions via your browser's extension
  manager.

## Proxy: stdout contract

The `agentview proxy` command uses stdout as a sacred channel for the wrapped
server's output. All proxy diagnostics (trace path, session complete) go to
stderr. The proxy never writes non-JSON content to stdout.

## MCP traffic

The proxy sees all JSON-RPC messages between Claude Desktop and the wrapped MCP
server. This includes tool names, tool arguments, and tool results. AgentView does
not redact this content by default. If the MCP server handles sensitive data
(tokens, passwords, PII), do not use AgentView in production without reviewing
the trace file content.

Trace files are written to the local filesystem only. AgentView does not transmit
trace data to any external service.

## Scope of v1 security review

The v1 local security posture was designed and tested against the threat model
above. It has not been reviewed by an external security auditor. Contributions
that improve the security model are welcome; see the GitHub issue tracker.
