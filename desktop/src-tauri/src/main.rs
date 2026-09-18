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
            for name in ["halter", "halter-bin"] {
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
    run_json_args(arg(&["version", "--json"])).await
}

#[tauri::command]
async fn halter_scan() -> Result<Value, String> {
    run_json_args(arg(&["scan", "--json"])).await
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
async fn halter_sessions_search(
    query: String,
    project: String,
    limit: u32,
    all_projects: bool,
) -> Result<Value, String> {
    let mut parts = vec![
        "sessions".to_string(),
        "search".to_string(),
        query,
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
async fn halter_sessions_context(r#ref: String, project: String, tail: u32) -> Result<String, String> {
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

/// Configured default models per harness (`[models]` in halter config.toml).
#[tauri::command]
async fn halter_models() -> Result<Value, String> {
    run_json_args(arg(&["models", "--json"])).await
}

/// Dispatch a task message to @mentioned harnesses via `halter run --detached`.
/// Returns task metadata (ids) immediately; the UI polls `halter_task_show`.
#[tauri::command]
async fn halter_dispatch_run(message: String, project: String, mode: String) -> Result<Value, String> {
    let mode = if mode == "yolo" { "yolo" } else { "safe" };
    run_json_args(arg(&[
        "run",
        "--detached",
        "--json",
        "--mode", mode,
        "--project", &project,
        &message,
    ]))
    .await
}

/// Fetch one dispatched task (status + output tail) for polling.
#[tauri::command]
async fn halter_task_show(task_id: String, tail: u32) -> Result<Value, String> {
    run_json_args(arg(&[
        "tasks",
        "show",
        &task_id,
        "--json",
        "--tail",
        &tail.to_string(),
    ]))
    .await
}

/// Run `halter sync`. `apply == false` is the CLI's default dry-run and performs
/// no writes; the UI must collect explicit confirmation before passing
/// `apply == true`.
#[tauri::command]
async fn halter_sync(apply: bool, layers: Vec<String>) -> Result<SidecarOutput, String> {
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
    tauri::async_runtime::spawn_blocking(move || run_halter(&args))
        .await
        .map_err(|e| format!("sidecar task failed: {e}"))?
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            halter_version,
            halter_scan,
            halter_sessions_list,
            halter_sessions_projects,
            halter_dispatch_run,
            halter_models,
            halter_task_show,
            halter_sessions_show,
            halter_sessions_search,
            halter_sessions_context,
            halter_sync,
        ])
        .run(tauri::generate_context!())
        .expect("error while running halter desktop");
}
