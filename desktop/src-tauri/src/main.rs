// Prevents an additional console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use serde_json::Value;
use std::path::PathBuf;
use std::process::Command;

/// Raw result of one `hcm` sidecar invocation, surfaced to the UI as-is.
#[derive(Debug, Serialize)]
pub struct SidecarOutput {
    pub ok: bool,
    pub code: i32,
    pub stdout: String,
    pub stderr: String,
}

/// Resolve the hcm executable:
///   1. `HCM_BINARY` env override (tests, custom installs)
///   2. a `hcm`/`hcm-bin` shipped next to the app executable (bundled sidecar)
///   3. plain `hcm` on PATH (development fallback)
fn resolve_hcm() -> String {
    if let Ok(path) = std::env::var("HCM_BINARY") {
        if !path.trim().is_empty() {
            return path;
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            for name in ["hcm", "hcm-bin"] {
                let candidate: PathBuf = dir.join(name);
                if candidate.is_file() {
                    return candidate.to_string_lossy().into_owned();
                }
            }
        }
    }
    "hcm".to_string()
}

fn run_hcm(args: &[String]) -> Result<SidecarOutput, String> {
    let program = resolve_hcm();
    let output = Command::new(&program)
        .args(args)
        .env("HCM_UI", "1")
        .env("NO_COLOR", "1")
        .output()
        .map_err(|e| format!("failed to spawn hcm sidecar `{program}`: {e}"))?;
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
        return Err(format!("hcm exited with code {}: {detail}", out.code));
    }
    serde_json::from_str(out.stdout.trim()).map_err(|e| {
        format!(
            "failed to parse hcm JSON output: {e}\n--- stdout ---\n{}",
            out.stdout
        )
    })
}

async fn run_json_args(args: Vec<String>) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || run_hcm(&args).and_then(|out| parse_json_output(&out)))
        .await
        .map_err(|e| format!("sidecar task failed: {e}"))?
}

async fn run_text_args(args: Vec<String>) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let out = run_hcm(&args)?;
        if out.ok {
            Ok(out.stdout)
        } else {
            let detail = if out.stderr.trim().is_empty() {
                out.stdout.trim().to_string()
            } else {
                out.stderr.trim().to_string()
            };
            Err(format!("hcm exited with code {}: {detail}", out.code))
        }
    })
    .await
    .map_err(|e| format!("sidecar task failed: {e}"))?
}

fn arg(parts: &[&str]) -> Vec<String> {
    parts.iter().map(|s| s.to_string()).collect()
}

#[tauri::command]
async fn hcm_version() -> Result<Value, String> {
    run_json_args(arg(&["version", "--json"])).await
}

#[tauri::command]
async fn hcm_scan() -> Result<Value, String> {
    run_json_args(arg(&["scan", "--json"])).await
}

#[tauri::command]
async fn hcm_sessions_list(project: String, limit: u32) -> Result<Value, String> {
    run_json_args(arg(&[
        "sessions",
        "list",
        "--project",
        &project,
        "--limit",
        &limit.to_string(),
        "--json",
    ]))
    .await
}

#[tauri::command]
async fn hcm_sessions_show(r#ref: String, project: String, tail: u32) -> Result<Value, String> {
    run_json_args(arg(&[
        "sessions",
        "show",
        &r#ref,
        "--project",
        &project,
        "--transcript",
        "--tail",
        &tail.to_string(),
        "--json",
    ]))
    .await
}

#[tauri::command]
async fn hcm_sessions_search(
    query: String,
    project: String,
    limit: u32,
) -> Result<Value, String> {
    run_json_args(arg(&[
        "sessions",
        "search",
        &query,
        "--project",
        &project,
        "--limit",
        &limit.to_string(),
        "--json",
    ]))
    .await
}

#[tauri::command]
async fn hcm_sessions_context(r#ref: String, project: String, tail: u32) -> Result<String, String> {
    run_text_args(arg(&[
        "sessions",
        "context",
        &r#ref,
        "--project",
        &project,
        "--tail",
        &tail.to_string(),
    ]))
    .await
}

/// Run `hcm sync`. `apply == false` is the CLI's default dry-run and performs
/// no writes; the UI must collect explicit confirmation before passing
/// `apply == true`.
#[tauri::command]
async fn hcm_sync(apply: bool, layers: Vec<String>) -> Result<SidecarOutput, String> {
    let allowed = ["skills", "mcp", "plugins", "hooks", "agents", "sessions"];
    let mut args: Vec<String> = vec!["sync".into()];
    for layer in allowed {
        if !layers.iter().any(|l| l == layer) {
            args.push(format!("--no-{layer}"));
        }
    }
    if apply {
        args.push("--apply".into());
    }
    tauri::async_runtime::spawn_blocking(move || run_hcm(&args))
        .await
        .map_err(|e| format!("sidecar task failed: {e}"))?
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            hcm_version,
            hcm_scan,
            hcm_sessions_list,
            hcm_sessions_show,
            hcm_sessions_search,
            hcm_sessions_context,
            hcm_sync,
        ])
        .run(tauri::generate_context!())
        .expect("error while running hcm desktop");
}
