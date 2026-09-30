// Prevents an additional console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use serde_json::Value;
use std::path::PathBuf;
use std::process::Command;


/// Raw result of one `halter` sidecar invocation, surfaced to the UI as-is.
#[derive(Debug, Serialize)]
pub struct SidecarOutput {
    pub ok: bool,
    pub code: i32,
    pub stdout: String,
    pub stderr: String,
}

/// Resolve the halter executable:
///   1. `HALTER_BINARY` env override (tests, custom installs)
///   2. a `halter`/`halter-bin` shipped next to the app executable (bundled sidecar)
///   3. plain `halter` on PATH (development fallback)
fn resolve_halter() -> String {
    if let Ok(path) = std::env::var("HALTER_BINARY") {
        if !path.trim().is_empty() {
            return path;
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            // Windows bundles carry the sidecar as halter.exe / halter-bin.exe;
            // extra .exe candidates are harmless on other platforms.
            for name in ["halter", "halter-bin", "halter.exe", "halter-bin.exe"] {
                let candidate: PathBuf = dir.join(name);
                if candidate.is_file() {
                    return candidate.to_string_lossy().into_owned();
                }
            }
        }
    }
    "halter".to_string()
}

fn run_halter(args: &[String]) -> Result<SidecarOutput, String> {
    let program = resolve_halter();
    let output = Command::new(&program)
        .args(args)
        .env("HALTER_UI", "1")
        .env("NO_COLOR", "1")
        .output()
        .map_err(|e| format!("failed to spawn halter sidecar `{program}`: {e}"))?;
    Ok(SidecarOutput {
        ok: output.status.success(),
        code: output.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
    })
}

fn parse_json_output(out: &SidecarOutput) -> Result<Value, String> {
    if !out.ok {
        let detail = if out.stderr.trim().is_empty() {
            out.stdout.trim()
        } else {
            out.stderr.trim()
        };
        return Err(format!("halter exited with code {}: {detail}", out.code));
    }
    serde_json::from_str(out.stdout.trim()).map_err(|e| {
        format!(
            "failed to parse halter JSON output: {e}\n--- stdout ---\n{}",
            out.stdout
        )
    })
}

async fn run_json_args(args: Vec<String>) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || run_halter(&args).and_then(|out| parse_json_output(&out)))
        .await
        .map_err(|e| format!("sidecar task failed: {e}"))?
}

async fn run_text_args(args: Vec<String>) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let out = run_halter(&args)?;
        if out.ok {
            Ok(out.stdout)
        } else {
            let detail = if out.stderr.trim().is_empty() {
                out.stdout.trim().to_string()
            } else {
                out.stderr.trim().to_string()
            };
            Err(format!("halter exited with code {}: {detail}", out.code))
        }
    })
    .await
    .map_err(|e| format!("sidecar task failed: {e}"))?
}

fn arg(parts: &[&str]) -> Vec<String> {
    parts.iter().map(|s| s.to_string()).collect()
}

#[tauri::command]
async fn halter_version() -> Result<Value, String> {
    let mut value = run_json_args(arg(&["version", "--json"])).await?;
    // Attach the desktop-bundled expectation so the UI can flag a stale
    // sidecar without trusting the sidecar's own report alone.
    if let Some(object) = value.as_object_mut() {
        object.insert(
            "desktop".to_string(),
            serde_json::json!(env!("CARGO_PKG_VERSION")),
        );
    }
    Ok(value)
}

#[tauri::command]
async fn halter_scan() -> Result<Value, String> {
    run_json_args(arg(&["scan", "--json"])).await
}

/// Full memory snapshot: library content + per-tool content and unified diffs.
#[tauri::command]
async fn halter_memory_show() -> Result<Value, String> {
    run_json_args(arg(&["memory", "show", "--json"])).await
}

/// Open a file with the system default program (Memory panel's open button).
/// Safety constraint: only files inside the user's home directory, and only
/// if they exist. Openers are tried in order (WSL: xdg-open via WSLg, then
/// wslview / explorer.exe).
fn open_path_sync(raw: &str) -> Result<(), String> {
    let home = if cfg!(windows) {
        std::env::var("USERPROFILE").map_err(|_| "cannot resolve home dir".to_string())?
    } else {
        std::env::var("HOME").map_err(|_| "cannot resolve home dir".to_string())?
    };
    let resolved = std::path::PathBuf::from(raw)
        .canonicalize()
        .map_err(|e| format!("file not found: {raw} ({e})"))?;
    // Windows canonicalize yields \\?\-prefixed paths; strip for the prefix check.
    let resolved_str = resolved.to_string_lossy().trim_start_matches(r"\\?\").to_string();
    if resolved_str != home && !resolved_str.starts_with(&format!("{home}{}", std::path::MAIN_SEPARATOR)) {
        return Err(format!("refusing to open outside home: {resolved_str}"));
    }
    let candidates: Vec<Vec<&str>> = if cfg!(target_os = "macos") {
        vec![vec!["open"]]
    } else if cfg!(windows) {
        vec![vec!["cmd", "/c", "start", ""]]
    } else {
        vec![vec!["xdg-open"], vec!["wslview"]]
    };
    for argv in candidates {
        let status = Command::new(argv[0]).args(&argv[1..]).arg(&resolved_str).status();
        if matches!(status, Ok(s) if s.success()) {
            return Ok(());
        }
    }
    // WSL 无显示会话时：wslpath -w 转成 \\wsl.localhost\…。优先 PowerShell
    // Invoke-Item（退出码决定成败），explorer.exe 兜底（其成功时惯常返回 1）。
    if cfg!(target_os = "linux") {
        if let Ok(out) = Command::new("wslpath").arg("-w").arg(&resolved_str).output() {
            if out.status.success() {
                let win = String::from_utf8_lossy(&out.stdout).trim().to_string();
                let ps_full = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe";
                let ps = if std::path::Path::new(ps_full).exists() { ps_full } else { "powershell.exe" };
                if let Ok(s) = Command::new(ps)
                    .args(["-NoProfile", "-Command"])
                    .arg(format!("Invoke-Item -LiteralPath '{win}'"))
                    .status()
                {
                    if s.success() {
                        return Ok(());
                    }
                }
                let exe = if std::path::Path::new("/mnt/c/Windows/explorer.exe").exists() {
                    "/mnt/c/Windows/explorer.exe"
                } else {
                    "explorer.exe"
                };
                if let Ok(s) = Command::new(exe).arg(&win).status() {
                    if s.success() || s.code() == Some(1) {
                        return Ok(());
                    }
                }
            }
        }
    }
    Err(format!("no opener succeeded for {resolved_str}"))
}

#[tauri::command]
async fn open_path(path: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || open_path_sync(&path))
        .await
        .map_err(|e| format!("sidecar task failed: {e}"))?
}

#[tauri::command]
async fn halter_sessions_list(
    project: String,
    limit: u32,
    all_projects: bool,
) -> Result<Value, String> {
    let mut parts = vec![
        "sessions".to_string(),
        "list".to_string(),
        "--limit".to_string(),
        limit.to_string(),
        "--json".to_string(),
    ];
    if all_projects {
        parts.push("--all-projects".to_string());
    } else {
        parts.push("--project".to_string());
        parts.push(project);
    }
    run_json_args(parts).await
}

#[tauri::command]
async fn halter_sessions_projects(project: String) -> Result<Value, String> {
    run_json_args(arg(&["sessions", "projects", "--project", &project, "--json"])).await
}

#[tauri::command]
async fn halter_sessions_show(r#ref: String, project: String, tail: u32) -> Result<Value, String> {
    run_json_args(arg(&[
        "sessions",
        "show",
        "--project",
        &project,
        "--transcript",
        "--tail",
        &tail.to_string(),
        "--json",
        "--",
        &r#ref,
    ]))
    .await
}

#[tauri::command]
async fn halter_sessions_search(
    query: String,
    project: String,
    limit: u32,
    all_projects: bool,
) -> Result<Value, String> {
    let mut parts = vec![
        "sessions".to_string(),
        "search".to_string(),
        "--limit".to_string(),
        limit.to_string(),
        "--json".to_string(),
    ];
    if all_projects {
        parts.push("--all-projects".to_string());
    } else {
        parts.push("--project".to_string());
        parts.push(project);
    }
    parts.push("--".to_string());
    parts.push(query);
    run_json_args(parts).await
}

#[tauri::command]
async fn halter_sessions_context(r#ref: String, project: String, tail: u32) -> Result<String, String> {
    run_text_args(arg(&[
        "sessions",
        "context",
        "--project",
        &project,
        "--tail",
        &tail.to_string(),
        "--",
        &r#ref,
    ]))
    .await
}

/// Run `halter sync`. `apply == false` is the CLI's default dry-run and performs
/// no writes; the UI must collect explicit confirmation before passing
/// `apply == true`.
#[tauri::command]
async fn halter_sync(apply: bool, layers: Vec<String>) -> Result<SidecarOutput, String> {
    let allowed = ["skills", "mcp", "plugins", "hooks", "agents", "memory", "sessions"];
    let mut args: Vec<String> = vec!["sync".into()];
    for layer in allowed {
        if !layers.iter().any(|l| l == layer) {
            args.push(format!("--no-{layer}"));
        }
    }
    if apply {
        args.push("--apply".into());
    }
    tauri::async_runtime::spawn_blocking(move || run_halter(&args))
        .await
        .map_err(|e| format!("sidecar task failed: {e}"))?
}

fn main() {
    let app = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            halter_version,
            halter_scan,
            halter_memory_show,
            open_path,
            halter_sessions_list,
            halter_sessions_projects,
            halter_sessions_show,
            halter_sessions_search,
            halter_sessions_context,
            halter_sync,
        ])
        .build(tauri::generate_context!())
        .expect("error while building halter desktop");

    app.run(|_app_handle, _event| {});
}

