//! Running the writing part as an administrator: macOS asks for the Mac's
//! password, Windows asks "Do you want to allow this app to make changes?".
//! Returns the helper's exit code, or why it couldn't start.

use std::path::Path;

pub const CANCELLED: &str = "You didn't allow Kulan USB Maker to write to the USB stick.";

/// Quoted for a POSIX shell: 'it'\''s'
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn shell_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', r"'\''"))
}

/// Inside an AppleScript string: backslashes and double quotes escaped.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn applescript_string(s: &str) -> String {
    format!("\"{}\"", s.replace('\\', r"\\").replace('"', "\\\""))
}

/// Quoted for a Windows command line (CommandLineToArgvW's rules).
#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
pub fn windows_quote(s: &str) -> String {
    if !s.is_empty() && !s.contains([' ', '\t', '"']) {
        return s.to_string();
    }
    let mut out = String::from("\"");
    let mut slashes = 0;
    for c in s.chars() {
        match c {
            '\\' => slashes += 1,
            '"' => {
                out.push_str(&"\\".repeat(slashes * 2 + 1));
                out.push('"');
                slashes = 0;
            }
            _ => {
                out.push_str(&"\\".repeat(slashes));
                out.push(c);
                slashes = 0;
            }
        }
    }
    out.push_str(&"\\".repeat(slashes * 2));
    out.push('"');
    out
}

#[cfg(target_os = "macos")]
pub fn run_helper(exe: &Path, args: &[String]) -> Result<i32, String> {
    let mut command = shell_quote(&exe.to_string_lossy());
    command.push_str(" --write-helper");
    for a in args {
        command.push(' ');
        command.push_str(&shell_quote(a));
    }
    let script = format!("do shell script {} with administrator privileges with prompt {}",
                         applescript_string(&command),
                         applescript_string("Kulan USB Maker needs your password to write to the USB stick."));
    let out = std::process::Command::new("osascript").args(["-e", &script]).output().map_err(|e| e.to_string())?;
    if out.status.success() {
        return Ok(0);
    }
    let err = String::from_utf8_lossy(&out.stderr);
    if err.contains("-128") {
        return Err(CANCELLED.into());
    }
    // "… (N)" at the end of osascript's message is the helper's exit code.
    let code = err.trim().rsplit('(').next().and_then(|s| s.trim_end_matches(')').parse().ok()).unwrap_or(1);
    Ok(code)
}

#[cfg(windows)]
pub fn run_helper(exe: &Path, args: &[String]) -> Result<i32, String> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, ERROR_CANCELLED};
    use windows_sys::Win32::System::Threading::{GetExitCodeProcess, WaitForSingleObject, INFINITE};
    use windows_sys::Win32::UI::Shell::{ShellExecuteExW, SEE_MASK_NOCLOSEPROCESS, SHELLEXECUTEINFOW};
    let wide = |s: &str| std::ffi::OsStr::new(s).encode_wide().chain(Some(0)).collect::<Vec<u16>>();
    let mut params = String::from("--write-helper");
    for a in args {
        params.push(' ');
        params.push_str(&windows_quote(a));
    }
    let verb = wide("runas");
    let file: Vec<u16> = exe.as_os_str().encode_wide().chain(Some(0)).collect();
    let params = wide(&params);
    unsafe {
        let mut info: SHELLEXECUTEINFOW = std::mem::zeroed();
        info.cbSize = std::mem::size_of::<SHELLEXECUTEINFOW>() as u32;
        info.fMask = SEE_MASK_NOCLOSEPROCESS;
        info.lpVerb = verb.as_ptr();
        info.lpFile = file.as_ptr();
        info.lpParameters = params.as_ptr();
        info.nShow = 0; // SW_HIDE
        if ShellExecuteExW(&mut info) == 0 {
            let error = GetLastError();
            return Err(if error == ERROR_CANCELLED { CANCELLED.into() } else {
                format!("The writing part couldn't start (error {error}).")
            });
        }
        if info.hProcess.is_null() {
            return Err("The writing part couldn't start.".into());
        }
        WaitForSingleObject(info.hProcess, INFINITE);
        let mut code = 1u32;
        GetExitCodeProcess(info.hProcess, &mut code);
        CloseHandle(info.hProcess);
        Ok(code as i32)
    }
}

#[cfg(target_os = "linux")]
pub fn run_helper(exe: &Path, args: &[String]) -> Result<i32, String> {
    // As root already (tests), directly; otherwise through pkexec.
    let root = std::process::Command::new("id").arg("-u").output().map(|o| o.stdout == b"0\n").unwrap_or(false);
    let mut command = if root { std::process::Command::new(exe) } else {
        let mut c = std::process::Command::new("pkexec");
        c.arg(exe);
        c
    };
    command.arg("--write-helper").args(args);
    let status = command.status().map_err(|e| e.to_string())?;
    match status.code() {
        Some(126) | Some(127) if !root => Err(CANCELLED.into()),
        Some(code) => Ok(code),
        None => Err("The writing part stopped unexpectedly.".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quoting() {
        assert_eq!(shell_quote("/Applications/Kulan USB Maker.app"), "'/Applications/Kulan USB Maker.app'");
        assert_eq!(shell_quote("it's"), r"'it'\''s'");
        assert_eq!(applescript_string(r#"a "b" \c"#), r#""a \"b\" \\c""#);
        assert_eq!(windows_quote("C:\\x\\y.iso"), "C:\\x\\y.iso");
        assert_eq!(windows_quote("C:\\My Files\\"), "\"C:\\My Files\\\\\"");
        assert_eq!(windows_quote("a\"b"), "\"a\\\"b\"");
        assert_eq!(windows_quote(""), "\"\"");
    }
}
