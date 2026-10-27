//! The part that runs as an administrator: it writes Kulan OS to the stick,
//! reads it back to check it, and, for an organisation's stick, adds its
//! settings after Kulan OS.
//!
//!   kulan-usb-maker --write-helper IMAGE DRIVE_ID STATUS_FILE STOP_FILE [CONFIG_FILE]
//!
//! It checks again that DRIVE_ID is a USB drive (never the computer's own
//! disk) and big enough. It writes everything but the first megabyte, then
//! the first megabyte last: until then the stick has no partition table, so
//! Windows and macOS don't mount half-written partitions. STATUS_FILE gets
//! one line of JSON at a time: {"phase","done","total"} or {"error"} or
//! {"finished":true}. If STOP_FILE appears, it stops.

use crate::drives;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::Path;
use std::time::Instant;

const MIB: u64 = 1 << 20;
const BLOCK: usize = 4 * MIB as usize;
/// After Kulan OS, this much is cleared (and the settings go at its start).
pub const CONFIG_AREA: u64 = 64 * MIB;
pub const CONFIG_MAGIC: &[u8] = b"KULAN-STICK-CONFIG 1\n";
pub const CONFIG_SIZE: usize = 4096;

struct Status<'a> {
    path: &'a Path,
    last: Instant,
}

impl Status<'_> {
    fn write(&mut self, value: serde_json::Value, force: bool) {
        if !force && self.last.elapsed().as_millis() < 200 {
            return;
        }
        self.last = Instant::now();
        // A new file, renamed into place, so a half-written line is never read.
        let tmp = self.path.with_extension("tmp");
        if std::fs::write(&tmp, format!("{value}\n")).is_ok() {
            let _ = std::fs::rename(&tmp, self.path);
        }
    }
}

pub fn helper_main(args: &[String]) -> i32 {
    if args.len() < 4 {
        eprintln!("usage: --write-helper IMAGE DRIVE_ID STATUS_FILE STOP_FILE [CONFIG_FILE]");
        return 2;
    }
    let mut status = Status { path: Path::new(&args[2]), last: Instant::now() };
    match write(Path::new(&args[0]), &args[1], &mut status, Path::new(&args[3]), args.get(4).map(Path::new)) {
        Ok(()) => {
            status.write(json!({"finished": true}), true);
            0
        }
        Err(e) => {
            status.write(json!({"error": e}), true);
            if e == crate::STOPPED { 3 } else { 1 }
        }
    }
}

/// The 4 KiB block an organisation's stick carries after Kulan OS: a line
/// saying what it is, the SHA-256 of the settings, the settings (JSON).
pub fn config_block(settings: &[u8]) -> Result<Vec<u8>, String> {
    let parsed: serde_json::Value = serde_json::from_slice(settings).map_err(|_| "the stick's settings aren't valid")?;
    let compact = serde_json::to_vec(&parsed).map_err(|e| e.to_string())?;
    let mut block = Vec::with_capacity(CONFIG_SIZE);
    block.extend_from_slice(CONFIG_MAGIC);
    block.extend_from_slice(hex::encode(Sha256::digest(&compact)).as_bytes());
    block.push(b'\n');
    block.extend_from_slice(&compact);
    block.push(b'\n');
    if block.len() > CONFIG_SIZE {
        return Err("the stick's settings are too long".into());
    }
    block.resize(CONFIG_SIZE, 0);
    Ok(block)
}

pub fn config_offset(image_len: u64) -> u64 {
    image_len.div_ceil(MIB) * MIB
}

fn write(image: &Path, drive_id: &str, status: &mut Status, stop: &Path, config: Option<&Path>) -> Result<(), String> {
    let drive = drives::find(drive_id)?;
    let mut source = File::open(image).map_err(|e| format!("{}: {e}", image.display()))?;
    let len = source.metadata().map_err(|e| e.to_string())?.len();
    let block = match config {
        Some(p) => Some(config_block(&std::fs::read(p).map_err(|e| e.to_string())?)?),
        None => None,
    };
    let needed = config_offset(len) + CONFIG_AREA;
    if drive.size < needed {
        return Err(format!("The USB stick is too small. Kulan OS needs one of at least {} GB.", needed.div_ceil(1_000_000_000)));
    }
    status.write(json!({"phase": "prepare", "done": 0, "total": len}), true);
    platform::prepare(&drive)?;
    let mut target = platform::open(&drive)?;

    // Everything but the first megabyte, then that.
    let first = len.min(MIB);
    let mut head = vec![0u8; first as usize];
    source.read_exact(&mut head).map_err(|e| e.to_string())?;
    let mut hasher = Sha256::new();
    hasher.update(&head);
    target.seek(SeekFrom::Start(first)).map_err(|e| e.to_string())?;
    let mut aligned = Aligned::new(BLOCK);
    let buf = aligned.slice();
    let mut done = first;
    while done < len {
        if stop.exists() {
            return Err(crate::STOPPED.into());
        }
        let n = read_full(&mut source, buf)?;
        if n == 0 {
            return Err("Kulan OS's file ended early".into());
        }
        hasher.update(&buf[..n]);
        write_padded(&mut target, &buf[..n])?;
        done += n as u64;
        status.write(json!({"phase": "write", "done": done, "total": len}), false);
    }
    let image_hash = hasher.finalize();
    // The organisation's settings (or nothing), with what an earlier stick had there cleared.
    let offset = config_offset(len);
    target.seek(SeekFrom::Start(offset)).map_err(|e| e.to_string())?;
    let zeros = vec![0u8; BLOCK];
    let mut cleared = 0;
    while cleared < CONFIG_AREA {
        target.write_all(&zeros).map_err(write_error)?;
        cleared += BLOCK as u64;
    }
    if let Some(block) = &block {
        target.seek(SeekFrom::Start(offset)).map_err(|e| e.to_string())?;
        target.write_all(block).map_err(write_error)?;
    }
    target.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    write_padded(&mut target, &head)?;
    target.flush().map_err(write_error)?;
    target.sync_all().map_err(write_error)?;
    drop(target);

    // Read it all back and compare.
    status.write(json!({"phase": "check", "done": 0, "total": len}), true);
    let mut target = platform::open_read(&drive)?;
    let mut hasher = Sha256::new();
    let mut done = 0u64;
    while done < len {
        if stop.exists() {
            return Err(crate::STOPPED.into());
        }
        let want = BLOCK.min((len - done).div_ceil(4096) as usize * 4096);
        let n = read_full(&mut target, &mut buf[..want])?;
        if n == 0 {
            return Err("The USB stick couldn't be read back.".into());
        }
        let used = n.min((len - done) as usize);
        hasher.update(&buf[..used]);
        done += used as u64;
        status.write(json!({"phase": "check", "done": done, "total": len}), false);
    }
    if hasher.finalize() != image_hash {
        return Err("The USB stick didn't keep what was written. Try another stick.".into());
    }
    if let Some(block) = &block {
        target.seek(SeekFrom::Start(offset)).map_err(|e| e.to_string())?;
        let mut back = vec![0u8; CONFIG_SIZE];
        target.read_exact(&mut back).map_err(|e| e.to_string())?;
        if &back != block {
            return Err("The USB stick didn't keep the organisation's settings. Try another stick.".into());
        }
    }
    drop(target);
    platform::finish(&drive);
    Ok(())
}

/// A buffer starting on a 4 KiB boundary: Windows reads straight from the
/// stick (without its cache) only into such memory.
struct Aligned {
    raw: Vec<u8>,
    start: usize,
    len: usize,
}

impl Aligned {
    fn new(len: usize) -> Self {
        let raw = vec![0u8; len + 4096];
        let start = raw.as_ptr().align_offset(4096);
        Aligned { raw, start, len }
    }
    fn slice(&mut self) -> &mut [u8] {
        &mut self.raw[self.start..self.start + self.len]
    }
}

fn read_full(r: &mut impl Read, buf: &mut [u8]) -> Result<usize, String> {
    let mut n = 0;
    while n < buf.len() {
        match r.read(&mut buf[n..]) {
            Ok(0) => break,
            Ok(k) => n += k,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
            Err(e) => return Err(format!("Reading failed: {e}")),
        }
    }
    Ok(n)
}

/// Raw disks take whole sectors: the last piece is padded with zeros.
fn write_padded(target: &mut File, data: &[u8]) -> Result<(), String> {
    if data.len() % 4096 == 0 {
        return target.write_all(data).map_err(write_error);
    }
    let mut padded = data.to_vec();
    padded.resize(data.len().div_ceil(4096) * 4096, 0);
    target.write_all(&padded).map_err(write_error)
}

fn write_error(e: std::io::Error) -> String {
    format!("Writing to the USB stick failed ({e}). Unplug it, plug it in again and try again, or try another stick.")
}

#[cfg(target_os = "macos")]
mod platform {
    use super::*;
    pub fn prepare(drive: &drives::Drive) -> Result<(), String> {
        let out = std::process::Command::new("diskutil").args(["unmountDisk", "force", &format!("/dev/{}", drive.id)])
            .output().map_err(|e| e.to_string())?;
        if !out.status.success() {
            return Err("The USB stick couldn't be unmounted. Close any windows showing it and try again.".into());
        }
        Ok(())
    }
    pub fn open(drive: &drives::Drive) -> Result<File, String> {
        OpenOptions::new().read(true).write(true).open(&drive.path).map_err(|e| format!("{}: {e}", drive.path))
    }
    pub fn open_read(drive: &drives::Drive) -> Result<File, String> {
        File::open(&drive.path).map_err(|e| format!("{}: {e}", drive.path))
    }
    pub fn finish(drive: &drives::Drive) {
        let _ = std::process::Command::new("diskutil").args(["eject", &format!("/dev/{}", drive.id)]).output();
    }
}

#[cfg(windows)]
mod platform {
    use super::*;
    use std::os::windows::process::CommandExt;
    pub fn prepare(drive: &drives::Drive) -> Result<(), String> {
        // Remove its partitions, so Windows lets go of them (and mounts none while writing).
        let script = std::env::temp_dir().join(format!("kulan-usb-maker-{}.txt", std::process::id()));
        std::fs::write(&script, format!("select disk {}\r\nclean\r\nrescan\r\n", drive.id)).map_err(|e| e.to_string())?;
        let out = std::process::Command::new("diskpart").arg("/s").arg(&script).creation_flags(0x0800_0000)
            .output().map_err(|e| e.to_string());
        let _ = std::fs::remove_file(&script);
        let out = out?;
        if !out.status.success() {
            return Err(format!("The USB stick couldn't be prepared ({}). Close any windows showing it and try again.",
                               String::from_utf8_lossy(&out.stdout).lines().last().unwrap_or("").trim()));
        }
        std::thread::sleep(std::time::Duration::from_secs(2));
        Ok(())
    }
    pub fn open(drive: &drives::Drive) -> Result<File, String> {
        use std::os::windows::fs::OpenOptionsExt;
        OpenOptions::new().read(true).write(true).share_mode(3).custom_flags(0x8000_0000) // FILE_FLAG_WRITE_THROUGH
            .open(&drive.path).map_err(|e| format!("The USB stick couldn't be opened: {e}"))
    }
    pub fn open_read(drive: &drives::Drive) -> Result<File, String> {
        use std::os::windows::fs::OpenOptionsExt;
        OpenOptions::new().read(true).share_mode(3).custom_flags(0x2000_0000) // FILE_FLAG_NO_BUFFERING: from the stick itself
            .open(&drive.path).map_err(|e| format!("The USB stick couldn't be read back: {e}"))
    }
    pub fn finish(_drive: &drives::Drive) {}
}

#[cfg(target_os = "linux")]
mod platform {
    use super::*;
    pub fn prepare(drive: &drives::Drive) -> Result<(), String> {
        // Unmount anything mounted from it.
        if let Ok(mounts) = std::fs::read_to_string("/proc/mounts") {
            for line in mounts.lines() {
                let dev = line.split_whitespace().next().unwrap_or("");
                if dev.starts_with(&drive.path) {
                    let _ = std::process::Command::new("umount").arg(dev).output();
                }
            }
        }
        Ok(())
    }
    pub fn open(drive: &drives::Drive) -> Result<File, String> {
        OpenOptions::new().read(true).write(true).open(&drive.path).map_err(|e| format!("{}: {e}", drive.path))
    }
    pub fn open_read(drive: &drives::Drive) -> Result<File, String> {
        let _ = std::process::Command::new("blockdev").args(["--flushbufs", &drive.path]).output();
        File::open(&drive.path).map_err(|e| format!("{}: {e}", drive.path))
    }
    pub fn finish(_drive: &drives::Drive) {}
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn config_blocks() {
        let block = config_block(br#"{ "organisation": "Kulan",  "join_code": "ABCD-EFGH" }"#).unwrap();
        assert_eq!(block.len(), CONFIG_SIZE);
        assert!(block.starts_with(CONFIG_MAGIC));
        let text = String::from_utf8_lossy(&block);
        let mut lines = text.lines();
        lines.next();
        let hash = lines.next().unwrap();
        let body = lines.next().unwrap();
        assert_eq!(body, r#"{"join_code":"ABCD-EFGH","organisation":"Kulan"}"#);
        assert_eq!(hash, hex::encode(Sha256::digest(body.as_bytes())));
        assert!(config_block(b"not json").is_err());
        assert!(config_block(format!("{{\"x\":\"{}\"}}", "a".repeat(5000)).as_bytes()).is_err());
    }

    #[test]
    fn offsets() {
        assert_eq!(config_offset(MIB), MIB);
        assert_eq!(config_offset(MIB + 1), 2 * MIB);
        assert_eq!(config_offset(3_000_000_000), 2862 * MIB);
    }
}
