//! Kulan USB Maker: makes a Kulan OS USB stick on a Mac or a Windows PC.
//! Started with --write-helper, it's the part that writes (as an administrator).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod app;
mod download;
mod drives;
mod elevate;
mod release;
mod writer;

/// Said when someone pressed Stop.
pub const STOPPED: &str = "stopped";

fn main() {
    let args: Vec<String> = std::env::args().collect();
    // Tests only: the drives as the helper sees them (with their fingerprints).
    #[cfg(feature = "test-devices")]
    if args.get(1).map(String::as_str) == Some("--list-drives") {
        let drives: Vec<_> = drives::list().unwrap_or_default().iter()
            .map(|d| serde_json::json!({"id": d.id, "fingerprint": d.fingerprint()})).collect();
        println!("{}", serde_json::Value::Array(drives));
        return;
    }
    if args.get(1).map(String::as_str) == Some("--write-helper") {
        std::process::exit(writer::helper_main(&args[2..]));
    }
    app::run();
}
