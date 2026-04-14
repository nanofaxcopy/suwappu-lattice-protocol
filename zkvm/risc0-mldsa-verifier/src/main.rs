//! RISC Zero zkVM guest: proves ML-DSA-65 (FIPS 204) signature validity.
//!
//! Identical proof obligation to the SP1 circuit:
//! Given private witnesses (operator_vk, signature, signable_payload),
//! verify ML-DSA-65 signature and commit public inputs to the journal.
//!
//! Requires: RISC Zero toolchain (rzup) for compilation.
//! Build: cargo risczero build

#![no_main]
risc0_zkvm::guest::entry!(main);

use sha3::{Digest, Sha3_256};

pub fn main() {
    // 1. Read private witnesses from host
    let operator_vk: Vec<u8> = risc0_zkvm::guest::env::read();      // 1952B
    let signature: Vec<u8> = risc0_zkvm::guest::env::read();         // 3309B
    let signable_payload: Vec<u8> = risc0_zkvm::guest::env::read();  // 56B

    assert_eq!(operator_vk.len(), 1952, "Invalid VK size");
    assert_eq!(signature.len(), 3309, "Invalid signature size");
    assert_eq!(signable_payload.len(), 56, "Invalid payload size");

    // 2. Verify ML-DSA-65 signature (FIPS 204)
    use ml_dsa::{VerifyingKey, Signature, EncodedVerifyingKey, EncodedSignature, MlDsa65};
    use ml_dsa::signature::Verifier;

    let vk_encoded = EncodedVerifyingKey::<MlDsa65>::try_from(operator_vk.as_slice())
        .expect("Failed to create EncodedVerifyingKey");
    let vk = VerifyingKey::<MlDsa65>::decode(&vk_encoded);

    let sig_encoded = EncodedSignature::<MlDsa65>::try_from(signature.as_slice())
        .expect("Failed to create EncodedSignature");
    let sig = Signature::<MlDsa65>::decode(&sig_encoded)
        .expect("Failed to decode signature");

    vk.verify(&signable_payload, &sig)
        .expect("ML-DSA-65 signature verification FAILED");

    // 3. Extract public inputs
    let sth_sequence = u64::from_be_bytes(signable_payload[0..8].try_into().unwrap());
    let tree_size = u64::from_be_bytes(signable_payload[8..16].try_into().unwrap());
    let sth_root_hash = &signable_payload[24..56];

    // 4. Compute operator_vk_hash = SHA3-256(operator_vk)
    let mut hasher = Sha3_256::new();
    hasher.update(&operator_vk);
    let operator_vk_hash = hasher.finalize();

    // 5. Commit public inputs to journal
    risc0_zkvm::guest::env::commit_slice(sth_root_hash);
    risc0_zkvm::guest::env::commit_slice(&operator_vk_hash);
    risc0_zkvm::guest::env::commit_slice(&tree_size.to_be_bytes());
    risc0_zkvm::guest::env::commit_slice(&sth_sequence.to_be_bytes());
}
