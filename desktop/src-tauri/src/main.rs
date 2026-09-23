// 机械设计物料选型引擎 —— Tauri 桌面壳
//
// 这个壳只做三件事：
//   1. 拉起随包分发的 Python sidecar（真正的服务端与计算引擎都在里面）
//   2. 等它在 stdout 打出 `MDS_READY <url>`，拿到实际端口
//   3. 开一个窗口指到那个地址；关窗时把 sidecar 一起收掉
//
// 端口由 sidecar 自己挑（--port 0）：装了两份、或本机端口被占用时不会起不来。
// 服务只监听 127.0.0.1，不对外暴露。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

/// sidecar 起不来时的等待上限。冷启动要解包依赖，给宽一点。
const READY_TIMEOUT: Duration = Duration::from_secs(60);

/// 持有 sidecar 句柄，退出时统一收掉，别留下孤儿进程。
struct Sidecar(Mutex<Option<Child>>);

impl Drop for Sidecar {
    fn drop(&mut self) {
        if let Ok(mut guard) = self.0.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let exe = app
                .path()
                .resolve("mds-server/mds-server.exe", tauri::path::BaseDirectory::Resource)
                .map_err(|e| format!("找不到随包的服务端程序：{e}"))?;

            // 把自己的 PID 交给 sidecar：万一本进程被强杀（Drop 不会执行），
            // 它自己会发现父进程没了并退出，不留孤儿服务占着端口
            let ppid = std::process::id().to_string();
            let mut child = Command::new(&exe)
                .args(["--port", "0", "--no-open", "--parent-pid", &ppid])
                .stdout(Stdio::piped())
                .stderr(Stdio::null())
                .spawn()
                .map_err(|e| format!("启动服务端失败（{}）：{e}", exe.display()))?;

            let url = wait_for_ready(&mut child)?;

            app.manage(Sidecar(Mutex::new(Some(child))));

            WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::External(url.parse().map_err(|e| format!("服务端地址无效：{e}"))?),
            )
            .title("机械选型引擎")
            .inner_size(1440.0, 920.0)
            .min_inner_size(1024.0, 700.0)
            .center()
            .build()?;

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Tauri 启动失败")
        .run(|app, event| {
            // 正常退出时主动收掉 sidecar；Drop 在某些退出路径上不保证执行
            if let tauri::RunEvent::Exit = event {
                if let Some(sidecar) = app.try_state::<Sidecar>() {
                    if let Ok(mut guard) = sidecar.0.lock() {
                        if let Some(mut child) = guard.take() {
                            let _ = child.kill();
                            let _ = child.wait();
                        }
                    }
                }
            }
        });
}

/// 读 sidecar 的 stdout，等它报出实际监听地址。
///
/// 不用固定 sleep：冷启动时间差别很大，sleep 短了会连不上，长了平白拖慢每次启动。
fn wait_for_ready(child: &mut Child) -> Result<String, String> {
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "拿不到服务端的 stdout".to_string())?;

    let started = Instant::now();
    let mut reader = BufReader::new(stdout);
    let mut line = String::new();

    loop {
        if started.elapsed() > READY_TIMEOUT {
            let _ = child.kill();
            return Err("服务端在 60 秒内没有就绪".into());
        }
        // 进程已经退出就别再等了，直接把退出码报出来
        if let Ok(Some(status)) = child.try_wait() {
            return Err(format!("服务端启动后立即退出（退出码 {status}）"));
        }

        line.clear();
        match reader.read_line(&mut line) {
            Ok(0) => return Err("服务端没有输出就绪信号就关闭了标准输出".into()),
            Ok(_) => {
                if let Some(url) = line.trim().strip_prefix("MDS_READY ") {
                    return Ok(url.to_string());
                }
            }
            Err(e) => return Err(format!("读取服务端输出失败：{e}")),
        }
    }
}
