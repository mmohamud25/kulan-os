//! USB sticks plugged into this computer. Only drives on USB (or an SD card
//! reader) are ever listed: never the computer's own disks.

use serde::Serialize;

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct Drive {
    /// What the helper is given: "disk4" (Mac), "2" (Windows), "/dev/sdb" (Linux).
    pub id: String,
    /// The device written to: /dev/rdisk4, \\.\PhysicalDrive2, /dev/sdb.
    pub path: String,
    pub name: String,
    pub size: u64,
    /// Its serial number where the system says it (Windows, Linux), else "".
    pub serial: String,
}

impl Drive {
    /// What the person saw and agreed to erase: the helper writes only if the
    /// drive behind the id still matches (an id can pass to another drive
    /// when sticks are swapped).
    pub fn fingerprint(&self) -> String {
        format!("{}|{}|{}", self.name, self.size, self.serial)
    }
}

pub fn list() -> Result<Vec<Drive>, String> {
    let mut drives = platform::list()?;
    // Built for tests only (the test-devices feature): stand-ins for a USB
    // stick (a RAM disk, a virtual disk, a loop device), named in
    // KULAN_USB_MAKER_TEST_DEVICES. The apps people get never have this.
    #[cfg(feature = "test-devices")]
    for id in std::env::var("KULAN_USB_MAKER_TEST_DEVICES").unwrap_or_default().split(',').map(str::trim) {
        if !id.is_empty() && !drives.iter().any(|d| d.id == id) {
            if let Some(d) = platform::test_drive(id) {
                drives.push(d);
            }
        }
    }
    drives.sort_by(|a, b| a.id.cmp(&b.id));
    Ok(drives)
}

/// The drive with this id, if it's still plugged in (and still a USB drive),
/// and, with a fingerprint, still the same drive.
pub fn find(id: &str, fingerprint: Option<&str>) -> Result<Drive, String> {
    let drive = list()?
        .into_iter()
        .find(|d| d.id == id)
        .ok_or_else(|| "The USB stick isn't plugged in any more.".to_string())?;
    if fingerprint.is_some_and(|f| f != drive.fingerprint()) {
        return Err("The USB stick was changed for another drive. Nothing was written. Choose it again.".into());
    }
    Ok(drive)
}

fn run(cmd: &str, args: &[&str]) -> Result<String, String> {
    let mut command = std::process::Command::new(cmd);
    command.args(args);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: no console flashing up
    }
    let out = command.output().map_err(|e| format!("{cmd}: {e}"))?;
    if !out.status.success() {
        return Err(format!("{cmd}: {}", String::from_utf8_lossy(&out.stderr).trim()));
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

// --- Mac -----------------------------------------------------------------------------------
/// From `diskutil info -plist diskN`: a USB or SD card drive, never internal or virtual.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))] // (tested on every system)
pub fn mac_drive(id: &str, info: &plist::Value) -> Option<Drive> {
    let dict = info.as_dictionary()?;
    let text = |k: &str| dict.get(k).and_then(|v| v.as_string()).unwrap_or("").to_string();
    let flag = |k: &str| dict.get(k).and_then(|v| v.as_boolean());
    let size = ["TotalSize", "Size"].iter().find_map(|k| dict.get(*k).and_then(|v| v.as_unsigned_integer()))?;
    let bus = text("BusProtocol");
    if flag("Internal") != Some(false) || text("VirtualOrPhysical") == "Virtual"
        || !(bus == "USB" || bus == "Secure Digital") || flag("WholeDisk") == Some(false) || size == 0 {
        return None;
    }
    let name = [text("MediaName"), text("IORegistryEntryName")].into_iter().find(|s| !s.is_empty())
        .unwrap_or_else(|| "USB drive".into());
    Some(Drive { id: id.into(), path: format!("/dev/r{id}"), name: name.trim().into(), size, serial: String::new() })
}

/// The whole disks the Mac is running from, from `diskutil info -plist /`:
/// never listed, even when it's a USB drive (a Mac started from an external disk).
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn mac_boot_disks(info: &plist::Value) -> Vec<String> {
    let whole = |s: &str| {
        let digits: String = s.strip_prefix("disk").unwrap_or("").chars().take_while(|c| c.is_ascii_digit()).collect();
        (!digits.is_empty()).then(|| format!("disk{digits}"))
    };
    let mut found = vec![];
    let Some(dict) = info.as_dictionary() else { return found };
    for key in ["ParentWholeDisk", "DeviceIdentifier"] {
        if let Some(d) = dict.get(key).and_then(|v| v.as_string()).and_then(whole) {
            found.push(d);
        }
    }
    if let Some(stores) = dict.get("APFSPhysicalStores").and_then(|v| v.as_array()) {
        for store in stores {
            if let Some(d) = store.as_dictionary().and_then(|d| d.get("APFSPhysicalStore")).and_then(|v| v.as_string()).and_then(whole) {
                found.push(d);
            }
        }
    }
    found
}

/// Whole disks named in `diskutil list -plist external physical`.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))] // (tested on every system)
pub fn mac_whole_disks(list: &plist::Value) -> Vec<String> {
    list.as_dictionary()
        .and_then(|d| d.get("WholeDisks"))
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|v| v.as_string()).filter(|s| is_mac_disk_id(s)).map(String::from).collect())
        .unwrap_or_default()
}

#[cfg_attr(not(target_os = "macos"), allow(dead_code))] // (tested on every system)
pub fn is_mac_disk_id(s: &str) -> bool {
    s.strip_prefix("disk").is_some_and(|n| !n.is_empty() && n.len() < 4 && n.chars().all(|c| c.is_ascii_digit()))
}

// --- Windows -------------------------------------------------------------------------------
/// From PowerShell's Get-Disk as JSON (one disk or a list): USB and SD drives
/// that Windows isn't started from.
#[cfg_attr(not(target_os = "windows"), allow(dead_code))] // (tested on every system)
pub fn windows_drives(json: &str) -> Vec<Drive> {
    let value: serde_json::Value = serde_json::from_str(json.trim()).unwrap_or(serde_json::Value::Null);
    let items = match value {
        serde_json::Value::Array(a) => a,
        serde_json::Value::Object(_) => vec![value],
        _ => vec![],
    };
    items.iter().filter_map(|d| {
        let number = d.get("Number")?.as_u64()?;
        let bus = d.get("BusType").map(|b| b.as_str().map(String::from).unwrap_or_else(|| b.to_string()))?;
        // BusType as text ("USB", "SD") or as Windows' number (7 = USB, 12 = SD).
        let usb = matches!(bus.as_str(), "USB" | "SD" | "7" | "12");
        let system = d.get("IsBoot").and_then(|v| v.as_bool()).unwrap_or(true)
            || d.get("IsSystem").and_then(|v| v.as_bool()).unwrap_or(true);
        let size = d.get("Size")?.as_u64()?;
        if !usb || system || size == 0 {
            return None;
        }
        let name = d.get("FriendlyName").and_then(|v| v.as_str()).unwrap_or("USB drive").trim().to_string();
        let serial = [d.get("SerialNumber"), d.get("UniqueId")].iter()
            .filter_map(|v| v.and_then(|v| v.as_str()).map(str::trim)).collect::<Vec<_>>().join("/");
        Some(Drive { id: number.to_string(), path: format!(r"\\.\PhysicalDrive{number}"), name, size, serial })
    }).collect()
}

// --- Linux (for testing; Linux has its own ways) -----------------------------------------
/// From `lsblk -J -b -d -o NAME,PATH,SIZE,TRAN,TYPE,MODEL,VENDOR`: whole USB disks
/// (and the test devices named).
#[cfg_attr(not(target_os = "linux"), allow(dead_code))] // (tested on every system)
pub fn linux_drives(json: &str, test_devices: &[String]) -> Vec<Drive> {
    let value: serde_json::Value = serde_json::from_str(json).unwrap_or(serde_json::Value::Null);
    let empty = vec![];
    let devices = value.get("blockdevices").and_then(|v| v.as_array()).unwrap_or(&empty);
    devices.iter().filter_map(|d| {
        let path = d.get("path")?.as_str()?.to_string();
        let usb = d.get("tran").and_then(|v| v.as_str()) == Some("usb") && d.get("type").and_then(|v| v.as_str()) == Some("disk");
        if !usb && !test_devices.contains(&path) {
            return None;
        }
        let size = d.get("size").and_then(|v| v.as_u64().or_else(|| v.as_str()?.parse().ok()))?;
        let name = [d.get("vendor"), d.get("model")].iter()
            .filter_map(|v| v.and_then(|v| v.as_str()).map(str::trim)).filter(|s| !s.is_empty())
            .collect::<Vec<_>>().join(" ");
        let serial = d.get("serial").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
        Some(Drive { id: path.clone(), path, name: if name.is_empty() { "USB drive".into() } else { name }, size, serial })
    }).filter(|d| d.size > 0).collect()
}

#[cfg(target_os = "macos")]
mod platform {
    use super::*;
    pub fn list() -> Result<Vec<Drive>, String> {
        let out = run("diskutil", &["list", "-plist", "external", "physical"])?;
        let value = plist::Value::from_reader_xml(out.as_bytes()).map_err(|e| e.to_string())?;
        // Without knowing the Mac's own disk, nothing is listed.
        let root = run("diskutil", &["info", "-plist", "/"])?;
        let boot = mac_boot_disks(&plist::Value::from_reader_xml(root.as_bytes()).map_err(|e| e.to_string())?);
        if boot.is_empty() {
            return Err("The Mac's own disk couldn't be found".into());
        }
        Ok(mac_whole_disks(&value).iter().filter(|id| !boot.contains(id)).filter_map(|id| {
            let info = run("diskutil", &["info", "-plist", id]).ok()?;
            mac_drive(id, &plist::Value::from_reader_xml(info.as_bytes()).ok()?)
        }).collect())
    }
    #[cfg(feature = "test-devices")]
    pub fn test_drive(id: &str) -> Option<Drive> {
        let info = run("diskutil", &["info", "-plist", id]).ok()?;
        let value = plist::Value::from_reader_xml(info.as_bytes()).ok()?;
        let size = value.as_dictionary()?.get("TotalSize")?.as_unsigned_integer()?;
        Some(Drive { id: id.into(), path: format!("/dev/r{id}"), name: "Test disk".into(), size, serial: String::new() })
    }
}

/// A program in Windows' own folder, by its full path (never one found in
/// the app's folder or elsewhere on the PATH: the helper runs as an administrator).
#[cfg(windows)]
pub fn system32(program: &str) -> String {
    // Asked of Windows itself, not from the environment (which the person can set).
    let mut buf = [0u16; 260];
    let n = unsafe { windows_sys::Win32::System::SystemInformation::GetSystemDirectoryW(buf.as_mut_ptr(), buf.len() as u32) };
    let dir = if n > 0 && (n as usize) < buf.len() { String::from_utf16_lossy(&buf[..n as usize]) } else { r"C:\Windows\System32".into() };
    format!(r"{dir}\{program}")
}

#[cfg(windows)]
fn powershell() -> String {
    system32(r"WindowsPowerShell\v1.0\powershell.exe")
}

#[cfg(windows)]
mod platform {
    use super::*;
    pub fn list() -> Result<Vec<Drive>, String> {
        let out = run(&powershell(), &["-NoProfile", "-NonInteractive", "-Command",
            "Storage\\Get-Disk | Select-Object Number,FriendlyName,Size,SerialNumber,UniqueId,@{n='BusType';e={[string]$_.BusType}},IsBoot,IsSystem | ConvertTo-Json -Compress"])?;
        Ok(windows_drives(&out))
    }
    #[cfg(feature = "test-devices")]
    pub fn test_drive(id: &str) -> Option<Drive> {
        let n: u32 = id.parse().ok()?;
        let out = run(&powershell(), &["-NoProfile", "-NonInteractive", "-Command",
            &format!("(Storage\\Get-Disk -Number {n}).Size")]).ok()?;
        let size = out.trim().parse().ok()?;
        Some(Drive { id: id.into(), path: format!(r"\\.\PhysicalDrive{n}"), name: "Test disk".into(), size, serial: String::new() })
    }
}

#[cfg(target_os = "linux")]
mod platform {
    use super::*;
    pub fn list() -> Result<Vec<Drive>, String> {
        let out = run("lsblk", &["-J", "-b", "-d", "-o", "NAME,PATH,SIZE,TRAN,TYPE,MODEL,VENDOR,SERIAL"])?;
        Ok(linux_drives(&out, &[]))
    }
    #[cfg(feature = "test-devices")]
    pub fn test_drive(id: &str) -> Option<Drive> {
        let out = run("lsblk", &["-J", "-b", "-d", "-o", "NAME,PATH,SIZE,TRAN,TYPE,MODEL,VENDOR,SERIAL", id]).ok()?;
        let mut d = linux_drives(&out, &[id.to_string()]).pop()?;
        d.name = "Test disk".into();
        Some(d)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mac_usb_stick_listed_internal_not() {
        let usb = r#"<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict>
            <key>BusProtocol</key><string>USB</string><key>Internal</key><false/>
            <key>MediaName</key><string>SanDisk Ultra </string><key>TotalSize</key><integer>32015679488</integer>
            <key>VirtualOrPhysical</key><string>Physical</string><key>WholeDisk</key><true/></dict></plist>"#;
        let value = plist::Value::from_reader_xml(usb.as_bytes()).unwrap();
        assert_eq!(mac_drive("disk4", &value), Some(Drive { id: "disk4".into(), path: "/dev/rdisk4".into(),
            name: "SanDisk Ultra".into(), size: 32015679488, serial: String::new() }));
        let internal = usb.replace("<key>Internal</key><false/>", "<key>Internal</key><true/>");
        assert_eq!(mac_drive("disk0", &plist::Value::from_reader_xml(internal.as_bytes()).unwrap()), None);
        let sata = usb.replace(">USB<", ">SATA<");
        assert_eq!(mac_drive("disk2", &plist::Value::from_reader_xml(sata.as_bytes()).unwrap()), None);
        let virt = usb.replace(">Physical<", ">Virtual<");
        assert_eq!(mac_drive("disk5", &plist::Value::from_reader_xml(virt.as_bytes()).unwrap()), None);
        let unknown = usb.replace("<key>Internal</key><false/>", "");
        assert_eq!(mac_drive("disk6", &plist::Value::from_reader_xml(unknown.as_bytes()).unwrap()), None);
    }

    #[test]
    fn mac_disk_ids() {
        let list = r#"<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict><key>WholeDisks</key>
            <array><string>disk4</string><string>disk4s1</string><string>../etc</string></array></dict></plist>"#;
        assert_eq!(mac_whole_disks(&plist::Value::from_reader_xml(list.as_bytes()).unwrap()), vec!["disk4"]);
    }

    #[test]
    fn mac_boot_disks_found() {
        let apfs = r#"<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict>
            <key>DeviceIdentifier</key><string>disk3s1s1</string><key>ParentWholeDisk</key><string>disk3</string>
            <key>APFSPhysicalStores</key><array><dict><key>APFSPhysicalStore</key><string>disk12s2</string></dict></array>
            </dict></plist>"#;
        assert_eq!(mac_boot_disks(&plist::Value::from_reader_xml(apfs.as_bytes()).unwrap()), vec!["disk3", "disk3", "disk12"]);
        assert!(mac_boot_disks(&plist::Value::from_reader_xml(r#"<?xml version="1.0"?><plist version="1.0"><dict/></plist>"#.as_bytes()).unwrap()).is_empty());
    }

    #[test]
    fn fingerprints() {
        let d = Drive { id: "2".into(), path: "p".into(), name: "Stick".into(), size: 8, serial: "S1".into() };
        assert_eq!(d.fingerprint(), "Stick|8|S1");
    }

    #[test]
    fn windows_usb_listed_system_not() {
        let json = r#"[{"Number":0,"FriendlyName":"NVMe SSD","Size":512110190592,"BusType":"NVMe","IsBoot":true,"IsSystem":true},
                       {"Number":2,"FriendlyName":"Kingston DataTraveler","Size":31004295168,"BusType":"USB","IsBoot":false,"IsSystem":false,"SerialNumber":"ABC123","UniqueId":null},
                       {"Number":3,"FriendlyName":"USB boot","Size":31004295168,"BusType":"USB","IsBoot":true,"IsSystem":false},
                       {"Number":4,"FriendlyName":"Card","Size":1000,"BusType":12,"IsBoot":false,"IsSystem":false}]"#;
        let drives = windows_drives(json);
        assert_eq!(drives.iter().map(|d| d.id.as_str()).collect::<Vec<_>>(), vec!["2", "4"]);
        assert_eq!(drives[0].path, r"\\.\PhysicalDrive2");
        assert_eq!(drives[0].serial, "ABC123");
        let one = r#"{"Number":5,"FriendlyName":"Stick","Size":8000,"BusType":"USB","IsBoot":false,"IsSystem":false}"#;
        assert_eq!(windows_drives(one).len(), 1);
        assert!(windows_drives("").is_empty());
        // Unknown boot state: not listed.
        assert!(windows_drives(r#"{"Number":6,"Size":8000,"BusType":"USB"}"#).is_empty());
    }

    #[test]
    fn linux_usb_and_test_devices() {
        let json = r#"{"blockdevices":[{"name":"nvme0n1","path":"/dev/nvme0n1","size":512110190592,"tran":"nvme","type":"disk","model":"SSD","vendor":null},
            {"name":"sdb","path":"/dev/sdb","size":"31004295168","tran":"usb","type":"disk","model":"DataTraveler","vendor":"Kingston "},
            {"name":"loop5","path":"/dev/loop5","size":4294967296,"tran":null,"type":"loop","model":null,"vendor":null}]}"#;
        let drives = linux_drives(json, &[]);
        assert_eq!(drives.len(), 1);
        assert_eq!(drives[0].name, "Kingston DataTraveler");
        assert_eq!(linux_drives(json, &["/dev/loop5".into()]).len(), 2);
    }
}
