"""Run the browser-first brand-system workspace initializer.

Usage: python3 scripts/workbench.py [DIRECTORY] [--port PORT]
The server only writes inside the selected workspace after an explicit UI action.
"""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "assets" / "brief.template.json"

HTML = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Brand System Workbench</title>
<style>
:root{font:16px/1.5 system-ui,sans-serif;color:#17202a;background:#f5f7fb}body{max-width:980px;margin:0 auto;padding:32px}main{background:#fff;border:1px solid #dfe5ee;border-radius:16px;padding:28px;box-shadow:0 8px 30px #17202a12}h1{margin-top:0}label{display:block;margin:14px 0 6px;font-weight:650}input,textarea,select{width:100%;box-sizing:border-box;padding:10px;border:1px solid #c7d0dc;border-radius:8px;font:inherit}input[readonly]{background:#f8fafc}.row{display:flex;gap:10px;align-items:stretch}.row input{flex:1}.row button{flex:0 0 auto;min-width:110px}button{margin-top:18px;padding:11px 16px;border:0;border-radius:8px;background:#315efb;color:#fff;font-weight:700;cursor:pointer}button.secondary{background:#e8edf5;color:#17202a}.card{border:1px solid #dfe5ee;border-radius:10px;padding:16px;margin:12px 0}.muted{color:#687386}.error{color:#a32626}.ok{color:#166534;white-space:pre-wrap}.log{background:#111827;color:#d1fae5;border-radius:10px;padding:14px;min-height:90px;max-height:220px;overflow:auto;white-space:pre-wrap;font:13px/1.55 ui-monospace,monospace}dialog{border:1px solid #dfe5ee;border-radius:12px;padding:22px;width:min(680px,calc(100% - 44px));box-shadow:0 20px 60px #17202a33}dialog::backdrop{background:#17202a66}.dialog-actions{display:flex;gap:10px;justify-content:flex-end}.dialog-actions button{margin-top:12px}
</style><body><main><h1>Brand System Workbench</h1><p class="muted">在同一个页面决定生成位置、产品信息，以及 AI 是否读取已有源码或文档。</p><p id="notice" class="error"></p>
<section id="setup"><h2>初始化品牌工作区</h2><label>生成目录</label><p class="muted">品牌系统文件会写入这里。默认使用 workbench 启动目录，推荐生成到它下面的 <code>brand</code>。</p><div class="row"><input id="outputPath" aria-label="生成目录" readonly><button id="chooseOutputButton" class="secondary">浏览选择</button></div><p id="outputHint" class="muted"></p>
<label>产品名称</label><input id="official" placeholder="例如 Tidewell"><label>产品功能和一句话描述</label><textarea id="oneLiner" rows="3" placeholder="例如：帮助独立团队管理客户反馈、路线图和发布计划"></textarea><label>主要功能（可选，用逗号分隔）</label><input id="capabilities" placeholder="例如：客户反馈、路线图、发布计划">
<label>可选：AI 参考资料目录</label><p class="muted">可选择源码、产品文档或设计资料所在目录。它只读，不会成为生成目录，也不会被改写。</p><div class="row"><input id="sourcePath" aria-label="AI 参考资料目录" readonly placeholder="未选择，AI 只使用本次填写的信息"><button id="chooseSourceButton" class="secondary">浏览选择</button></div><p id="sourceHint" class="muted"></p>
<button id="initButton">创建工作区并打开阶段 0</button><p id="result"></p><h3>活动日志</h3><div id="log" class="log" aria-live="polite"></div></section>
<section id="workspace"><h2>工作区状态</h2><div id="state"><p class="muted">创建或选择工作区后，当前阶段会显示在这里。</p></div><button id="checkButton">运行当前阶段检查</button><pre id="check"></pre><h3>活动日志</h3><div id="workspaceLog" class="log" aria-live="polite"></div></section></main>
<dialog id="chooser"><h3 id="chooserTitle">浏览选择目录</h3><p class="muted" id="chooserHelp"></p><select id="chooserList" size="8"></select><div class="dialog-actions"><button id="cancelChooser" class="secondary">取消</button><button id="confirmChooser">选择此目录</button></div></dialog>
<script>
let current='',recommended='',items=[],chooserMode='output'; const $=s=>document.querySelector(s); const dialog=$('#chooser');
function log(message){const boxes=[$('#log'),$('#workspaceLog')];boxes.forEach(box=>{box.textContent+=(box.textContent?'\n':'')+'['+new Date().toLocaleTimeString()+'] '+message;box.scrollTop=box.scrollHeight})}
async function get(path,opts){let r=await fetch(path,opts);let j=await r.json();if(!r.ok)throw Error(j.error||'请求失败');return j}
async function scan(path=''){try{log('正在扫描主目录候选…');const j=await get('/api/scan'+(path?'?path='+encodeURIComponent(path):''));recommended=j.recommended;items=j.items;$('#outputPath').value=recommended;renderItems();log('已找到 '+items.length+' 个可选择目录。')}catch(e){$('#notice').textContent='工作台连接失败：'+e.message;log('扫描失败：'+e.message)}}
function renderItems(){const list=$('#chooserList');list.replaceChildren();const fresh=document.createElement('option');fresh.value=recommended;fresh.textContent='推荐生成目录：'+recommended;fresh.dataset.workspace='false';list.appendChild(fresh);items.forEach(x=>{const o=document.createElement('option');o.value=x.path;o.textContent=(x.workspace?'已有工作区：':'目录：')+x.path;o.dataset.workspace=String(x.workspace);list.appendChild(o)});$('#outputHint').textContent='生成目录：'+$('#outputPath').value}
function openChooser(mode){chooserMode=mode;$('#chooserTitle').textContent=mode==='output'?'选择生成目录':'选择 AI 参考资料目录';$('#chooserHelp').textContent=mode==='output'?'默认推荐新建 brand；已有工作区可以直接继续。':'选择启动目录读取源码和文档，或选择它下面的具体目录。';dialog.showModal()}
function confirmChooser(){const o=$('#chooserList').selectedOptions[0];if(!o)return; if(chooserMode==='output'){$('#outputPath').value=o.value;$('#outputHint').textContent=o.dataset.workspace==='true'?'将继续此已有品牌工作区。':'品牌系统文件将在此目录创建。'}else{$('#sourcePath').value=o.value;checkSourcePath()}dialog.close()}
async function checkSourcePath(){const value=$('#sourcePath').value.trim();if(!value){$('#sourceHint').textContent='未选择参考资料目录。';return}try{log('正在读取参考资料目录：'+value);const j=await get('/api/scan?path='+encodeURIComponent(value));$('#sourceHint').textContent='已找到 '+j.fileCount+' 个可读取文件，不会写入此目录。';log('参考资料读取准备完成，共 '+j.fileCount+' 个文件。')}catch(e){$('#sourceHint').className='error';$('#sourceHint').textContent=e.message;log('参考资料读取失败：'+e.message)}}
function updateUrl(){const u=new URL(location.href);u.searchParams.set('workspace',current);u.searchParams.set('phase','0');history.pushState({},'',u)}
async function initWorkspace(){const output=$('#outputPath').value,source=$('#sourcePath').value.trim();const capabilities=$('#capabilities').value.split(/[,，]/).map(x=>x.trim()).filter(Boolean);try{log('开始初始化，生成目录：'+output);if(source)log('将读取参考资料：'+source);const j=await get('/api/init',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:output,official:$('#official').value,oneLiner:$('#oneLiner').value,capabilities,sourcePath:source})});current=j.path;updateUrl();log('已写入阶段 0 文件。');await loadState()}catch(e){$('#result').className='error';$('#result').textContent=e.message;log('初始化失败：'+e.message)}}
async function loadState(){if(!current)return;try{const j=await get('/api/state?path='+encodeURIComponent(current));$('#state').innerHTML='<div class="card"><b>'+esc(j.path)+'</b><br>阶段 '+j.phase+' · '+esc(j.state)+'<br>下一步：'+esc((j.next||[]).join('、')||'填写 brief 并生成方向审阅页')+'</div>';log('当前进度：阶段 '+j.phase+' · '+j.state)}catch(e){$('#state').textContent=e.message}}
async function checkWorkspace(){if(!current){$('#check').textContent='请先创建或选择工作区。';return}try{log('正在运行当前阶段检查…');const j=await get('/api/check?path='+encodeURIComponent(current));$('#check').className=j.ok?'ok':'error';$('#check').textContent=j.output;log(j.ok?'阶段检查完成。':'阶段检查发现问题。')}catch(e){$('#check').textContent=e.message;log('检查失败：'+e.message)}}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
$('#chooseOutputButton').addEventListener('click',()=>openChooser('output'));$('#chooseSourceButton').addEventListener('click',()=>openChooser('source'));$('#cancelChooser').addEventListener('click',()=>dialog.close());$('#confirmChooser').addEventListener('click',confirmChooser);$('#initButton').addEventListener('click',initWorkspace);$('#checkButton').addEventListener('click',checkWorkspace);window.addEventListener('popstate',()=>{const p=new URL(location.href).searchParams.get('workspace');if(p){current=p;loadState()}});scan();const initial=new URL(location.href).searchParams.get('workspace');if(initial){current=initial;loadState()}
</script></body></html>'''




def is_workspace(path):
    return (path / "brand.brief.json").is_file() and (path / "project" / "status.json").is_file()


def recommended_folder(root):
    target = root / "brand"
    suffix = 2
    while target.exists():
        target = root / ("brand-%d" % suffix)
        suffix += 1
    return target


def candidates(root):
    items = []
    try:
        file_count = sum(1 for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)
    except OSError:
        file_count = 0
    if is_workspace(root):
        items.append({"path": str(root), "kind": "已有品牌工作区", "workspace": True})
    else:
        items.append({"path": str(root), "kind": "指定目录 · %d 个可读取文件" % file_count, "workspace": False})
        for child in sorted(root.iterdir()):
            if child.is_dir() and child.name not in {".git", "node_modules", ".venv"}:
                items.append({"path": str(child), "kind": "已有目录" + (" · 品牌工作区" if is_workspace(child) else ""), "workspace": is_workspace(child)})
    return items


def init_workspace(path, official, one_liner, source_path=None, capabilities=None):
    path = Path(path).expanduser().resolve()
    source = None
    if source_path:
        source = Path(source_path).expanduser().resolve()
        if not source.is_dir() or not source.is_relative_to(path.parent.parent):
            raise ValueError("参考资料目录必须是启动目录父目录下的可读取文件夹")
    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()) and not is_workspace(path):
        # Existing files are safe to retain, but avoid silently claiming them as a workspace.
        if not (path / "brand.brief.json").exists():
            raise ValueError("目标目录已有内容，请选择新子目录，或明确使用已有品牌工作区")
    if is_workspace(path):
        return path
    brief = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    brief["name"]["official"] = official or None
    brief["product"]["oneLiner"] = one_liner or None
    brief["product"]["capabilities"] = capabilities or []
    brief["name"]["final"] = bool(official)
    (path / "brand.brief.json").write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = {
        "README.md": "# Brand workspace\n\nManaged by brand-system workbench.\n",
        "project/plan.md": "# Plan\n\nComplete brief, strategy, and three directions before G1.\n",
        "project/decisions.md": "# Decisions\n\nNo decisions recorded yet.\n",
        "project/handoff.md": "# Handoff\n\nOpen the workbench to continue.\n",
        "docs/scope-matrix.md": "# Scope matrix\n\n| module | status |\n|---|---|\n| brand foundation | required |\n",
        "docs/environment.md": "# Environment\n\nCreated by the browser workbench; verify tools during phase 0.\n",
        "docs/references.md": "# References\n\nNo external references recorded yet.\n",
        "docs/strategy.md": "# Strategy\n\nDraft pending brief completion.\n",
        "docs/voice.md": "# Voice\n\nDraft pending brief completion.\n",
    }
    for rel, content in files.items():
        target = path / rel; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(content, encoding="utf-8")
    if source_path:
        count = sum(1 for item in source.rglob("*") if item.is_file() and ".git" not in item.parts)
        (path / "docs/references.md").write_text(
            "# References\n\n"
            "AI reference source directory (read-only): `%s`\n\n"
            "Files available at initialization: %d\n" % (source, count), encoding="utf-8"
        )
        files = [str(item.relative_to(source)) for item in sorted(source.rglob("*")) if item.is_file() and ".git" not in item.parts]
        (path / "project/source.json").write_text(json.dumps({"root": str(source), "files": files}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (path / "project/status.json").write_text(json.dumps({"phase": 0, "state": "draft", "completed": [], "next": ["complete brief", "generate directions"], "blockers": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (path / "project/approvals.json").write_text("[]\n", encoding="utf-8")
    return path


class Handler(BaseHTTPRequestHandler):
    root = Path.cwd().resolve()
    def send_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                data = HTML.encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
            from urllib.parse import parse_qs
            q = parse_qs(parsed.query)
            path = Path(q.get("path", [""])[0]).expanduser().resolve()
            if parsed.path == "/api/scan":
                target = path if q.get("path") else self.root
                if not target.is_dir(): raise ValueError("指定路径不是可读取的文件夹")
                if not target.is_relative_to(self.root.parent): raise ValueError("为安全起见，请指定启动目录或其父目录下的文件夹")
                file_count = sum(1 for item in target.rglob("*") if item.is_file() and ".git" not in item.parts)
                return self.send_json({"root": str(target), "items": candidates(target), "fileCount": file_count, "recommended": str(recommended_folder(target))})
            if parsed.path == "/api/state":
                status = json.loads((path / "project/status.json").read_text()); return self.send_json({"path": str(path), **status})
            if parsed.path == "/api/check":
                import subprocess, sys
                p = subprocess.run([sys.executable, str(ROOT / "scripts/check_workspace.py"), str(path), "--phase", str(json.loads((path / "project/status.json").read_text())["phase"])], capture_output=True, text=True)
                return self.send_json({"ok": p.returncode == 0, "output": p.stdout + p.stderr})
            self.send_json({"error": "not found"}, 404)
        except Exception as exc: self.send_json({"error": str(exc)}, 400)
    def do_POST(self):
        if urlparse(self.path).path != "/api/init": return self.send_json({"error": "not found"}, 404)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            target = Path(body.get("path") or (self.root / "brand-workspace"))
            if not target.is_absolute(): target = self.root / target
            path = init_workspace(target, body.get("official", ""), body.get("oneLiner", ""), body.get("sourcePath") or None, body.get("capabilities") or []); self.send_json({"path": str(path)})
        except Exception as exc: self.send_json({"error": str(exc)}, 400)
    def log_message(self, *_): pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("directory", nargs="?", default="."); parser.add_argument("--port", type=int, default=8765); args = parser.parse_args(argv)
    Handler.root = Path(args.directory).expanduser().resolve(); Handler.root.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("Brand System Workbench: http://127.0.0.1:%d/" % args.port, flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == "__main__": main()
