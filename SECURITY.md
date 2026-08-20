# Security Policy

## Data sensitivity

The Library of Context may store conversations, source code, documents, tool outputs,
embeddings, and derived context. Treat its data directory as sensitive.

## Supported versions

The `main` branch receives security fixes. The project provides no stable release line
or long-term support promise.

## Report a vulnerability

Please use GitHub's private vulnerability-reporting feature for this repository when it
is available. Do not open a public issue containing exploit details, credentials, or
private data. If private reporting is unavailable, open a minimal public issue asking
the maintainers for a secure contact channel without describing the vulnerability.

Include affected versions, impact, reproduction conditions, and suggested mitigation
when possible. Do not access data or systems you do not own or have permission to test.

## Security boundary

- SQLite is authoritative and contains plaintext unless protected by the operating
  system or encrypted storage.
- Process RAM and Redis may contain copies of sensitive context.
- The default Redis and HTTP configurations are intended only for loopback/local use.
- The HTTP service has no authentication or transport encryption.
- The included Redis client does not support `rediss://`.
- The default hashing embedder is local; Ollama is expected to be local unless the user
  explicitly changes its endpoint.
- No team ACL, authenticated sync, or multi-tenant isolation is implemented.

Never expose the HTTP service or Redis instance directly to an untrusted or shared
network.

## Threats contributors should consider

- prompt/context injection stored as durable memory;
- retrieval of context across project, user, or thread boundaries;
- malicious metadata or oversized payloads causing resource exhaustion;
- secrets copied into logs, caches, issue reports, or team promotion;
- stale authorization cached after permission revocation;
- tampered outbox events or replay from an untrusted device;
- symlink/path traversal around SQLite or document ingestion;
- denial of service through expensive novel retrieval queries;
- unencrypted local backups, WAL files, and crash dumps;
- supply-chain risk from embedding, vector-index, or broker dependencies.

## Deployment guidance

- Keep the database and services on trusted local storage and loopback interfaces.
- Use a dedicated Redis instance; do not reconfigure an unrelated shared instance.
- Apply operating-system access controls and encrypted disk storage.
- Do not store credentials or raw secrets as Library records.
- Set disk, RAM, request-size, result-size, and queue limits.
- Back up and test restoration of SQLite before relying on it.
- Add authenticated identity, authorization, TLS, audit, retention, and deletion policy
  before any multi-host deployment.
- Enforce authorization during candidate retrieval, not only after text is returned.
