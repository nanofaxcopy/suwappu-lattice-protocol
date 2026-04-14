//! RISC Zero Host Binary — generates real ZK proofs for ETP bridge.
//!
//! Reads witness data from stdin, generates proof via RISC Zero SDK, writes to stdout.
//!
//! NOTE: Requires the RISC Zero toolchain (rzup) and compiled guest ELF.
//! Build guest first: cd ../risc0-mldsa-verifier && cargo risczero build
//! Then build host: cargo build --release

use std::io::{self, Read, Write};

fn main() {
    eprintln!("RISC Zero host binary — placeholder");
    eprintln!("The RISC Zero toolchain (rzup) must be installed to compile the full implementation.");
    eprintln!("Install: curl -L https://risczero.com/install | bash && rzup");
    eprintln!("");
    eprintln!("Once installed, this binary will:");
    eprintln!("  1. Read witnesses from stdin (same format as sp1-host)");
    eprintln!("  2. Load the compiled guest ELF");
    eprintln!("  3. Generate a RISC Zero proof via the SDK");
    eprintln!("  4. Write serialized receipt to stdout");
    std::process::exit(2); // Exit 2 = not implemented (vs 0=success, 1=failure)
}
