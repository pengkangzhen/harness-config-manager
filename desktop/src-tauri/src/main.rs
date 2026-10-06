// Prevents an additional console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use serde_json::Value;
use std::io::Write;
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

/// Like `run_halter`, but pipes `stdin_data` to the child's stdin. Used by
/// `providers add --token-stdin` so tokens never appear in argv (process list).
fn run_halter_stdin(args: &[String], stdin_data: &str) -> Result<SidecarOutput, String> {
    use std::process::Stdio;
    let program = resolve_halter();
    let mut child = Command::new(&program)
        .args(args)
        .env("HALTER_UI", "1")
        .env("NO_COLOR", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("failed to spawn halter sidecar `{program}`: {e}"))?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin
            .write_all(stdin_data.as_bytes())
            .and_then(|_| stdin.flush())
            .map_err(|e| format!("failed to write token to sidecar stdin: {e}"))?;
    }
    let output = child
        .wait_with_output()
        .map_err(|e| format!("failed to wait for halter sidecar: {e}"))?;
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
async fn halter_scan(machine: Option<String>) -> Result<Value, String> {
    let mut parts = vec!["scan".to_string()];
    if let Some(m) = machine.as_deref().filter(|m| !m.trim().is_empty()) {
        parts.push("--machine".into());
        parts.push(m.into());
    }
    parts.push("--json".into());
    run_json_args(parts).await
}

/// Registered remote machines (`halter machines list --json`).
#[tauri::command]
async fn halter_machines_list() -> Result<Value, String> {
    run_json_args(arg(&["machines", "list", "--json"])).await
}

#[tauri::command]
async fn halter_machines_add(
    name: String,
    host: String,
    user: Option<String>,
    port: Option<u32>,
    halter_path: Option<String>,
) -> Result<SidecarOutput, String> {
    let mut args = vec!["machines".into(), "add".into(), name];
    args.push("--host".into());
    args.push(host);
    if let Some(u) = user.as_deref().filter(|u| !u.trim().is_empty()) {
        args.push("--user".into());
        args.push(u.into());
    }
    if let Some(p) = port.filter(|p| *p != 0) {
        args.push("--port".into());
        args.push(p.to_string());
    }
    if let Some(hp) = halter_path.as_deref().filter(|hp| !hp.trim().is_empty()) {
        args.push("--halter-path".into());
        args.push(hp.into());
    }
    tauri::async_runtime::spawn_blocking(move || run_halter(&args))
        .await
        .map_err(|e| format!("sidecar task failed: {e}"))?
}

#[tauri::command]
async fn halter_machines_remove(name: String) -> Result<SidecarOutput, String> {
    tauri::async_runtime::spawn_blocking(move || run_halter(&arg(&["machines", "remove", &name])))
        .await
        .map_err(|e| format!("sidecar task failed: {e}"))?
}

/// Run `halter push <layer> <item>`: copy one entry between machines.
/// Exactly one of `to` / `from` names a remote machine; the other side is
/// always this machine and is simply omitted from the CLI invocation.
#[tauri::command]
async fn halter_push(
    layer: String,
    item: String,
    to: Option<String>,
    from: Option<String>,
    with_secrets: bool,
) -> Result<SidecarOutput, String> {
    let mut args = vec!["push".into(), layer, item];
    if let Some(t2) = to.as_deref().filter(|t2| !t2.trim().is_empty()) {
        args.push("--to".into());
        args.push(t2.into());
    }
    if let Some(f) = from.as_deref().filter(|f| !f.trim().is_empty()) {
        args.push("--from".into());
        args.push(f.into());
    }
    if with_secrets {
        args.push("--with-secrets".into());
    }
    args.push("--apply".into());
    tauri::async_runtime::spawn_blocking(move || run_halter(&args))
        .await
        .map_err(|e| format!("sidecar task failed: {e}"))?
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
/// `apply == true`. `tool` / `items` narrow the run to a single matrix cell
/// (the layer is already narrowed via `layers`). `machine` reroutes the whole
/// run onto a registered remote machine.
#[tauri::command]
async fn halter_sync(
    apply: bool,
    layers: Vec<String>,
    tool: Option<String>,
    items: Option<Vec<String>>,
    machine: Option<String>,
) -> Result<SidecarOutput, String> {
    let allowed = ["skills", "mcp", "plugins", "hooks", "agents", "memory", "sessions"];
    let mut args: Vec<String> = vec!["sync".into()];
    if let Some(m) = machine.as_deref().filter(|m| !m.trim().is_empty()) {
        args.push("--machine".into());
        args.push(m.into());
    }
    for layer in allowed {
        if !layers.iter().any(|l| l == layer) {
            args.push(format!("--no-{layer}"));
        }
    }
    if let Some(t) = tool.as_deref().filter(|t| !t.trim().is_empty()) {
        args.push("--tool".into());
        args.push(t.into());
    }
    if let Some(list) = items.as_ref() {
        for item in list.iter().filter(|i| !i.trim().is_empty()) {
            args.push("--item".into());
            args.push(item.clone());
        }
    }
    if apply {
        args.push("--apply".into());
    }
    tauri::async_runtime::spawn_blocking(move || run_halter(&args))
        .await
        .map_err(|e| format!("sidecar task failed: {e}"))?
}

/// Provider manifest + per-tool current state (`halter providers list --json`).
#[tauri::command]
async fn halter_providers_list() -> Result<Value, String> {
    run_json_args(arg(&["providers", "list", "--json"])).await
}

/// One-click provider switch; `official` restores vendor defaults.
#[tauri::command]
async fn halter_providers_switch(id: String, tool: String) -> Result<SidecarOutput, String> {
    tauri::async_runtime::spawn_blocking(move || {
        run_halter(&arg(&["providers", "switch", &id, "--tool", &tool]))
    })
    .await
    .map_err(|e| format!("sidecar task failed: {e}"))?
}

/// Create/extend a provider entry. `token` travels Tauri IPC -> sidecar stdin
/// (`--token-stdin`), never through argv.
#[tauri::command]
async fn halter_providers_add(
    id: String,
    tool: String,
    base_url: String,
    label: Option<String>,
    model: Option<String>,
    token: Option<String>,
    envs: Option<Vec<String>>,
    wire_api: Option<String>,
    reasoning_effort: Option<String>,
    context_window: Option<u64>,
) -> Result<SidecarOutput, String> {
    let mut args = vec!["providers".into(), "add".into(), id, "--tool".into(), tool];
    args.push("--base-url".into());
    args.push(base_url);
    if let Some(l) = label.as_deref().filter(|l| !l.trim().is_empty()) {
        args.push("--label".into());
        args.push(l.into());
    }
    if let Some(m) = model.as_deref().filter(|m| !m.trim().is_empty()) {
        args.push("--model".into());
        args.push(m.into());
    }
    if let Some(list) = envs.as_ref() {
        for pair in list.iter().filter(|p| !p.trim().is_empty()) {
            args.push("--env".into());
            args.push(pair.clone());
        }
    }
    if let Some(w) = wire_api.as_deref().filter(|w| !w.trim().is_empty()) {
        args.push("--wire-api".into());
        args.push(w.into());
    }
    if let Some(r) = reasoning_effort.as_deref().filter(|r| !r.trim().is_empty()) {
        args.push("--reasoning-effort".into());
        args.push(r.into());
    }
    if let Some(c) = context_window.filter(|c| *c != 0) {
        args.push("--context-window".into());
        args.push(c.to_string());
    }
    let token_data = token.unwrap_or_default();
    if !token_data.trim().is_empty() {
        args.push("--token-stdin".into());
    }
    tauri::async_runtime::spawn_blocking(move || run_halter_stdin(&args, &token_data))
        .await
        .map_err(|e| format!("sidecar task failed: {e}"))?
}

/// Remove a provider entry (manifest + its tokens; target configs untouched).
#[tauri::command]
async fn halter_providers_remove(id: String) -> Result<SidecarOutput, String> {
    tauri::async_runtime::spawn_blocking(move || {
        run_halter(&arg(&["providers", "remove", &id, "--apply"]))
    })
    .await
    .map_err(|e| format!("sidecar task failed: {e}"))?
}

/// Adopt existing provider configs from claude env / codex [model_providers.*].
#[tauri::command]
async fn halter_providers_adopt() -> Result<SidecarOutput, String> {
    tauri::async_runtime::spawn_blocking(move || {
        run_halter(&arg(&["providers", "adopt", "--apply"]))
    })
    .await
    .map_err(|e| format!("sidecar task failed: {e}"))?
}

fn main() {
    let app = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            halter_version,
            halter_scan,
            halter_machines_list,
            halter_machines_add,
            halter_machines_remove,
            halter_push,
            halter_memory_show,
            open_path,
            halter_sessions_list,
            halter_sessions_projects,
            halter_sessions_show,
            halter_sessions_search,
            halter_sessions_context,
            halter_sync,
            halter_providers_list,
            halter_providers_switch,
            halter_providers_add,
            halter_providers_remove,
            halter_providers_adopt,
        ])
        .build(tauri::generate_context!())
        .expect("error while building halter desktop");

    app.run(|_app_handle, _event| {});
}

