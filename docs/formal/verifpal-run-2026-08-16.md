# Verifpal run — 2026-08-16

First recorded verification run of [`etp-protocol.vp`](etp-protocol.vp).
Interpretation and follow-ups live in [`ANALYSIS.md`](ANALYSIS.md); this
file is the evidence record.

## Environment

| | |
|---|---|
| Tool | Verifpal **0.27.4** (the last Go release, matching the `v0.27+` this repo's docs specify), built from source at tag `v0.27.4` with Go 1.24.7 |
| Command | `verifpal verify docs/formal/etp-protocol.vp` |
| Attacker | `active` (Dolev-Yao), unbounded sessions |
| Runtime | ~10 minutes, ~499,000 analysis states |
| Model | `etp-protocol.vp` as revised 2026-08-16 (the 2026-03-29 model did not pass Verifpal's model checks — change log in the model header) |

Note: Verifpal 1.0 (the Rust rewrite) uses a different surface syntax
(`PUBKEY`/`DH_KEX` instead of `G^`); the model targets the 0.27 syntax
it was written for.

## Verdicts

```
analysis • Stage 4-5 completed, ~499,000 states
 Verifpal • Verification completed
 Verifpal • Summary of failed queries will follow.
```

| Query | Verdict |
|---|---|
| `confidentiality? cek` | ✅ verified (not in failed-query summary; no attack trace found) |
| `confidentiality? content` | ✅ verified |
| `authentication? Sender -> Receiver: commitment` | ❌ failed — trace below |
| `authentication? Sender -> Receiver: sealed_key` | ❌ failed — trace below |

## Failed-query traces (abridged; attacker-controlled values marked)

### `authentication? Sender -> Receiver: commitment`

```
commitment → SIGN(sender_sk, HASH(CONCAT(HASH(content),
             HASH(AEAD_ENC(cek, content, entity_nonce)))))  ← obtained by Attacker
...
commitment (...), sent by Attacker and not by Sender, is successfully
used in SIGNVERIF(G^sender_sk, HASH(CONCAT(HASH(content),
HASH(AEAD_ENC(cek, content, entity_nonce)))), ...) within Receiver's state.
```

The attacker cannot forge the ML-DSA signature (modeled as `SIGN`); the
finding is that the *delivery* of the commitment is unauthenticated — the
attacker can obtain the public record and deliver it itself. For a
published, self-authenticating record this is close to by-design, but it
is a genuine failure of Verifpal's message-agreement property.

### `authentication? Sender -> Receiver: sealed_key`

```
sealed_key → AEAD_ENC(G^receiver_sk^sender_sk,
             CONCAT(cek, HASH(content)), entity_nonce)  ← obtained by Attacker
In another session:
sealed_key ... ← mutated by Attacker (replayed)
...
sealed_key (...), sent by Attacker and not by Sender, is successfully
used in AEAD_DEC(G^sender_sk^receiver_sk, ..., entity_nonce)?
within Receiver's state.
```

**Cross-session replay.** A sealed lattice key captured in one session is
accepted by the Receiver in another: nothing in the sealed key binds it
to a session, a freshness value, or the receiver's encapsulation key.
This corroborates the KEM ciphertext-binding gap (Bhargavan et al.
binding property) disclosed in whitepaper §3.3. Planned mitigation:
receiver encapsulation-key fingerprint + entity_id in the sealed key's
AEAD associated data, plus a freshness component (future protocol
revision); `max_materializations` policy enforcement bounds the impact
meanwhile.

## Reproducing

```bash
# Verifpal 0.27.4 (Go). The Rust 1.0 rewrite changed the model syntax.
git clone --branch v0.27.4 https://github.com/symbolicsoft/verifpal
cd verifpal && go build -o verifpal ./cmd/verifpal

./verifpal verify docs/formal/etp-protocol.vp
```

Expected: the two confidentiality queries absent from the failed-query
summary, the two authentication queries present with traces equivalent
to the above. Open an issue with the `verifpal-output` label if you see
anything different.
