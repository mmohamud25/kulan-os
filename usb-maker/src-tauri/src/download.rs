//! Downloading Kulan OS: resumed where it stopped, and hashed as it arrives.

use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

pub const CHUNK: usize = 1 << 20;

pub fn client() -> Result<reqwest::blocking::Client, String> {
    reqwest::blocking::Client::builder()
        .user_agent(concat!("Kulan USB Maker/", env!("CARGO_PKG_VERSION")))
        .connect_timeout(Duration::from_secs(20))
        .timeout(None)
        .build()
        .map_err(|e| e.to_string())
}

/// A small file (SHA256SUMS and its signature), whole.
pub fn fetch_small(client: &reqwest::blocking::Client, url: &str) -> Result<Vec<u8>, String> {
    let response = client.get(url).timeout(Duration::from_secs(60)).send().map_err(net_error)?;
    if !response.status().is_success() {
        return Err(format!("the server said {}", response.status()));
    }
    let bytes = response.bytes().map_err(net_error)?;
    if bytes.len() > 1 << 20 {
        return Err("an unexpectedly large file".into());
    }
    Ok(bytes.to_vec())
}

pub fn net_error(e: reqwest::Error) -> String {
    if e.is_connect() || e.is_timeout() {
        "Kulan OS couldn't be downloaded. Check the internet connection and try again.".into()
    } else {
        format!("Kulan OS couldn't be downloaded: {e}")
    }
}

/// The SHA-256 of a file, with progress (done, total) and a way to stop.
pub fn hash_file(path: &Path, progress: &mut dyn FnMut(u64, u64), stop: &AtomicBool) -> Result<String, String> {
    let mut file = File::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let total = file.metadata().map(|m| m.len()).unwrap_or(0);
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; CHUNK];
    let mut done = 0u64;
    loop {
        if stop.load(Ordering::Relaxed) {
            return Err(crate::STOPPED.into());
        }
        let n = file.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
        done += n as u64;
        progress(done, total);
    }
    Ok(hex::encode(hasher.finalize()))
}

/// Download `url` to `dest`, carrying on from `dest.part` if an earlier one
/// stopped. Returns the SHA-256 of the whole file; the caller compares it.
pub fn download(client: &reqwest::blocking::Client, url: &str, dest: &Path,
                progress: &mut dyn FnMut(u64, u64), stop: &AtomicBool) -> Result<String, String> {
    let part = part_path(dest);
    let mut hasher = Sha256::new();
    let mut have = 0u64;
    if let Ok(mut old) = File::open(&part) {
        // What's already here goes into the hash first.
        let mut buf = vec![0u8; CHUNK];
        loop {
            let n = old.read(&mut buf).map_err(|e| e.to_string())?;
            if n == 0 {
                break;
            }
            hasher.update(&buf[..n]);
            have += n as u64;
        }
    }
    let mut request = client.get(url);
    if have > 0 {
        request = request.header("Range", format!("bytes={have}-"));
    }
    let mut response = request.send().map_err(net_error)?;
    let status = response.status();
    let total;
    let mut out;
    let range = response.headers().get("content-range").and_then(|v| v.to_str().ok()).map(String::from);
    if status.as_u16() == 206 && have > 0 && content_range_start(range.as_deref()) != Some(have) {
        // Not where it stopped: from the start next time.
        let _ = fs::remove_file(&part);
        return Err("The download couldn't carry on where it stopped. Try again.".into());
    }
    if status.as_u16() == 206 && have > 0 {
        total = content_range_total(range.as_deref()).ok_or("the server's answer couldn't be understood")?;
        out = OpenOptions::new().append(true).open(&part).map_err(|e| e.to_string())?;
    } else if status.as_u16() == 416 && have > 0 {
        // Already complete.
        total = have;
        out = OpenOptions::new().append(true).open(&part).map_err(|e| e.to_string())?;
    } else if status.is_success() {
        // From the start (no earlier part, or the server can't resume).
        hasher = Sha256::new();
        have = 0;
        total = response.content_length().unwrap_or(0);
        out = File::create(&part).map_err(|e| format!("{}: {e}", part.display()))?;
    } else {
        return Err(format!("Kulan OS couldn't be downloaded: the server said {status}"));
    }
    if status.as_u16() != 416 {
        let mut buf = vec![0u8; CHUNK];
        loop {
            if stop.load(Ordering::Relaxed) {
                return Err(crate::STOPPED.into());
            }
            let n = response.read(&mut buf).map_err(|e| format!("The download stopped: {e}. Try again to carry on."))?;
            if n == 0 {
                break;
            }
            out.write_all(&buf[..n]).map_err(disk_error)?;
            hasher.update(&buf[..n]);
            have += n as u64;
            progress(have, total.max(have));
        }
    }
    out.sync_all().map_err(disk_error)?;
    if total > 0 && have != total {
        return Err("The download stopped before the end. Try again to carry on.".into());
    }
    fs::rename(&part, dest).map_err(|e| e.to_string())?;
    Ok(hex::encode(hasher.finalize()))
}

pub fn part_path(dest: &Path) -> PathBuf {
    let mut name = dest.file_name().unwrap_or_default().to_os_string();
    name.push(".part");
    dest.with_file_name(name)
}

fn disk_error(e: std::io::Error) -> String {
    if e.raw_os_error() == Some(28) || e.raw_os_error() == Some(112) {
        "There isn't enough free space on this computer for Kulan OS (about 4 GB is needed).".into()
    } else {
        format!("The download couldn't be saved: {e}")
    }
}

/// "bytes 100-199/1000" -> 100
pub fn content_range_start(value: Option<&str>) -> Option<u64> {
    value?.strip_prefix("bytes ")?.split('-').next()?.trim().parse().ok()
}

/// "bytes 100-199/1000" -> 1000
pub fn content_range_total(value: Option<&str>) -> Option<u64> {
    value?.rsplit('/').next()?.trim().parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn content_range() {
        assert_eq!(content_range_total(Some("bytes 100-199/1000")), Some(1000));
        assert_eq!(content_range_total(Some("bytes */1000")), Some(1000));
        assert_eq!(content_range_total(Some("bytes 1-2/*")), None);
        assert_eq!(content_range_total(None), None);
        assert_eq!(content_range_start(Some("bytes 100-199/1000")), Some(100));
        assert_eq!(content_range_start(Some("bytes */1000")), None);
    }

    #[test]
    fn hashing_a_file() {
        let dir = std::env::temp_dir().join(format!("kum-hash-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("a");
        fs::write(&path, b"abc").unwrap();
        let mut seen = 0;
        let hash = hash_file(&path, &mut |d, _t| seen = d, &AtomicBool::new(false)).unwrap();
        assert_eq!(hash, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        assert_eq!(seen, 3);
        assert!(hash_file(&path, &mut |_, _| {}, &AtomicBool::new(true)).is_err());
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn part_files() {
        assert_eq!(part_path(Path::new("/x/kulan.iso")), Path::new("/x/kulan.iso.part"));
    }
}
