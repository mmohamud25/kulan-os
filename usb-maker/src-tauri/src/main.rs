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
    if args.get(1).map(String::as_str) == Some("--write-helper") {
        std::process::exit(writer::helper_main(&args[2..]));
    }
    app::run();
}
