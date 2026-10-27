//! The part that runs as an administrator: it writes Kulan OS to the stick,
//! reads it back to check it, and, for an organisation's stick, adds its
//! settings after Kulan OS.
//!
//!   kulan-usb-maker --write-helper IMAGE SHA256 DRIVE_ID FINGERPRINT STATUS_FILE STOP_FILE [CONFIG_FILE]
//!
//! Before writing it checks again that DRIVE_ID is a USB drive (never the
//! computer's own disk), still the one the person agreed to erase
//! (FINGERPRINT: its name, size and serial number), and big enough. It clears
//! the stick's old partition tables (its first and last megabyte), writes
//! everything but Kulan OS's first megabyte, checks that what it wrote has
//! the SHA-256 the app verified (so a file swapped meanwhile is never made
//! startable), then writes that megabyte last: until then the stick has no
//! partition table, so nothing mounts half-written partitions. Then it reads
//! it all back. STATUS_FILE gets one line of JSON at a time:
//! {"phase","done","total"}, {"error"} or {"finished":true}. If STOP_FILE
//! appears, it stops.

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
        // As an administrator, in a folder the person's programs can write to:
        // a new file only (never through a link someone put there), renamed
        // into place (a rename replaces a link, never what it points to).
        let tmp = self.path.with_extension("tmp");
        let _ = std::fs::remove_file(&tmp);
        if let Ok(mut f) = OpenOptions::new().write(true).create_new(true).open(&tmp) {
            if f.write_all(format!("{value}\n").as_bytes()).is_ok() {
                drop(f);
                let _ = std::fs::rename(&tmp, self.path);
            }
        }
    }
}

pub fn helper_main(args: &[String]) -> i32 {
    if args.len() < 6 {
        eprintln!("usage: --write-helper IMAGE SHA256 DRIVE_ID FINGERPRINT STATUS_FILE STOP_FILE [CONFIG_FILE]");
        return 2;
    }
    let mut status = Status { path: Path::new(&args[4]), last: Instant::now() };
    let job = Job { image: Path::new(&args[0]), sha256: &args[1], drive: &args[2], fingerprint: &args[3],
                    stop: Path::new(&args[5]), config: args.get(6).map(Path::new) };
    match write(&job, &mut status) {
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

struct Job<'a> {
    image: &'a Path,
    sha256: &'a str,
    drive: &'a str,
    fingerprint: &'a str,
    stop: &'a Path,
    config: Option<&'a Path>,
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

fn write(job: &Job, status: &mut Status) -> Result<(), String> {
    let stop = job.stop;
    let drive = drives::find(job.drive, Some(job.fingerprint))?;
    let mut source = File::open(job.image).map_err(|e| format!("{}: {e}", job.image.display()))?;
    let len = source.metadata().map_err(|e| e.to_string())?.len();
    let block = match job.config {
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
    let mut aligned = Aligned::new(BLOCK);
    let buf = aligned.slice();

    // The old partition tables (at the start, and GPT's copy at the end) go first.
    let zeros = vec![0u8; MIB as usize];
    for at in [0, (drive.size / MIB - 1) * MIB] {
        target.seek(SeekFrom::Start(at)).map_err(|e| e.to_string())?;
        target.write_all(&zeros).map_err(write_error)?;
    }

    // Everything but the first megabyte, then that.
    let first = len.min(MIB);
    let mut head = vec![0u8; first as usize];
    source.read_exact(&mut head).map_err(|e| e.to_string())?;
    let mut hasher = Sha256::new();
    hasher.update(&head);
    target.seek(SeekFrom::Start(first)).map_err(|e| e.to_string())?;
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
    if !hex::encode(image_hash).eq_ignore_ascii_case(job.sha256) {
        return Err("Kulan OS's file changed after it was checked. Nothing on the USB stick will start. Try again.".into());
    }
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
    match target.sync_all() {
        // macOS's /dev/rdisk can't be asked to (ENOTTY): its writes already went straight to the stick.
        Err(e) if e.raw_os_error() == Some(25) => {}
        other => other.map_err(write_error)?,
    }

    // Read it all back and compare.
    status.write(json!({"phase": "check", "done": 0, "total": len}), true);
    let mut target = platform::for_reading(&drive, target)?;
    target.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
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
        let back = &mut buf[..CONFIG_SIZE];
        read_full(&mut target, back)?;
        if back != &block[..] {
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
        let out = std::process::Command::new("/usr/sbin/diskutil").args(["unmountDisk", "force", &format!("/dev/{}", drive.id)])
            .output().map_err(|e| e.to_string())?;
        if !out.status.success() {
            return Err("The USB stick couldn't be unmounted. Close any windows showing it and try again.".into());
        }
        Ok(())
    }
    pub fn open(drive: &drives::Drive) -> Result<File, String> {
        OpenOptions::new().read(true).write(true).open(&drive.path).map_err(|e| format!("{}: {e}", drive.path))
    }
    /// The same open stick: /dev/rdisk reads come from the stick itself, and
    /// it stays open, so macOS doesn't mount the new partitions mid-check.
    pub fn for_reading(_drive: &drives::Drive, target: File) -> Result<File, String> {
        Ok(target)
    }
    pub fn finish(drive: &drives::Drive) {
        let _ = std::process::Command::new("/usr/sbin/diskutil").args(["eject", &format!("/dev/{}", drive.id)]).output();
    }
}

#[cfg(windows)]
mod platform {
    use super::*;
    use std::os::windows::process::CommandExt;
    pub fn prepare(drive: &drives::Drive) -> Result<(), String> {
        // Remove its partitions, so Windows lets go of them (and mounts none
        // while writing). The script goes to diskpart directly, never through
        // a file another program could change first.
        let number: u32 = drive.id.parse().map_err(|_| "not a disk number")?;
        let mut child = std::process::Command::new(drives::system32("diskpart.exe"))
            .stdin(std::process::Stdio::piped()).stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::piped())
            .creation_flags(0x0800_0000).spawn().map_err(|e| e.to_string())?;
        if let Some(mut stdin) = child.stdin.take() {
            let _ = stdin.write_all(format!("select disk {number}\r\nclean\r\nrescan\r\nexit\r\n").as_bytes());
        }
        let out = child.wait_with_output().map_err(|e| e.to_string())?;
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
    /// Opened again without Windows' cache, so it really comes from the stick.
    pub fn for_reading(drive: &drives::Drive, target: File) -> Result<File, String> {
        use std::os::windows::fs::OpenOptionsExt;
        drop(target);
        OpenOptions::new().read(true).share_mode(3).custom_flags(0x2000_0000) // FILE_FLAG_NO_BUFFERING
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
    pub fn for_reading(drive: &drives::Drive, target: File) -> Result<File, String> {
        drop(target);
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
