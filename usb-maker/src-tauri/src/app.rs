//! The window: pick the stick, click Make. The work happens on a thread, and
//! the window hears how it's going through "progress" events:
//!   {phase: "release" | "download" | "verify" | "permission" | "prepare" | "write" | "check",
//!    done, total}, then {phase: "done"} or {phase: "error", text}.

use crate::{download, drives, elevate, release, STOPPED};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Emitter, Manager, State};

#[derive(Default)]
struct Job {
    running: bool,
    stop: Arc<AtomicBool>,
    stop_file: Option<PathBuf>,
}

#[derive(Default)]
struct Jobs(Mutex<Job>);

#[derive(Deserialize, Debug)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum Source {
    /// The newest Kulan OS: "amd64" (PCs) or "arm64".
    Download { arch: String },
    /// An .iso already on this computer.
    File { path: String },
}

#[derive(Serialize, Clone)]
struct Progress {
    phase: &'static str,
    done: u64,
    total: u64,
    text: String,
}

fn emit(app: &AppHandle, phase: &'static str, done: u64, total: u64, text: &str) {
    let _ = app.emit("progress", Progress { phase, done, total, text: text.to_string() });
}

/// The USB sticks, each with the fingerprint the window hands back to Make.
#[tauri::command]
fn list_drives() -> Result<Vec<serde_json::Value>, String> {
    Ok(drives::list()?.into_iter().map(|d| {
        let mut v = serde_json::to_value(&d).unwrap_or_default();
        v["fingerprint"] = json!(d.fingerprint());
        v
    }).collect())
}

#[tauri::command]
fn start(app: AppHandle, jobs: State<Jobs>, source: Source, drive: String, fingerprint: String,
         organisation: Option<serde_json::Value>) -> Result<(), String> {
    let mut job = jobs.0.lock().map_err(|e| e.to_string())?;
    if job.running {
        return Err("Already making a stick".into());
    }
    drives::find(&drive, Some(&fingerprint))?;
    let work = std::env::temp_dir().join(format!("kulan-usb-maker-{}-{}", std::process::id(),
                                                 std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
                                                     .map(|d| d.as_millis()).unwrap_or(0)));
    std::fs::create_dir_all(&work).map_err(|e| e.to_string())?;
    let stop = Arc::new(AtomicBool::new(false));
    *job = Job { running: true, stop: stop.clone(), stop_file: Some(work.join("stop")) };
    drop(job);
    std::thread::spawn(move || {
        let result = make(&app, &source, &drive, &fingerprint, organisation.as_ref(), &work, &stop);
        match result {
            Ok(()) => emit(&app, "done", 0, 0, ""),
            Err(e) if e == STOPPED => emit(&app, "stopped", 0, 0, ""),
            Err(e) => emit(&app, "error", 0, 0, &e),
        }
        let _ = std::fs::remove_dir_all(&work);
        if let Ok(mut job) = app.state::<Jobs>().0.lock() {
            job.running = false;
            job.stop_file = None;
        }
    });
    Ok(())
}

#[tauri::command]
fn stop(jobs: State<Jobs>) {
    if let Ok(job) = jobs.0.lock() {
        job.stop.store(true, Ordering::Relaxed);
        if let Some(f) = &job.stop_file {
            let _ = std::fs::write(f, b"stop");
        }
    }
}

fn make(app: &AppHandle, source: &Source, drive: &str, fingerprint: &str, organisation: Option<&serde_json::Value>,
        work: &Path, stop: &AtomicBool) -> Result<(), String> {
    let (image, sha256) = match source {
        Source::Download { arch } => get_release(arch, &mut |phase, d, t| emit(app, phase, d, t, ""), stop)?,
        Source::File { path } => {
            let path = PathBuf::from(path);
            check_iso(&path)?;
            let sha = download::hash_file(&path, &mut |d, t| emit(app, "verify", d, t, ""), stop)?;
            (path, sha)
        }
    };
    let mut args = vec![image.to_string_lossy().into_owned(), sha256, drive.to_string(), fingerprint.to_string(),
                        work.join("status").to_string_lossy().into_owned(), work.join("stop").to_string_lossy().into_owned()];
    if let Some(org) = organisation {
        // Tied to this Kulan OS: a stick later written over with another
        // (by another app) doesn't pass on these settings.
        let mut org = org.clone();
        org["image"] = json!(iso_identity(&image)?);
        let file = work.join("organisation.json");
        std::fs::write(&file, serde_json::to_vec(&org).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
        args.push(file.to_string_lossy().into_owned());
    }
    if stop.load(Ordering::Relaxed) {
        return Err(STOPPED.into());
    }
    emit(app, "permission", 0, 0, "");
    // The helper writes its status to a file: passed on as events while it runs.
    let finished = Arc::new(AtomicBool::new(false));
    let watcher = {
        let (app, status, finished) = (app.clone(), work.join("status"), finished.clone());
        std::thread::spawn(move || {
            let mut last = String::new();
            while !finished.load(Ordering::Relaxed) {
                if let Ok(text) = std::fs::read_to_string(&status) {
                    if text != last {
                        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                            let phase = match v.get("phase").and_then(|p| p.as_str()) {
                                Some("prepare") => "prepare",
                                Some("write") => "write",
                                Some("check") => "check",
                                _ => "",
                            };
                            if !phase.is_empty() {
                                emit(&app, phase, v["done"].as_u64().unwrap_or(0), v["total"].as_u64().unwrap_or(0), "");
                            }
                        }
                        last = text;
                    }
                }
                std::thread::sleep(std::time::Duration::from_millis(150));
            }
        })
    };
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let code = elevate::run_helper(&exe, &args);
    finished.store(true, Ordering::Relaxed);
    let _ = watcher.join();
    let code = code?;
    let status: serde_json::Value = std::fs::read_to_string(work.join("status")).ok()
        .and_then(|t| serde_json::from_str(&t).ok()).unwrap_or(json!({}));
    if code == 0 && status.get("finished").is_some() {
        Ok(())
    } else if code == 3 || stop.load(Ordering::Relaxed) {
        Err(STOPPED.into())
    } else {
        Err(status.get("error").and_then(|e| e.as_str()).map(String::from)
            .unwrap_or_else(|| "Writing the USB stick stopped unexpectedly.".into()))
    }
}

/// The newest Kulan OS, downloaded (or kept from before) and checked against
/// the signed SHA256SUMS.
/// The newest Kulan OS and its (verified) SHA-256.
pub fn get_release(arch: &str, progress: &mut dyn FnMut(&'static str, u64, u64), stop: &AtomicBool)
                   -> Result<(PathBuf, String), String> {
    if arch != "amd64" && arch != "arm64" {
        return Err("Unknown kind of computer".into());
    }
    progress("release", 0, 0);
    let client = download::client()?;
    let sums = download::fetch_small(&client, &format!("{}SHA256SUMS", release::BASE_URL))?;
    let sig = download::fetch_small(&client, &format!("{}SHA256SUMS.sig", release::BASE_URL))?;
    release::verify_signature(&sums, &sig).map_err(|e| format!("Kulan OS's download couldn't be trusted: {e}."))?;
    let name = release::iso_name(arch);
    let expected = release::expected_hash(&String::from_utf8_lossy(&sums), &name)
        .ok_or("The newest release has no Kulan OS for this kind of computer.")?;
    let cache = dirs::cache_dir().ok_or("No folder to download to")?.join("Kulan USB Maker");
    std::fs::create_dir_all(&cache).map_err(|e| e.to_string())?;
    let file = cache.join(format!("kulan-os-{arch}-{}.iso", &expected[..12]));
    // Older downloads are removed: only the newest is kept.
    if let Ok(entries) = std::fs::read_dir(&cache) {
        for e in entries.flatten() {
            let n = e.file_name().to_string_lossy().into_owned();
            if n.starts_with(&format!("kulan-os-{arch}-")) && !n.starts_with(&file.file_name().unwrap().to_string_lossy().into_owned()) {
                let _ = std::fs::remove_file(e.path());
            }
        }
    }
    if file.exists() {
        let have = download::hash_file(&file, &mut |d, t| progress("verify", d, t), stop)?;
        if have == expected {
            return Ok((file, expected));
        }
        let _ = std::fs::remove_file(&file);
    }
    let got = download::download(&client, &format!("{}{}", release::BASE_URL, name), &file,
                                 &mut |d, t| progress("download", d, t), stop)?;
    if got != expected {
        let _ = std::fs::remove_file(&file);
        return Err("The download was damaged on the way. Try again.".into());
    }
    Ok((file, expected))
}

/// An ISO 9660 image's volume name and creation time (its primary volume
/// descriptor): what the installer compares with the stick it starts from.
pub fn iso_identity(path: &Path) -> Result<serde_json::Value, String> {
    use std::io::{Read, Seek, SeekFrom};
    let mut f = std::fs::File::open(path).map_err(|e| e.to_string())?;
    let mut pvd = [0u8; 2048];
    f.seek(SeekFrom::Start(16 * 2048)).and_then(|_| f.read_exact(&mut pvd)).map_err(|_| not_iso())?;
    if &pvd[1..6] != b"CD001" {
        return Err(not_iso());
    }
    let text = |r: std::ops::Range<usize>| String::from_utf8_lossy(&pvd[r]).trim_end_matches([' ', '\0']).to_string();
    Ok(json!({"volume": text(40..72), "created": text(813..829)}))
}

/// An .iso someone picked: a real ISO 9660 image, of a sensible size.
pub fn check_iso(path: &Path) -> Result<(), String> {
    use std::io::{Read, Seek, SeekFrom};
    let mut f = std::fs::File::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let len = f.metadata().map_err(|e| e.to_string())?.len();
    let mut magic = [0u8; 5];
    f.seek(SeekFrom::Start(0x8001)).and_then(|_| f.read_exact(&mut magic)).map_err(|_| not_iso())?;
    if &magic != b"CD001" || len < 100 << 20 {
        return Err(not_iso());
    }
    Ok(())
}

fn not_iso() -> String {
    "That file isn't a Kulan OS .iso file.".into()
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(Jobs::default())
        .invoke_handler(tauri::generate_handler![list_drives, start, stop])
        .run(tauri::generate_context!())
        .expect("Kulan USB Maker couldn't start");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sources_from_the_window() {
        let s: Source = serde_json::from_str(r#"{"kind":"download","arch":"amd64"}"#).unwrap();
        assert!(matches!(s, Source::Download { arch } if arch == "amd64"));
        let s: Source = serde_json::from_str(r#"{"kind":"file","path":"/x.iso"}"#).unwrap();
        assert!(matches!(s, Source::File { path } if path == "/x.iso"));
    }

    /// The real thing: the newest Kulan OS downloaded (about 2 GB), checked
    /// against the signed SHA256SUMS, then found again without downloading.
    ///   cargo test -- --ignored the_newest_release
    #[test]
    #[ignore]
    fn the_newest_release() {
        let stop = AtomicBool::new(false);
        let mut phases = vec![];
        let (file, sha) = get_release("amd64", &mut |p, _d, _t| if phases.last() != Some(&p) { phases.push(p) }, &stop).unwrap();
        assert_eq!(sha.len(), 64);
        println!("{}", iso_identity(&file).unwrap());
        assert!(phases.contains(&"download") || phases.contains(&"verify"), "{phases:?}");
        check_iso(&file).unwrap();
        let mut again = vec![];
        let (same, _) = get_release("amd64", &mut |p, _d, _t| if again.last() != Some(&p) { again.push(p) }, &stop).unwrap();
        assert_eq!(same, file);
        assert_eq!(again, vec!["release", "verify"], "kept from before, checked, not downloaded again");
        println!("{}", file.display());
    }

    #[test]
    fn only_iso_files() {
        let dir = std::env::temp_dir().join(format!("kum-iso-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let small = dir.join("small.iso");
        std::fs::write(&small, b"hello").unwrap();
        assert!(check_iso(&small).is_err());
        let fake = dir.join("fake.iso");
        let f = std::fs::File::create(&fake).unwrap();
        f.set_len(200 << 20).unwrap();
        assert!(check_iso(&fake).is_err());
        {
            use std::io::{Seek, SeekFrom, Write};
            let mut f = std::fs::OpenOptions::new().write(true).open(&fake).unwrap();
            f.seek(SeekFrom::Start(0x8001)).unwrap();
            f.write_all(b"CD001").unwrap();
        }
        assert!(check_iso(&fake).is_ok());
        {
            use std::io::{Seek, SeekFrom, Write};
            let mut f = std::fs::OpenOptions::new().write(true).open(&fake).unwrap();
            f.seek(SeekFrom::Start(0x8000 + 40)).unwrap();
            f.write_all(b"Kulan OS 2.10 amd64             ").unwrap();
            f.seek(SeekFrom::Start(0x8000 + 813)).unwrap();
            f.write_all(b"2026102712000000\0").unwrap();
        }
        assert_eq!(iso_identity(&fake).unwrap(), json!({"volume": "Kulan OS 2.10 amd64", "created": "2026102712000000"}));
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
