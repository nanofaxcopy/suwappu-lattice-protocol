//! SP1 Verify Binary — verifies a real ZK proof.
//! Exit 0 = valid, Exit 1 = invalid.

use sp1_sdk::{Prover, ProverClient, SP1ProofWithPublicValues};
use std::io::{self, Read};

const ELF: &[u8] = include_bytes!(
    "../../sp1-mldsa-verifier/target/elf-compilation/riscv64im-succinct-zkvm-elf/release/sp1-mldsa-verifier"
);

#[tokio::main]
async fn main() {
    let mut proof_data = Vec::new();
    io::stdin().read_to_end(&mut proof_data).expect("Failed to read stdin");

    if proof_data.is_empty() {
        eprintln!("ERROR: No proof data");
        std::process::exit(1);
    }

    let proof: SP1ProofWithPublicValues = match bincode::deserialize(&proof_data) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("ERROR: Deserialize failed: {}", e);
            std::process::exit(1);
        }
    };

    eprintln!("Public values: {} bytes", proof.public_values.as_slice().len());

    let client = ProverClient::builder().cpu().build().await;
    let _pk = client.setup(sp1_sdk::Elf::Static(ELF)).await.expect("Setup failed");

    use sp1_sdk::ProvingKey;
    match client.verify(&proof, _pk.verifying_key(), None) {
        Ok(()) => {
            eprintln!("VERIFICATION: PASSED");
            std::process::exit(0);
        }
        Err(e) => {
            eprintln!("VERIFICATION: FAILED — {}", e);
            std::process::exit(1);
        }
    }
}
