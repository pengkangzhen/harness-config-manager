// Prevents an additional console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use tauri::Emitter;
use serde_json::Value;
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;

/// 托管的 `halter ahp serve` 进程（桌面 App 生命周期内复用）。
static AHP_CHILD: Mutex<Option<Child>> = Mutex::new(None);
static AHP_PORT: Mutex<Option<u16>> = Mutex::new(None);

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

/// AHP client 桥状态：base URL、client id、SSE reader 的取消句柄。
struct AhpBridge {
    client: reqwest::Client,
    base: String,
    client_id: String,
    sse_task: Mutex<Option<tauri::async_runtime::JoinHandle<()>>>,
}

static AHP_BRIDGE: Mutex<Option<AhpBridge>> = Mutex::new(None);

/// 连接 AHP host（HTTP+SSE 传输）：initialize 并启动 action 事件流，
/// 每个 server->client 消息以 `ahp-message` 事件转发给前端。
#[tauri::command]
async fn halter_ahp_connect(app: tauri::AppHandle) -> Result<Value, String> {
    // 先确保 host 在跑（外部已起则采用）
    let _ = halter_ahp_ensure().await;
    let port = AHP_PORT.lock().unwrap().ok_or("no AHP host available")?;
    let base = format!("http://127.0.0.1:{port}");
    let client_id = format!("halter-desktop-{}", std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis()).unwrap_or(0));

    // 已连接则复用
    {
        let guard = AHP_BRIDGE.lock().unwrap();
        if let Some(b) = guard.as_ref() {
            if b.base == base {
                return Ok(serde_json::json!({ "base": base, "connected": true, "reused": true }));
            }
        }
    }

    let client = reqwest::Client::new();

    // initialize（拿 agents 目录快照）
    let init_body = serde_json::json!({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "channel": "ahp-root://",
            "protocolVersions": ["0.9.0"],
            "clientId": client_id,
            "clientInfo": { "name": "halter-desktop", "version": "0.1.0" },
            "initialSubscriptions": ["ahp-root://"],
        }
    });
    let resp = client.post(format!("{base}/rpc"))
        .query(&[("client", client_id.as_str())])
        .json(&init_body)
        .send().await
        .map_err(|e| format!("AHP initialize failed: {e}"))?
        .error_for_status()
        .map_err(|e| format!("AHP initialize HTTP: {e}"))?;
    let init: Value = resp.json().await
        .map_err(|e| format!("AHP initialize decode: {e}"))?;

    // SSE 事件流 -> 前端事件
    let sse_client = client.clone();
    let sse_base = base.clone();
    let sse_client_id = client_id.clone();
    let app2 = app.clone();
    let sse_task = tauri::async_runtime::spawn(async move {
        let url = format!("{sse_base}/rpc/stream?client={sse_client_id}");
        loop {
            let resp = match sse_client.get(&url).send().await {
                Ok(r) => r,
                Err(_) => { tokio::time::sleep(Duration::from_secs(2)).await; continue; }
            };
            let mut stream = resp.bytes_stream();
            use futures_util::StreamExt;
            let mut buf: Vec<u8> = Vec::new();
            while let Some(chunk) = stream.next().await {
                match chunk {
                    Ok(bytes) => {
                        buf.extend_from_slice(&bytes);
                        while let Some(pos) = find_double_newline(&buf) {
                            let frame: Vec<u8> = buf.drain(..pos).collect();
                            buf.drain(..2); //


                            let text = String::from_utf8_lossy(&frame).to_string();
                            for line in text.lines() {
                                if let Some(data) = line.strip_prefix("data: ") {
                                    let _ = app2.emit("ahp-message", data.to_string());
                                }
                            }
                        }
                    }
                    Err(_) => break,
                }
            }
            tokio::time::sleep(Duration::from_secs(1)).await;
        }
    });

    if let Some(old_bridge) = AHP_BRIDGE.lock().unwrap().take() {
        if let Some(task) = old_bridge.sse_task.lock().unwrap().take() {
            task.abort();
        }
    }
    *AHP_BRIDGE.lock().unwrap() = Some(AhpBridge {
        client, base: base.clone(), client_id,
        sse_task: Mutex::new(Some(sse_task)),
    });

    Ok(serde_json::json!({
        "base": base, "connected": true, "reused": false,
        "init": init,
    }))
}

fn find_double_newline(buf: &[u8]) -> Option<usize> {
    buf.windows(2).position(|w| w == b"\n\n")
}

/// 经 Rust 桥发送 AHP 请求（带 id，返回 result）。
#[tauri::command]
async fn halter_ahp_rpc(method: String, params: Value) -> Result<Value, String> {
    let (client, base, client_id) = {
        let guard = AHP_BRIDGE.lock().unwrap();
        let b = guard.as_ref().ok_or("AHP not connected")?;
        (b.client.clone(), b.base.clone(), b.client_id.clone())
    };
    let id = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(1);
    let body = serde_json::json!({ "jsonrpc": "2.0", "id": id, "method": method, "params": params });
    let resp = client.post(format!("{base}/rpc"))
        .query(&[("client", client_id.as_str())])
        .json(&body)
        .send().await
        .map_err(|e| format!("AHP rpc failed: {e}"))?
        .error_for_status()
        .map_err(|e| format!("AHP rpc HTTP: {e}"))?;
    let msg: Value = resp.json().await
        .map_err(|e| format!("AHP rpc decode: {e}"))?;
    if let Some(err) = msg.get("error") {
        return Err(err.get("message").and_then(|m| m.as_str())
            .unwrap_or("AHP error").to_string());
    }
    Ok(msg.get("result").cloned().unwrap_or(Value::Null))
}

/// 经 Rust 桥发送 AHP 通知（dispatchAction / unsubscribe，无响应）。
#[tauri::command]
async fn halter_ahp_notify(method: String, params: Value) -> Result<(), String> {
    let (client, base, client_id) = {
        let guard = AHP_BRIDGE.lock().unwrap();
        let b = guard.as_ref().ok_or("AHP not connected")?;
        (b.client.clone(), b.base.clone(), b.client_id.clone())
    };
    let body = serde_json::json!({ "jsonrpc": "2.0", "method": method, "params": params });
    let resp = client.post(format!("{base}/rpc"))
        .query(&[("client", client_id.as_str())])
        .json(&body)
        .send().await
        .map_err(|e| format!("AHP notify failed: {e}"))?;
    let status = resp.status();
    if status.is_success() || status == 204 {
        Ok(())
    } else {
        Err(format!("AHP notify HTTP {status}"))
    }
}

fn port_in_use(port: u16) -> bool {
    TcpStream::connect(("127.0.0.1", port)).is_ok()
}

/// 确保本机有一个 `halter ahp serve` 在跑：
/// 1) 已托管 -> 直接返回端口；
/// 2) 7433..=7439 有服务在监听 -> 视为外部已启动，直接连；
/// 3) 否则 spawn sidecar 并等待端口就绪。
#[tauri::command]
async fn halter_ahp_ensure() -> Result<Value, String> {
    if let Some(port) = *AHP_PORT.lock().unwrap() {
        if port_in_use(port) {
            return Ok(serde_json::json!({ "port": port, "started": false, "managed": true }));
        }
    }
    for port in 7433..=7439u16 {
        if port_in_use(port) {
            // 外部已启动的 host（用户手动 halter ahp serve）
            *AHP_PORT.lock().unwrap() = Some(port);
            return Ok(serde_json::json!({ "port": port, "started": false, "managed": false }));
        }
    }
    let port: u16 = 7433;
    let program = resolve_halter();
    let child = Command::new(&program)
        .args(["ahp", "serve", "--host", "127.0.0.1", "--port", &port.to_string()])
        .env("HALTER_UI", "1")
        .env("NO_COLOR", "1")
        .spawn()
        .map_err(|e| format!("failed to spawn halter ahp serve `{program}`: {e}"))?;
    *AHP_CHILD.lock().unwrap() = Some(child);
    *AHP_PORT.lock().unwrap() = Some(port);
    // 等待端口就绪（最多 ~3s）
    for _ in 0..30 {
        if port_in_use(port) {
            return Ok(serde_json::json!({ "port": port, "started": true, "managed": true }));
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    Err("halter ahp serve did not become ready within 3s".into())
}

/// 停止托管的 AHP host（App 退出或用户手动停止）。
#[tauri::command]
async fn halter_ahp_stop() -> Result<Value, String> {
    if let Some(bridge) = AHP_BRIDGE.lock().unwrap().take() {
        if let Some(task) = bridge.sse_task.lock().unwrap().take() {
            task.abort();
        }
    }
    let mut guard = AHP_CHILD.lock().unwrap();
    if let Some(mut child) = guard.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
    *AHP_PORT.lock().unwrap() = None;
    Ok(serde_json::json!({ "stopped": true }))
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
            halter_ahp_ensure,
            halter_ahp_stop,
            halter_ahp_connect,
            halter_ahp_rpc,
            halter_ahp_notify,
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
