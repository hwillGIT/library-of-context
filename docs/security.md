# Security

The Library may contain private conversations, source code, documents, and tool output.
Treat its SQLite files, WAL files, Redis data, process memory, logs, and backups as
sensitive.

!!! warning
    The current loopback HTTP service has no authentication or TLS. The Redis client
    does not support `rediss://`. Do not expose either service directly to a shared or
    untrusted network.

Use encrypted local storage, operating-system access controls, a dedicated Redis
instance, resource quotas, and sanitized tests. Do not shelve credentials or publish
real prompts in issues.

Report vulnerabilities privately through GitHub security advisories when available.
See the full
[SECURITY.md](https://github.com/hwillGIT/library-of-context/blob/main/SECURITY.md).
