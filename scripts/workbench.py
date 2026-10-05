"""Run the browser-first brand-system workspace initializer.

Usage: python3 scripts/workbench.py [DIRECTORY] [--port PORT]
The server only writes inside the selected workspace after an explicit UI action.
"""
import argparse
import copy
import json
import os
import threading
import time
import subprocess
import selectors
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "assets" / "brief.template.json"

HTML = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Brand System Workbench</title>
<style>
:root{font:16px/1.5 system-ui,sans-serif;color:#17202a;background:#f5f7fb}body{max-width:980px;margin:0 auto;padding:32px}main{background:#fff;border:1px solid #dfe5ee;border-radius:16px;padding:28px;box-shadow:0 8px 30px #17202a12}.deliverables{margin-top:16px;padding:18px;border:1px solid #cbd8ef;border-radius:12px;background:#f7f9ff}.deliverables h3{margin:0 0 12px}.deliverable-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.deliverable-card{display:block;padding:12px;border:1px solid #dfe5ee;border-radius:9px;background:#fff;color:#17202a;text-decoration:none}.deliverable-card:hover{border-color:#315efb;background:#f8faff}.deliverable-card strong{display:block;color:#315efb}.deliverable-card small{color:#687386}h1{margin-top:0}label{display:block;margin:14px 0 6px;font-weight:650}input,textarea,select{width:100%;box-sizing:border-box;padding:10px;border:1px solid #c7d0dc;border-radius:8px;font:inherit}input[readonly]{background:#f8fafc}.row{display:flex;gap:10px;align-items:center}.row input{flex:1}.row button{flex:0 0 auto;min-width:110px;margin-top:0;height:46px}button{margin-top:18px;padding:11px 16px;border:0;border-radius:8px;background:#315efb;color:#fff;font-weight:700;cursor:pointer}button:disabled{opacity:.48;cursor:wait}button.secondary{background:#e8edf5;color:#17202a}.card{border:1px solid #dfe5ee;border-radius:10px;padding:16px;margin:12px 0}.muted{color:#687386}.error{color:#a32626}.ok{color:#166534;white-space:pre-wrap}.log,.phase-log{background:#111827;color:#d1fae5;border-radius:10px;padding:14px;min-height:60px;max-height:180px;overflow:auto;white-space:pre-wrap;user-select:text;font:13px/1.55 ui-monospace,monospace}.phase{border:1px solid #dfe5ee;border-radius:10px;margin:12px 0;overflow:hidden}.phase-header{display:block;width:100%;margin:0;border:0;border-radius:0;background:#eef2f7;color:#17202a;text-align:left}.phase.current .phase-header{background:#e8efff;color:#19327a;border-left:4px solid #315efb}.phase.current .phase-header:hover{background:#dce7ff}.phase-header:disabled{cursor:not-allowed;color:#7d8796;background:#f7f8fa}.phase-body{padding:16px;background:#fff}.phase-body[hidden]{display:none}.phase-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.review-link{font-weight:700;color:#315efb}.phase-actions button{margin-top:0}.phase-check{white-space:pre-wrap;margin:10px 0 0;color:#a32626}.phase-check.ok{color:#166534}dialog{border:1px solid #dfe5ee;border-radius:12px;padding:22px;width:min(680px,calc(100% - 44px));box-shadow:0 20px 60px #17202a33}dialog::backdrop{background:#17202a66}.dialog-actions{display:flex;gap:10px;justify-content:flex-end}.dialog-actions button{margin-top:12px}
</style><body><main><h1>Brand System Workbench</h1><p class="muted">在同一个页面决定生成位置、产品信息，以及 AI 是否读取已有源码或文档。</p><p id="notice" class="error"></p>
<section id="setup"><h2>初始化品牌工作区</h2><label>生成目录</label><p class="muted">品牌系统文件会写入这里。默认使用 workbench 启动目录，推荐生成到它下面的 <code>brand</code>。</p><div class="row"><input id="outputPath" aria-label="生成目录" readonly><button id="chooseOutputButton" class="secondary">浏览选择</button></div><p id="outputHint" class="muted"></p>
<label>产品名称</label><input id="official" placeholder="例如 Tidewell"><label>产品功能和一句话描述</label><textarea id="oneLiner" rows="3" placeholder="例如：帮助独立团队管理客户反馈、路线图和发布计划"></textarea><label>主要功能（可选，用逗号分隔）</label><input id="capabilities" placeholder="例如：客户反馈、路线图、发布计划"><label>技术栈</label><select id="stackProfile"><option value="html-css-js">html-css-js</option><option value="react">react</option></select><label>目标平台（可多选）</label><select id="platforms" multiple size="4"><option value="web" selected>web</option><option value="desktop">desktop</option><option value="ios">ios</option><option value="android">android</option></select>
<label>可选：AI 参考资料目录</label><p class="muted">可选择源码、产品文档或设计资料所在目录。它只读，不会成为生成目录，也不会被改写。</p><div class="row"><input id="sourcePath" aria-label="AI 参考资料目录" readonly placeholder="未选择，AI 只使用本次填写的信息"><button id="chooseSourceButton" class="secondary">浏览选择</button></div><p id="sourceHint" class="muted"></p>
<button id="initButton">创建工作区并打开阶段 0</button><p id="result"></p></section>
<section id="workspace"><h2>工作区状态</h2><div id="state"><p class="muted">创建或选择工作区后，当前阶段会显示在这里。</p></div><div id="uiContext" class="card" aria-live="polite"><p class="muted">UI unit 状态会显示在这里。</p></div><div id="phases"></div></section></main>
<dialog id="chooser"><h3 id="chooserTitle">浏览选择目录</h3><p class="muted" id="chooserHelp"></p><select id="chooserList" size="8"></select><div class="dialog-actions"><button id="cancelChooser" class="secondary">取消</button><button id="confirmChooser">选择此目录</button></div></dialog>
<script>
let current='',recommended='',items=[],chooserMode='output',activePhase=0,phaseLogs=Array.from({length:6},()=>[]); const $=s=>document.querySelector(s); const dialog=$('#chooser');
function log(message,phase=activePhase){phaseLogs[phase].push('['+new Date().toLocaleTimeString()+'] '+message);const box=$('#phaseLog-'+phase);if(box){box.textContent=phaseLogs[phase].join('\n');box.scrollTop=box.scrollHeight}}
async function get(path,opts){let r=await fetch(path,opts);let j=await r.json();if(!r.ok)throw Error(j.error||'请求失败');return j}
async function scan(path=''){try{log('正在扫描主目录候选…',0);const j=await get('/api/scan'+(path?'?path='+encodeURIComponent(path):''));recommended=j.recommended;items=j.items;$('#outputPath').value=recommended;$('#outputPath').dataset.workspace='false';$('#initButton').textContent='创建工作区并打开阶段 0';renderItems();log('已找到 '+items.length+' 个可选择目录。',0)}catch(e){$('#notice').textContent='工作台连接失败：'+e.message;log('扫描失败：'+e.message,0)}}
function renderItems(){const list=$('#chooserList');list.replaceChildren();const fresh=document.createElement('option');fresh.value=recommended;fresh.textContent='推荐生成目录：'+recommended;fresh.dataset.workspace='false';list.appendChild(fresh);items.forEach(x=>{const o=document.createElement('option');o.value=x.path;o.textContent=(x.workspace?'已有工作区：':'目录：')+x.path;o.dataset.workspace=String(x.workspace);list.appendChild(o)});$('#outputHint').textContent='生成目录：'+$('#outputPath').value}
function openChooser(mode){chooserMode=mode;$('#chooserTitle').textContent=mode==='output'?'选择生成目录':'选择 AI 参考资料目录';$('#chooserHelp').textContent=mode==='output'?'默认推荐新建 brand；已有工作区可以直接继续。':'选择启动目录读取源码和文档，或选择它下面的具体目录。';dialog.showModal()}
function confirmChooser(){const o=$('#chooserList').selectedOptions[0];if(!o)return; if(chooserMode==='output'){const existing=o.dataset.workspace==='true';$('#outputPath').value=o.value;$('#outputPath').dataset.workspace=String(existing);$('#initButton').textContent=existing?'打开已有工作区':'创建工作区并打开阶段 0';$('#outputHint').textContent=existing?'将打开此已有品牌工作区，保留当前阶段和文件。':'品牌系统文件将在此目录创建。'}else{$('#sourcePath').value=o.value;checkSourcePath()}dialog.close()}
async function checkSourcePath(){const value=$('#sourcePath').value.trim();if(!value){$('#sourceHint').textContent='未选择参考资料目录。';return}try{log('正在读取参考资料目录：'+value,0);const j=await get('/api/scan?path='+encodeURIComponent(value));$('#sourceHint').textContent='已找到 '+j.fileCount+' 个可读取文件，不会写入此目录。';log('参考资料读取准备完成，共 '+j.fileCount+' 个文件。',0)}catch(e){$('#sourceHint').className='error';$('#sourceHint').textContent=e.message;log('参考资料读取失败：'+e.message,0)}}
function phaseInstruction(phase){return ['先补全 brief、环境、策略和范围文件，再检查进入 Phase 1。','先点击“生成 Phase 1 方向”，查看三套方案后选择 A/B/C，再记录 G1。','先确认 G1；然后点击“生成并检查 Phase 2”，系统会生成 review/02-identity.html、BRAND_SYSTEM.md、config/brand.json 及身份文档。','先查看 Phase 3 系统审阅页并确认 G3，再生成或进入 Phase 4。','先查看 Phase 4 资产审阅页并确认 G4，再生成或进入 Phase 5。','先生成 Phase 5 发布交付物，再检查最终交付物。'][phase]||'先完成当前阶段标注的交付物，再重新检查。'}
function updateUrl(phase,replace=false){const u=new URL(location.href);u.searchParams.set('workspace',current);u.searchParams.set('phase',String(phase));(replace?history.replaceState:history.pushState).call(history,{},'',u)}
async function initWorkspace(){const output=$('#outputPath').value,source=$('#sourcePath').value.trim(),existing=$('#outputPath').dataset.workspace==='true';const capabilities=$('#capabilities').value.split(/[,，]/).map(x=>x.trim()).filter(Boolean);const platforms=Array.from($('#platforms').selectedOptions).map(x=>x.value);try{if(existing){current=output;await loadState();log('已打开已有品牌工作区。',activePhase);return}log('开始初始化，生成目录：'+output,0);if(source)log('将读取参考资料：'+source,0);const j=await get('/api/init',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:output,official:$('#official').value,oneLiner:$('#oneLiner').value,capabilities,sourcePath:source,stackProfile:$('#stackProfile').value,platforms})});current=j.path;updateUrl(0);log('已写入阶段 0 文件。',0);await loadState()}catch(e){$('#result').className='error';$('#result').textContent=e.message;log('初始化失败：'+e.message,0)}}
function renderPhases(currentPhase){activePhase=currentPhase;const names=['发现与计划','三套方向','品牌身份','设计系统','资产与平台','交付与发布'];const desc=['完善 brief、环境、策略和范围。','生成三套真正不同的方向并准备 G1。','完成 Logo、颜色、字体和身份规范。','完成 tokens、组件和无障碍规范。','生成平台资产、图标和导出清单。','完成 QA、交接和发布包。'];const box=$('#phases');box.replaceChildren();for(let i=0;i<6;i++){const panel=document.createElement('section');panel.className='phase '+(i===currentPhase?'current':'');const header=document.createElement('button');header.className='phase-header';header.textContent=(i<currentPhase?'✓ ':i===currentPhase?'● ':'🔒 ')+'Phase '+i+' · '+names[i]+' · '+desc[i];header.disabled=i>currentPhase;const body=document.createElement('div');body.className='phase-body';const actions=i===currentPhase&&i<=5?((i===1?'<button id="generateButton">生成 Phase 1 方向</button><div id="reviewGate" hidden><a class="review-link" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=review/01-directions.html">查看三套方向</a><label class="direction-choice">选择方向 <select id="directionChoice"><option value="A">A</option><option value="B">B</option><option value="C">C</option></select></label><button id="approveProgressButton">选择方向并进入 Phase 2</button></div>':'')+(i===2?'<button id="progressButton">生成 Phase 2 身份并检查 → Phase 3</button><div id="reviewGate2" hidden><a class="review-link" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=review/02-identity.html">查看 Phase 2 身份</a><button id="approveG2Button">确认 G2 并进入 Phase 3</button></div>':'')+(i===3?'<button id="progressButton">生成 Phase 3 系统并检查 → Phase 4</button><div id="reviewGate3" hidden><a class="review-link" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=review/03-system.html">查看 Phase 3 系统</a><button id="approveG3Button">确认 G3 并进入 Phase 4</button></div>':'')+(i===4?'<button id="progressButton">生成 Phase 4 资产并检查 → Phase 5</button><div id="reviewGate4" hidden><a class="review-link" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=review/04-assets.html">查看 Phase 4 资产</a><button id="approveG4Button">确认 G4 并进入 Phase 5</button></div>':'')+(i===5?'<button id="progressButton">生成 Phase 5 发布包</button><div id="reviewGate5" class="deliverables" hidden><h3>Phase 5 交付物</h3><p class="muted">生成完成后，从这里查看和交接品牌系统。</p><div class="deliverable-grid"><a class="deliverable-card" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=review/05-release.html"><strong>发布审阅页</strong><small>review/05-release.html</small></a><a class="deliverable-card" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=CHANGELOG.md"><strong>变更记录</strong><small>CHANGELOG.md</small></a><a class="deliverable-card" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=docs/handoff-by-role.md"><strong>交接文档</strong><small>docs/handoff-by-role.md</small></a><a class="deliverable-card" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=reports/qa-report.md"><strong>QA 报告</strong><small>reports/qa-report.md</small></a></div></div>':'')+(i===0?'<button id="progressButton">检查并进入 Phase 1</button>':'')+'<pre id="phaseCheck-'+i+'" class="phase-check"></pre>'):'' ;body.innerHTML='<p class="muted">'+(i<currentPhase?'已完成，可点击标题回看。':i===currentPhase?'当前阶段，完成检查后进入下一阶段。':'尚未到达，完成前置阶段后解锁。')+'</p>'+(i>0&&i<=5?'<p id=\"phaseJobStatus\" class=\"muted\" aria-live=\"polite\"></p>':'')+actions+(i===1&&currentPhase>1?'<div class=\"phase-actions\"><label>补录 G1 方向 <select id=\"historyDirectionChoice\"><option value=\"A\">A</option><option value=\"B\">B</option><option value=\"C\">C</option></select></label><button id=\"approveHistoryButton\" class=\"secondary\">记录 G1 并解锁后续生成</button></div>':'')+(i===2&&currentPhase>2?'<div class="phase-actions"><a class="review-link" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=review/02-identity.html">查看 Phase 2 身份</a><button id="approveHistoryG2Button" class="secondary">记录 G2 并解锁后续生成</button></div>':'')+(i===3&&currentPhase>3?'<div class="phase-actions"><a class="review-link" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=review/03-system.html">查看 Phase 3 系统</a><button id="approveHistoryG3Button" class="secondary">记录 G3 并解锁后续生成</button></div>':'')+(i===4&&currentPhase>4?'<div class="phase-actions"><a class="review-link" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=review/04-assets.html">查看 Phase 4 资产</a><button id="approveHistoryG4Button" class="secondary">记录 G4 并解锁后续生成</button></div>':'')+'<h4>Phase '+i+' 活动日志</h4><div id="phaseLog-'+i+'" class="phase-log" aria-live="polite">'+phaseLogs[i].join('\n')+'</div>';if(i!==currentPhase)body.hidden=true;header.addEventListener('click',()=>{if(i<=currentPhase)body.hidden=!body.hidden});panel.append(header,body);box.appendChild(panel)}}
async function loadState(){if(!current)return;try{const j=await get('/api/state?path='+encodeURIComponent(current));const phase=Number(j.phase);$('#state').innerHTML='<div class="card"><b>'+esc(j.path)+'</b><br>阶段 '+phase+' · '+esc(j.state)+'<br>下一步：'+esc(phaseInstruction(phase))+'</div>';const units=j.units||[];const currentUnit=units.find(x=>x.status==='in-progress'||x.status==='in-review'||x.status==='changes-requested')||units[units.length-1];$('#uiContext').innerHTML='<b>UI workflow</b><br>stack: '+esc(j.uiConfig?.stackProfile||'未设置')+' · platform: '+esc((j.uiConfig?.platforms||[]).join(', ')||'未设置')+'<br>当前 unit: '+esc(currentUnit?.unitId||'无')+' · review: '+esc(currentUnit?.status||'未开始');renderPhases(phase);updateUrl(phase,true);log('当前进度：阶段 '+phase+' · '+j.state,phase)}catch(e){$('#state').textContent=e.message}}
let activeJob='',activeJobPhase=0;
async function generatePhase(){if(!current||activePhase<1||activePhase>5)return;const b=$(activePhase===1?'#generateButton':'#progressButton');if(b){b.disabled=true;b.setAttribute('aria-busy','true');b.textContent='正在生成 Phase '+activePhase+'…'}log('正在启动 Phase '+activePhase+' 生成任务…',activePhase);try{const j=await get('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:current,phase:activePhase})});activeJob=j.job;activeJobPhase=activePhase;pollJob()}catch(e){if(b){b.disabled=false;b.removeAttribute('aria-busy');b.textContent='重新生成 Phase '+activePhase}log('生成启动失败：'+e.message,activePhase)}}
async function pollJob(){if(!activeJob)return;try{const j=await get('/api/job?id='+encodeURIComponent(activeJob));const status=$('#phaseJobStatus');if(status){const age=Math.max(0,Math.round((Date.now()/1000-j.updatedAt)));status.textContent=j.status==='running'?(age>120?'可能无响应：当前步骤 '+(j.step||'处理中')+'，最后更新 '+age+' 秒前':'当前步骤：'+(j.step||'处理中')+' · 最近更新 '+age+' 秒前'):(j.status==='done'?'任务已完成，可以检查。':'任务失败：'+(j.error||'未知错误'))}if(j.logs&&j.logs.length){phaseLogs[activeJobPhase]=j.logs.slice();const box=$('#phaseLog-'+activeJobPhase);if(box){const next=phaseLogs[activeJobPhase].join('\n');const previous=box.textContent;const follow=box.scrollTop+box.clientHeight>=box.scrollHeight-8;if(next!==previous){if(previous&&next.startsWith(previous)){box.append(document.createTextNode(next.slice(previous.length)))}else{box.textContent=next}}if(follow)box.scrollTop=box.scrollHeight}}if(j.status==='running'){setTimeout(pollJob,700);return}const b=$(activeJobPhase===1?'#generateButton':'#progressButton');if(b){b.disabled=false;b.removeAttribute('aria-busy');if(j.status!=='done')b.textContent='重新生成 Phase '+activeJobPhase}if(j.status==='done'&&activeJobPhase===1){const gate=$('#reviewGate');if(gate)gate.hidden=false;if(b){b.disabled=true;b.textContent='Phase 1 方向已生成'}}if(j.status==='done'&&activeJobPhase===2){const gate=$('#reviewGate2');if(gate)gate.hidden=false;if(b){b.disabled=true;b.textContent='Phase 2 身份已生成'}}if(j.status==='done'&&activeJobPhase>=3&&activeJobPhase<=4){const gate=$('#reviewGate'+activeJobPhase);if(gate)gate.hidden=false;if(b){b.disabled=true;b.textContent='Phase '+activeJobPhase+' 交付物已生成'}}if(j.status==='done'&&activeJobPhase===5){const gate=$('#reviewGate5');if(gate)gate.hidden=false;if(b){b.textContent='检查 Phase 5 交付物'}}if(j.status==='done'&&activeJobPhase!==1&&activeJobPhase!==5&&b)b.textContent='检查并进入 Phase '+(activeJobPhase+1);if(j.status==='done')log('生成完成，可以查看方案并继续。',activeJobPhase);else log('生成任务失败：'+(j.error||'请查看日志。'),activeJobPhase)}catch(e){if(e.message&&e.message.indexOf('任务不存在')>=0){const b=$(activeJobPhase===1?'#generateButton':'#progressButton');if(b){b.disabled=false;b.removeAttribute('aria-busy');b.textContent='重新生成 Phase '+activeJobPhase}activeJob='';const status=$('#phaseJobStatus');if(status)status.textContent='任务状态已丢失，请重新生成。';log('生成任务状态已丢失，请重新生成。',activeJobPhase)}else{log('读取生成进度失败：'+e.message,activeJobPhase)}}}
async function approveHistory(){const choice=$('#historyDirectionChoice')?.value||'A';try{await get('/api/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:current,gate:'G1',choice})});log('已补录 G1 审批：选择方向 '+choice+'。',1);const b=$('#approveHistoryButton');if(b)b.disabled=true}catch(e){log('G1 补录失败：'+e.message,1)}}
async function approveGate(gate){try{await get('/api/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:current,gate})});log('已记录 '+gate+' 审批。',Number(gate.slice(1)));return checkWorkspace()}catch(e){log(gate+' 审批失败：'+e.message,Number(gate.slice(1)))}}
async function approveHistoryGate(gate){try{await get('/api/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:current,gate})});log('已补录 '+gate+' 审批。',Number(gate.slice(1)));const b=$('#approveHistory'+gate+'Button');if(b)b.disabled=true}catch(e){log(gate+' 补录失败：'+e.message,Number(gate.slice(1)))}}
async function approveG2(){try{await get('/api/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:current,gate:'G2'})});log('已记录 G2 审批。',2);return checkWorkspace()}catch(e){log('G2 审批失败：'+e.message,2)}}
async function approveHistoryG2(){try{await get('/api/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:current,gate:'G2'})});log('已补录 G2 审批。',2);const b=$('#approveHistoryG2Button');if(b)b.disabled=true}catch(e){log('G2 补录失败：'+e.message,2)}}
async function approveProgress(){try{await approveG1();return checkWorkspace()}catch(e){log('G1 审批失败：'+e.message,1)}}
async function approveG1(){const choice=$('#directionChoice')?.value||'A';await get('/api/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:current,gate:'G1',choice})});log('已记录 G1 审批：选择方向 '+choice+'。',1)}
async function progressAction(){const button=$('#progressButton');if(activeJob&&activeJobPhase===activePhase&&button&&button.textContent.indexOf('检查')===0){if(activePhase===1){try{await approveG1()}catch(e){log('G1 审批失败：'+e.message,1);return}}return checkWorkspace()}if(activePhase>=2&&activePhase<=5){activeJob='';return generatePhase()}return checkWorkspace()}
async function checkWorkspace(){if(!current)return;const output=$('#phaseCheck-'+activePhase);try{log('正在检查当前 Phase 并准备推进…',activePhase);const j=await get('/api/check?path='+encodeURIComponent(current));if(!j.ok){output.className='error';output.textContent=j.output.trim()+'\n\n下一步：'+phaseInstruction(activePhase);log('当前 Phase 未通过：'+phaseInstruction(activePhase),activePhase);return}if(activePhase===5){output.className='success';output.textContent=j.output.trim()+'\n\nPhase 5 检查通过，发布交付物已完成。';log('Phase 5 检查通过，全部阶段已完成。',activePhase);return}await advancePhase()}catch(e){output.className='error';output.textContent=e.message;log('检查失败：'+e.message,activePhase)}}
async function advancePhase(){const s=await get('/api/state?path='+encodeURIComponent(current));await get('/api/advance',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:current,fromPhase:Number(s.phase)})});log('已进入 Phase '+(Number(s.phase)+1)+'。',activePhase);await loadState()}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
$('#chooseOutputButton').addEventListener('click',()=>openChooser('output'));$('#chooseSourceButton').addEventListener('click',()=>openChooser('source'));$('#cancelChooser').addEventListener('click',()=>dialog.close());$('#confirmChooser').addEventListener('click',confirmChooser);$('#initButton').addEventListener('click',initWorkspace);$('#phases').addEventListener('click',event=>{if(event.target.id==='generateButton')generatePhase();if(event.target.id==='progressButton')progressAction();if(event.target.id==='approveProgressButton')approveProgress();if(event.target.id==='approveG2Button')approveG2();if(event.target.id==='approveHistoryButton')approveHistory();if(event.target.id==='approveHistoryG2Button')approveHistoryG2();if(event.target.id==='approveG3Button')approveGate('G3');if(event.target.id==='approveG4Button')approveGate('G4');if(event.target.id==='approveHistoryG3Button')approveHistoryGate('G3');if(event.target.id==='approveHistoryG4Button')approveHistoryGate('G4')});window.addEventListener('popstate',()=>{const p=new URL(location.href).searchParams.get('workspace');current=p||'';if(current)loadState()});scan();const initial=new URL(location.href).searchParams.get('workspace');if(initial){current=initial;loadState()}
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


UI_STACKS = {"html-css-js", "react"}
UI_PLATFORMS = {"web", "desktop", "ios", "android"}
UI_UNIT_KINDS = ("page-map", "layout", "component", "page", "platform-adaptation")
UI_UNIT_DEPENDENCIES = {
    "page-map": (),
    "layout": ("page-map",),
    "component": ("layout",),
    "page": ("component",),
    "platform-adaptation": ("page",),
}
UI_UNIT_STATUSES = {"in-progress", "in-review", "approved", "changes-requested", "completed"}


def _read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validate_ui_selection(stack_profile, platforms):
    if stack_profile not in UI_STACKS:
        raise ValueError("未知 stack profile")
    if not isinstance(platforms, list) or not platforms or any(platform not in UI_PLATFORMS for platform in platforms):
        raise ValueError("platform 必须是 web、desktop、ios 或 android，且至少选择一个")
    if len(set(platforms)) != len(platforms):
        raise ValueError("platform 不能重复")


def _manifest_payload(stack_profile, platforms):
    return {
        "manifestVersion": "1.0.0",
        "hash": "sha256:" + ("0" * 64),
        "units": [
            {"id": kind, "kind": kind, "status": "in-progress", "files": [], "platforms": list(platforms)}
            for kind in UI_UNIT_KINDS
        ],
    }


def _manifest_hash(manifest):
    import hashlib
    payload = copy.deepcopy(manifest)
    payload.pop("hash", None)
    for unit in payload.get("units", []):
        if isinstance(unit, dict):
            unit.pop("status", None)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def sync_manifest_hash(workspace):
    workspace = Path(workspace).expanduser().resolve()
    manifest_path = workspace / "src/ui/ir/manifest.json"
    manifest = _read_json(manifest_path)
    manifest["hash"] = _manifest_hash(manifest)
    _write_json(manifest_path, manifest)
    return manifest["hash"]


def _unit_record(manifest, unit_id):
    for unit in manifest.get("units", []):
        if unit.get("id") == unit_id:
            return unit
    raise ValueError("未知 UI unit: %s" % unit_id)


def _sync_status_unit(workspace, unit_id, status, note=None):
    status_path = workspace / "project/status.json"
    state = _read_json(status_path)
    units = state.setdefault("units", [])
    record = next((item for item in units if item.get("unitId") == unit_id), None)
    if record is None:
        record = {"unitId": unit_id, "status": status}
        units.append(record)
    else:
        record["status"] = status
    if note:
        record["note"] = note
    record["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write_json(status_path, state)


def set_unit_status(workspace, unit_id, status):
    if status not in UI_UNIT_STATUSES:
        raise ValueError("未知 UI unit status")
    workspace = Path(workspace).expanduser().resolve()
    manifest_path = workspace / "src/ui/ir/manifest.json"
    manifest = _read_json(manifest_path)
    unit = _unit_record(manifest, unit_id)
    unit["status"] = status
    _write_json(manifest_path, manifest)
    _sync_status_unit(workspace, unit_id, status)
    sync_manifest_hash(workspace)
    return unit


def prepare_unit_generation(workspace, unit_id):
    workspace = Path(workspace).expanduser().resolve()
    manifest = _read_json(workspace / "src/ui/ir/manifest.json")
    unit = _unit_record(manifest, unit_id)
    if unit.get("status") in {"approved", "completed"}:
        raise ValueError("unit 已完成，不能重复生成")
    approvals = _read_json(workspace / "project/approvals.json", [])
    approved_reviews = {
        record.get("unitId")
        for record in approvals
        if isinstance(record, dict) and record.get("kind") == "unit-review" and record.get("status") == "approved" and record.get("conclusion") == "approved"
    }
    missing = []
    for dependency_id in UI_UNIT_DEPENDENCIES[unit_id]:
        dependency = _unit_record(manifest, dependency_id)
        if dependency.get("status") != "approved" or dependency_id not in approved_reviews:
            missing.append(dependency_id)
    if missing:
        raise ValueError("dependencies 未 approved: %s" % ", ".join(missing))
    return unit


def _safe_unit_file(workspace, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("unit 文件路径无效")
    target = (workspace / path).resolve()
    if not target.is_relative_to(workspace):
        raise ValueError("unit 文件路径必须位于工作区内")
    return target


def mark_unit_in_review(workspace, unit_id, files):
    workspace = Path(workspace).expanduser().resolve()
    manifest_path = workspace / "src/ui/ir/manifest.json"
    manifest = _read_json(manifest_path)
    unit = _unit_record(manifest, unit_id)
    if not isinstance(files, list) or not files:
        raise ValueError("in-review unit 必须有文件")
    normalized = []
    for relative in files:
        target = _safe_unit_file(workspace, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text("<!-- unit output pending external review -->\n", encoding="utf-8")
        normalized.append(str(Path(relative)))
    unit["files"] = normalized
    unit["status"] = "in-review"
    _write_json(manifest_path, manifest)
    _sync_status_unit(workspace, unit_id, "in-review")
    sync_manifest_hash(workspace)
    metadata = {
        "unitId": unit_id,
        "status": "in-review",
        "files": normalized,
        "manifestVersion": manifest["manifestVersion"],
        "manifestHash": _read_json(manifest_path)["hash"],
    }
    metadata_path = workspace / "src/ui/units" / unit_id / "metadata.json"
    _write_json(metadata_path, metadata)
    return metadata


def g1_choice(records):
    for record in reversed(records):
        if not isinstance(record, dict) or record.get("kind", "gate") != "gate" or record.get("gate") != "G1":
            continue
        # The latest G1 record decides; a withdrawal must not fall back to an older approval.
        if record.get("status") == "approved":
            snapshot = record.get("snapshot", "")
            confirmation = record.get("confirmation", "")
            for choice in ("A", "B", "C"):
                if snapshot == "direction-" + choice and choice in confirmation:
                    return choice
        break
    raise ValueError("缺少有效 G1 approved 方向选择")


def append_unit_review(workspace, unit_id, conclusion, reviewer, evidence, file_scope):
    if conclusion not in {"approved", "changes-requested"}:
        raise ValueError("unit review conclusion 无效")
    if not isinstance(reviewer, dict) or reviewer.get("type") != "subagent":
        raise ValueError("unit review 必须由 subagent 提供")
    if not isinstance(evidence, list) or not evidence or any(not str(item).strip() for item in evidence):
        raise ValueError("unit review 必须包含非空 evidence")
    if not isinstance(file_scope, list) or not file_scope:
        raise ValueError("unit review 必须包含 fileScope")
    workspace = Path(workspace).expanduser().resolve()
    manifest = _read_json(workspace / "src/ui/ir/manifest.json")
    unit = _unit_record(manifest, unit_id)
    manifest_hash = manifest.get("hash") or sync_manifest_hash(workspace)
    if unit.get("status") != "in-review" or not unit.get("files"):
        raise ValueError("unit 尚未进入 in-review")
    for scope in file_scope:
        if not isinstance(scope, dict) or not isinstance(scope.get("path"), str) or not isinstance(scope.get("startLine"), int) or not isinstance(scope.get("endLine"), int) or scope["startLine"] < 1 or scope["endLine"] < scope["startLine"]:
            raise ValueError("fileScope 必须包含有效的 path 和行号")
    record = {
        "kind": "unit-review",
        "unitId": unit_id,
        "manifestVersion": manifest["manifestVersion"],
        "manifestHash": manifest_hash,
        "fileScope": file_scope,
        "reviewer": reviewer,
        "conclusion": conclusion,
        "evidence": evidence,
        "status": conclusion,
        "reviewedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    approvals_path = workspace / "project/approvals.json"
    approvals = _read_json(approvals_path, [])
    approvals.append(record)
    _write_json(approvals_path, approvals)
    set_unit_status(workspace, unit_id, "approved" if conclusion == "approved" else "changes-requested")
    return record


def init_workspace(path, official, one_liner, source_path=None, capabilities=None, stack_profile="html-css-js", platforms=None):
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
    selected_platforms = ["web"] if platforms is None else platforms
    _validate_ui_selection(stack_profile, selected_platforms)
    if not (path / "brand.brief.json").exists():
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
        target = path / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    if source_path and not (path / "project/source.json").exists():
        count = sum(1 for item in source.rglob("*") if item.is_file() and ".git" not in item.parts)
        references = path / "docs/references.md"
        if not references.exists() or references.read_text(encoding="utf-8").startswith("# References\n\nNo external"):
            references.write_text(
                "# References\n\nAI reference source directory (read-only): `%s`\n\n"
                "Files available at initialization: %d\n" % (source, count), encoding="utf-8"
            )
        source_files = [str(item.relative_to(source)) for item in sorted(source.rglob("*")) if item.is_file() and ".git" not in item.parts]
        _write_json(path / "project/source.json", {"root": str(source), "files": source_files})
    status_path = path / "project/status.json"
    if status_path.exists():
        status = _read_json(status_path)
    else:
        status = {"phase": 0, "state": "draft", "completed": [], "next": ["complete brief", "generate directions"], "blockers": []}
    if "units" not in status:
        status["units"] = [{"unitId": kind, "status": "in-progress"} for kind in UI_UNIT_KINDS]
    _write_json(status_path, status)
    approvals_path = path / "project/approvals.json"
    if not approvals_path.exists():
        approvals_path.write_text("[]\n", encoding="utf-8")
    ui_path = path / "config/ui.json"
    if not ui_path.exists():
        _write_json(ui_path, {"version": "1.0.0", "platforms": list(selected_platforms), "stackProfile": stack_profile, "tokenSource": "tokens/src", "deliveryStatus": "preview-only"})
    manifest_path = path / "src/ui/ir/manifest.json"
    if not manifest_path.exists():
        _write_json(manifest_path, _manifest_payload(stack_profile, selected_platforms))
        sync_manifest_hash(path)
    return path


JOBS = {}
JOBS_LOCK = threading.Lock()

def _phase_requirements(phase):
    config = _read_json(ROOT / "config/phase_requirements.json")
    phases = config.get("phases", [])
    if isinstance(phases, dict):
        entry = phases.get(str(phase), {})
        return entry.get("required", entry.get("creates", []))
    for entry in phases:
        if int(entry.get("phase", -1)) == int(phase):
            return entry.get("required", entry.get("creates", []))
    return []


def start_generation(path, phase, unit_id=None):
    path = Path(path).expanduser().resolve()
    approvals = _read_json(path / "project/approvals.json", [])
    choice = g1_choice(approvals) if phase >= 2 else None
    if unit_id:
        prepare_unit_generation(path, unit_id)
    job_id = str(int(time.time() * 1000))
    requirements = _phase_requirements(phase)
    deliverables = "、".join(requirements)
    direction_instruction = (
        "Phase 5 发布页必须读取 config/brand.json 和 project/approvals.json，"
        "只展示 G1 已选中的当前方向（本工作区为 Direction %s），"
        "不要在最终发布页并列展示 A/B/C 方案；可在说明文字中记录选择依据。"
        % choice
        if choice
        else "Phase 1 先生成三套方向并准备后续 G1 选择，不要假设已有方向审批。"
    )
    prompt = f"""在当前品牌工作区完成 Phase {phase} 交付。只写入当前工作区目录，不修改技能仓库或其他目录。读取 brand.brief.json、review/、docs/、project/ 和 config/ 中现有资料；如有 source.json，读取其中列出的参考资料。创建并验证本阶段必需文件：{deliverables}。保持方向选择、审批和暂定假设可追溯，不把未确认内容写成最终事实。确保每张方向卡片的 hero 标题、描述和图标有独立空间，文字与图标不能重叠，并检查浅色与深色背景下的对比度。每套方向必须包含一个较大的产品界面配色 demo，展示背景、文字、按钮、状态、层级和真实场景，不要只放色板。{direction_instruction}同步更新 project/status.json 为 phase {phase}、state in-review，并保存 reports/phase-{phase}-check.txt。不要只解释，直接创建文件。"""
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "running", "logs": [], "error": "", "updatedAt": time.time(), "step": "读取工作区资料", "prompt": prompt, "unitId": unit_id}
    def add_log(message, step=None):
        stamp = time.strftime("%H:%M:%S")
        with JOBS_LOCK:
            JOBS[job_id]["logs"].append("[" + stamp + "] " + message)
            JOBS[job_id]["updatedAt"] = time.time()
            if step:
                JOBS[job_id]["step"] = step
    def run():
        cmd = ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "-s", "danger-full-access", "-C", str(path), "--add-dir", str(path), "--add-dir", str(ROOT), "--json", prompt]
        try:
            add_log("正在读取工作区资料…", "读取工作区资料")
            workspace_files = sorted(item for item in path.rglob("*") if item.is_file() and ".git" not in item.parts)
            add_log("已读取工作区资料：%d 个文件" % len(workspace_files))
            key_inputs = [path / "brand.brief.json", path / "project" / "status.json", path / "project" / "source.json", path / "review" / "01-directions.html"]
            for item in key_inputs:
                if item.is_file():
                    add_log("关键输入：" + str(item.relative_to(path)))
            source_manifest = path / "project" / "source.json"
            if source_manifest.is_file():
                try:
                    source_files = json.loads(source_manifest.read_text(encoding="utf-8")).get("files", [])
                    add_log("已读取参考资料：%d 个文件" % len(source_files))
                except (OSError, json.JSONDecodeError):
                    add_log("参考资料清单读取失败，继续使用工作区文件。")
            phase_labels = {
                1: ("正在生成三套视觉方向和 review/01-directions.html…", "生成方向页面"),
                2: ("正在生成 Phase 2 核心身份和 review/02-identity.html…", "生成核心身份"),
                3: ("正在生成 Phase 3 设计系统交付物…", "生成设计系统"),
                4: ("正在生成 Phase 4 平台资产交付物…", "生成平台资产"),
                5: ("正在生成 Phase 5 发布交付物…", "生成发布包"),
            }
            phase_message, phase_step = phase_labels[phase]
            add_log(phase_message, phase_step)
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(path))
            selector = selectors.DefaultSelector(); selector.register(proc.stdout, selectors.EVENT_READ)
            deadline = time.time() + 600
            while True:
                if time.time() > deadline:
                    proc.kill()
                    raise TimeoutError("生成任务超过 10 分钟未完成，已停止；可以重新生成")
                ready = selector.select(timeout=1)
                if ready:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    try:
                        json.loads(line)
                    except json.JSONDecodeError:
                        continue
                elif proc.poll() is not None:
                    break
            selector.close()
            code = proc.wait()
            if code == 0:
                generated = sorted(item for item in path.rglob("*") if item.is_file() and ".git" not in item.parts)
                for item in generated:
                    if item not in workspace_files:
                        add_log("已生成：" + str(item.relative_to(path)))
                if unit_id:
                    unit_output = "src/ui/units/%s/output.html" % unit_id
                    mark_unit_in_review(path, unit_id, [unit_output])
                    add_log("unit %s 已进入 in-review，等待外部 subagent review。" % unit_id, "等待 unit review")
                add_log("正在运行检查和文件验证…", "运行 Phase %d 检查" % phase)
                check = subprocess.run(["python3", str(ROOT / "scripts/check_workspace.py"), str(path), "--phase", str(phase)], capture_output=True, text=True)
                result = (check.stdout + check.stderr).strip()
                add_log("检查结果：" + (result or "无输出"))
                with JOBS_LOCK:
                    JOBS[job_id]["status"] = "done"
                add_log("生成任务已完成，正在等待页面检查。", "等待页面检查")
            else:
                with JOBS_LOCK:
                    JOBS[job_id]["status"] = "error"
                    JOBS[job_id]["error"] = "生成进程退出码 %d" % code
                add_log("生成任务失败。", "生成失败")
        except Exception as exc:
            with JOBS_LOCK:
                JOBS[job_id]["status"] = "error"
                JOBS[job_id]["error"] = str(exc)
                JOBS[job_id]["logs"].append("生成任务失败：" + str(exc))
    threading.Thread(target=run, daemon=True).start()
    return job_id

class Handler(BaseHTTPRequestHandler):
    root = Path.cwd().resolve()
    @classmethod
    def workspace_path(cls, raw):
        path = Path(raw).expanduser().resolve()
        if not path.is_relative_to(cls.root):
            raise ValueError("工作区必须位于启动目录内")
        return path
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
            if parsed.path == "/preview":
                preview_path = self.workspace_path(q.get("path", [""])[0])
                relative = q.get("file", [""])[0]
                if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
                    raise ValueError("预览文件路径无效")
                target = (preview_path / relative).resolve()
                if not target.is_relative_to(preview_path) or not target.is_file():
                    raise ValueError("预览文件不存在")
                data = target.read_bytes()
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
            if parsed.path == "/api/scan":
                target = path if q.get("path") else self.root
                if not target.is_dir(): raise ValueError("指定路径不是可读取的文件夹")
                if not target.is_relative_to(self.root.parent): raise ValueError("为安全起见，请指定启动目录或其父目录下的文件夹")
                file_count = sum(1 for item in target.rglob("*") if item.is_file() and ".git" not in item.parts)
                return self.send_json({"root": str(target), "items": candidates(target), "fileCount": file_count, "recommended": str(recommended_folder(target))})
            if parsed.path == "/api/state":
                path = self.workspace_path(q.get("path", [""])[0])
                status = _read_json(path / "project/status.json")
                payload = {"path": str(path), **status}
                payload["uiConfig"] = _read_json(path / "config/ui.json", {})
                payload["manifest"] = _read_json(path / "src/ui/ir/manifest.json", {})
                return self.send_json(payload)
            if parsed.path == "/api/job":
                job_id = q.get("id", [""])[0]
                with JOBS_LOCK:
                    job = JOBS.get(job_id)
                    if not job: raise ValueError("生成任务不存在或已过期")
                    return self.send_json(dict(job))
            if parsed.path == "/api/check":
                path = self.workspace_path(q.get("path", [""])[0])
                import subprocess, sys
                p = subprocess.run([sys.executable, str(ROOT / "scripts/check_workspace.py"), str(path), "--phase", str(json.loads((path / "project/status.json").read_text())["phase"])], capture_output=True, text=True)
                return self.send_json({"ok": p.returncode == 0, "output": p.stdout + p.stderr})
            self.send_json({"error": "not found"}, 404)
        except Exception as exc: self.send_json({"error": str(exc)}, 400)
    def do_POST(self):
        endpoint = urlparse(self.path).path
        if endpoint not in {"/api/init", "/api/advance", "/api/generate", "/api/approve"}: return self.send_json({"error": "not found"}, 404)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            if endpoint == "/api/approve":
                path = self.workspace_path(body.get("path", ""))
                if body.get("kind") == "unit-review":
                    record = append_unit_review(
                        path,
                        body.get("unitId", ""),
                        body.get("conclusion", ""),
                        body.get("reviewer"),
                        body.get("evidence"),
                        body.get("fileScope"),
                    )
                    return self.send_json({"ok": True, "record": record})
                gate = body.get("gate")
                gate_specs = {
                    "G1": (1, "Phase 1 strategy and selected visual direction", "direction-%s" % body.get("choice"), "用户在工作台选择方向 %s 并确认 G1" % body.get("choice")),
                    "G2": (2, "Phase 2 core identity", "review-02-identity", "用户在工作台查看 Phase 2 身份审阅页并确认 G2"),
                    "G3": (3, "Phase 3 design system", "review-03-system", "用户在工作台查看 Phase 3 系统审阅页并确认 G3"),
                    "G4": (4, "Phase 4 platform assets", "review-04-assets", "用户在工作台查看 Phase 4 资产审阅页并确认 G4"),
                }
                if gate not in gate_specs:
                    raise ValueError("只支持 G1、G2、G3 或 G4 审批")
                if gate == "G1" and body.get("choice") not in ("A", "B", "C"):
                    raise ValueError("G1 必须选择 A、B 或 C 方向")
                required_phase, scope, snapshot, confirmation = gate_specs[gate]
                status = json.loads((path / "project/status.json").read_text(encoding="utf-8"))
                if int(status.get("phase", -1)) < required_phase:
                    raise ValueError("当前阶段还不能记录 %s" % gate)
                approvals_path = path / "project/approvals.json"
                records = json.loads(approvals_path.read_text(encoding="utf-8"))
                records.append({"gate":gate,"status":"approved","scope":scope,"snapshot":snapshot,"confirmation":confirmation,"approvedAt":time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),"version":"workbench-%s-%d" % (gate.lower(), int(time.time()))})
                approvals_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                return self.send_json({"ok": True, "gate": gate})
            if endpoint == "/api/generate":
                path = self.workspace_path(body.get("path", ""))
                status = _read_json(path / "project/status.json")
                unit_id = body.get("unitId")
                phase = int(body.get("phase", 1))
                if unit_id:
                    prepare_unit_generation(path, unit_id)
                    job_id = start_generation(path, phase, unit_id=unit_id)
                    return self.send_json({"job": job_id, "unitId": unit_id})
                if int(status.get("phase", -1)) != phase or phase not in (1, 2, 3, 4, 5):
                    raise ValueError("只能为当前阶段生成交付物")
                if phase >= 2:
                    records = _read_json(path / "project/approvals.json")
                    latest = {item.get("gate"): item.get("status") for item in records if isinstance(item, dict)}
                    needed = ["G1", "G2", "G3", "G4"][:phase - 1]
                    missing = [gate for gate in needed if latest.get(gate) != "approved"]
                    if missing:
                        raise ValueError("Phase %d 生成前必须完成审批：%s" % (phase, ", ".join(missing)))
                job_id = start_generation(path, phase)
                return self.send_json({"job": job_id})
            if endpoint == "/api/advance":
                path = self.workspace_path(body.get("path", ""))
                status_path = path / "project/status.json"
                status = json.loads(status_path.read_text(encoding="utf-8"))
                phase = int(status["phase"])
                if phase != int(body.get("fromPhase", -1)):
                    raise ValueError("工作区阶段已经变化，请刷新后重试")
                if phase >= 5:
                    raise ValueError("已经是最后阶段")
                required_gate = {1: "G1", 2: "G2", 3: "G3", 4: "G4"}.get(phase)
                if required_gate:
                    records = json.loads((path / "project/approvals.json").read_text(encoding="utf-8"))
                    latest = {item.get("gate"): item.get("status") for item in records if isinstance(item, dict)}
                    if latest.get(required_gate) != "approved":
                        review_names = {"G1": "方向审阅页", "G2": "身份审阅页", "G3": "系统审阅页", "G4": "资产审阅页"}
                        raise ValueError("Phase %d 已生成交付物，但不能进入 Phase %d：请先查看%s并确认 %s" % (phase, phase + 1, review_names[required_gate], required_gate))
                import subprocess, sys
                check = subprocess.run([sys.executable, str(ROOT / "scripts/check_workspace.py"), str(path), "--phase", str(phase)], capture_output=True, text=True)
                if check.returncode != 0:
                    raise ValueError("当前阶段检查未通过，不能推进：\n" + check.stdout + check.stderr)
                status["phase"] = phase + 1
                status["state"] = "draft"
                status["next"] = ["complete current phase", "run phase check"]
                status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                return self.send_json({"path": str(path), "phase": phase + 1})
            target = Path(body.get("path") or (self.root / "brand-workspace"))
            if not target.is_absolute(): target = self.root / target
            path = init_workspace(target, body.get("official", ""), body.get("oneLiner", ""), body.get("sourcePath") or None, body.get("capabilities") or [], body.get("stackProfile", "html-css-js"), body.get("platforms"))
            self.send_json({"path": str(path)})
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
