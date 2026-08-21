# Security Policy

## Data sensitivity

The Library of Context may store conversations, source code, documents, tool outputs,
embeddings, and derived context. Treat the data directory as sensitive. The
[glossary](docs/GLOSSARY.md) defines shared terms.

## Supported versions

The `main` branch receives security fixes. The project does not provide a stable
release line or a long-term support promise.

## Report a vulnerability

Use GitHub private vulnerability reporting when that feature is available. Do not put
exploit details, credentials, or private data in a public issue.

If private reporting is unavailable, request a secure contact channel in a minimal
public issue. Do not describe the vulnerability in that issue.

Include affected versions, impact, reproduction conditions, and a possible mitigation.
Do not access a system without ownership or permission.

## Security boundary

- SQLite is authoritative. It contains plaintext unless the operating system or
  encrypted storage protects it.
- Process random-access memory (RAM) and Redis may contain sensitive context.
- Default Redis and Hypertext Transfer Protocol (HTTP) configurations support local
  loopback use only.
- The HTTP service requires a bearer credential.
- The HTTP service rejects requests from browser origins and untrusted host names.
- The HTTP service does not provide transport encryption or a user identity model.
- The credential authenticates a process as the local daemon user.
- The credential does not authorize records, teams, or sessions.
- The included Redis client does not support `rediss://`.
- The default hashing embedder operates locally.
- Ollama operates locally unless the user changes its endpoint.
- The system does not provide team access control, authenticated synchronization, or
  isolation between tenants.

Never expose the HTTP service or Redis instance directly to an untrusted or shared
network.

## Local filesystem boundary

The default storage model trusts one operating-system user. It also trusts a data
directory that this user controls.

The daemon token, SQLite database, SQLite sidecar files, and owner lock must be regular
files. Each file must have one hard link. The runtime rejects symbolic links.

On Portable Operating System Interface (POSIX) systems, the effective user must own the
daemon token. The token must not grant access to a group or other users. Token creation
uses mode `0600`.

The runtime checks ownership of the database, owner lock, and SQLite sidecar files.
Sidecars include the write-ahead log, shared-memory file, and rollback journal. The
runtime sets these files to mode `0600` when it opens them.

On Windows, the runtime rejects symbolic links, non-regular files, and multiple hard
links. Python mode bits do not define or verify a New Technology File System access
control list.

Files inherit the access rules of their parent directory. Store the data directory and
token file in a directory for the intended Windows account only.

These checks apply to files that the runtime observes at startup. They do not create a
sandbox around the parent directory. SQLite can recreate sidecar files during
operation.

Keep the data directory on trusted local storage. Give access only to the owner or the
intended Windows account. Apply the same controls to backups, exports, crash dumps, and
copied token files.

## Threats contributors should consider

- prompt injection stored as durable context.
- retrieval across project, user, or thread boundaries.
- resource exhaustion from malicious metadata or large payloads.
- secrets in logs, caches, issue reports, or team promotion.
- stale authorization after permission revocation.
- changed outbox events or replay from an untrusted device.
- symbolic-link, hard-link, or path traversal around credentials, SQLite, sidecars,
  or document ingestion.
- denial of service through expensive retrieval queries.
- unencrypted local backups, write-ahead logs, and crash dumps.
- supply-chain risk from embedding, vector-index, or broker dependencies.

## Retrieved context boundary

The prompt formatter labels retrieved books as untrusted reference data. It applies
Extensible Markup Language (XML) escaping to book text and identifiers. It also escapes
source and wrapper identifiers.

The formatter puts this data in `library-book` and `library-context` elements. Stored
text cannot create or close these structural elements in a formatted prompt.

This boundary does not determine truth, relevance, or safe action. It does not prevent
semantic prompt injection. Ordinary prose can still influence a model.

The host must keep Library data below trusted system and developer policy. The host
must authorize records before retrieval. It must enforce tool permissions outside the
model. It must verify consequential claims or actions with an independent source.

## Deployment guidance

- Keep the database, token file, and services on trusted local storage.
- Bind network services to loopback interfaces.
- Use a dedicated authenticated Redis instance.
- Do not change an unrelated shared Redis instance.
- The Windows Subsystem for Linux installer creates a separate service and credential.
- Apply operating-system access controls and encrypted disk storage.
- Do not store credentials or raw secrets as Library records.
- Set disk, RAM, request-size, result-size, and queue limits.
- Back up and test restoration of SQLite before relying on it.
- Add authenticated identity and authorization before a multi-host deployment.
- Add Transport Layer Security, audit, retention, and deletion policies before a
  multi-host deployment.
- Enforce authorization during candidate retrieval, not only after text is returned.
