//! Where Kulan OS comes from, and how a download is known to be the real one.
//!
//! Every release on GitHub has SHA256SUMS, signed (SHA256SUMS.sig) with the
//! same key as the update channel. The key is built into this app, so a
//! download is only used when its SHA-256 is in a SHA256SUMS signed by it.

use pgp::composed::{Deserializable, DetachedSignature, SignedPublicKey};

/// The newest release's files (GitHub sends the latest one).
pub const BASE_URL: &str = "https://github.com/mmohamud25/kulan-os/releases/latest/download/";

/// The update channel's public key (packages/kulan-archive-keyring.gpg).
const KEYRING: &[u8] = include_bytes!("../../../packages/kulan-archive-keyring.gpg");

pub fn iso_name(arch: &str) -> String {
    format!("kulan-os-{arch}.iso")
}

/// Is `sums` signed by the Kulan OS key?
pub fn verify_signature(sums: &[u8], sig: &[u8]) -> Result<(), String> {
    verify_with(KEYRING, sums, sig)
}

pub fn verify_with(keyring: &[u8], sums: &[u8], sig: &[u8]) -> Result<(), String> {
    let key = SignedPublicKey::from_bytes(keyring).map_err(|e| format!("the built-in key couldn't be read: {e}"))?;
    let signature = DetachedSignature::from_bytes(sig)
        .or_else(|_| DetachedSignature::from_armor_single(sig).map(|(s, _)| s))
        .map_err(|_| "the release's signature couldn't be read".to_string())?;
    if signature.verify(&key.primary_key, sums).is_ok() {
        return Ok(());
    }
    for sub in &key.public_subkeys {
        if signature.verify(&sub.key, sums).is_ok() {
            return Ok(());
        }
    }
    Err("the release isn't signed by Kulan OS".to_string())
}

/// The SHA-256 (lower-case hex) of `name` in a SHA256SUMS file.
pub fn expected_hash(sums: &str, name: &str) -> Option<String> {
    sums.lines().find_map(|line| {
        let mut parts = line.split_whitespace();
        let hash = parts.next()?;
        let file = parts.next()?.trim_start_matches('*');
        (file == name && hash.len() == 64 && hash.chars().all(|c| c.is_ascii_hexdigit()))
            .then(|| hash.to_ascii_lowercase())
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    // The real 2.9c release's SHA256SUMS and its signature.
    const SUMS: &[u8] = b"02be3f533910cb1566e71b284c777e22869e72d727903d3a2376e642d9d713d6  kulan-os-amd64.iso\n\
9fab41a75f34f28ea1d5caf525f3c3e420518f7aec9408f5f14e4802609a21ff  kulan-os-arm64.iso\n";
    const SIG: &[u8] = include_bytes!("../tests/SHA256SUMS.sig");

    #[test]
    fn the_real_release_is_signed() {
        verify_signature(SUMS, SIG).unwrap();
    }

    #[test]
    fn a_changed_sums_file_is_refused() {
        let mut changed = SUMS.to_vec();
        changed[0] = b'1';
        assert!(verify_signature(&changed, SIG).is_err());
    }

    #[test]
    fn rubbish_is_refused() {
        assert!(verify_signature(SUMS, b"not a signature").is_err());
    }

    #[test]
    fn hashes_are_found_by_name() {
        let sums = std::str::from_utf8(SUMS).unwrap();
        assert_eq!(expected_hash(sums, "kulan-os-arm64.iso").unwrap(),
                   "9fab41a75f34f28ea1d5caf525f3c3e420518f7aec9408f5f14e4802609a21ff");
        assert!(expected_hash(sums, "kulan-os-amd64.iso.zip").is_none());
        assert!(expected_hash("xyz  kulan-os-amd64.iso\n", "kulan-os-amd64.iso").is_none());
        assert_eq!(expected_hash(&format!("{}  *a.iso", "A".repeat(64)), "a.iso").unwrap(), "a".repeat(64));
    }
}
