# Security

The Library may contain private conversations, source code, documents, and tool output.
Treat its files, Redis data, process memory, logs, and backups as sensitive. The
[glossary](GLOSSARY.md) defines shared terms.

!!! warning
    The local Hypertext Transfer Protocol (HTTP) service requires a bearer credential.
    It does not provide Transport Layer Security (TLS) or a user identity model.
    The Redis client does not support `rediss://`. Do not expose either service to an
    untrusted or shared network.

The default file boundary trusts one operating-system user. Token and SQLite paths
reject symbolic links, non-regular files, and multiple hard links.

On Portable Operating System Interface systems, the effective user owns runtime files.
These files grant no access to groups or other users. Runtime files use mode `0600`.

On Windows, files inherit the parent directory access rules. Python mode bits cannot
define or verify these rules. Store the data and token files under the intended Windows
account only.

The checks apply to observed files. They do not protect the parent directory or copied
files. Use an owner-controlled data directory. Protect backups, exports, and crash
dumps in the same way.

## Retrieved context

Prompt construction labels retrieved books as untrusted reference data. The formatter
applies Extensible Markup Language escaping to text, identifiers, and sources. Stored
text cannot forge the `library-book` or `library-context` elements.

Structural escaping does not prevent semantic prompt injection. Ordinary book content
can still influence a model.

The host must keep retrieved context below trusted system and developer policy. It must
authorize records before retrieval. It must enforce tool permissions outside the
model. It must verify consequential claims or actions with an independent source.

Use encrypted local storage and operating-system access controls. Use a dedicated
authenticated Redis instance. Set resource limits. Use sanitized test data.

Do not store credentials as Library records. Do not publish real prompts in issues.

Report vulnerabilities privately through GitHub security advisories. Use this feature
when it is available. See the full
[SECURITY.md](https://github.com/hwillGIT/library-of-context/blob/main/SECURITY.md).
