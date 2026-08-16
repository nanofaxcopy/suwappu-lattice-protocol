# Research round — 2026-08-16 (pre-publication)

A literature pass run against the 0.2.0 whitepaper before publication, to
check its cryptographic positioning against current (2024–2026) work.
Applied changes went into `docs/WHITEPAPER.md` 0.2.0; this file records
sources and the backlog that did not make the publication cut.

## Applied to the whitepaper

| Topic | Finding | Where applied |
|---|---|---|
| KEM binding taxonomy | Cremers–Dax–Medinger ("Keeping Up with the KEMs", ePrint 2023/1933) define the X-BIND-P-Q hierarchy; Schmieg (ePrint 2024/523) shows ML-KEM is LEAK-BIND-K-CT / LEAK-BIND-K-PK but **not** MAL-BIND-K-CT / MAL-BIND-K-PK. Consequence: the sealed-key AAD binding LTP plans is *necessary at the protocol layer* — no KEM parameter choice discharges it. | §3.3 KEM-binding disclosure, now citing both papers and RFC 9180's context/AAD guidance |
| PQ transition posture | NIST IR 8547 schedules deprecation of quantum-vulnerable algorithms ~2030, removal by 2035; deployed hybrid practice is X25519MLKEM768 in TLS 1.3; X-Wing is the natural combined-KEM candidate for a future hybrid profile. | §8.8 hybrid-KEM paragraph |
| AEAD standardization | XChaCha20-Poly1305 exists only as an expired IRTF draft (`draft-irtf-cfrg-xchacha`), though ubiquitously implemented. FIPS/standards-track-constrained deployments need the AES-256-GCM alternative called out explicitly. | §2.1.1 nonce-derivation section |

Sources:
- https://eprint.iacr.org/2023/1933 (Keeping Up with the KEMs)
- https://eprint.iacr.org/2024/523 (Unbindable Kemmy Schmidt)
- https://csrc.nist.gov/pubs/ir/8547/ipd (NIST IR 8547, PQC transition)
- https://www.rfc-editor.org/rfc/rfc9180.html (HPKE)
- https://datatracker.ietf.org/doc/draft-irtf-cfrg-xchacha/ (XChaCha status)

## Backlog (post-publication)

Ordered by leverage:

1. **Sealed-key AAD binding (the replay fix).** Design + implement the
   receiver-ek-fingerprint + entity_id AAD binding with a freshness
   component; re-run the Verifpal model and confirm the
   `authentication? sealed_key` query flips to verified. The Verifpal
   harness and recorded baseline are already in `docs/formal/`.
2. **HPKE-shaped sealing.** Evaluate replacing the bespoke
   KEM+AEAD sealing with RFC 9180 HPKE (base mode + info binding), which
   would import a machine-analyzed construction instead of maintaining a
   bespoke one. Blocked on HPKE ML-KEM ciphersuite standardization.
3. **Conformant fast erasure backend.** An optimized GF(2⁸) kernel
   (e.g., Intel ISA-L `ec_encode_data` driven by the §2.1.1 Vandermonde
   matrix) that reproduces the non-systematic shards byte-for-byte, so
   the zfec opt-in (non-conformant, systematic) can be retired. Gate it
   with `test_default_backend_matches_whitepaper_vector`.
4. **EasyCrypt/CryptoVerif treatment of Theorems 3–8** — the game-based
   reductions remain pen-and-paper (§3.3.8). The SandboxAQ
   "Keeping Up with the KEMs, Formally" EasyCrypt work is the closest
   starting point for the KEM layer.
5. **Certora spec for `LTPAnchorRegistry.sol`** (carried over from
   `FORMAL_VERIFICATION_STATUS.md` wishlist — unchanged).
6. **Hybrid profile (X-Wing)** behind crypto-agility negotiation, per
   the §8.8 paragraph.
7. **Empirical benchmarks** for §6.4's α and the commit/materialize
   latency model — the paper still ships with no measurements, disclosed
   as such.
