# -*- coding: utf-8 -*-
# Python 3.9 reads a script in fixed-size chunks and rejects a multi-byte character split across a chunk
# boundary unless the encoding is declared; the embedded page has very long lines of Chinese text.
"""Run the browser-first brand-system workspace initializer.

Usage: python3 scripts/workbench.py [DIRECTORY] [--port PORT]
The server only writes inside the selected workspace after an explicit UI action.
"""
import argparse
import copy
import hashlib
import json
import os
import sys
import threading
import time
import subprocess
import selectors
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import check_workspace

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "assets" / "brief.template.json"

HTML = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Brand System Workbench</title>
<style>
:root{font:16px/1.5 system-ui,sans-serif;color:#17202a;background:#f5f7fb}body{max-width:980px;margin:0 auto;padding:32px}main{background:#fff;border:1px solid #dfe5ee;border-radius:16px;padding:28px;box-shadow:0 8px 30px #17202a12}.deliverables{margin-top:16px;padding:18px;border:1px solid #cbd8ef;border-radius:12px;background:#f7f9ff}.deliverables h3{margin:0 0 12px}.deliverable-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.deliverable-card{display:block;padding:12px;border:1px solid #dfe5ee;border-radius:9px;background:#fff;color:#17202a;text-decoration:none}.deliverable-card:hover{border-color:#315efb;background:#f8faff}.deliverable-card strong{display:block;color:#315efb}.deliverable-card small{color:#687386}h1{margin-top:0}label{display:block;margin:14px 0 6px;font-weight:650}input,textarea,select{width:100%;box-sizing:border-box;padding:10px;border:1px solid #c7d0dc;border-radius:8px;font:inherit}input[readonly]{background:#f8fafc}.row{display:flex;gap:10px;align-items:center}.row input{flex:1}.row button{flex:0 0 auto;min-width:110px;margin-top:0;height:46px}button{margin-top:18px;padding:11px 16px;border:0;border-radius:8px;background:#315efb;color:#fff;font-weight:700;cursor:pointer}button:disabled{opacity:.48;cursor:wait}button.secondary{background:#e8edf5;color:#17202a}.card{border:1px solid #dfe5ee;border-radius:10px;padding:16px;margin:12px 0}.muted{color:#687386}.error{color:#a32626}.ok{color:#166534;white-space:pre-wrap}.log,.phase-log{background:#111827;color:#d1fae5;border-radius:10px;padding:14px;min-height:60px;max-height:180px;overflow:auto;white-space:pre-wrap;user-select:text;font:13px/1.55 ui-monospace,monospace}.phase{border:1px solid #dfe5ee;border-radius:10px;margin:12px 0;overflow:hidden}.phase-header{display:block;width:100%;margin:0;border:0;border-radius:0;background:#eef2f7;color:#17202a;text-align:left}.phase.current .phase-header{background:#e8efff;color:#19327a;border-left:4px solid #315efb}.phase.current .phase-header:hover{background:#dce7ff}.phase-header:disabled{cursor:not-allowed;color:#7d8796;background:#f7f8fa}.phase-body{padding:16px;background:#fff}.phase-body[hidden]{display:none}.phase-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.review-link{font-weight:700;color:#315efb}.phase-actions button{margin-top:0}.phase-check{white-space:pre-wrap;margin:10px 0 0;color:#a32626}.phase-check.ok{color:#166534}dialog{border:1px solid #dfe5ee;border-radius:12px;padding:22px;width:min(680px,calc(100% - 44px));box-shadow:0 20px 60px #17202a33}dialog::backdrop{background:#17202a66}.dialog-actions{display:flex;gap:10px;justify-content:flex-end}.dialog-actions button{margin-top:12px}.unit-row{border:1px solid #dfe5ee;border-radius:10px;padding:12px 14px;margin:8px 0}.unit-row h5{margin:0 0 4px;font-size:15px}.unit-row p{margin:4px 0}.unit-actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.unit-actions button{margin-top:6px}
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
function phaseInstruction(phase){return ['先补全 brief、环境、策略和范围文件，再检查进入 Phase 1。','先点击“生成 Phase 1 方向”，查看三套方案后选择 A/B/C，再记录 G1。','先确认 G1；然后点击“生成并检查 Phase 2”，系统会生成 review/02-identity.html、BRAND_SYSTEM.md、config/brand.json 及身份文档。','先查看 Phase 3 系统审阅页并确认 G3，再生成或进入 Phase 4。','先在下方按顺序生成并审查全部 UI 工作单元，再生成 Phase 4 资产、查看审阅页并确认 G4，然后进入 Phase 5。','先生成 Phase 5 发布交付物，再检查最终交付物。'][phase]||'先完成当前阶段标注的交付物，再重新检查。'}
function updateUrl(phase,replace=false){const u=new URL(location.href);u.searchParams.set('workspace',current);u.searchParams.set('phase',String(phase));(replace?history.replaceState:history.pushState).call(history,{},'',u)}
async function initWorkspace(){const output=$('#outputPath').value,source=$('#sourcePath').value.trim(),existing=$('#outputPath').dataset.workspace==='true';const capabilities=$('#capabilities').value.split(/[,，]/).map(x=>x.trim()).filter(Boolean);const platforms=Array.from($('#platforms').selectedOptions).map(x=>x.value);try{if(existing){current=output;await loadState();log('已打开已有品牌工作区。',activePhase);return}log('开始初始化，生成目录：'+output,0);if(source)log('将读取参考资料：'+source,0);const j=await get('/api/init',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:output,official:$('#official').value,oneLiner:$('#oneLiner').value,capabilities,sourcePath:source,stackProfile:$('#stackProfile').value,platforms})});current=j.path;updateUrl(0);log('已写入阶段 0 文件。',0);await loadState()}catch(e){$('#result').className='error';$('#result').textContent=e.message;log('初始化失败：'+e.message,0)}}
function renderPhases(currentPhase){activePhase=currentPhase;const names=['发现与计划','三套方向','品牌身份','设计系统','资产与平台','交付与发布'];const desc=['完善 brief、环境、策略和范围。','生成三套真正不同的方向并准备 G1。','完成 Logo、颜色、字体和身份规范。','完成 tokens、组件和无障碍规范。','生成平台资产、图标和导出清单。','完成 QA、交接和发布包。'];const box=$('#phases');box.replaceChildren();for(let i=0;i<6;i++){const panel=document.createElement('section');panel.className='phase '+(i===currentPhase?'current':'');const header=document.createElement('button');header.className='phase-header';header.textContent=(i<currentPhase?'✓ ':i===currentPhase?'● ':'🔒 ')+'Phase '+i+' · '+names[i]+' · '+desc[i];header.disabled=i>currentPhase;const body=document.createElement('div');body.className='phase-body';const actions=i===currentPhase&&i<=5?((i===1?'<button id="generateButton">生成 Phase 1 方向</button><div id="reviewGate" hidden><a class="review-link" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=review/01-directions.html">查看三套方向</a><label class="direction-choice">选择方向 <select id="directionChoice"><option value="A">A</option><option value="B">B</option><option value="C">C</option></select></label><button id="approveProgressButton">选择方向并进入 Phase 2</button></div>':'')+(i===2?'<button id="progressButton">生成 Phase 2 身份并检查 → Phase 3</button><div id="reviewGate2" hidden><a class="review-link" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=review/02-identity.html">查看 Phase 2 身份</a><button id="approveG2Button">确认 G2 并进入 Phase 3</button></div>':'')+(i===3?'<button id="progressButton">生成 Phase 3 系统并检查 → Phase 4</button><div id="reviewGate3" hidden><a class="review-link" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=review/03-system.html">查看 Phase 3 系统</a><button id="approveG3Button">确认 G3 并进入 Phase 4</button></div>':'')+(i===4?'<button id="progressButton">生成 Phase 4 资产并检查 → Phase 5</button><div id="reviewGate4" hidden><a class="review-link" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=review/04-assets.html">查看 Phase 4 资产</a><button id="approveG4Button">确认 G4 并进入 Phase 5</button></div><div id="unitPanel" aria-live="polite"></div>':'')+(i===5?'<button id="progressButton">生成 Phase 5 发布包</button><div id="reviewGate5" class="deliverables" hidden><h3>Phase 5 交付物</h3><p class="muted">生成完成后，从这里查看和交接品牌系统。</p><div class="deliverable-grid"><a class="deliverable-card" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=review/05-release.html"><strong>发布审阅页</strong><small>review/05-release.html</small></a><a class="deliverable-card" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=CHANGELOG.md"><strong>变更记录</strong><small>CHANGELOG.md</small></a><a class="deliverable-card" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=docs/handoff-by-role.md"><strong>交接文档</strong><small>docs/handoff-by-role.md</small></a><a class="deliverable-card" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=reports/qa-report.md"><strong>QA 报告</strong><small>reports/qa-report.md</small></a></div></div>':'')+(i===0?'<button id="progressButton">检查并进入 Phase 1</button>':'')+'<pre id="phaseCheck-'+i+'" class="phase-check"></pre>'):'' ;body.innerHTML='<p class="muted">'+(i<currentPhase?'已完成，可点击标题回看。':i===currentPhase?'当前阶段，完成检查后进入下一阶段。':'尚未到达，完成前置阶段后解锁。')+'</p>'+(i>0&&i<=5?'<p id=\"phaseJobStatus\" class=\"muted\" aria-live=\"polite\"></p>':'')+actions+(i===1&&currentPhase>1?'<div class=\"phase-actions\"><label>补录 G1 方向 <select id=\"historyDirectionChoice\"><option value=\"A\">A</option><option value=\"B\">B</option><option value=\"C\">C</option></select></label><button id=\"approveHistoryButton\" class=\"secondary\">记录 G1 并解锁后续生成</button></div>':'')+(i===2&&currentPhase>2?'<div class="phase-actions"><a class="review-link" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=review/02-identity.html">查看 Phase 2 身份</a><button id="approveHistoryG2Button" class="secondary">记录 G2 并解锁后续生成</button></div>':'')+(i===3&&currentPhase>3?'<div class="phase-actions"><a class="review-link" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=review/03-system.html">查看 Phase 3 系统</a><button id="approveHistoryG3Button" class="secondary">记录 G3 并解锁后续生成</button></div>':'')+(i===4&&currentPhase>4?'<div class="phase-actions"><a class="review-link" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file=review/04-assets.html">查看 Phase 4 资产</a><button id="approveHistoryG4Button" class="secondary">记录 G4 并解锁后续生成</button></div>':'')+'<h4>Phase '+i+' 活动日志</h4><div id="phaseLog-'+i+'" class="phase-log" aria-live="polite">'+phaseLogs[i].join('\n')+'</div>';if(i!==currentPhase)body.hidden=true;header.addEventListener('click',()=>{if(i<=currentPhase)body.hidden=!body.hidden});panel.append(header,body);box.appendChild(panel)}}
async function loadState(){if(!current)return;try{const j=await get('/api/state?path='+encodeURIComponent(current));const phase=Number(j.phase);$('#state').innerHTML='<div class="card"><b>'+esc(j.path)+'</b><br>阶段 '+phase+' · '+esc(j.state)+'<br>下一步：'+esc(phaseInstruction(phase))+'</div>';const units=j.uiUnits||[];const currentUnit=units.find(x=>x.status!=='approved'&&x.status!=='completed');$('#uiContext').innerHTML='<b>UI workflow</b><br>stack: '+esc(j.uiConfig?.stackProfile||'未设置')+' · platform: '+esc((j.uiConfig?.platforms||[]).join(', ')||'未设置')+'<br>当前 unit: '+(units.length?(currentUnit?esc(UNIT_LABELS[currentUnit.id]||currentUnit.id)+' · '+esc(UNIT_STATUS[currentUnit.status]||currentUnit.status):'全部已通过'):'无');renderPhases(phase);renderUnits(j.uiUnits||[]);if(j.activeJob&&!activeJob){activeJob=j.activeJob.id;activeJobPhase=phase;activeJobUnit=j.activeJob.unitId||'';pollJob()}updateUrl(phase,true);log('当前进度：阶段 '+phase+' · '+j.state,phase)}catch(e){$('#state').textContent=e.message}}
let activeJob='',activeJobPhase=0,activeJobUnit='';
const UNIT_LABELS={'page-map':'页面地图','layout':'布局','component':'组件','page':'页面','platform-adaptation':'平台适配'};const UNIT_STATUS={'not-started':'未开始','in-progress':'生成中','in-review':'待审查','approved':'已通过','changes-requested':'需要修改','completed':'已完成'};
function renderUnits(units){const box=$('#unitPanel');if(!box||!units.length)return;const blocker=units[0].phaseBlocker;box.innerHTML='<h4>UI 工作单元</h4><p class="muted">按依赖顺序逐个生成；每个 unit 生成后由独立的只读审查进程给出结论，通过后才解锁下游。</p>'+(blocker?'<p class="muted">'+esc(blocker)+'</p>':'')+units.map(u=>{const review=u.review?'<p>审查（'+esc(u.review.reviewer||'subagent')+'）：'+esc(UNIT_STATUS[u.review.conclusion]||u.review.conclusion)+(u.review.current?'':' · 输出已变化，结论不再适用')+(u.review.evidence&&u.review.evidence[1]?' · '+esc(u.review.evidence[1]):'')+'</p>':'';const waiting=u.blockedBy.length?' · 等待：'+u.blockedBy.map(d=>esc(UNIT_LABELS[d]||d)).join('、'):'';const preview=u.outputExists?'<a class="review-link" target="_blank" rel="noopener" href="/preview?path='+encodeURIComponent(current)+'&file='+encodeURIComponent(u.output)+'">查看输出</a>':'';const generate='<button data-unit="'+esc(u.id)+'" data-action="generate"'+(u.canGenerate?'':' disabled')+'>'+(u.status==='not-started'?'生成并审查':'重新生成并审查')+'</button>';const again=u.status==='in-review'?'<button class="secondary" data-unit="'+esc(u.id)+'" data-action="review"'+(u.canReview?'':' disabled')+'>只重新审查</button>':'';return '<div class="unit-row"><h5>'+esc(UNIT_LABELS[u.id]||u.id)+' · '+esc(UNIT_STATUS[u.status]||u.status)+'</h5><p class="muted">依赖：'+(u.dependsOn.length?u.dependsOn.map(d=>esc(UNIT_LABELS[d]||d)).join('、'):'无')+waiting+'</p>'+(u.note?'<p class="error">'+esc(u.note)+'</p>':'')+review+'<div class="unit-actions">'+generate+again+preview+'</div></div>'}).join('');box.onclick=e=>{const b=e.target.closest('button[data-unit]');if(b&&!b.disabled)runUnit(b.dataset.unit,b.dataset.action)}}
async function runUnit(unitId,action){if(!current||activeJob)return;document.querySelectorAll('#unitPanel button').forEach(b=>b.disabled=true);try{const j=await get(action==='review'?'/api/review':'/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:current,phase:activePhase,unitId})});activeJob=j.job;activeJobPhase=activePhase;activeJobUnit=unitId;log((action==='review'?'开始重新审查 unit ':'开始生成 unit ')+unitId+'。',activePhase);pollJob()}catch(e){log('unit '+unitId+' 无法开始：'+e.message,activePhase);loadState()}}
async function generatePhase(){if(!current||activePhase<1||activePhase>5)return;const b=$(activePhase===1?'#generateButton':'#progressButton');if(b){b.disabled=true;b.setAttribute('aria-busy','true');b.textContent='正在生成 Phase '+activePhase+'…'}log('正在启动 Phase '+activePhase+' 生成任务…',activePhase);try{const j=await get('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:current,phase:activePhase})});activeJob=j.job;activeJobPhase=activePhase;pollJob()}catch(e){if(b){b.disabled=false;b.removeAttribute('aria-busy');b.textContent='重新生成 Phase '+activePhase}log('生成启动失败：'+e.message,activePhase)}}
async function pollJob(){if(!activeJob)return;try{const j=await get('/api/job?id='+encodeURIComponent(activeJob));const status=$('#phaseJobStatus');if(status){const age=Math.max(0,Math.round((Date.now()/1000-j.updatedAt)));status.textContent=j.status==='running'?(age>120?'可能无响应：当前步骤 '+(j.step||'处理中')+'，最后更新 '+age+' 秒前':'当前步骤：'+(j.step||'处理中')+' · 最近更新 '+age+' 秒前'):(j.status==='done'?'任务已完成，可以检查。':'任务失败：'+(j.error||'未知错误'))}if(j.logs&&j.logs.length){phaseLogs[activeJobPhase]=j.logs.slice();const box=$('#phaseLog-'+activeJobPhase);if(box){const next=phaseLogs[activeJobPhase].join('\n');const previous=box.textContent;const follow=box.scrollTop+box.clientHeight>=box.scrollHeight-8;if(next!==previous){if(previous&&next.startsWith(previous)){box.append(document.createTextNode(next.slice(previous.length)))}else{box.textContent=next}}if(follow)box.scrollTop=box.scrollHeight}}if(j.status==='running'){setTimeout(pollJob,700);return}if(j.check){const out=$('#phaseCheck-'+activeJobPhase);if(out){out.textContent=(j.check.passed?'Phase '+activeJobPhase+' 检查通过。':'Phase '+activeJobPhase+' 检查未通过（推进前需要处理）：\n'+j.check.output);out.classList.toggle('ok',j.check.passed)}}if(activeJobUnit){log(j.status==='done'?'unit '+activeJobUnit+' 已生成并完成审查。':'unit '+activeJobUnit+' 失败：'+(j.error||'请查看日志。'),activeJobPhase);activeJob='';activeJobUnit='';return loadState()}const b=$(activeJobPhase===1?'#generateButton':'#progressButton');if(b){b.disabled=false;b.removeAttribute('aria-busy');if(j.status!=='done')b.textContent='重新生成 Phase '+activeJobPhase}if(j.status==='done'&&activeJobPhase===1){const gate=$('#reviewGate');if(gate)gate.hidden=false;if(b){b.disabled=true;b.textContent='Phase 1 方向已生成'}}if(j.status==='done'&&activeJobPhase===2){const gate=$('#reviewGate2');if(gate)gate.hidden=false;if(b){b.disabled=true;b.textContent='Phase 2 身份已生成'}}if(j.status==='done'&&activeJobPhase>=3&&activeJobPhase<=4){const gate=$('#reviewGate'+activeJobPhase);if(gate)gate.hidden=false;if(b){b.disabled=true;b.textContent='Phase '+activeJobPhase+' 交付物已生成'}}if(j.status==='done'&&activeJobPhase===5){const gate=$('#reviewGate5');if(gate)gate.hidden=false;if(b){b.textContent='检查 Phase 5 交付物'}}if(j.status==='done'&&activeJobPhase!==1&&activeJobPhase!==5&&b)b.textContent='检查并进入 Phase '+(activeJobPhase+1);if(j.status==='done')log('生成完成，可以查看方案并继续。',activeJobPhase);else log('生成任务失败：'+(j.error||'请查看日志。'),activeJobPhase)}catch(e){if(e.message&&e.message.indexOf('任务不存在')>=0){const b=$(activeJobPhase===1?'#generateButton':'#progressButton');if(b){b.disabled=false;b.removeAttribute('aria-busy');b.textContent='重新生成 Phase '+activeJobPhase}activeJob='';const status=$('#phaseJobStatus');if(status)status.textContent='任务状态已丢失，请重新生成。';log('生成任务状态已丢失，请重新生成。',activeJobPhase)}else{log('读取生成进度失败：'+e.message,activeJobPhase)}}}
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
# Seeds dependsOn in a new manifest; generation and the checker both read the manifest afterwards.
UI_UNIT_DEPENDENCIES = {
    "page-map": (),
    "layout": ("page-map",),
    "component": ("layout",),
    "page": ("component",),
    "platform-adaptation": ("page",),
}
UI_UNIT_PHASE = 4
UNIT_REVIEWER = {"type": "subagent", "name": "codex-unit-reviewer"}
# ponytail: one process-wide lock for every status/manifest/approvals read-modify-write; per-workspace locks if contention shows up.
STATE_LOCK = threading.RLock()


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


def unit_dir(unit_id):
    return "src/ui/units/%s" % unit_id


def unit_output_path(unit_id):
    return unit_dir(unit_id) + "/output.html"


def _manifest_payload(stack_profile, platforms):
    # files are declared up front: they are hashed, so filling them in during progression would
    # invalidate every review already bound to the manifest.
    return {
        "manifestVersion": "1.0.0",
        "hash": "sha256:" + ("0" * 64),
        "units": [
            {
                "id": kind,
                "kind": kind,
                "status": "not-started",
                "files": [unit_output_path(kind)],
                "platforms": list(platforms),
                "dependsOn": list(UI_UNIT_DEPENDENCIES[kind]),
            }
            for kind in UI_UNIT_KINDS
        ],
    }


def _manifest_hash(manifest):
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


def _unit_states(workspace):
    units = _read_json(workspace / "project/status.json").get("units", [])
    return {item.get("unitId"): item.get("status") for item in units if isinstance(item, dict)}


def set_unit_status(workspace, unit_id, status, note=None):
    """Write the unit status to status.json (the progress authority) and mirror it into the manifest."""
    if status not in check_workspace.UNIT_STATUSES:
        raise ValueError("未知 UI unit status")
    workspace = Path(workspace).expanduser().resolve()
    with STATE_LOCK:
        manifest_path = workspace / "src/ui/ir/manifest.json"
        manifest = _read_json(manifest_path)
        unit = _unit_record(manifest, unit_id)
        unit["status"] = status
        _write_json(manifest_path, manifest)
        status_path = workspace / "project/status.json"
        state = _read_json(status_path)
        units = state.setdefault("units", [])
        record = next((item for item in units if item.get("unitId") == unit_id), None)
        if record is None:
            record = {"unitId": unit_id}
            units.append(record)
        record["status"] = status
        record.pop("note", None)
        if note:
            record["note"] = note
        record["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_json(status_path, state)
        sync_manifest_hash(workspace)
        return unit


def _dependents(manifest, unit_id):
    found, frontier = [], [unit_id]
    while frontier:
        current = frontier.pop()
        for unit in manifest.get("units", []):
            if current in unit.get("dependsOn", []) and unit["id"] not in found:
                found.append(unit["id"])
                frontier.append(unit["id"])
    return found


def prepare_unit_generation(workspace, unit_id):
    workspace = Path(workspace).expanduser().resolve()
    manifest = _read_json(workspace / "src/ui/ir/manifest.json")
    unit = _unit_record(manifest, unit_id)
    missing = check_workspace.unsatisfied_dependencies(
        workspace,
        manifest,
        _unit_states(workspace),
        check_workspace.latest_unit_reviews(_read_json(workspace / "project/approvals.json", [])),
        unit,
    )
    if missing:
        raise ValueError("dependencies 未 approved: %s" % ", ".join(missing))
    return unit


def begin_unit_generation(workspace, unit_id):
    """Claim a unit for (re)generation; units built on its previous output must be redone."""
    workspace = Path(workspace).expanduser().resolve()
    with STATE_LOCK:
        prepare_unit_generation(workspace, unit_id)
        manifest = _read_json(workspace / "src/ui/ir/manifest.json")
        states = _unit_states(workspace)
        for dependent in _dependents(manifest, unit_id):
            if states.get(dependent, "not-started") != "not-started":
                set_unit_status(workspace, dependent, "changes-requested", "上游 unit %s 已重新生成，需要按新产出重做" % unit_id)
        set_unit_status(workspace, unit_id, "in-progress")


def mark_unit_in_review(workspace, unit_id):
    workspace = Path(workspace).expanduser().resolve()
    with STATE_LOCK:
        manifest = _read_json(workspace / "src/ui/ir/manifest.json")
        unit = _unit_record(manifest, unit_id)
        if _unit_states(workspace).get(unit_id) != "in-progress":
            raise ValueError("unit %s 不在生成中" % unit_id)
        missing = [relative for relative in unit.get("files", []) if not (workspace / relative).is_file()]
        if not unit.get("files") or missing:
            raise ValueError("unit %s 缺少 output：%s" % (unit_id, ", ".join(missing) or "未声明文件"))
        set_unit_status(workspace, unit_id, "in-review")
        manifest = _read_json(workspace / "src/ui/ir/manifest.json")
        metadata = {
            "unitId": unit_id,
            "status": "in-review",
            "files": unit["files"],
            "manifestVersion": manifest["manifestVersion"],
            "manifestHash": manifest["hash"],
            "outputHash": check_workspace.unit_output_hash(workspace, unit),
        }
        _write_json(workspace / unit_dir(unit_id) / "metadata.json", metadata)
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


def append_unit_review(workspace, unit_id, conclusion, reviewer, evidence, file_scope, output_hash):
    if conclusion not in check_workspace.REVIEW_CONCLUSIONS:
        raise ValueError("unit review conclusion 无效")
    if not isinstance(reviewer, dict) or reviewer.get("type") != "subagent" or not str(reviewer.get("name") or "").strip():
        raise ValueError("unit review 必须由有名字的 subagent 提供")
    if not isinstance(evidence, list) or not evidence or any(not isinstance(item, str) or not item.strip() for item in evidence):
        raise ValueError("unit review 必须包含非空 evidence")
    if not isinstance(file_scope, list) or not file_scope:
        raise ValueError("unit review 必须包含 fileScope")
    workspace = Path(workspace).expanduser().resolve()
    with STATE_LOCK:
        manifest = _read_json(workspace / "src/ui/ir/manifest.json")
        unit = _unit_record(manifest, unit_id)
        if _unit_states(workspace).get(unit_id) != "in-review":
            raise ValueError("unit 尚未进入 in-review")
        for scope in file_scope:
            if not isinstance(scope, dict) or not isinstance(scope.get("path"), str) or type(scope.get("startLine")) is not int or type(scope.get("endLine")) is not int or scope["startLine"] < 1 or scope["endLine"] < scope["startLine"]:
                raise ValueError("fileScope 必须包含有效的 path 和行号")
            if scope["path"] not in unit["files"]:
                raise ValueError("fileScope 只能引用该 unit 的输出文件")
        if output_hash != check_workspace.unit_output_hash(workspace, unit):
            raise ValueError("outputHash 与当前输出不一致：审查的不是这一版输出")
        record = {
            "kind": "unit-review",
            "unitId": unit_id,
            "manifestVersion": manifest["manifestVersion"],
            "manifestHash": manifest["hash"],
            "outputHash": output_hash,
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
        set_unit_status(workspace, unit_id, conclusion)
        return record


def unit_overview(workspace):
    """Per-unit state, blockers and allowed actions, computed here so the page only renders it."""
    workspace = Path(workspace).expanduser().resolve()
    manifest = _read_json(workspace / "src/ui/ir/manifest.json", {})
    if not manifest.get("units"):
        return []
    status = _read_json(workspace / "project/status.json")
    states = _unit_states(workspace)
    approvals = _read_json(workspace / "project/approvals.json", [])
    reviews = check_workspace.latest_unit_reviews(approvals)
    notes = {item.get("unitId"): item.get("note") for item in status.get("units", []) if isinstance(item, dict)}
    try:
        _require_unit_phase(workspace, UI_UNIT_PHASE)
        phase_blocker = ""
    except ValueError as exc:
        phase_blocker = str(exc)
    busy = _running_job(workspace)
    overview = []
    for unit in manifest["units"]:
        state = states.get(unit["id"], "not-started")
        blocked_by = check_workspace.unsatisfied_dependencies(workspace, manifest, states, reviews, unit)
        review = reviews.get(unit["id"])
        overview.append({
            "id": unit["id"],
            "kind": unit.get("kind"),
            "status": state,
            "note": notes.get(unit["id"]) or "",
            "dependsOn": unit.get("dependsOn", []),
            "blockedBy": blocked_by,
            "output": unit_output_path(unit["id"]),
            "outputExists": (workspace / unit_output_path(unit["id"])).is_file(),
            "review": None if review is None else {
                "conclusion": review.get("conclusion"),
                "reviewer": (review.get("reviewer") or {}).get("name"),
                "evidence": review.get("evidence", []),
                "current": check_workspace.review_current(workspace, review, manifest, unit),
            },
            "phaseBlocker": phase_blocker,
            "canGenerate": not phase_blocker and not blocked_by and not busy,
            "canReview": not phase_blocker and state == "in-review" and not busy,
        })
    return overview


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
        status["units"] = [{"unitId": kind, "status": "not-started"} for kind in UI_UNIT_KINDS]
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


def _run_codex(cmd, cwd, timeout):
    """Run one `codex exec` to completion and return its exit code."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(cwd))
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    deadline = time.time() + timeout
    try:
        while True:
            if time.time() > deadline:
                proc.kill()
                raise TimeoutError("codex 进程超过 %d 分钟未完成，已停止" % (timeout // 60))
            if selector.select(timeout=1):
                if not proc.stdout.readline():
                    break
            elif proc.poll() is not None:
                break
    finally:
        selector.close()
    return proc.wait()


def _running_job(path):
    with JOBS_LOCK:
        return next((job_id for job_id, job in JOBS.items() if job.get("path") == str(path) and job["status"] == "running"), None)


def _create_job(path, prompt, unit_id=None):
    with JOBS_LOCK:
        if any(job.get("path") == str(path) and job["status"] == "running" for job in JOBS.values()):
            raise ValueError("该工作区已有正在运行的任务，请等它完成后再操作")
        job_id = str(time.time_ns())
        JOBS[job_id] = {
            "status": "running", "logs": [], "error": "", "updatedAt": time.time(), "step": "读取工作区资料",
            "prompt": prompt, "unitId": unit_id, "path": str(path), "check": None,
        }
    return job_id


def _job_log(job_id, message, step=None):
    with JOBS_LOCK:
        job = JOBS[job_id]
        job["logs"].append("[" + time.strftime("%H:%M:%S") + "] " + message)
        job["updatedAt"] = time.time()
        if step:
            job["step"] = step


def _finish_job(job_id, status, error=""):
    with JOBS_LOCK:
        JOBS[job_id]["status"] = status
        JOBS[job_id]["error"] = error
        JOBS[job_id]["updatedAt"] = time.time()
    _job_log(job_id, "任务失败：" + error if error else "任务已完成。", "失败" if error else "已完成")


def _record_phase_check(job_id, path, phase):
    """The phase check is reported next to the job, not folded into it: mid-progression it is expected to fail."""
    _job_log(job_id, "正在运行 Phase %d 检查…" % phase, "运行 Phase %d 检查" % phase)
    check = subprocess.run([sys.executable, str(ROOT / "scripts/check_workspace.py"), str(path), "--phase", str(phase)], capture_output=True, text=True)
    output = (check.stdout + check.stderr).strip()
    with JOBS_LOCK:
        JOBS[job_id]["check"] = {"passed": check.returncode == 0, "output": output}
    _job_log(job_id, ("检查通过。" if check.returncode == 0 else "检查未通过：") + ("" if check.returncode == 0 else output))


def _require_gates(path, phase):
    records = _read_json(path / "project/approvals.json", [])
    latest = {item.get("gate"): item.get("status") for item in records if isinstance(item, dict) and item.get("kind", "gate") == "gate"}
    missing = [gate for gate in ("G1", "G2", "G3", "G4")[:max(phase - 1, 0)] if latest.get(gate) != "approved"]
    if missing:
        raise ValueError("Phase %d 生成前必须完成审批：%s" % (phase, ", ".join(missing)))


def _require_unit_phase(path, phase):
    status = _read_json(path / "project/status.json")
    if phase != UI_UNIT_PHASE or int(status.get("phase", -1)) != UI_UNIT_PHASE:
        raise ValueError("UI unit 只能在 Phase 4（设计系统 G3 批准之后）生成和审查")
    _require_gates(path, UI_UNIT_PHASE)


def _phase_prompt(phase, choice):
    deliverables = "、".join(_phase_requirements(phase))
    direction_instruction = (
        "Phase 5 发布页必须读取 config/brand.json 和 project/approvals.json，"
        "只展示 G1 已选中的当前方向（本工作区为 Direction %s），"
        "不要在最终发布页并列展示 A/B/C 方案；可在说明文字中记录选择依据。"
        % choice
        if choice
        else "Phase 1 先生成三套方向并准备后续 G1 选择，不要假设已有方向审批。"
    )
    return f"""在当前品牌工作区完成 Phase {phase} 交付。只写入当前工作区目录，不修改技能仓库或其他目录。读取 brand.brief.json、review/、docs/、project/ 和 config/ 中现有资料；如有 source.json，读取其中列出的参考资料。创建并验证本阶段必需文件：{deliverables}。保持方向选择、审批和暂定假设可追溯，不把未确认内容写成最终事实。确保每张方向卡片的 hero 标题、描述和图标有独立空间，文字与图标不能重叠，并检查浅色与深色背景下的对比度。每套方向必须包含一个较大的产品界面配色 demo，展示背景、文字、按钮、状态、层级和真实场景，不要只放色板。{direction_instruction}同步更新 project/status.json 为 phase {phase}、state in-review，并保存 reports/phase-{phase}-check.txt。不要只解释，直接创建文件。"""


def start_generation(path, phase):
    path = Path(path).expanduser().resolve()
    choice = g1_choice(_read_json(path / "project/approvals.json", [])) if phase >= 2 else None
    prompt = _phase_prompt(phase, choice)
    job_id = _create_job(path, prompt)

    def run():
        cmd = ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "-s", "danger-full-access", "-C", str(path), "--add-dir", str(path), "--add-dir", str(ROOT), "--json", prompt]
        try:
            workspace_files = sorted(item for item in path.rglob("*") if item.is_file() and ".git" not in item.parts)
            _job_log(job_id, "已读取工作区资料：%d 个文件" % len(workspace_files))
            for item in (path / "brand.brief.json", path / "project/status.json", path / "project/source.json", path / "review/01-directions.html"):
                if item.is_file():
                    _job_log(job_id, "关键输入：" + str(item.relative_to(path)))
            _job_log(job_id, "正在生成 Phase %d 交付物…" % phase, "生成 Phase %d" % phase)
            code = _run_codex(cmd, path, 600)
            if code != 0:
                raise RuntimeError("生成进程退出码 %d" % code)
            for item in sorted(item for item in path.rglob("*") if item.is_file() and ".git" not in item.parts):
                if item not in workspace_files:
                    _job_log(job_id, "已生成：" + str(item.relative_to(path)))
            _record_phase_check(job_id, path, phase)
            _finish_job(job_id, "done")
        except Exception as exc:
            _finish_job(job_id, "error", str(exc))
    threading.Thread(target=run, daemon=True).start()
    return job_id


def _unit_prompt(path, unit, choice):
    ui = _read_json(path / "config/ui.json", {})
    inputs = "、".join(unit_output_path(dep) for dep in unit.get("dependsOn", [])) or "无"
    return f"""只完成 UI unit「{unit['id']}」（类型 {unit.get('kind')}），把页面写到 {unit_output_path(unit['id'])}。只写入 {unit_dir(unit['id'])}/ 目录；不要修改 project/、config/、src/ui/ir/、tokens/ 以及其他 unit 的目录。读取 src/ui/ir/manifest.json、config/ui.json、brand.brief.json、tokens/src/，以及依赖 unit 的输出：{inputs}。按 {ROOT / "references/ui.md"} 中该类型的要求完成（该文件只读）；颜色、字号、间距只引用 tokens/src 的 token，不复制数值。目标平台：{'、'.join(ui.get('platforms', []))}；技术栈：{ui.get('stackProfile', '')}；Desktop 与 Mobile 以 Web preview 呈现，同时写清平台语义，方便转换为 native 代码。品牌方向为 Direction {choice}。不要只解释，直接创建文件。"""


def _review_prompt(unit):
    return f"""你是独立审查者，只读不写。审查 UI unit「{unit['id']}」（类型 {unit.get('kind')}）的输出 {unit_output_path(unit['id'])}。对照 {ROOT / "references/ui.md"}、{ROOT / "references/accessibility.md"}、src/ui/ir/manifest.json 中该 unit 的 platforms 与 dependsOn、tokens/src/，以及依赖 unit 的输出。检查：是否满足该类型的职责，组件是否复用而不是重复造，状态（hover、focus-visible、disabled、loading、invalid、空、错误）是否齐全，颜色与间距是否只引用 token，平台语义是否写清，可访问性。每个问题给出严重度 P0–P3、位置和具体问题。只有没有 P0/P1 时结论才是 approved，否则是 changes-requested。"""


# Paths a unit job may not change outside its own unit directory.
PROTECTED_ROOTS = ("project", "config", "src/ui/ir", "src/ui/units")


def _protected_snapshot(path, unit_id):
    own = path / unit_dir(unit_id)
    files = {}
    for root in PROTECTED_ROOTS:
        base = path / root
        if base.is_dir():
            for item in base.rglob("*"):
                if item.is_file() and not item.is_relative_to(own):
                    files[str(item.relative_to(path))] = item.read_bytes()
    return files


def _restore_outside_unit(path, unit_id, before):
    after = _protected_snapshot(path, unit_id)
    changed = sorted(rel for rel in set(before) | set(after) if before.get(rel) != after.get(rel))
    for rel in changed:
        target = path / rel
        if rel in before:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(before[rel])
        else:
            target.unlink()
    return changed


def _review_unit(job_id, path, unit_id):
    unit = _unit_record(_read_json(path / "src/ui/ir/manifest.json"), unit_id)
    digest = check_workspace.unit_output_hash(path, unit)
    verdict_path = path / unit_dir(unit_id) / "review.json"
    if verdict_path.exists():
        verdict_path.unlink()
    _job_log(job_id, "正在启动独立审查进程（只读）…", "审查 unit %s" % unit_id)
    cmd = [
        "codex", "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only", "-C", str(path),
        "--output-schema", str(ROOT / "assets/unit-review-verdict.schema.json"), "-o", str(verdict_path), _review_prompt(unit),
    ]
    code = _run_codex(cmd, path, 600)
    if code != 0:
        raise RuntimeError("审查进程退出码 %d" % code)
    try:
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("审查进程没有给出可读的结论")
    findings = verdict.get("findings") if isinstance(verdict, dict) else None
    if (
        not isinstance(verdict, dict)
        or verdict.get("conclusion") not in check_workspace.REVIEW_CONCLUSIONS
        or not isinstance(verdict.get("summary"), str) or not verdict["summary"].strip()
        or not isinstance(findings, list)
        or any(not isinstance(item, dict) or item.get("severity") not in ("P0", "P1", "P2", "P3") for item in findings)
    ):
        raise ValueError("审查结论不符合 assets/unit-review-verdict.schema.json")
    output = path / unit_output_path(unit_id)
    with STATE_LOCK:
        if check_workspace.unit_output_hash(path, unit) != digest:
            raise ValueError("审查期间输出被改动，这次结论作废")
        evidence = [str(verdict_path.relative_to(path)), verdict["summary"].strip()] + [
            "%s %s：%s" % (item["severity"], item.get("location", ""), item.get("problem", "")) for item in findings
        ]
        scope = [{"path": unit_output_path(unit_id), "startLine": 1, "endLine": max(1, len(output.read_text(encoding="utf-8").splitlines()))}]
        record = append_unit_review(path, unit_id, verdict["conclusion"], UNIT_REVIEWER, evidence, scope, digest)
    _job_log(job_id, "审查结论：%s · %s" % (verdict["conclusion"], verdict["summary"].strip()))
    return record


def _fail_unit_job(job_id, path, unit_id, exc):
    error = str(exc)
    try:
        state = _unit_states(path).get(unit_id)
        if state == "in-progress":
            set_unit_status(path, unit_id, "in-progress", "生成失败：%s" % exc)
        elif state == "in-review":
            set_unit_status(path, unit_id, "in-review", "自动审查失败：%s" % exc)
    except Exception as note_error:
        error += "（记录失败原因时也出错：%s）" % note_error
    # Always reached: a job left "running" refuses every later job, approval and advance in this workspace.
    _finish_job(job_id, "error", error)


def start_unit_job(path, phase, unit_id):
    """Generate one UI unit, then have an independent read-only codex process review it."""
    path = Path(path).expanduser().resolve()
    _require_unit_phase(path, phase)
    choice = g1_choice(_read_json(path / "project/approvals.json", []))
    unit = _unit_record(_read_json(path / "src/ui/ir/manifest.json"), unit_id)
    prompt = _unit_prompt(path, unit, choice)
    job_id = _create_job(path, prompt, unit_id)
    try:
        begin_unit_generation(path, unit_id)
        before = _protected_snapshot(path, unit_id)
    except Exception:
        with JOBS_LOCK:
            JOBS.pop(job_id, None)
        raise

    def run():
        # No --add-dir: extra dirs become writable, and the skill repo must stay out of a unit job's reach.
        cmd = ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "-s", "workspace-write", "-C", str(path), "--json", prompt]
        try:
            _job_log(job_id, "正在生成 unit %s…" % unit_id, "生成 unit %s" % unit_id)
            run_error, code = None, None
            try:
                code = _run_codex(cmd, path, 600)
            except Exception as exc:
                run_error = exc
            # Restore before reporting anything: a timeout or crash may already have written outside the unit.
            changed = _restore_outside_unit(path, unit_id, before)
            if changed:
                raise RuntimeError(
                    ("%s；" % run_error if run_error else "")
                    + "生成进程改动了 unit 目录以外的文件，已恢复原样：" + "、".join(changed)
                )
            if run_error:
                raise run_error
            if code != 0:
                raise RuntimeError("生成进程退出码 %d" % code)
            mark_unit_in_review(path, unit_id)
            _job_log(job_id, "unit %s 已生成，进入审查。" % unit_id)
            _review_unit(job_id, path, unit_id)
            _record_phase_check(job_id, path, phase)
            _finish_job(job_id, "done")
        except Exception as exc:
            _fail_unit_job(job_id, path, unit_id, exc)
    threading.Thread(target=run, daemon=True).start()
    return job_id


def start_review_job(path, phase, unit_id):
    """Re-run only the independent review for a unit whose output is waiting for one."""
    path = Path(path).expanduser().resolve()
    _require_unit_phase(path, phase)
    if _unit_states(path).get(unit_id) != "in-review":
        raise ValueError("只有等待审查的 unit 可以重新审查")
    job_id = _create_job(path, "review " + unit_id, unit_id)

    def run():
        try:
            _review_unit(job_id, path, unit_id)
            _record_phase_check(job_id, path, phase)
            _finish_job(job_id, "done")
        except Exception as exc:
            _fail_unit_job(job_id, path, unit_id, exc)
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
                payload["uiUnits"] = unit_overview(path)
                job_id = _running_job(path)
                payload["activeJob"] = {"id": job_id, "unitId": JOBS[job_id].get("unitId")} if job_id else None
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
        if endpoint not in {"/api/init", "/api/advance", "/api/generate", "/api/review", "/api/approve"}: return self.send_json({"error": "not found"}, 404)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            if endpoint in ("/api/approve", "/api/advance") and _running_job(self.workspace_path(body.get("path", ""))):
                raise ValueError("该工作区有任务正在运行，完成后再记录审批或推进阶段")
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
                        body.get("outputHash"),
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
                    return self.send_json({"job": start_unit_job(path, phase, unit_id), "unitId": unit_id})
                if int(status.get("phase", -1)) != phase or phase not in (1, 2, 3, 4, 5):
                    raise ValueError("只能为当前阶段生成交付物")
                _require_gates(path, phase)
                return self.send_json({"job": start_generation(path, phase)})
            if endpoint == "/api/review":
                path = self.workspace_path(body.get("path", ""))
                unit_id = body.get("unitId", "")
                return self.send_json({"job": start_review_job(path, int(body.get("phase", 0)), unit_id), "unitId": unit_id})
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
