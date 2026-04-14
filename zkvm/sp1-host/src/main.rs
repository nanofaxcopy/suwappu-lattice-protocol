//! SP1 Host Binary — generates real ZK proofs for ETP bridge.
//!
//! Reads witness data from stdin, generates proof via SP1 SDK, writes to stdout.

use sp1_sdk::{Prover, ProverClient, SP1Stdin};
use std::io::{self, Read, Write};

/// Compiled SP1 circuit ELF (embedded at build time).
const ELF: &[u8] = include_bytes!(
    "../../sp1-mldsa-verifier/target/elf-compilation/riscv64im-succinct-zkvm-elf/release/sp1-mldsa-verifier"
);

#[tokio::main]
async fn main() {
    // 1. Read witness data from stdin
    let mut stdin_data = Vec::new();
    io::stdin().read_to_end(&mut stdin_data).expect("Failed to read stdin");

    if stdin_data.is_empty() {
        eprintln!("ERROR: No witness data on stdin");
        std::process::exit(1);
    }

    // 2. Parse length-prefixed vectors
    let vectors = parse_vectors(&stdin_data);
    if vectors.len() != 3 {
        eprintln!("ERROR: Expected 3 witness vectors, got {}", vectors.len());
        std::process::exit(1);
    }

    eprintln!("Witnesses: vk={}B sig={}B payload={}B",
        vectors[0].len(), vectors[1].len(), vectors[2].len());

    // 3. Set up SP1 stdin
    let mut sp1_stdin = SP1Stdin::new();
    for v in &vectors {
        sp1_stdin.write_vec(v.to_vec());
    }

    // 4. Create client and setup
    let client = ProverClient::builder().cpu().build().await;
    let pk = client.setup(sp1_sdk::Elf::Static(ELF)).await.expect("Setup failed");
    eprintln!("Circuit setup complete. Generating proof...");

    // 5. Generate proof
    let proof = client
        .prove(&pk, sp1_stdin)
        .await
        .expect("Proof generation failed");

    eprintln!("Proof generated! Public values: {} bytes", proof.public_values.as_slice().len());
    eprintln!("PUBLIC_VALUES_HEX:{}", hex::encode(proof.public_values.as_slice()));

    // 6. Verify locally
    {
        use sp1_sdk::ProvingKey;
        client.verify(&proof, pk.verifying_key(), None).expect("Self-verification failed");
    }
    eprintln!("Self-verification PASSED");

    // 7. Serialize to stdout
    let serialized = bincode::serialize(&proof).expect("Serialization failed");
    eprintln!("Serialized proof: {} bytes", serialized.len());
    io::stdout().write_all(&serialized).expect("stdout write failed");
}

fn parse_vectors(data: &[u8]) -> Vec<Vec<u8>> {
    let mut vectors = Vec::new();
    let mut offset = 0;
    while offset + 4 <= data.len() {
        let len = u32::from_le_bytes(data[offset..offset + 4].try_into().unwrap()) as usize;
        offset += 4;
        if offset + len > data.len() { break; }
        vectors.push(data[offset..offset + len].to_vec());
        offset += len;
    }
    vectors
}
