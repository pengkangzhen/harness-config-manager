// Prevents an additional console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use tauri::Emitter;
use serde_json::Value;
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tokio::sync::Mutex as AsyncMutex;
use std::time::Duration;

/// 托管的 `halter ahp serve` 进程（桌面 App 生命周期内复用）。
static AHP_CHILD: Mutex<Option<Child>> = Mutex::new(None);
static AHP_PORT: Mutex<Option<u16>> = Mutex::new(None);
static AHP_TOKEN: Mutex<Option<String>> = Mutex::new(None);
static AHP_ENSURE_LOCK: AsyncMutex<()> = AsyncMutex::const_new(());

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

/// Configured default models per harness (`[models]` in halter config.toml).
#[tauri::command]
async fn halter_models() -> Result<Value, String> {
    run_json_args(arg(&["models", "--json"])).await
}

#[tauri::command]
async fn halter_model_configure(
    model: String,
    base_url: Option<String>,
    api_key_env: Option<String>,
) -> Result<Value, String> {
    let mut args = vec![
        "model".to_string(),
        "configure".to_string(),
        "--model".to_string(),
        model,
        "--json".to_string(),
    ];
    if let Some(base_url) = base_url.as_deref() {
        if !base_url.trim().is_empty() {
            args.push("--base-url".to_string());
            args.push(base_url.trim().to_string());
        }
    }
    if let Some(api_key_env) = api_key_env.as_deref() {
        args.push("--api-key-env".to_string());
        args.push(api_key_env.to_string());
    }
    run_json_args(args).await
}

#[tauri::command]
async fn halter_audit_list(limit: u32) -> Result<Value, String> {
    run_json_args(arg(&["audit", "list", "--limit", &limit.to_string(), "--json"])).await
}

#[tauri::command]
async fn halter_audit_show(
    session_id: String,
    kind: Option<String>,
    tail: u32,
) -> Result<Value, String> {
    let mut args = vec![
        "audit".to_string(),
        "show".to_string(),
        "--tail".to_string(),
        tail.to_string(),
        "--json".to_string(),
    ];
    if let Some(kind) = kind.as_deref() {
        if !kind.trim().is_empty() {
            args.push("--kind".to_string());
            args.push(kind.trim().to_string());
        }
    }
    args.push("--".to_string());
    args.push(session_id);
    run_json_args(args).await
}

/// AHP client 桥状态：base URL、client id、SSE reader 的取消句柄。
struct AhpBridge {
    client: reqwest::Client,
    base: String,
    client_id: String,
    sse_task: Mutex<Option<tauri::async_runtime::JoinHandle<()>>>,
}

static AHP_BRIDGE: Mutex<Option<AhpBridge>> = Mutex::new(None);

fn ahp_token_path() -> PathBuf {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join(".config").join("halter").join("ahp-token")
}

fn read_ahp_token() -> Result<String, String> {
    std::fs::read_to_string(ahp_token_path())
        .map(|value| value.trim().to_string())
        .map_err(|e| format!("failed to read AHP token: {e}"))
}

fn abort_ahp_bridge(bridge: Option<AhpBridge>) {
    if let Some(bridge) = bridge {
        if let Some(task) = bridge.sse_task.lock().unwrap().take() {
            task.abort();
        }
    }
}

async fn port_open(port: u16) -> bool {
    tauri::async_runtime::spawn_blocking(move || {
        TcpStream::connect_timeout(
            &format!("127.0.0.1:{port}").parse().expect("valid socket address"),
            Duration::from_millis(150),
        )
        .is_ok()
    })
    .await
    .unwrap_or(false)
}

fn ahp_client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(2))
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|e| format!("failed to create AHP HTTP client: {e}"))
}

async fn ahp_health(base: &str) -> Result<Value, String> {
    let client = ahp_client()?;
    let value: Value = client
        .get(format!("{base}/healthz"))
        .send()
        .await
        .map_err(|e| format!("AHP health check failed: {e}"))?
        .error_for_status()
        .map_err(|e| format!("AHP health check HTTP: {e}"))?
        .json()
        .await
        .map_err(|e| format!("AHP health check decode: {e}"))?;
    let name = value
        .get("serverInfo")
        .and_then(|item| item.get("name"))
        .and_then(|item| item.as_str())
        .unwrap_or_default();
    if name != "halter-agent-host" {
        return Err(format!("port is not a halter AHP host: {name}"));
    }
    Ok(value)
}

async fn ahp_token_valid(base: &str, token: &str) -> bool {
    let Ok(client) = ahp_client() else { return false };
    let body = serde_json::json!({
        "jsonrpc": "2.0", "id": 0, "method": "ping", "params": {}
    });
    let Ok(resp) = client
        .post(format!("{base}/rpc"))
        .query(&[("client", "halter-desktop-probe")])
        .bearer_auth(token)
        .json(&body)
        .send()
        .await
    else {
        return false;
    };
    resp.status().is_success()
}

/// 连接 AHP host（HTTP+SSE 传输）：initialize 并启动 action 事件流，
/// 每个 server->client 消息以 `ahp-message` 事件转发给前端。
#[tauri::command]
async fn halter_ahp_connect(app: tauri::AppHandle) -> Result<Value, String> {
    halter_ahp_ensure().await?;
    let port = AHP_PORT.lock().unwrap().ok_or("no AHP host available")?;
    let token = AHP_TOKEN
        .lock()
        .unwrap()
        .clone()
        .ok_or("no AHP authentication token available")?;
    let base = format!("http://127.0.0.1:{port}");
    let client_id = format!(
        "halter-desktop-{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    );

    // 已连接则复用；连接不同 host 前先停止旧的 reconnect task。
    {
        let old = AHP_BRIDGE.lock().unwrap();
        if let Some(bridge) = old.as_ref() {
            if bridge.base == base {
                return Ok(serde_json::json!({ "base": base, "connected": true, "reused": true }));
            }
        }
    }
    abort_ahp_bridge(AHP_BRIDGE.lock().unwrap().take());

    let client = ahp_client()?;
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
    let resp = client
        .post(format!("{base}/rpc"))
        .query(&[("client", client_id.as_str())])
        .bearer_auth(&token)
        .json(&init_body)
        .send()
        .await
        .map_err(|e| format!("AHP initialize failed: {e}"))?
        .error_for_status()
        .map_err(|e| format!("AHP initialize HTTP: {e}"))?;
    let init: Value = resp
        .json()
        .await
        .map_err(|e| format!("AHP initialize decode: {e}"))?;

    // SSE 事件流 -> 前端事件
    let sse_client = client.clone();
    let sse_base = base.clone();
    let sse_client_id = client_id.clone();
    let sse_token = token.clone();
    let app2 = app.clone();
    let sse_task = tauri::async_runtime::spawn(async move {
        let url = format!("{sse_base}/rpc/stream");
        loop {
            let resp = match sse_client
                .get(&url)
                .query(&[("client", sse_client_id.as_str())])
                .bearer_auth(&sse_token)
                .send()
                .await
            {
                Ok(resp) => resp,
                Err(_) => {
                    tokio::time::sleep(Duration::from_secs(2)).await;
                    continue;
                }
            };
            if resp.status() == reqwest::StatusCode::UNAUTHORIZED {
                let _ = app2.emit("ahp-message", "{\"unauthorized\":true}");
                break;
            }
            let mut stream = resp.bytes_stream();
            use futures_util::StreamExt;
            let mut buf: Vec<u8> = Vec::new();
            while let Some(chunk) = stream.next().await {
                let bytes = match chunk {
                    Ok(bytes) => bytes,
                    Err(_) => break,
                };
                buf.extend_from_slice(&bytes);
                while let Some((pos, delimiter_len)) = find_frame_end(&buf) {
                    let frame: Vec<u8> = buf.drain(..pos).collect();
                    for _ in 0..delimiter_len {
                        buf.remove(0);
                    }
                    let text = String::from_utf8_lossy(&frame).to_string();
                    let mut data_lines: Vec<String> = Vec::new();
                    for line in text.lines() {
                        let line = line.trim_end_matches('\r');
                        if let Some(data) = line.strip_prefix("data:") {
                            data_lines.push(data.strip_prefix(' ').unwrap_or(data).to_string());
                        }
                    }
                    if !data_lines.is_empty() {
                        let data = data_lines.join("\n");
                        let _ = app2.emit("ahp-message", data);
                    }
                }
                if buf.len() > 1024 * 1024 {
                    let _ = app2.emit("ahp-message", "{\"error\":\"SSE frame too large\"}");
                    break;
                }
            }
            tokio::time::sleep(Duration::from_secs(1)).await;
        }
    });

    *AHP_BRIDGE.lock().unwrap() = Some(AhpBridge {
        client,
        base: base.clone(),
        client_id,
        sse_task: Mutex::new(Some(sse_task)),
    });

    Ok(serde_json::json!({
        "base": base, "connected": true, "reused": false,
        "init": init,
    }))
}

fn find_frame_end(buf: &[u8]) -> Option<(usize, usize)> {
    if buf.len() < 2 {
        return None;
    }
    if buf.len() >= 4 {
        if let Some(pos) = buf.windows(4).position(|window| window == b"\r\n\r\n") {
            return Some((pos, 4));
        }
    }
    if let Some(pos) = buf.windows(2).position(|window| window == b"\n\n") {
        return Some((pos, 2));
    }
    buf.windows(2).position(|window| window == b"\r\r").map(|pos| (pos, 2))
}

/// 经 Rust 桥发送 AHP 请求（带 id，返回 result）。
#[tauri::command]
async fn halter_ahp_rpc(method: String, params: Value) -> Result<Value, String> {
    let (client, base, client_id, token) = {
        let guard = AHP_BRIDGE.lock().unwrap();
        let bridge = guard.as_ref().ok_or("AHP not connected")?;
        (
            bridge.client.clone(),
            bridge.base.clone(),
            bridge.client_id.clone(),
            AHP_TOKEN.lock().unwrap().clone().ok_or("no AHP token")?,
        )
    };
    let id = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(1);
    let body = serde_json::json!({
        "jsonrpc": "2.0", "id": id, "method": method, "params": params
    });
    let resp = client
        .post(format!("{base}/rpc"))
        .query(&[("client", client_id.as_str())])
        .bearer_auth(&token)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("AHP rpc failed: {e}"))?
        .error_for_status()
        .map_err(|e| format!("AHP rpc HTTP: {e}"))?;
    let msg: Value = resp
        .json()
        .await
        .map_err(|e| format!("AHP rpc decode: {e}"))?;
    if let Some(err) = msg.get("error") {
        return Err(err
            .get("message")
            .and_then(|message| message.as_str())
            .unwrap_or("AHP error")
            .to_string());
    }
    Ok(msg.get("result").cloned().unwrap_or(Value::Null))
}

/// 经 Rust 桥发送 AHP 通知（dispatchAction / unsubscribe，无响应）。
#[tauri::command]
async fn halter_ahp_notify(method: String, params: Value) -> Result<(), String> {
    let (client, base, client_id, token) = {
        let guard = AHP_BRIDGE.lock().unwrap();
        let bridge = guard.as_ref().ok_or("AHP not connected")?;
        (
            bridge.client.clone(),
            bridge.base.clone(),
            bridge.client_id.clone(),
            AHP_TOKEN.lock().unwrap().clone().ok_or("no AHP token")?,
        )
    };
    let body = serde_json::json!({ "jsonrpc": "2.0", "method": method, "params": params });
    let resp = client
        .post(format!("{base}/rpc"))
        .query(&[("client", client_id.as_str())])
        .bearer_auth(&token)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("AHP notify failed: {e}"))?;
    let status = resp.status();
    if status.is_success() || status == reqwest::StatusCode::NO_CONTENT {
        Ok(())
    } else {
        Err(format!("AHP notify HTTP {status}"))
    }
}

/// 确保本机有一个可认证的 `halter ahp serve` 在跑：
/// 1) 已托管 -> 校验进程与 token；
/// 2) 7433..=7439 只采用健康检查和 token 均匹配的外部 halter host；
/// 3) 否则 spawn sidecar 并等待健康与认证检查通过。
#[tauri::command]
async fn halter_ahp_ensure() -> Result<Value, String> {
    let _guard = AHP_ENSURE_LOCK.lock().await;

    let managed_port = *AHP_PORT.lock().unwrap();
    if let Some(port) = managed_port {
        if port_open(port).await {
            let token = read_ahp_token().ok();
            let authenticated = match token {
                Some(ref token) => ahp_token_valid(&format!("http://127.0.0.1:{port}"), token).await,
                None => false,
            };
            if authenticated {
                if let Some(token) = token {
                    *AHP_TOKEN.lock().unwrap() = Some(token);
                }
                return Ok(serde_json::json!({ "port": port, "started": false, "managed": true }));
            }
        }
        let _ = halter_ahp_stop().await;
    }

    for port in 7433..=7439u16 {
        if !port_open(port).await {
            continue;
        }
        let base = format!("http://127.0.0.1:{port}");
        if ahp_health(&base).await.is_err() {
            continue;
        }
        let Ok(token) = read_ahp_token() else { continue };
        if !ahp_token_valid(&base, &token).await {
            continue;
        }
        *AHP_PORT.lock().unwrap() = Some(port);
        *AHP_TOKEN.lock().unwrap() = Some(token);
        return Ok(serde_json::json!({ "port": port, "started": false, "managed": false }));
    }

    // The synchronous probe is intentionally run off the async runtime below.
    let port = {
        let result = tauri::async_runtime::spawn_blocking(move || {
            (7433..=7439u16).find(|port| !TcpStream::connect(("127.0.0.1", *port)).is_ok())
        })
        .await
        .map_err(|e| format!("AHP port probe failed: {e}"))?;
        result.ok_or("no available AHP port in 7433..=7439")?
    };

    let token_path = ahp_token_path();
    if let Some(parent) = token_path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("failed to create AHP config dir: {e}"))?;
    }
    let _ = std::fs::remove_file(&token_path);
    let program = resolve_halter();
    let port_text = port.to_string();
    let token_path_text = token_path.to_string_lossy().into_owned();
    let child = Command::new(&program)
        .args([
            "ahp",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            &port_text,
            "--token-file",
            &token_path_text,
        ])
        .env("HALTER_UI", "1")
        .env("NO_COLOR", "1")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("failed to spawn halter ahp serve `{program}`: {e}"))?;
    *AHP_CHILD.lock().unwrap() = Some(child);
    *AHP_PORT.lock().unwrap() = Some(port);

    for _ in 0..30 {
        if !port_open(port).await {
            tokio::time::sleep(Duration::from_millis(100)).await;
            continue;
        }
        let base = format!("http://127.0.0.1:{port}");
        if ahp_health(&base).await.is_err() {
            tokio::time::sleep(Duration::from_millis(100)).await;
            continue;
        }
        if let Ok(token) = read_ahp_token() {
            if ahp_token_valid(&base, &token).await {
                *AHP_TOKEN.lock().unwrap() = Some(token);
                return Ok(serde_json::json!({ "port": port, "started": true, "managed": true }));
            }
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    let _ = halter_ahp_stop().await;
    Err("halter ahp serve did not become ready and authenticated within 3s".into())
}

/// 停止托管的 AHP host，并释放桌面端桥接资源。
#[tauri::command]
async fn halter_ahp_stop() -> Result<Value, String> {
    abort_ahp_bridge(AHP_BRIDGE.lock().unwrap().take());
    let mut guard = AHP_CHILD.lock().unwrap();
    if let Some(mut child) = guard.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
    *AHP_PORT.lock().unwrap() = None;
    *AHP_TOKEN.lock().unwrap() = None;
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
        "--",
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
        "--json",
        "--tail",
        &tail.to_string(),
        "--",
        &task_id,
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
    let app = tauri::Builder::default()
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
            halter_model_configure,
            halter_audit_list,
            halter_audit_show,
            halter_task_show,
            halter_sessions_show,
            halter_sessions_search,
            halter_sessions_context,
            halter_sync,
        ])
        .build(tauri::generate_context!())
        .expect("error while building halter desktop");

    app.run(|_app_handle, event| {
        if let tauri::RunEvent::Exit = event {
            tauri::async_runtime::block_on(async {
                let _ = halter_ahp_stop().await;
            });
        }
    });
}
