//! SP1 zkVM circuit: proves ML-DSA-65 (FIPS 204) signature validity.
//!
//! Given private witnesses (operator_vk, signature, signable_payload),
//! the circuit asserts that ML-DSA-65 verification succeeds, then commits
//! the public inputs (sth_root_hash, operator_vk_hash, tree_size, sth_sequence).
//!
//! This is the core proof obligation for the ETP cross-chain bridge:
//! "The operator's STH signature is valid" — proved in zero knowledge.

#![no_main]
sp1_zkvm::entrypoint!(main);

use sha3::{Digest, Sha3_256};

pub fn main() {
    // -----------------------------------------------------------------------
    // Step 1: Read private witnesses from SP1 stdin
    // -----------------------------------------------------------------------
    let operator_vk: Vec<u8> = sp1_zkvm::io::read_vec();      // 1952 bytes (ML-DSA-65 VK)
    let signature: Vec<u8> = sp1_zkvm::io::read_vec();         // 3309 bytes (ML-DSA-65 sig)
    let signable_payload: Vec<u8> = sp1_zkvm::io::read_vec();  // 56 bytes

    // Validate witness sizes
    assert_eq!(operator_vk.len(), 1952, "Invalid VK size");
    assert_eq!(signature.len(), 3309, "Invalid signature size");
    assert_eq!(signable_payload.len(), 56, "Invalid payload size");

    // -----------------------------------------------------------------------
    // Step 2: Verify ML-DSA-65 signature (FIPS 204)
    //
    // This is the proof obligation. If verification fails, the SP1 prover
    // will abort and no proof is generated (soundness guarantee).
    // -----------------------------------------------------------------------
    use ml_dsa::{MlDsa65, VerifyingKey, Signature, EncodedVerifyingKey, EncodedSignature};
    use ml_dsa::signature::Verifier;

    // Parse verification key from raw bytes
    let vk_encoded = EncodedVerifyingKey::<MlDsa65>::try_from(operator_vk.as_slice())
        .expect("Failed to create EncodedVerifyingKey (wrong size)");
    let vk = VerifyingKey::<MlDsa65>::decode(&vk_encoded);

    // Parse signature from raw bytes
    let sig_encoded = EncodedSignature::<MlDsa65>::try_from(signature.as_slice())
        .expect("Failed to create EncodedSignature (wrong size)");
    let sig = Signature::<MlDsa65>::decode(&sig_encoded)
        .expect("Failed to decode ML-DSA-65 signature");

    // Verify — this is the proof obligation
    vk.verify(&signable_payload, &sig)
        .expect("ML-DSA-65 signature verification FAILED — proof is unsound");

    // -----------------------------------------------------------------------
    // Step 3: Extract public inputs from signable_payload
    //
    // Layout: sequence(8B BE) || tree_size(8B BE) || timestamp(8B BE) || root_hash(32B)
    // -----------------------------------------------------------------------
    let sth_sequence = u64::from_be_bytes(
        signable_payload[0..8].try_into().unwrap()
    );
    let tree_size = u64::from_be_bytes(
        signable_payload[8..16].try_into().unwrap()
    );
    // timestamp at [16..24] is not part of public inputs (private)
    let sth_root_hash = &signable_payload[24..56]; // 32 bytes

    // -----------------------------------------------------------------------
    // Step 4: Compute operator_vk_hash = SHA3-256(operator_vk)
    // -----------------------------------------------------------------------
    let mut hasher = Sha3_256::new();
    hasher.update(&operator_vk);
    let operator_vk_hash = hasher.finalize();

    // -----------------------------------------------------------------------
    // Step 5: Commit public inputs (verifiable by anyone with the proof)
    //
    // These values become the "public statement" of the ZK proof:
    // "There exists a valid ML-DSA-65 signature by operator_vk_hash
    //  on an STH with root_hash, tree_size, and sequence."
    // -----------------------------------------------------------------------
    sp1_zkvm::io::commit_slice(sth_root_hash);                      // 32 bytes
    sp1_zkvm::io::commit_slice(&operator_vk_hash);                  // 32 bytes
    sp1_zkvm::io::commit_slice(&tree_size.to_be_bytes());           // 8 bytes
    sp1_zkvm::io::commit_slice(&sth_sequence.to_be_bytes());        // 8 bytes
}
