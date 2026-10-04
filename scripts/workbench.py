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
:root{font:16px/1.5 system-ui,sans-serif;color:#17202a;background:#f5f7fb}body{max-width:980px;margin:0 auto;padding:32px}main{background:#fff;border:1px solid #dfe5ee;border-radius:16px;padding:28px;box-shadow:0 8px 30px #17202a12}h1{margin-top:0}label{display:block;margin:14px 0 6px;font-weight:650}input,textarea,select{width:100%;box-sizing:border-box;padding:10px;border:1px solid #c7d0dc;border-radius:8px;font:inherit}button{margin-top:18px;padding:11px 16px;border:0;border-radius:8px;background:#315efb;color:#fff;font-weight:700;cursor:pointer}button.secondary{background:#e8edf5;color:#17202a}.card{border:1px solid #dfe5ee;border-radius:10px;padding:16px;margin:12px 0}.muted{color:#687386}.error{color:#a32626}.ok{color:#166534;white-space:pre-wrap}.row{display:flex;gap:10px;align-items:center}.row>*{flex:1}.hidden{display:none}
</style><body><main><h1>Brand System Workbench</h1><p class="muted">在网页中初始化和继续品牌工作区。不会自动覆盖已有文件。</p>
<p id="notice" class="error"></p><section id="scan"><h2>选择工作区文件夹</h2><p class="muted">已扫描当前目录和一级子目录。推荐项会明确标注，你可以先选择文件夹，再决定继续或创建。</p><select id="folder" aria-label="工作区文件夹"></select><p id="folderHint" class="muted"></p><div class="row"><button onclick="useSelected()">使用这个文件夹</button><button onclick="scan()" class="secondary">重新扫描</button></div></section>
<section id="form" class="hidden"><h2>初始化品牌工作区</h2><p id="target" class="muted"></p><label>品牌正式名称</label><input id="official" placeholder="例如 Tidewell"><label>一句话产品描述</label><textarea id="oneLiner" rows="2" placeholder="给谁解决什么问题"></textarea><label>工作区路径</label><input id="path"><button onclick="initWorkspace()">创建工作区并打开阶段 0</button><p id="result"></p></section>
<section id="workspace" class="hidden"><h2>工作区状态</h2><div id="state"></div><button onclick="checkWorkspace()">运行当前阶段检查</button><button onclick="openForm()" class="secondary">新建工作区</button><pre id="check"></pre></section></main>
<script>
let current='';
async function get(path,opts){let r=await fetch(path,opts);let j=await r.json();if(!r.ok)throw Error(j.error||'请求失败');return j}
let recommended='';let items=[];
async function scan(){try{
 const j=await get('/api/scan'); recommended=j.recommended;items=j.items;const el=document.querySelector('#folder');el.replaceChildren();document.querySelector('#notice').textContent='';
 const fresh=document.createElement('option');fresh.value=recommended;fresh.textContent='推荐：新建 '+recommended;fresh.dataset.workspace='false';el.appendChild(fresh);
 items.forEach(x=>{const option=document.createElement('option');option.value=x.path;option.textContent=(x.workspace?'已有工作区：':'已有文件夹：')+x.path;option.dataset.workspace=String(x.workspace);el.appendChild(option)});
 updateFolderHint();
}catch(e){document.querySelector('#notice').textContent='工作台连接失败：'+e.message+'。请确认 workbench.py 仍在运行。'}}
document.addEventListener('change',e=>{if(e.target.id==='folder')updateFolderHint()});
function updateFolderHint(){const el=document.querySelector('#folder');const option=el.options[el.selectedIndex];document.querySelector('#folderHint').textContent=option&&option.dataset.workspace==='true'?'这是已有品牌工作区，可以继续。':'将在选定目录中创建品牌工作区；如果目录已有其他文件，建议保留推荐的新文件夹。'}
function useSelected(){const el=document.querySelector('#folder');const option=el.options[el.selectedIndex];if(option.dataset.workspace==='true')selectPath(option.value,true);else openForm(option.value)}
function selectPath(p,existing){if(existing){current=p;document.querySelector('#scan').classList.add('hidden');document.querySelector('#workspace').classList.remove('hidden');loadState()}else{openForm(p)}}
function openForm(p){document.querySelector('#scan').classList.add('hidden');document.querySelector('#workspace').classList.add('hidden');document.querySelector('#form').classList.remove('hidden');document.querySelector('#path').value=p||recommended;document.querySelector('#target').textContent=p?'将在此目录创建品牌工作区。':'请选择一个目录或输入新子目录路径。'}
async function initWorkspace(){let body={path:document.querySelector('#path').value,official:document.querySelector('#official').value,oneLiner:document.querySelector('#oneLiner').value};try{let j=await get('/api/init',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});current=j.path;document.querySelector('#form').classList.add('hidden');document.querySelector('#workspace').classList.remove('hidden');await loadState();document.querySelector('#result').textContent='已创建';}catch(e){document.querySelector('#result').className='error';document.querySelector('#result').textContent=e.message}}
async function loadState(){let j=await get('/api/state?path='+encodeURIComponent(current));document.querySelector('#state').innerHTML='<div class="card"><b>'+esc(j.path)+'</b><br>阶段 '+j.phase+' · '+esc(j.state)+'<br>下一步：'+esc((j.next||[]).join('、')||'填写 brief 并生成方向审阅页')+'</div>'}
async function checkWorkspace(){try{let j=await get('/api/check?path='+encodeURIComponent(current));document.querySelector('#check').className=j.ok?'ok':'error';document.querySelector('#check').textContent=j.output}catch(e){document.querySelector('#check').textContent=e.message}}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}scan();
</script></body></html>'''


def is_workspace(path):
    return (path / "brand.brief.json").is_file() and (path / "project" / "status.json").is_file()


def recommended_folder(root):
    target = root / "brand-workspace"
    suffix = 2
    while target.exists():
        target = root / ("brand-workspace-%d" % suffix)
        suffix += 1
    return target


def candidates(root):
    items = []
    if is_workspace(root):
        items.append({"path": str(root), "kind": "已有品牌工作区", "workspace": True})
    else:
        items.append({"path": str(root), "kind": "当前目录（可直接使用或创建子目录）", "workspace": False})
        for child in sorted(root.iterdir()):
            if child.is_dir() and child.name not in {".git", "node_modules", ".venv"}:
                items.append({"path": str(child), "kind": "已有目录" + (" · 品牌工作区" if is_workspace(child) else ""), "workspace": is_workspace(child)})
    return items


def init_workspace(path, official, one_liner):
    path = Path(path).expanduser().resolve()
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
            if parsed.path == "/api/scan": return self.send_json({"items": candidates(self.root), "recommended": str(recommended_folder(self.root))})
            path = Path(q.get("path", [""])[0]).expanduser().resolve()
            if not path.is_relative_to(self.root.parent): raise ValueError("路径必须位于启动目录或其子目录")
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
            path = init_workspace(target, body.get("official", ""), body.get("oneLiner", "")); self.send_json({"path": str(path)})
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
