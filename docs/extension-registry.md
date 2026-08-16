# LTP Extension Type Registry

This document is the authoritative registry for the `x-ltp/` shape namespace defined in
the whitepaper (§1.1.1, [`WHITEPAPER.md`](WHITEPAPER.md)). The `x-ltp/` namespace is
reserved for LTP-defined extension types. Registration prevents independently developed
implementations from assigning conflicting meanings to the same subtype.

IANA media types (`type/subtype` per RFC 6838) do NOT require registration here — they
are governed by the IANA media-type registry.

## Registration procedure

To register a new `x-ltp/` shape, open a pull request against this file adding one row
to the table below. The PR MUST include:

1. **Shape name** — the full media-type-style identifier, `x-ltp/<name>`. The `<name>`
   MUST be lowercase, matching `[a-z0-9][a-z0-9.-]*`.
2. **Description** — a one-paragraph semantic description of the content the shape
   denotes (in the PR description; the table carries a one-line summary).
3. **Canonicalization notes** — any shape-specific canonicalization beyond the
   whitepaper §1.1.1 rules (parameter ordering, required parameters), or "none".
4. **Contact** — a maintainer or team responsible for the shape's semantics.

Registration is lightweight: review checks only for name collisions, syntax, and a
non-empty semantic description. It does not evaluate the merits of the shape.

Subtypes not yet registered SHOULD be prefixed with a reverse-domain identifier
(e.g., `x-ltp/com.example.my-type`) to avoid collisions during local experimentation.

## Registry

Entries are **append-only**: once registered, a shape name is never removed or
reassigned. A shape whose semantics are retired is marked *Deprecated* in the Status
column but keeps its row and meaning.

| Shape | Summary | Canonicalization notes | Contact | Status |
|-------|---------|------------------------|---------|--------|
| `x-ltp/reserved-example` | Reserved placeholder illustrating the entry format; carries no semantics and MUST NOT be committed | none | LTP maintainers | Reserved |

## Versioning

Changes to this document follow the repository's normal review process. Because entries
are append-only, implementations MAY cache this registry; a cached copy can only be
incomplete, never wrong.
