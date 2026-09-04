"""HTTP adapter for the stock research service layer (routing, static files, error mapping)."""

from __future__ import annotations

import json
import mimetypes
import zipfile
import xml.etree.ElementTree as ET
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from .service import (
    add_company_source,
    add_llm_model_payload,
    add_llm_models_payload,
    add_trusted_host_payload,
    chat_entry_payload,
    chat_payload,
    configure_provider,
    create_project_with_session,
    create_session_for_project,
    discover_llm_models_payload,
    history_payload,
    llm_config_payload,
    provider_status,
    remove_company,
    remove_company_source,
    remove_llm_model_payload,
    remove_llm_provider_payload,
    remove_trusted_host_payload,
    run_research_payload,
    save_llm_provider_payload,
    set_approval_mode_payload,
    set_llm_selection_payload,
    set_session_status,
)


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI Stock Research</title>
  <style>
    :root { color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; background: #f5f7fb; color: #172033; }
    main { max-width: 1100px; margin: 0 auto; padding: 32px 20px 64px; }
    h1 { margin-bottom: 4px; } .subtle { color: #667085; }
    .workspace { display: grid; grid-template-columns: 260px 1fr; gap: 20px; align-items: start; }
    .card { background: white; border: 1px solid #e4e7ec; border-radius: 12px; padding: 18px; box-shadow: 0 3px 12px #1018280a; }
    label { display: block; margin: 12px 0 6px; font-size: 13px; font-weight: 600; }
    input, textarea { width: 100%; box-sizing: border-box; border: 1px solid #d0d5dd; border-radius: 8px; padding: 9px 10px; font: inherit; }
    textarea { min-height: 150px; resize: vertical; }
    button { margin-top: 16px; border: 0; border-radius: 8px; padding: 10px 14px; background: #2457d6; color: white; font-weight: 600; cursor: pointer; }
    button:disabled { opacity: .6; cursor: wait; }
    #status { margin: 14px 0; color: #475467; min-height: 22px; }
    pre { white-space: pre-wrap; word-break: break-word; background: #101828; color: #e6edf5; border-radius: 8px; padding: 14px; overflow: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; } th, td { text-align: left; border-bottom: 1px solid #eaecf0; padding: 8px 5px; }
    .pill { display: inline-block; padding: 3px 8px; background: #eef4ff; color: #2457d6; border-radius: 999px; font-size: 12px; margin: 3px 4px 3px 0; }
    .side-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; } .side-head button { margin:0; padding:5px 8px; }
    .company { display:block; width:100%; text-align:left; background:#f8faff; color:#172033; margin:7px 0 0; padding:10px; border:1px solid #e4e7ec; } .company span { display:block; color:#667085; font-size:12px; margin-top:4px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .chat-shell { min-height:620px; display:flex; flex-direction:column; } .messages { flex:1; min-height:400px; max-height:600px; overflow:auto; padding:14px 0; } .welcome { color:#475467; padding:30px 12px; text-align:center; }
    .message { display:flex; margin:10px 0; } .message.user { justify-content:flex-end; } .bubble { max-width:82%; border-radius:12px; padding:10px 12px; background:#f2f4f7; } .message.user .bubble { background:#2457d6; color:#fff; }
    .composer { display:flex; gap:8px; border-top:1px solid #eaecf0; padding-top:14px; } .composer input { flex:1; } .composer button { margin:0; }
    .settings { margin-top:12px; color:#667085; font-size:13px; } .settings-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:8px; } .settings label { margin:0; } .settings .wide { grid-column:1 / -1; } .settings textarea { min-height:70px; }
    .report { margin-top:10px; color:inherit; } .report pre { max-height:320px; }
    @media (max-width: 800px) { .workspace { grid-template-columns: 1fr; } .sidebar { order:2; } .settings-grid { grid-template-columns:1fr; } .settings .wide { grid-column:auto; } }
  </style>
</head>
<body>
<main>
  <header><h1>AI Stock Research</h1><p class="subtle">你的长期股票研究助手</p></header>
  <div class="workspace">
    <aside class="card sidebar"><div class="side-head"><strong>最近研究</strong><button id="refresh" type="button">刷新</button></div><div id="companies" class="subtle">正在加载……</div></aside>
    <section class="card chat-shell">
      <div id="chat-title"><strong>选择一家公司开始研究</strong></div>
      <div id="messages" class="messages"><div class="welcome"><h2>从一个问题开始</h2><p>例如：“研究腾讯是否适合长期持有，重点看现金流和估值。”</p><p class="subtle">研究结果、进度、证据和历史报告都会留在这个会话里。</p></div></div>
      <form id="chat-form" class="composer"><input id="message" autocomplete="off" placeholder="输入研究问题或追问……" required /><button id="send" type="submit">发送</button></form>
      <details class="settings"><summary>研究设置（可选）</summary><div class="settings-grid"><label>公司名称<input id="name" value="腾讯（示例）" /></label><label>港股代码<input id="symbol" value="00700" /></label><label>截止日期<input id="as-of-date" type="date" value="2025-12-31" /></label><label class="wide">资料文本<textarea id="document" placeholder="粘贴财报或公告文本；留空使用示例资料。"></textarea></label></div></details>
      <div id="status" class="subtle">正在检查模型配置……</div>
    </section>
  </div>
</main>
<script>
const form = document.getElementById('chat-form');
const button = document.getElementById('send');
const status = document.getElementById('status');
const messages = document.getElementById('messages');
const companies = document.getElementById('companies');
const title = document.getElementById('chat-title');
let currentSessionId = null;
const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function addMessage(role, text, extra='') { messages.insertAdjacentHTML('beforeend', `<div class="message ${role}"><div class="bubble">${escape(text).replace(/\n/g,'<br>')}${extra}</div></div>`); messages.scrollTop = messages.scrollHeight; }
function addReport(report) { addMessage('assistant', report.summary.join('\n'), `<details class="report"><summary>打开研究报告</summary><pre>${escape(report.markdown)}</pre></details>`); }
function clearMessages() { messages.innerHTML = ''; }
async function openSession(sessionId, companyName) {
  currentSessionId = sessionId; title.innerHTML = `<strong>${escape(companyName || '研究会话')}</strong>`; clearMessages();
  const data = await (await fetch('/api/sessions/' + sessionId)).json();
  (data.messages || []).forEach(item => addMessage(item.role === 'user' ? 'user' : 'assistant', item.content?.text || item.content?.title || ''));
  if (!data.messages?.length) addMessage('assistant', '这是一个新的研究会话。告诉我你想研究什么。');
}
async function loadCompanies() {
  const rows = await (await fetch('/api/companies')).json();
  if (!rows.length) { companies.textContent = '暂无公司，直接在下方输入问题即可开始。'; return; }
  companies.innerHTML = rows.map(row => `<button type="button" class="company" data-project="${escape(row.id)}"><strong>${escape(row.name)}</strong><span>${escape(row.symbol)} · ${escape(row.latest_event_preview || '暂无活动')}</span></button>`).join('');
  companies.querySelectorAll('[data-project]').forEach(btn => btn.addEventListener('click', async () => {
    const sessions = await (await fetch('/api/projects/' + btn.dataset.project + '/sessions')).json();
    if (sessions.length) await openSession(sessions[0].id, btn.querySelector('strong').textContent);
  }));
}
form.addEventListener('submit', async (event) => {
  event.preventDefault(); button.disabled = true; status.textContent = '正在处理……';
  const text = document.getElementById('message').value.trim(); if (!text) return;
  document.getElementById('message').value = ''; addMessage('user', text);
  try {
    const payload = { content: text, as_of_date: document.getElementById('as-of-date').value };
    let data;
    if (currentSessionId) {
      const response = await fetch('/api/sessions/' + currentSessionId + '/messages', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
      data = await response.json(); if (!response.ok) throw new Error(data.error || '处理失败');
      if (data.report) addReport(data.report); else if (data.message) addMessage('assistant', data.message);
    } else {
      const response = await fetch('/api/chat', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:document.getElementById('name').value, symbol:document.getElementById('symbol').value, as_of_date:document.getElementById('as-of-date').value, content:text}) });
      data = await response.json(); if (!response.ok) throw new Error(data.error || '处理失败'); currentSessionId = data.session_id; title.innerHTML = `<strong>${escape(document.getElementById('name').value)}</strong>`; if (data.report) addReport(data.report); else if (data.message) addMessage('assistant', data.message);
    }
    status.textContent = '已完成'; await loadCompanies();
  } catch (error) { status.textContent = error.message; addMessage('assistant', '处理失败：' + error.message); }
  finally { button.disabled = false; }
});
document.getElementById('refresh').addEventListener('click', loadCompanies); loadCompanies();
fetch('/api/status').then(response => response.json()).then(data => {
  status.textContent = data.llm_enabled ? `模型：${data.model}（${data.provider}）` : '未配置真实模型，请先在设置中接入模型';
}).catch(() => { status.textContent = '模型状态暂时不可用'; });
</script>
</body>
</html>"""


def _frontend_dist() -> Path:
    return Path(__file__).parents[2] / "frontend" / "dist"


class ResearchRequestHandler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/" or self.path == "/index.html":
            built_index = _frontend_dist() / "index.html"
            if built_index.is_file():
                self._send(200, built_index.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path.startswith("/assets/"):
            asset = (_frontend_dist() / self.path.removeprefix("/" )).resolve()
            root = _frontend_dist().resolve()
            if asset.is_file() and root in asset.parents:
                self._send(200, asset.read_bytes(), mimetypes.guess_type(str(asset))[0] or "application/octet-stream")
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        elif self.path in {"/api/status", "/api/settings"}:
            self._send(200, json.dumps(provider_status(), ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
        elif self.path == "/api/llm/config":
            self._send(200, json.dumps(llm_config_payload(), ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
        elif self.path.startswith(("/api/companies", "/api/company-catalog", "/api/company-panel", "/api/article", "/api/quote/", "/api/news", "/api/projects", "/api/search", "/api/runs", "/api/jobs/", "/api/reports/", "/api/sessions/", "/api/trusted-hosts")):
            try:
                response = history_payload(self.path)
                self._send(200, json.dumps(response, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")
            except KeyError as exc:
                self._send(404, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            except (ValueError, TypeError) as exc:
                self._send(400, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
                self._send(502, json.dumps({"error": f"港股公司列表暂时不可用：{exc}"}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path not in {"/api/research", "/api/chat", "/api/settings", "/api/projects", "/api/trusted-hosts", "/api/llm/providers", "/api/llm/models", "/api/llm/models/discover", "/api/llm/models/batch", "/api/llm/selection", "/api/llm/approval"} and not self.path.startswith(("/api/projects/", "/api/sessions/", "/api/company-panel", "/api/reports/", "/api/llm/providers/")):
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 2_000_000:
                raise ValueError("request body is too large")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if self.path == "/api/settings":
                response = configure_provider(payload)
            elif self.path == "/api/projects":
                response = create_project_with_session(payload)
            elif self.path.startswith("/api/sessions/") and self.path.endswith("/messages"):
                session_id = UUID(self.path.split("/")[3])
                response = chat_payload(session_id, str(payload.get("content", "")), as_of_date=date.fromisoformat(payload["as_of_date"]) if payload.get("as_of_date") else None, llm_config=payload.get("llm"), document_urls=payload.get("document_urls"))
            elif self.path.startswith("/api/projects/") and self.path.endswith("/sessions"):
                response = create_session_for_project(UUID(self.path.split("/")[3]), str(payload.get("title") or "新研究"))
            elif self.path.startswith("/api/sessions/") and self.path.endswith("/archive"):
                response = set_session_status(UUID(self.path.split("/")[3]), "archived")
            elif self.path.startswith("/api/sessions/") and self.path.endswith("/activate"):
                response = set_session_status(UUID(self.path.split("/")[3]), "active")
            elif self.path == "/api/research":
                response = run_research_payload(payload)
            elif self.path.startswith("/api/reports/") and self.path.endswith("/recalculate"):
                report_id = self.path.removeprefix("/api/reports/").removesuffix("/recalculate")
                response = recalculate_report(report_id, payload.get("dcf_assumptions") if isinstance(payload, dict) else None)
            elif self.path == "/api/company-panel/sources":
                symbol = str(payload.get("symbol", "")).strip()
                market = str(payload.get("market", "HK")).strip().upper() or "HK"
                url = str(payload.get("url", "")).strip()
                title = str(payload.get("title", "")).strip() or None
                source_class = str(payload.get("source_class", "private")).strip() or "private"
                if not symbol or not url:
                    raise ValueError("symbol and url are required")
                response = add_company_source(symbol, market, url, title, source_class)
            elif self.path == "/api/trusted-hosts":
                response = add_trusted_host_payload(payload)
            elif self.path == "/api/llm/providers":
                response = save_llm_provider_payload(payload)
            elif self.path.startswith("/api/llm/providers/"):
                payload["id"] = self.path.rsplit("/", 1)[-1]
                response = save_llm_provider_payload(payload)
            elif self.path == "/api/llm/models":
                response = add_llm_model_payload(payload)
            elif self.path == "/api/llm/models/discover":
                response = discover_llm_models_payload(payload)
            elif self.path == "/api/llm/models/batch":
                response = add_llm_models_payload(payload)
            elif self.path == "/api/llm/selection":
                response = set_llm_selection_payload(payload)
            elif self.path == "/api/llm/approval":
                response = set_approval_mode_payload(payload)
            elif self.path == "/api/chat":
                response = chat_entry_payload(payload)
            else:
                self._send(404, b'{"error":"not found"}', "application/json")
                return
            self._send(200, json.dumps(response, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")
        except Exception as exc:
            self._send(400, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path.startswith("/api/llm/providers/"):
            try:
                response = remove_llm_provider_payload(self.path.rsplit("/", 1)[-1])
                self._send(200, json.dumps(response, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            except Exception as exc:
                self._send(400, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        if self.path.startswith("/api/llm/models/"):
            try:
                response = remove_llm_model_payload(self.path.rsplit("/", 1)[-1])
                self._send(200, json.dumps(response, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            except Exception as exc:
                self._send(400, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        if self.path.startswith("/api/trusted-hosts/"):
            try:
                remove_trusted_host_payload(self.path.rsplit("/", 1)[-1])
                self._send(200, b'{"ok": true}', "application/json; charset=utf-8")
            except Exception as exc:
                self._send(400, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        if not self.path.startswith("/api/company-panel/sources/"):
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        try:
            remove_company_source(self.path.rsplit("/", 1)[-1])
            self._send(200, b'{"ok": true}', "application/json; charset=utf-8")
        except Exception as exc:
            self._send(400, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    _recover_jobs_in_background()
    server = ThreadingHTTPServer((host, port), ResearchRequestHandler)
    print(f"AI Stock Research MVP running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _recover_jobs_in_background() -> None:
    """Resume jobs interrupted by a previous process, without blocking startup."""
    from .jobs import recover_stuck_jobs
    from .service import database_path

    def _run() -> None:
        try:
            summary = recover_stuck_jobs(database_path())
            if summary.get("recovered") or summary.get("failed"):
                print(f"任务恢复完成：续跑 {summary.get('recovered', 0)} 个，放弃 {summary.get('failed', 0)} 个")
        except Exception as exc:  # recovery must never block the server
            print(f"任务恢复失败：{exc}")

    import threading

    threading.Thread(target=_run, name="job-recovery", daemon=True).start()
