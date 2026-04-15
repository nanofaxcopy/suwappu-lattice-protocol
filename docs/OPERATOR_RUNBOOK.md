# ETP / LTP Operator Runbook

Operational guide for running an Entanglement Transfer Protocol node in
production. This document complements `docs/DEPLOYMENT_GUIDE.md` and the
alerts defined in `deploy/observability/prometheus/alerts.yml`.

> Audience: SREs, node operators, on-call engineers.
> Scope: single-node and small-cluster deployments. Multi-region HA is
> a superset — the same runbook entries apply to each replica.

---

## 1. Prerequisites

### Hardware (minimum)

| Component | Requirement |
|-----------|-------------|
| CPU       | 4 cores (AVX2 recommended for PQC) |
| Memory    | 8 GiB |
| Disk      | 200 GiB SSD (Merkle log + shard store grow over time) |
| Network   | 100 Mbps symmetric, public IP for gossip |

### Software

- Linux x86_64 (kernel 5.10+) — tested on Ubuntu 22.04 LTS and Amazon Linux 2023.
- Python 3.12+.
- Optional: Docker 24+ (for the observability stack).
- Optional: `liboqs` system package for hardware-accelerated ML-KEM / ML-DSA.

### Accounts & secrets

- A dedicated KMS key (AWS KMS, GCP KMS, or HSM-backed PKCS#11 token).
- Operator ML-DSA signing key (generated once, rotated quarterly).
- TLS certificate (X.509) issued by a publicly trusted CA or an internal CA
  shared with peer operators.
- Ethereum/L2 signer key for bridge anchoring (optional; required only if
  this node publishes cross-chain anchors).

---

## 2. KMS setup

All cryptographic material (ML-DSA signing key, XChaCha20 data-encryption
key, bridge signer) is held in a KMS. The node never persists unwrapped
key bytes on disk.

### AWS KMS

```bash
aws kms create-key \
    --description "ETP node signing key" \
    --key-usage SIGN_VERIFY \
    --key-spec ECC_NIST_P384

aws kms create-alias \
    --alias-name alias/etp-node-signer \
    --target-key-id <key-id>

# Grant the node's IAM role:
aws kms create-grant \
    --key-id alias/etp-node-signer \
    --grantee-principal arn:aws:iam::<acct>:role/etp-node \
    --operations Sign Verify GetPublicKey DescribeKey
```

Environment for the node process:

```
ETP_KMS_PROVIDER=aws
ETP_KMS_KEY_ID=alias/etp-node-signer
AWS_REGION=us-east-1
```

### GCP KMS

```bash
gcloud kms keyrings create etp --location us-central1
gcloud kms keys create signer \
    --keyring etp --location us-central1 \
    --purpose asymmetric-signing \
    --default-algorithm ec-sign-p384-sha384
```

Env:

```
ETP_KMS_PROVIDER=gcp
ETP_KMS_KEY_ID=projects/<proj>/locations/us-central1/keyRings/etp/cryptoKeys/signer/cryptoKeyVersions/1
```

### HSM (PKCS#11)

```
ETP_KMS_PROVIDER=pkcs11
ETP_PKCS11_MODULE=/usr/lib/softhsm/libsofthsm2.so
ETP_PKCS11_SLOT=0
ETP_PKCS11_PIN_FILE=/etc/etp/hsm.pin
ETP_KMS_KEY_LABEL=etp-node-signer
```

Quarterly rotation: see §8 `KeyRotationFailure`.

---

## 3. TLS setup

Nodes expose REST (`/v1/*`) and metrics (`/metrics`) over HTTPS.

### Generate a CSR

```bash
openssl req -new -newkey ec:<(openssl ecparam -name prime256v1) \
    -keyout /etc/etp/tls/node.key \
    -out /etc/etp/tls/node.csr \
    -subj "/CN=etp-node-us-east-1.example.com" \
    -nodes
```

Submit the CSR to your CA. Install the resulting certificate chain:

```
/etc/etp/tls/node.key      (0600 root:etp)
/etc/etp/tls/node.crt      (0644)
/etc/etp/tls/chain.crt     (0644)
```

Node config:

```
ETP_TLS_CERT=/etc/etp/tls/node.crt
ETP_TLS_KEY=/etc/etp/tls/node.key
ETP_TLS_CHAIN=/etc/etp/tls/chain.crt
ETP_TLS_MIN_VERSION=1.3
```

### Rotation (quarterly or on expiry)

1. Issue a new certificate from the same CA, same CN.
2. Stage it next to the live cert (e.g. `node.crt.new`).
3. `systemctl reload etp-node` — the server reloads TLS material without
   dropping existing connections (SIGHUP handler).
4. Confirm with `openssl s_client -connect <host>:8080 -servername <host>`
   that the new fingerprint is served.
5. Remove the old cert after the next gossip cycle (~5 min).

---

## 4. Seed peers

Each node bootstraps its gossip view from a static list of seed peers.

```yaml
# /etc/etp/peers.yaml
seeds:
  - id:   etp-mainnet-us-east-1
    addr: https://etp-us-east-1.example.com:8080
    vk:   ml-dsa65:base64:AAAA...
  - id:   etp-mainnet-eu-west-1
    addr: https://etp-eu-west-1.example.com:8080
    vk:   ml-dsa65:base64:BBBB...
```

The node pins each seed's ML-DSA verification key on first contact and
refuses to accept gossip whose signature doesn't match. Update the pin
only after out-of-band confirmation.

---

## 5. Starting the node

### systemd unit

```ini
# /etc/systemd/system/etp-node.service
[Unit]
Description=ETP / LTP node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=etp
Group=etp
EnvironmentFile=/etc/etp/node.env
ExecStart=/usr/local/bin/etp-node serve \
    --config /etc/etp/node.yaml \
    --data-dir /var/lib/etp \
    --bind 0.0.0.0:8080
Restart=on-failure
RestartSec=5s
LimitNOFILE=65536
LimitNPROC=4096

# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/etp
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now etp-node
systemctl status etp-node
journalctl -u etp-node -f
```

### Health checks

```bash
# Liveness
curl -fsS https://localhost:8080/healthz

# Readiness (requires at least one peer + STH freshness <180s)
curl -fsS https://localhost:8080/readyz

# Metrics
curl -s https://localhost:8080/metrics | head
```

---

## 6. Observability

```bash
cd deploy/observability
docker-compose up -d
```

- Prometheus: `http://localhost:9090`
- Grafana:    `http://localhost:3000` (admin / admin, change on first login)
- AlertManager: `http://localhost:9093`

The dashboard `ETP / LTP Node Health` is auto-provisioned under the `ETP`
folder. Edit `deploy/observability/prometheus/prometheus.yml` to add
your production node endpoints.

---

## 7. Alert runbook

Alert names link back from the `runbook:` annotation in
`deploy/observability/prometheus/alerts.yml`.

### `#node-down`

**Symptom:** Prometheus can't scrape the node for >2 minutes.

**Triage:**

1. `systemctl status etp-node` — is the process running?
2. `journalctl -u etp-node -n 200` — look for panics, OOM, DB open
   failures.
3. `curl -fsS https://localhost:8080/healthz` from the host — network or
   application?
4. Check disk: `df -h /var/lib/etp`. If full, see §10 disk pressure.
5. Check KMS connectivity: `aws kms describe-key --key-id <id>` (or equivalent).

**Remediation:**

- Restart: `systemctl restart etp-node`.
- If start fails repeatedly, freeze the node (disable unit) and escalate.
  Do **not** wipe `/var/lib/etp` — the Merkle log is the chain of custody.

---

### `#sth-publish-gap`

**Symptom:** `etp_sth_publish_gap_seconds > 600` for >5 minutes.

**Triage:**

1. Are new records arriving? `rate(etp_rest_ingest_total[5m])` on the
   dashboard.
2. Is the signer reachable? Look for `kms.Sign` errors in
   `journalctl -u etp-node | grep sth`.
3. Is the Merkle worker stuck?
   `curl -s localhost:8080/debug/goroutines | grep merkle` (Python:
   `/debug/tasks`).

**Remediation:**

- Rotate the signer if the KMS is returning `InvalidKeyUsageException`.
- Manually trigger a publish:
  `curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" https://localhost:8080/admin/sth/publish`.
- If the log has diverged from peers (compare `/v1/log/sth` across
  nodes), STOP. A forked log is a P0 — engage the core team.

---

### `#audit-failures`

**Symptom:** `etp_audit_failure_rate > 0.02` for >10 minutes.

**Triage:**

1. Per-peer breakdown:
   `sum by (peer) (increase(etp_audit_failures_by_peer_total[10m]))`.
2. If one peer dominates, check the gossip view — is it known-byzantine?
3. If spread across peers, suspect local shard corruption.

**Remediation:**

- Single peer: quarantine via `PUT /admin/peers/<id>/quarantine`.
- Widespread: run `etp-node audit --repair` to re-fetch failed shards
  from replicas. Do NOT delete shards before repair completes.
- If repair can't source a shard quorum, the entity is `DISPUTED`;
  follow the dispute playbook (§9).

---

### `#key-rotation-failure`

**Symptom:** `etp_key_rotation_failures_total` increased.

**Triage:**

1. `journalctl -u etp-node | grep -i rotate` — read the failure reason.
2. KMS permissions: `aws kms create-grant --dry-run ...` or equivalent.
3. If using PKCS#11, check PIN and token presence: `pkcs11-tool -L`.

**Remediation:**

- Fix the underlying IAM / HSM issue.
- Re-run rotation:
  `curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" https://localhost:8080/admin/keys/rotate`.
- Failures must not be silenced: a node with an expired signing key
  will stop publishing STHs within 24h.

---

### `#bridge-stuck`

**Symptom:** `etp_bridge_message_age_seconds > 3600` for >10 minutes.

**Triage:**

1. Is the L1/L2 RPC healthy?
   `curl -s "$ETP_BRIDGE_RPC_URL" -d '{"jsonrpc":"2.0","method":"eth_blockNumber","id":1}'`.
2. Is the bridge signer funded? `cast balance <signer>`.
3. `etp_bridge_retry_queue_size` climbing while
   `etp_bridge_records_bridged_total` flat → worker is retrying but
   failing.

**Remediation:**

- Top up the signer if balance < 0.01 ETH.
- Point at a healthy RPC provider (edit
  `ETP_BRIDGE_RPC_URL` in `/etc/etp/node.env` and restart).
- If the contract is paused (admin MultiSig), wait for unpause — bridge
  records will drain automatically.

---

### `#nonce-violation`

**Symptom:** `etp_nonce_violation_total` increased.

**Severity:** P0. A monotonic-nonce violation means someone replayed or
rewound a signed message — treat as an active attack until disproven.

**Triage:**

1. `journalctl -u etp-node | grep NonceViolation` — peer, record id,
   observed nonce, expected nonce.
2. Correlate with gossip peer churn: did a peer reconnect with a stale
   state snapshot?

**Remediation:**

- Quarantine the offending peer immediately.
- Do NOT restart the node — that may mask the evidence.
- Preserve logs: `journalctl -u etp-node --since "-30m" > /var/log/etp/incident-$(date +%s).log`.
- Page the security on-call.

---

## 8. Key rotation (scheduled)

Quarterly rotation schedule (or on-demand after a suspected compromise):

```bash
# 1. Create a new KMS key version
aws kms create-key --description "ETP node signer (Q2 2026)" ...

# 2. Update /etc/etp/node.env with the new key id
#    KEEP the old key id in ETP_KMS_KEY_IDS_PREVIOUS for signature verification

# 3. Rotate
curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" \
    https://localhost:8080/admin/keys/rotate

# 4. Wait for the next STH publish (up to 30s) — it must be signed by the NEW key
curl -s https://localhost:8080/v1/log/sth | jq .signer_vk_hash

# 5. After 24h with no verification failures, schedule deletion of the old key
aws kms schedule-key-deletion --key-id <old-id> --pending-window-in-days 30
```

Signer sequence numbers are monotonic and persisted: a new key starts at
`sequence = max(prev_sequence) + 1`. The registry contract enforces this
on-chain.

---

## 9. Disputes

An entity enters `DISPUTED` when:

- An audit quorum fails to materialize it, OR
- Two peers produce conflicting STHs for overlapping ranges.

**Playbook:**

1. Freeze ingest for the affected entity: `PUT /admin/entities/<id>/freeze`.
2. Run `etp-node dispute --entity <id> --export /tmp/evidence.tar.gz` —
   this collects local shards, peer responses, and STH inclusion proofs.
3. Upload to the dispute S3 bucket (`s3://gsx-etp-disputes/<incident>/`).
4. File an on-chain dispute:
   `cast send $REGISTRY "dispute(bytes32,bytes32)" $ENTITY_ID $EVIDENCE_HASH`.
5. Wait for multisig resolution (up to 48h). Do not force-transition the
   entity state locally.

---

## 10. Backup & recovery

### What to back up

| Path | Contents | Backup freq |
|------|----------|-------------|
| `/var/lib/etp/merkle/` | Merkle log (STH-signed) | hourly |
| `/var/lib/etp/state/`  | Entity state machine   | hourly |
| `/var/lib/etp/shards/` | Local shard replicas   | daily (content-addressed; full resync is possible) |
| `/etc/etp/`            | Config, TLS, peers     | on change |

Recommended tooling: restic or borgbackup, encrypted with a KMS-wrapped
key, shipped to an offsite bucket.

### Recovery

```bash
systemctl stop etp-node
restic -r $REPO restore latest --target /

# Verify Merkle log integrity before restart
etp-node verify --data-dir /var/lib/etp

systemctl start etp-node

# Re-sync missing shards from peers (idempotent)
curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" \
    https://localhost:8080/admin/shards/reconcile
```

---

## 11. Upgrades

1. Read the release notes — migrations are documented per-version.
2. Stage the new binary in `/usr/local/bin/etp-node.new`.
3. `etp-node --version` on the staged binary and confirm.
4. `systemctl stop etp-node`.
5. Back up `/var/lib/etp/state/` (state-machine migrations are
   reversible only from a backup).
6. Move the binary into place: `mv /usr/local/bin/etp-node.new /usr/local/bin/etp-node`.
7. `systemctl start etp-node`.
8. Watch `journalctl -u etp-node -f` for `migration complete`.
9. Run `curl -fsS https://localhost:8080/readyz` — must return 200 within 60s.

---

## 12. Contact

- **Core team:** core@globalsettlement.dev
- **Security on-call:** security@globalsettlement.dev (PGP key on keybase)
- **Slack:** `#etp-ops` (GSX workspace)
- **Incident tracker:** https://github.com/GlobalSettlementNetwork/gsx-lattice-protocol/issues
