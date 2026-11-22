/* Original framework-free creation canvas; external model calls are server-side only. */
(() => {
'use strict';
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = { user:null, models:[], projects:[], project:null, kind:'image', zoom:1, panX:120, panY:75,
  nodes:[], edges:[], selected:null, connectedFrom:null, reference:null, loginMode:'login', adminTab:'models',
 adminModels:[], activeView:'canvas', drag:null, suppressNodeClick:false, saveTimer:null, loading:false };
const renderJimeng = window.createJimengPanel({$, api, esc, state, toast, renderModels});
let toastTimer;
let refreshVideoDurationPicker=()=>{};
const pageReferences={image:null,video:null};
const pageReferenceWidgets={};
function toast(message){ const el=$('toast'); el.textContent=message; el.classList.add('visible'); clearTimeout(toastTimer); toastTimer=setTimeout(()=>el.classList.remove('visible'),3100); }
async function api(path,opts={}){ const headers={...(opts.headers||{})}; if(opts.body && !(opts.body instanceof FormData)) headers['Content-Type']='application/json'; const res=await fetch('/api'+path,{credentials:'same-origin',...opts,headers}); const type=res.headers.get('content-type')||''; let out=type.includes('application/json')?await res.json():await res.text(); if(!res.ok) throw Error(typeof out?.detail==='string'?out.detail:(Array.isArray(out?.detail)?out.detail.map(x=>x.msg).join('; '):`请求失败 ${res.status}`)); return out; }
function setUser(user){state.user=user;$('accountName').textContent=user.username;$('accountRole').textContent=user.role==='admin'?'平台管理员':'创作用户';$('avatar').textContent=user.username[0]?.toUpperCase()||'Y';$('topPoints').textContent=Number(user.points).toLocaleString();$('sidePoints').textContent=Number(user.points).toLocaleString();document.querySelectorAll('.admin-only').forEach(e=>e.style.display=user.role==='admin'?'flex':'none');}
async function refreshUser(){try {setUser(await api('/me'));} catch(e){$('loginOverlay').hidden=false;}}
const defaultNodes=()=>[{id:'welcome-text',type:'text',title:'灵感 / 剧本',content:'一位古风少女走进夜晚的庭院，暖色灯笼照亮侧脸。\n\n在下方输入提示词，选择模型开始创作。',x:140,y:36},{id:'welcome-image',type:'image',title:'图片生成',content:'在这里预览生成结果',x:515,y:98}];
function newId(){return (crypto?.randomUUID?.()||Math.random().toString(36).slice(2)).replaceAll('-','').slice(0,24);}
async function loadWorkspace(){state.projects=await api('/projects');if(state.projects.length===0){const d=await api('/projects',{method:'POST',body:JSON.stringify({title:'我的第一个画布',nodes:defaultNodes(),edges:[{id:'start-edge',source:'welcome-text',target:'welcome-image'}]})});state.projects=await api('/projects'); await openProject(d.id);}else await openProject(state.projects[0].id);renderProjects();}
async function openProject(id){state.project=await api('/projects/'+id);state.nodes=state.project.nodes||[];state.edges=state.project.edges||[];state.selected=null;state.reference=null;state.panX=120;state.panY=75;state.zoom=1;$('projectTitle').textContent=state.project.title;applyTransform();renderNodes();renderProjects();setView('canvas');state.nodes.filter(n=>n.jobId&&n.status==='running').forEach(n=>watchJob(n.jobId,n.id));}
function renderProjects(){const list=$('projectList');list.innerHTML=state.projects.map(p=>`<div class="project-row"><button class="project-item ${state.project?.id===p.id?'selected':''}" data-project="${p.id}" title="${esc(p.title)}">▦ ${esc(p.title)}</button><button class="project-action" data-manage-project="${p.id}" title="编辑或删除画布" aria-label="编辑或删除画布 ${esc(p.title)}">⋮</button></div>`).join('');list.querySelectorAll('[data-project]').forEach(e=>e.onclick=()=>openProject(+e.dataset.project).catch(e=>toast(e.message)));list.querySelectorAll('[data-manage-project]').forEach(e=>e.onclick=()=>manageProject(+e.dataset.manageProject));}
function manageProject(id){
 const project=state.projects.find(p=>p.id===id);if(!project)return;
 const dialog=document.createElement('dialog');dialog.className='node-editor';dialog.innerHTML='<form><h3>编辑画布</h3><label>画布名称<input name="title" maxlength="80" required></label><p class="inline-error" role="alert"></p><div class="editor-actions"><button type="button" data-delete class="danger-btn">删除画布</button><button type="button" data-cancel>取消</button><button type="submit">保存</button></div></form>';
 document.body.append(dialog);const form=dialog.querySelector('form');form.elements.title.value=project.title;const close=()=>{dialog.close();dialog.remove();};dialog.querySelector('[data-cancel]').onclick=close;dialog.oncancel=e=>{e.preventDefault();close();};
 const run=async action=>{form.querySelectorAll('button').forEach(b=>b.disabled=true);try{await action();close();}catch(e){dialog.querySelector('[role=alert]').textContent=e.message;form.querySelectorAll('button').forEach(b=>b.disabled=false);}};
 form.onsubmit=e=>{e.preventDefault();const title=form.elements.title.value.trim();if(!title)return;run(async()=>{if(state.project?.id===id){clearTimeout(state.saveTimer);state.saveTimer=null;state.project.title=title;await saveGraph();$('projectTitle').textContent=title;}else{const graph=await api('/projects/'+id);await api('/projects/'+id,{method:'PUT',body:JSON.stringify({...graph,title})});}state.projects=await api('/projects');renderProjects();toast('画布已更新');});};
 dialog.querySelector('[data-delete]').onclick=()=>{if(!confirm(`删除画布“${project.title}”？画布节点和连线会删除，已生成作品仍保留。`))return;run(async()=>{const current=state.project?.id===id;if(current){clearTimeout(state.saveTimer);state.saveTimer=null;}await api('/projects/'+id,{method:'DELETE'});state.projects=await api('/projects');if(current){state.project=null;state.nodes=[];state.edges=[];state.selected=null;state.reference=null;state.connectedFrom=null;renderNodes();$('projectTitle').textContent='未选择画布';$('saveStatus').textContent='';if(state.projects.length)await openProject(state.projects[0].id);else setView('history');}renderProjects();toast('画布已删除');});};dialog.showModal();
}
function queueSave(){if(!state.project)return;$('saveStatus').textContent='◌ 保存中';clearTimeout(state.saveTimer);state.saveTimer=setTimeout(()=>saveGraph().catch(e=>toast('保存失败：'+e.message)),700);}
async function saveGraph(){if(!state.project)return;await api('/projects/'+state.project.id,{method:'PUT',body:JSON.stringify({title:state.project.title,nodes:state.nodes,edges:state.edges})});$('saveStatus').textContent='● 已保存';}
function applyTransform(){$('canvasTransform').style.transform=`translate(${state.panX}px,${state.panY}px) scale(${state.zoom})`;$('zoomLabel').textContent=Math.round(state.zoom*100)+'%';}
function nodeDims(n){let bodyHeight=n.type==='video'?276:n.type==='image'?232:165;return {w:250,h:bodyHeight};}
function renderEdges(){const byId=new Map(state.nodes.map(n=>[n.id,n]));$('connections').innerHTML=state.edges.map(edge=>{const a=byId.get(edge.source),b=byId.get(edge.target);if(!a||!b)return '';const aw=nodeDims(a),bh=nodeDims(b);const x1=a.x+aw.w,y1=a.y+aw.h*.46,x2=b.x,y2=b.y+bh.h*.46, bend=Math.max(80,Math.abs(x2-x1)*.45);return `<path d="M${x1} ${y1} C${x1+bend} ${y1},${x2-bend} ${y2},${x2} ${y2}"/><circle class="edge-dot" cx="${x1}" cy="${y1}" r="3"/><circle class="edge-dot" cx="${x2}" cy="${y2}" r="3"/>`;}).join('');}
function nodeMedia(n){if(n.src){const src=esc(n.src);return n.type==='video'?`<video src="${src}" controls preload="metadata" playsinline></video>`:`<img src="${src}" alt="${esc(n.title)}" loading="lazy">`;}let icon=n.type==='video'?'▷':n.type==='image'?'▧':'✎';return `<div class="node-placeholder"><div class="placeholder-art">${icon}</div><div>${esc(n.content||'等待创作')}</div></div>`;}
function renderNodes(){const host=$('nodeLayer');host.innerHTML=state.nodes.map(n=>{
 const isRef=n.type==='image'&&(n.assetId||n.jobId)&&n.status!=='failed'&&n.status!=='running';const body=n.type==='text'?`<div class="node-body">${esc(n.content||'双击编辑文字内容')}</div>`:`<div class="node-body">${nodeMedia(n)}${n.status==='running'?'<span class="node-status">⟳ 生成中</span>':n.status==='failed'?'<span class="node-status">生成失败</span>':''}</div>`;
 return `<div class="node ${esc(n.type)} ${state.selected===n.id?'selected':''}" data-id="${esc(n.id)}" style="left:${n.x}px;top:${n.y}px"><div class="node-head"><span class="node-type">${n.type==='text'?'✎':n.type==='image'?'▧':'▷'}</span><span class="node-title">${esc(n.title||'未命名')}</span><span>⋮</span></div>${body}<div class="node-footer"><span>${n.type==='text'?'文本节点':n.type==='image'?'图片节点':'视频节点'}</span>${isRef?'<button class="select-ref" data-ref="'+esc(n.id)+'">选为参考图</button>':''}</div><button class="port in ${state.connectedFrom?'active-port':''}" data-port="in" title="连接输入">+</button><button class="port out" data-port="out" title="连接输出">+</button></div>`;
 }).join('');host.querySelectorAll('.node').forEach(el=>{const n=state.nodes.find(x=>x.id===el.dataset.id);el.addEventListener('pointerdown',evt=>{
   if(evt.target.closest('button,video'))return;selectNode(n.id);
   if(evt.target.closest('.node-head')){evt.preventDefault();state.drag={kind:'node',id:n.id,startX:evt.clientX,startY:evt.clientY,x:n.x,y:n.y};el.setPointerCapture(evt.pointerId);}
 });el.querySelector('.node-head').addEventListener('dblclick',()=>editNode(n.id));
 const menu=document.createElement('button');menu.className='node-edit';menu.textContent='⋮';menu.title='编辑节点';menu.setAttribute('aria-label','编辑节点');el.querySelector('.node-head').lastElementChild.replaceWith(menu);menu.onclick=e=>{e.stopPropagation();editNode(n.id);};
 if(n.type==='text')el.querySelector('.node-body').ondblclick=()=>editNode(n.id);
 el.addEventListener('click',evt=>{if(evt.target.closest('button,video,.port'))return;if(state.suppressNodeClick){state.suppressNodeClick=false;return;}editNode(n.id);});
 el.querySelectorAll('[data-port]').forEach(p=>p.addEventListener('click',evt=>{evt.stopPropagation();if(p.dataset.port==='out'){state.connectedFrom=n.id;toast('连接开始：点击目标节点左侧圆点');renderNodes();}else if(state.connectedFrom&&state.connectedFrom!==n.id){if(!state.edges.some(e=>e.source===state.connectedFrom&&e.target===n.id)){state.edges.push({id:newId(),source:state.connectedFrom,target:n.id});queueSave();}state.connectedFrom=null;renderNodes();}else{toast('请先点击另一个节点右侧圆点');}}));
 const choose=el.querySelector('[data-ref]');if(choose)choose.addEventListener('click',ev=>{ev.stopPropagation();setReference(n);});
 });renderEdges();}
function selectNode(id){state.selected=id;document.querySelectorAll('.node').forEach(e=>e.classList.toggle('selected',e.dataset.id===id));}
function editNode(id){
 const n=state.nodes.find(x=>x.id===id);if(!n)return;
 const dialog=document.createElement('dialog');dialog.className='node-editor';
 dialog.innerHTML=`<form><h3>编辑${n.type==='image'?'图片':n.type==='video'?'视频':'文字'}节点</h3><label>节点名称<input name="title" maxlength="160" required></label><label>${n.type==='text'?'文字内容':n.type==='image'?'描述你想要的画面':'描述视频画面、动作和运镜'}<textarea name="content" rows="6" placeholder="输入提示词"></textarea></label><div class="editor-options"></div><div class="editor-actions"><button type="button" data-action="delete">删除节点</button><button type="button" data-action="disconnect">断开连线</button><button type="button" data-action="cancel">取消</button>${n.type==='text'?'<button type="submit">保存</button>':'<button type="button" data-action="generate">生成</button>'}</div></form>`;
 document.body.append(dialog);const form=dialog.querySelector('form');form.elements.title.value=n.title||'';form.elements.content.value=n.type==='text'?n.content||'':n.prompt||'';
 if(n.src){const preview=document.createElement('button');preview.type='button';preview.className='reference-thumb';preview.title='预览图片';preview.textContent='预览';preview.onclick=()=>previewReference({url:n.src,name:n.title,mime:n.type==='video'?'video/mp4':'image/jpeg'});form.querySelector('h3').append(preview);}
 const close=()=>{dialog.close();dialog.remove();};
 const commit=()=>{n.title=form.elements.title.value.trim()||'未命名';if(n.type==='text')n.content=form.elements.content.value;else n.prompt=form.elements.content.value;renderNodes();queueSave();};
 form.onsubmit=e=>{e.preventDefault();commit();close();};
 dialog.querySelector('[data-action="cancel"]').onclick=close;
 dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
 dialog.querySelector('[data-action="delete"]').onclick=()=>{state.nodes=state.nodes.filter(x=>x.id!==id);state.edges=state.edges.filter(e=>e.source!==id&&e.target!==id);if(state.reference&&(state.reference.jobId===n.jobId||state.reference.assetId===n.assetId))clearReference();state.selected=null;renderNodes();queueSave();close();};
 dialog.querySelector('[data-action="disconnect"]').onclick=()=>{state.edges=state.edges.filter(e=>e.source!==id&&e.target!==id);renderEdges();queueSave();close();};
 dialog.showModal();
 if(n.type!=='text'){
  const options=form.querySelector('.editor-options');
  const field=(label,name,control)=>{const wrap=document.createElement('label');wrap.textContent=label;control.name=name;wrap.append(control);options.append(wrap);return control;};
  const modelSelect=field('模型','model',document.createElement('select'));modelSelect.innerHTML=state.models.filter(m=>m.kind===n.type).map(m=>`<option value="${m.id}">${esc(m.name)}</option>`).join('')||'<option value="">没有可用模型</option>';if(n.modelId)modelSelect.value=String(n.modelId);
  const ratio=field('画幅','ratio',document.createElement('select'));ratio.innerHTML=['1:1','16:9','9:16','4:3'].map(v=>`<option>${v}</option>`).join('');ratio.value=n.ratio|| (n.type==='video'?'16:9':'1:1');
  const resolution=field('分辨率','resolution',document.createElement('select'));resolution.innerHTML=(n.type==='image'?['auto','1K','2K','4K']:['480p','720p','1080p']).map(v=>`<option>${v}</option>`).join('');resolution.value=n.resolution|| (n.type==='image'?'auto':'720p');
  if(n.type==='video'){updateVideoResolutions(resolution,modelSelect.value);modelSelect.addEventListener('change',()=>updateVideoResolutions(resolution,modelSelect.value));}
  const duration=field('时长（秒）','duration',document.createElement('input'));duration.type='number';duration.setAttribute('list','videoDurationPresets');duration.min='1';duration.max='30';duration.step='1';duration.required=n.type==='video';duration.value=String(n.duration||5);duration.hidden=n.type!=='video';if(n.type==='video')mountDurationPicker(duration,modelSelect);
  const count=field('张数','count',document.createElement('select'));count.innerHTML=[1,2,3,4].map(v=>`<option>${v}</option>`).join('');count.value=String(n.count||1);count.hidden=n.type!=='image';
  const style=field('风格','style',document.createElement('input'));style.placeholder='例如：电影感、写实';style.value=n.style||'';style.hidden=n.type!=='image';
  const camera=field(n.type==='image'?'相机 / 镜头':'运镜','camera',document.createElement('input'));camera.placeholder=n.type==='image'?'例如：人像特写':'例如：缓慢推进';camera.value=n.camera||'';
  const lens=field('焦段编辑','lens',document.createElement('input'));lens.placeholder='例如：50mm';lens.value=n.lens||'';lens.hidden=n.type!=='image';
  const enhance=field('AI 识别素材','enhance',document.createElement('input'));enhance.type='checkbox';enhance.checked=!!n.enhance;
  const supplement=field('补全提示词','supplement',document.createElement('input'));supplement.type='checkbox';supplement.checked=n.supplement!==false;
  const refRow=document.createElement('div');refRow.className='editor-reference';const refLabel=document.createElement('span');refLabel.textContent=n.referenceName||'未上传参考图';const uploadButton=document.createElement('button');uploadButton.type='button';uploadButton.textContent=n.type==='video'?'参考视频':'参考图';const refInput=document.createElement('input');refInput.type='file';refInput.accept=n.type==='video'?'video/mp4,video/webm,video/quicktime':'image/jpeg,image/png,image/webp';refInput.hidden=true;refRow.append(uploadButton,refLabel,refInput);options.append(refRow);
  if(n.type==='video'){refInput.accept='image/jpeg,image/png,image/webp,video/mp4,video/webm';uploadButton.textContent='参考图片 / 视频';}uploadButton.onclick=()=>{refInput.value='';refInput.click();};refInput.onchange=async()=>{const file=refInput.files[0];if(!file)return;uploadButton.disabled=true;const generateButton=form.querySelector('[data-action="generate"]');const wasDisabled=generateButton.disabled;generateButton.disabled=true;refLabel.textContent='上传中…';try{const fd=new FormData();fd.append('file',file);const asset=await api('/uploads',{method:'POST',body:fd});referenceWidget.remove();n.referenceAssetId=asset.id;n.referenceJobId=null;n.referenceName=file.name;n.referenceUrl=asset.url;n.referenceMime=file.type;referenceWidget.render();queueSave();toast('参考素材上传成功');}catch(e){refLabel.textContent=n.referenceName||'未上传参考图';toast('上传失败：'+e.message);}finally{uploadButton.disabled=false;generateButton.disabled=wasDisabled;}};
  const referenceWidget=mountReference(form.elements.content,refRow,()=>n.referenceAssetId?{assetId:n.referenceAssetId,name:n.referenceName,url:n.referenceUrl,mime:n.referenceMime}:n.referenceJobId?{jobId:n.referenceJobId,name:n.referenceName,url:n.referenceUrl}:null,ref=>{n.referenceAssetId=ref?.assetId||null;n.referenceJobId=ref?.jobId||null;n.referenceName=ref?.name||'';n.referenceUrl=ref?.url||'';n.referenceMime=ref?.mime||'';n.prompt=form.elements.content.value;queueSave();},()=>refLabel.textContent=n.referenceName||'未上传参考图');
  const button=form.querySelector('[data-action="generate"]');button.disabled=n.status==='running';
  button.onclick=async()=>{if(n.type==='video'&&!duration.reportValidity())return;const promptText=form.elements.content.value.trim();if(!promptText||!modelSelect.value){toast('请填写提示词并选择模型');return;}button.disabled=true;button.textContent='生成中…';try{commit();n.status='running';n.content='生成中…';renderNodes();const job=await submitJob({type:n.type,prompt:promptText,modelId:+modelSelect.value,ratio:ratio.value,resolution:resolution.value,duration:+duration.value,reference:n.referenceAssetId?{assetId:n.referenceAssetId}:n.referenceJobId?{jobId:n.referenceJobId}:null,projectId:state.project.id,replaceNode:n,style:style.value,camera:camera.value,lens:lens.value,count:+count.value,enhance:enhance.checked,supplement:supplement.checked});n.count=+count.value;n.style=style.value;n.camera=camera.value;n.lens=lens.value;n.enhance=enhance.checked;n.supplement=supplement.checked;await saveGraph();close();refreshUser();watchJob(job.id,n.id);}catch(e){button.disabled=false;button.textContent='生成';toast(e.message);}};
 }
 if(n.type!=='text'&&!n.prompt&&n.jobId)api('/jobs/'+n.jobId).then(j=>{if(dialog.isConnected&&!form.elements.content.value)form.elements.content.value=j.prompt||'';}).catch(e=>toast(e.message));
}

function referenceToken(ref){return '@参考图_'+(ref.assetId?'A'+ref.assetId:'J'+ref.jobId);}
function previewReference(ref){
 const dialog=document.createElement('dialog');dialog.className='reference-preview';
 const close=document.createElement('button');close.type='button';close.textContent='×';close.title='关闭预览';close.setAttribute('aria-label','关闭预览');
 const media=document.createElement(ref.mime?.startsWith('audio/')?'audio':ref.mime?.startsWith('video/')?'video':'img');media.src=ref.url;media.alt=ref.name||'参考素材';if(media.tagName!=='IMG')media.controls=true;
 const title=document.createElement('p');title.textContent=ref.name||'参考图';
 const dismiss=()=>{dialog.close();dialog.remove();};close.onclick=dismiss;dialog.oncancel=e=>{e.preventDefault();dismiss();};
 dialog.append(close,media,title);document.body.append(dialog);dialog.showModal();
}
function mountReference(prompt,host,get,set,onRender){
 const card=document.createElement('div');card.className='reference-card';host.append(card);
 const menu=document.createElement('div');menu.className='reference-menu';menu.hidden=true;menu.setAttribute('role','listbox');prompt.insertAdjacentElement('afterend',menu);
 let active=0,choices=[],match=null;
 const available=()=>{
  const refs=[get(),...state.nodes.filter(n=>n.type==='image'&&n.src&&(n.assetId||n.jobId)&&n.status==='completed').map(n=>({assetId:n.assetId,jobId:n.assetId?null:n.jobId,url:n.src,name:n.title,mime:'image/jpeg'}))].filter(Boolean);
  return refs.filter((r,i)=>refs.findIndex(x=>referenceToken(x)===referenceToken(r))===i);
 };
 const strip=ref=>{if(ref)prompt.value=prompt.value.split(referenceToken(ref)).join('');};
 const remove=()=>{strip(get());set(null);render();menu.hidden=true;};
 const choose=ref=>{
  const start=match?.index??prompt.selectionStart,end=match?prompt.selectionStart:prompt.selectionEnd;
  const before=prompt.value.slice(0,start),after=prompt.value.slice(end),old=get();
  const clean=text=>old?text.split(referenceToken(old)).join(''):text;
  const inserted=clean(before)+referenceToken(ref)+' ';
  prompt.value=inserted+clean(after);set(ref);render();menu.hidden=true;prompt.focus();prompt.setSelectionRange(inserted.length,inserted.length);match=null;
 };
 const render=()=>{
  card.replaceChildren();const ref=get();card.hidden=!ref;onRender?.();if(!ref)return;
  const preview=document.createElement('button');preview.type='button';preview.className='reference-thumb';preview.title='预览参考素材';preview.setAttribute('aria-label','预览参考素材');
  if(ref.url){if(ref.mime?.startsWith('audio/'))preview.textContent='音频';else{const media=document.createElement(ref.mime?.startsWith('video/')?'video':'img');media.src=ref.url;media.alt=ref.name||'参考素材';preview.append(media);}preview.onclick=()=>previewReference(ref);}else{preview.textContent='素材';preview.disabled=true;}
  const name=document.createElement('span');name.textContent=ref.name||'参考图';
  const mention=document.createElement('button');mention.type='button';mention.textContent='@';mention.title='引用到提示词';mention.setAttribute('aria-label','引用到提示词');mention.onclick=()=>{match=null;choose(ref);};
  const view=document.createElement('button');view.type='button';view.textContent='预览';view.disabled=!ref.url;view.onclick=()=>previewReference(ref);
  const del=document.createElement('button');del.type='button';del.textContent='删除';del.title='移除参考素材';del.setAttribute('aria-label','移除参考素材');del.onclick=remove;
  card.append(preview,name,view,mention,del);
 };
 const show=()=>{
  match=prompt.value.slice(0,prompt.selectionStart).match(/@([^@\s]*)$/);menu.replaceChildren();
  if(!match){menu.hidden=true;return;}
  const query=match[1].toLowerCase();choices=available().filter(r=>(r.name||'').toLowerCase().includes(query)||referenceToken(r).includes(query));active=0;
  menu.hidden=false;
  if(!choices.length){menu.textContent='暂无匹配素材';return;}
  choices.forEach((ref,i)=>{const button=document.createElement('button');button.type='button';button.setAttribute('role','option');button.setAttribute('aria-selected',String(i===0));if(ref.url){const img=document.createElement('img');img.src=ref.url;img.alt='';button.append(img);}const name=document.createElement('span');name.textContent=ref.name||'参考图';button.append(name);button.onmousedown=e=>e.preventDefault();button.onclick=()=>choose(ref);menu.append(button);});
 };
 prompt.addEventListener('input',show);prompt.addEventListener('click',show);
 prompt.addEventListener('keydown',e=>{
  if(menu.hidden)return;
  if(e.key==='Escape'){e.preventDefault();e.stopPropagation();menu.hidden=true;return;}
  if(!choices.length)return;
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();active=(active+(e.key==='ArrowDown'?1:-1)+choices.length)%choices.length;Array.from(menu.children).forEach((b,i)=>b.setAttribute('aria-selected',String(i===active)));}
  if(e.key==='Enter'){e.preventDefault();choose(choices[active]);}
 });
 prompt.addEventListener('blur',()=>setTimeout(()=>{menu.hidden=true;},150));
 render();return {render,remove};
}

function setReference(n){if(!n||n.type!=='image'||(!n.assetId&&!n.jobId)){toast('请先上传图片，或生成图片后再作为参考');return;}state.reference={assetId:n.assetId||null,jobId:n.jobId||null,name:n.title};toast('已选为下一次生成的参考图片');}
function clearReference(){state.reference=null;}
function addNode(type, extra={}){const pos={x:Math.round((250-state.panX)/state.zoom+Math.random()*100),y:Math.round((120-state.panY)/state.zoom+Math.random()*90)};
 if(state.selected){const prev=state.nodes.find(n=>n.id===state.selected);if(prev){pos.x=prev.x+350;pos.y=prev.y+35;}}
 const n={id:newId(),type,title:type==='image'?'图片节点':type==='video'?'视频节点':'文字节点',content:type==='text'?'双击文字节点编辑内容':'等待创作',...pos,...extra};state.nodes.push(n);if(state.selected){state.edges.push({id:newId(),source:state.selected,target:n.id});}selectNode(n.id);renderNodes();queueSave();return n;}
function centerCanvas(){state.panX=120;state.panY=75;state.zoom=1;applyTransform();}
function fitCanvas(){if(!state.nodes.length)return centerCanvas();let minX=Math.min(...state.nodes.map(n=>n.x)),minY=Math.min(...state.nodes.map(n=>n.y)),maxX=Math.max(...state.nodes.map(n=>n.x+nodeDims(n).w)),maxY=Math.max(...state.nodes.map(n=>n.y+nodeDims(n).h));const stage=$('stage');const availW=stage.clientWidth-200,availH=stage.clientHeight-300;state.zoom=Math.max(.45,Math.min(1.1,availW/(maxX-minX),availH/(maxY-minY)));state.panX=(stage.clientWidth-(maxX-minX)*state.zoom)/2-minX*state.zoom;state.panY=(stage.clientHeight-(maxY-minY)*state.zoom)/2-minY*state.zoom-55;applyTransform();}
function setKind(kind){state.kind=kind;renderModels();}
function renderModels(){const fill=(el,kind)=>{if(!el)return;const current=el.value;const list=state.models.filter(m=>m.kind===kind);el.innerHTML=list.map(m=>`<option value="${m.id}">${esc(m.name)}</option>`).join('')||'<option value="">请先在后台添加模型</option>';el.value=list.some(m=>String(m.id)===current)?current:String(list[0]?.id||'');};fill($('modelSelect'),state.kind);fill($('imageModel'),'image');fill($('videoModel'),'video');refreshVideoDurationPicker();updateVideoResolutions($('videoResolution'),$('videoModel').value);updateCost();}
function updateCost(){const el=$('modelSelect'),label=$('costLabel');if(!el||!label)return;const m=state.models.find(m=>String(m.id)===el.value);label.textContent=m?`≈ ${m.cost} 积分`:'暂无模型';}
async function upload(){const input=$('fileInput');input.accept=state.uploadTarget==='video'?'image/jpeg,image/png,image/webp,video/mp4,video/webm,audio/mpeg,audio/wav,.mp3,.wav':'image/jpeg,image/png,image/webp';input.value='';input.click();}
async function onFile(file,target='canvas'){if(!file)return;const form=new FormData();form.append('file',file);try{toast('上传参考图片中…');const r=await api('/uploads',{method:'POST',body:form});if(target==='canvas'){const n=addNode('image',{title:file.name.slice(0,35),src:r.url,assetId:r.id,status:'completed'});setReference(n);}else{pageReferenceWidgets[target].remove();pageReferences[target]={assetId:r.id,url:r.url,name:file.name,mime:file.type};pageReferenceWidgets[target].render();}toast('图片上传成功');}catch(e){toast('上传失败：'+e.message);}}
async function submitJob({type,prompt,modelId,ratio,resolution,duration,reference=state.pageReference,projectId=null,replaceNode=null,style='',camera='',lens='',count=1,enhance=false}){const model=state.models.find(m=>m.id===+modelId&&m.kind===type);if(!model)throw Error('当前没有对应类型的可用模型，请先在后台配置');let finalPrompt=prompt;if(style)finalPrompt+=`\nStyle: ${style}`;if(camera)finalPrompt+=`\nCamera: ${camera}`;if(lens)finalPrompt+=`\nLens: ${lens}`;if(enhance)finalPrompt+=`\nEnhance and refine the prompt while preserving its subject and intent.`;const job=await api('/jobs',{method:'POST',body:JSON.stringify({project_id:projectId,model_id:+modelId,prompt:finalPrompt,ratio,duration,resolution,reference_asset_id:reference?.assetId||null,reference_job_id:reference?.jobId||null,image_count:count})});if(replaceNode){replaceNode.prompt=finalPrompt;replaceNode.modelId=+modelId;replaceNode.ratio=ratio;replaceNode.resolution=resolution;replaceNode.duration=duration;replaceNode.jobId=job.id;replaceNode.status='running';replaceNode.content='正在生成';replaceNode.src='';}else if(projectId&&state.project?.id===projectId){const n=addNode(type,{title:model.name+' · '+prompt.slice(0,12),prompt:finalPrompt,modelId:+modelId,ratio,resolution,duration,content:'任务提交成功，正在生成…',status:'running',jobId:job.id});if(reference?.jobId||reference?.assetId){const ref=state.nodes.find(x=>x.assetId===reference.assetId||x.jobId===reference.jobId);if(ref&&!state.edges.some(e=>e.source===ref.id&&e.target===n.id))state.edges.push({id:newId(),source:ref.id,target:n.id});}}await saveGraph();if(reference===state.pageReference)state.pageReference=null;refreshUser();if(projectId)watchJob(job.id,replaceNode?.id||state.nodes.at(-1)?.id);return job;}
async function generate(){if(state.loading||!['image','video'].includes(state.activeView))return;const type=state.activeView;if(type==='video'&&!$('videoDuration').reportValidity())return;const prompt=$(type+'Prompt').value.trim();const modelId=+$(type+'Model').value;if(!prompt){toast('请先输入提示词');return;}const btn=$(type+'Generate');state.loading=true;btn.disabled=true;try{const job=await submitJob({type,prompt,modelId,ratio:$(type+'Ratio').value,resolution:$(type+'Resolution').value,duration:type==='video'?+$(type+'Duration').value:5,reference:pageReferences[type],projectId:null,style:type==='image'?$('imageStyle').value:'',camera:type==='image'?$('imageCamera').value:'',lens:type==='image'?$('imageLens').value:'',count:type==='image'?+$('imageCount').value:1,enhance:type==='image'?$('imageEnhance').checked:false});toast('已提交生成任务');renderPageResult(job,type);}catch(e){toast('生成失败：'+e.message);}finally{state.loading=false;btn.disabled=false;}}
function renderPageResult(job,type){const host=$(`${type}Results`);const card=document.createElement('div');card.className='generation-result';card.dataset.jobId=job.id;card.innerHTML='<div class="result-pending">生成中…<span class="result-spinner">⟳</span></div>';host.prepend(card);const poll=async()=>{try{const j=await api('/jobs/'+job.id);if(j.status==='completed'){card.innerHTML=(type==='video'?`<video src="${esc(j.result_url)}" controls></video>`:`<img src="${esc(j.result_url)}" alt="生成结果">`)+`<div class="result-actions"><button class="small-btn" data-result-edit="${esc(j.id)}">编辑并重新生成</button><a class="small-btn" href="${esc(j.result_url)}" download target="_blank" rel="noopener">下载</a></div>`;card.querySelector('[data-result-edit]').onclick=async()=>{const latest=await api('/jobs/'+j.id);$(`${type}Prompt`).value=latest.prompt||'';setView(type);$(`${type}Prompt`).focus();toast('已载入提示词，可修改后重新生成');};return;}if(j.status==='failed'){card.innerHTML=`<div class="result-error">生成失败：${esc(j.error||'未知错误')}</div><button class="small-btn" data-result-edit="${esc(j.id)}">修改后重试</button>`;card.querySelector('[data-result-edit]').onclick=async()=>{$(`${type}Prompt`).value=j.prompt||'';$(`${type}Prompt`).focus();};return;}setTimeout(poll,2500);}catch(e){card.textContent=e.message;}};setTimeout(poll,2000);}
async function watchJob(jobId,nodeId){for(let attempt=0;attempt<240;attempt++){await new Promise(r=>setTimeout(r,3000));try{const j=await api('/jobs/'+jobId);if(j.status==='completed'||j.status==='failed'){
 const n=state.nodes.find(x=>x.id===nodeId&&x.jobId===jobId);if(n){n.status=j.status;n.src=j.result_url;n.content=j.status==='failed'?'生成失败：'+j.error:'生成完成';renderNodes();queueSave();}toast(j.status==='completed'?'生成完成，点击节点预览':'生成失败：'+j.error.slice(0,100));refreshUser();return;}
 }catch(e){if(e.message.includes('登录'))return;}}
 toast('任务执行较久，请在“我的作品”继续查看进度');}
function setView(view){if(view==='canvas'&&state.project){let frame=$('infiniteCanvasFrame');if(!frame){frame=document.createElement('iframe');frame.id='infiniteCanvasFrame';frame.title='雪宝 Infinite Canvas';$('canvasView').append(frame);}const target='/canvas/studio-'+state.project.id;if(frame.dataset.project!==String(state.project.id)){frame.dataset.project=String(state.project.id);frame.src=target;}}const selectedNav=view;state.activeView=view;document.querySelectorAll('.view').forEach(e=>e.classList.toggle('active',e.id===(view==='canvas'?'canvasView':view==='image'?'imageView':view==='video'?'videoView':view==='history'?'historyView':'adminView')));document.querySelectorAll('[data-view]').forEach(e=>e.classList.toggle('active',e.dataset.view===selectedNav));$('pageTitle').textContent=view==='canvas'?'节点画布':view==='image'?'图片生成':view==='video'?'视频生成':view==='history'?'我的作品':'管理后台';$('sidebar').classList.remove('open');if(view==='image'||view==='video'){setKind(view);$('imagePrompt').focus({preventScroll:true});}if(view==='history')loadHistory();if(view==='admin')loadAdmin();}
async function loadHistory(){try{await refreshUser();const items=await api('/jobs');$('historyGrid').innerHTML=items.length?items.map(j=>{
 const m=j.result_url?(j.kind==='video'?`<video src="${esc(j.result_url)}" controls preload="metadata"></video>`:`<img src="${esc(j.result_url)}" alt="生成作品">`):'<span>◷ 任务'+(j.status==='failed'?'失败':'执行中')+'</span>';
 return `<article class="history-card"><div class="history-media">${m}</div><div class="history-info"><strong>${esc(j.prompt.slice(0,70))}</strong><small>${esc(j.model_name||'模型')} · ${esc(j.created_at.slice(0,16).replace('T',' '))}</small><span class="status-pill ${esc(j.status)}">${j.status==='completed'?'已完成':j.status==='failed'?'失败':'执行中'}</span> ${j.result_url?`<a href="${esc(j.result_url)}" download target="_blank" rel="noopener">查看 / 下载</a>`:''}${j.error?`<small>${esc(j.error.slice(0,130))}</small>`:''}${['completed','failed'].includes(j.status)?`<button class="small-btn danger-btn" data-delete-job="${esc(j.id)}">删除作品</button>`:''}</div></article>`;}).join(''):'<div class="empty-note">还没有作品。前往节点画布开始第一次创作。</div>';
 $('historyGrid').querySelectorAll('[data-delete-job]').forEach(button=>button.onclick=async()=>{if(!confirm('从我的作品中删除此作品？画布中的图片会保留。'))return;button.disabled=true;try{await api('/jobs/'+button.dataset.deleteJob,{method:'DELETE'});await loadHistory();toast('作品已删除');}catch(e){button.disabled=false;toast(e.message);}});
 }catch(e){toast(e.message);}}
async function loadAdmin(){if(state.user?.role!=='admin')return;try{const stats=await api('/admin/overview');$('statsGrid').innerHTML=[['注册用户',stats.users],['任务总量',stats.jobs],['已完成',stats.completed],['模型接口',stats.models]].map(([name,value])=>`<div class="stat-card"><strong>${value}</strong><span>${name}</span></div>`).join('');await renderAdminTable();renderJimeng.refreshNotices?.();}catch(e){toast(e.message);}}

async function renderInvitations(host){
 const rows=await api('/admin/invitations');
 host.innerHTML='<form id="inviteForm" class="admin-user-actions"><label>生成数量 <input name="count" type="number" min="1" max="50" value="1" required style="width:70px"></label><button class="primary-btn" type="submit">生成邀请码</button></form><div id="newInvitationCodes"></div><table class="data-table"><thead><tr><th>邀请码</th><th>状态</th><th>使用人</th><th>生成时间 / 使用时间</th><th>操作</th></tr></thead><tbody>'+rows.map(r=>'<tr><td><code>'+esc(r.code)+'</code></td><td>'+(r.used_at?'已使用':r.revoked_at?'已作废':'未使用')+'</td><td>'+esc(r.used_username||'-')+'</td><td>'+esc(r.created_at.slice(0,16).replace('T',' '))+'<br>'+esc(r.used_at?.slice(0,16).replace('T',' ')||'-')+'</td><td>'+(!r.used_at&&!r.revoked_at?'<button class="small-btn" data-copy-invite="'+esc(r.code)+'">复制</button> <button class="small-btn danger-btn" data-revoke-invite="'+r.id+'">作废</button>':'')+'</td></tr>').join('')+'</tbody></table>';
 const copy=async text=>{try{await navigator.clipboard.writeText(text);toast('邀请码已复制');}catch{prompt('邀请码',text);}};
 host.querySelectorAll('[data-copy-invite]').forEach(b=>b.onclick=()=>copy(b.dataset.copyInvite));
 host.querySelectorAll('[data-revoke-invite]').forEach(b=>b.onclick=async()=>{if(!confirm('作废后此邀请码将无法注册，确定吗？'))return;b.disabled=true;try{await api('/admin/invitations/'+b.dataset.revokeInvite,{method:'DELETE'});await renderInvitations(host);}catch(e){b.disabled=false;toast(e.message);}});
 host.querySelector('form').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;try{const result=await api('/admin/invitations',{method:'POST',body:JSON.stringify({count:Number(e.target.elements.count.value)})});await renderInvitations(host);const output=host.querySelector('#newInvitationCodes');const area=document.createElement('textarea');area.readOnly=true;area.rows=Math.min(8,result.codes.length);area.value=result.codes.join('\n');area.style.width='100%';const copyButton=document.createElement('button');copyButton.textContent='复制本批邀请码';copyButton.className='small-btn';copyButton.onclick=()=>copy(area.value);output.append(area,copyButton);toast('已生成 '+result.codes.length+' 个一次性邀请码');}catch(error){button.disabled=false;toast(error.message);}};
}

async function renderAdminTable(){const tab=state.adminTab;const host=$('adminTable');$('modelActions').hidden=tab!=='models';if(tab==='jimeng'){await renderJimeng(host);return;}if(tab==='messages'){await renderJimeng.renderMessages(host);return;}if(tab==='invitations'){await renderInvitations(host);return;}if(tab==='models'){
 state.adminModels=await api('/admin/models');host.innerHTML=state.adminModels.length?`<table class="data-table"><thead><tr><th>名称 / 模型</th><th>类型</th><th>协议</th><th>扣费</th><th>状态</th><th>操作</th></tr></thead><tbody>${state.adminModels.map(m=>`<tr><td><strong>${esc(m.name)}</strong><br><code>${esc(m.model_code)}</code></td><td>${m.kind==='image'?'图片':'视频'}</td><td>${esc(m.protocol)}</td><td>${m.cost} 积分</td><td><span class="status-pill ${m.enabled?'completed':'failed'}">${m.enabled?'启用':'停用'}</span></td><td><button class="small-btn" data-edit-model="${m.id}">配置接口</button> <button class="small-btn" data-toggle-model="${m.id}" data-enabled="${m.enabled?'0':'1'}">${m.enabled?'停用':'启用'}</button> <button class="small-btn danger-btn" data-delete-model="${m.id}" data-delete-model-name="${esc(m.name)}">删除模型</button></td></tr>`).join('')}</tbody></table>`:'<div class="empty-note">尚未配置模型</div>';
 host.querySelectorAll('[data-edit-model]').forEach(btn=>btn.onclick=()=>openModel(state.adminModels.find(m=>m.id===+btn.dataset.editModel)));
 host.querySelectorAll('[data-toggle-model]').forEach(btn=>btn.onclick=()=>toggleModel(btn));
 host.querySelectorAll('[data-delete-model]').forEach(btn=>btn.onclick=()=>deleteModel(+btn.dataset.deleteModel,btn.dataset.deleteModelName));
 }else if(tab==='users'){
 const rows=await api('/admin/users');host.innerHTML=`<div class="admin-user-actions"><button class="primary-btn" id="createUserBtn">＋ 新增用户</button></div><table class="data-table"><thead><tr><th>用户 ID</th><th>用户名</th><th>角色</th><th>积分余额</th><th>操作</th></tr></thead><tbody>${rows.map(u=>`<tr><td>#${u.id}</td><td>${esc(u.username)}</td><td>${u.role==='admin'?'管理员':'普通用户'}</td><td>${u.points.toLocaleString()}</td><td><button class="small-btn" data-password-id="${u.id}" data-password-name="${esc(u.username)}">修改密码</button> <button class="small-btn" data-credit-id="${u.id}" data-credit-name="${esc(u.username)}">调整积分</button> ${u.role==='admin'?'<span class="status-pill">删除受保护</span>':`<button class="small-btn danger-btn" data-delete-user="${u.id}" data-delete-name="${esc(u.username)}">删除用户</button>`}</td></tr>`).join('')}</tbody></table>`;
 host.querySelector('#createUserBtn').onclick=createUser;
 host.querySelectorAll('[data-credit-id]').forEach(btn=>btn.onclick=()=>changeCredits(+btn.dataset.creditId,btn.dataset.creditName));
 host.querySelectorAll('[data-password-id]').forEach(btn=>btn.onclick=()=>changeUserPassword(+btn.dataset.passwordId,btn.dataset.passwordName));
 host.querySelectorAll('[data-delete-user]').forEach(btn=>btn.onclick=()=>deleteUser(+btn.dataset.deleteUser,btn.dataset.deleteName));
 }else{
 const rows=await api('/admin/jobs');host.innerHTML=`<table class="data-table"><thead><tr><th>任务</th><th>用户</th><th>模型</th><th>状态</th><th>扣费</th><th>时间</th></tr></thead><tbody>${rows.map(j=>`<tr><td><code>${esc(j.id.slice(0,10))}</code></td><td>${esc(j.username)}</td><td>${esc(j.model_name)}</td><td><span class="status-pill ${esc(j.status)}">${esc(j.status)}</span>${j.error?'<br><small title="'+esc(j.error)+'">'+esc(j.error.slice(0,40))+'</small>':''}</td><td>${j.charged_points}</td><td>${esc(j.created_at.slice(0,16).replace('T',' '))}</td></tr>`).join('')}</tbody></table>`;
 }}
async function changeCredits(userId,name){const value=prompt(`请输入要为 ${name} 增加的积分（负数表示扣除）：`,'100');if(value===null)return;const amount=Number(value);if(!Number.isInteger(amount)||Math.abs(amount)>1000000){toast('请输入有效整数');return;}try{await api(`/admin/users/${userId}/credits`,{method:'POST',body:JSON.stringify({amount})});toast('积分调整成功');await renderAdminTable();refreshUser();}catch(e){toast(e.message);}}
async function toggleModel(button){
 const enabled=button.dataset.enabled==='1',label=button.textContent;
 const buttons=button.closest('tr').querySelectorAll('button');
 buttons.forEach(b=>b.disabled=true);button.textContent=enabled?'启用中…':'停用中…';
 try{
  await api(`/admin/models/${button.dataset.toggleModel}/enabled`,{method:'PATCH',body:JSON.stringify({enabled})});
  toast(enabled?'模型已启用':'模型已停用');
  state.models=await api('/models');renderModels();
  if(state.adminTab==='models')await renderAdminTable();
 }catch(e){toast(e.message);}
 finally{if(button.isConnected){buttons.forEach(b=>b.disabled=false);button.textContent=label;}}
}
async function deleteModel(id,name){if(!confirm(`确定删除模型“${name}”？`))return;try{await api(`/admin/models/${id}`,{method:'DELETE'});toast('模型已删除');state.models=await api('/models');renderModels();await renderAdminTable();}catch(e){toast(e.message);}}
async function createUser(){const username=prompt('输入新用户名（英文、数字、下划线或短横线）：');if(username===null)return;const password=prompt('输入初始密码（至少 8 位）：');if(password===null)return;const points=prompt('初始积分：','100');if(points===null)return;try{await api('/admin/users',{method:'POST',body:JSON.stringify({username,password,points:Number(points)})});toast('用户创建成功');await renderAdminTable();}catch(e){toast(e.message);}}
async function changeUserPassword(id,name){const password=prompt(`请输入 ${name} 的新密码（至少 8 位）：`);if(password===null)return;try{await api(`/admin/users/${id}/password`,{method:'PUT',body:JSON.stringify({password})});toast('密码修改成功');}catch(e){toast(e.message);}}
async function deleteUser(id,name){if(!confirm(`确定删除用户 ${name} 及其项目、任务和素材记录吗？`))return;try{await api(`/admin/users/${id}`,{method:'DELETE'});toast('用户已删除');await renderAdminTable();}catch(e){toast(e.message);}}
function openModel(model=null){$('modelOverlay').hidden=false;const form=$('modelForm');form.reset();form.dataset.editId=model?.id||'';$('modelModalTitle').textContent=model?'编辑模型接口':'创建模型接口';$('modelError').textContent='';const vals=model||{protocol:'sync_json',kind:'image',auth_mode:'bearer',auth_header:'X-API-Key',request_template:'{"model":"{{model}}","prompt":"{{prompt}}","aspect_ratio":"{{ratio}}"}',result_path:'data.0.url',task_id_path:'id',status_path:'status',completed_status:'completed,succeeded,success',failed_status:'failed,error',cost:10,enabled:true};for(const [key,value] of Object.entries(vals)){const inp=form.elements.namedItem(key);if(!inp||key==='api_key')continue;if(inp.type==='checkbox')inp.checked=!!value;else inp.value=value??'';}}
async function submitModel(event){event.preventDefault();const form=$('modelForm');const out={};new FormData(form).forEach((value,key)=>out[key]=value);out.cost=+out.cost;out.enabled=form.elements.enabled.checked;out.api_key=out.api_key||'';try{const id=form.dataset.editId;await api(id?`/admin/models/${id}`:'/admin/models',{method:id?'PUT':'POST',body:JSON.stringify(out)});$('modelOverlay').hidden=true;toast('模型配置已保存');state.models=await api('/models');renderModels();loadAdmin();}catch(e){$('modelError').textContent=e.message;}}
async function authenticate(event){event.preventDefault();const username=$('loginName').value.trim(),password=$('loginPassword').value;try{await api('/auth/'+(state.loginMode==='login'?'login':'register'),{method:'POST',body:JSON.stringify({username,password,...(state.loginMode==='register'?{invitation_code:$('invitationCode').value.trim()}: {})})});$('authError').textContent='';$('loginOverlay').hidden=true;await bootstrap();toast('欢迎进入雪宝 Studio');}catch(e){$('authError').textContent=e.message;}}
async function bootstrap(){setUser(await api('/me'));state.models=await api('/models');renderModels();await loadWorkspace();}
function updateVideoResolutions(select,modelId){
 const model=state.models.find(item=>item.kind==='video'&&String(item.id)===String(modelId));
 const options=model?.resolutions?.length?model.resolutions:['480p','720p','1080p'];
 const current=select.value;
 select.replaceChildren(...options.map(value=>new Option(value,value)));
 select.value=options.includes(current)?current:options.includes('720p')?'720p':options[0];
}
function videoDurationCaps(modelId){
 const model=state.models.find(item=>item.kind==='video'&&String(item.id)===String(modelId));
 const min=Number(model?.duration_min??1),max=Number(model?.duration_max??30);
 const options=Array.isArray(model?.duration_options)?model.duration_options.map(Number):[5,10,15,30].filter(value=>value>=min&&value<=max);
 return {min,max,options:options.length?options:[min,max]};
}
function applyVideoDurationLimits(input,modelId){
 const caps=videoDurationCaps(modelId);input.min=String(caps.min);input.max=String(caps.max);input.step='1';
 const value=input.value.trim();if(value&&Number.isFinite(Number(value)))input.value=String(Math.max(caps.min,Math.min(caps.max,Number(value))));
 return caps;
}
function mountDurationPicker(input,modelSelect){
 input.removeAttribute('list');
 const wrap=document.createElement('div');wrap.className='duration-picker';input.replaceWith(wrap);wrap.append(input);
 const toggle=document.createElement('button');toggle.type='button';toggle.className='duration-toggle';toggle.textContent='▾';toggle.title='选择常用时长';toggle.setAttribute('aria-label','选择常用时长');toggle.setAttribute('aria-expanded','false');
 const menu=document.createElement('div');menu.className='duration-menu';menu.hidden=true;menu.id='duration-options-'+newId();menu.setAttribute('role','listbox');menu.setAttribute('aria-label','常用时长');toggle.setAttribute('aria-controls',menu.id);
 const close=()=>{menu.hidden=true;toggle.setAttribute('aria-expanded','false');};
 let buttons=[];
 const refresh=()=>{const caps=applyVideoDurationLimits(input,modelSelect.value);menu.replaceChildren();buttons=caps.options.map(value=>{const b=document.createElement('button');b.type='button';b.textContent=value+' 秒';b.setAttribute('role','option');b.dataset.value=value;b.onclick=e=>{e.preventDefault();input.value=String(value);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));close();input.focus();};menu.append(b);return b;});};
 const open=()=>{refresh();menu.hidden=false;toggle.setAttribute('aria-expanded','true');buttons.forEach(b=>b.setAttribute('aria-selected',String(b.dataset.value===input.value)));menu.scrollTop=0;};
 toggle.onclick=e=>{e.preventDefault();if(menu.hidden)open();else close();};input.onclick=()=>open();input.oninput=close;
 wrap.addEventListener('focusout',()=>setTimeout(()=>{if(!wrap.contains(document.activeElement))close();},0));
 wrap.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close();input.focus();}else if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();if(menu.hidden)open();const current=buttons.indexOf(document.activeElement);const next=current<0?(e.key==='ArrowDown'?0:buttons.length-1):(current+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;buttons[next].focus();buttons[next].scrollIntoView({block:'nearest'});}});
 wrap.append(toggle,menu);modelSelect.addEventListener('change',refresh);refresh();return refresh;
}
function wireEvents(){
 refreshVideoDurationPicker=mountDurationPicker($('videoDuration'),$('videoModel'));
 $('videoModel').addEventListener('change',()=>updateVideoResolutions($('videoResolution'),$('videoModel').value));
 let canvasRefreshTimer;window.addEventListener('message',event=>{if(event.origin!==location.origin||event.source!==$('infiniteCanvasFrame')?.contentWindow||event.data?.type!=='studio-canvas-changed')return;clearTimeout(canvasRefreshTimer);canvasRefreshTimer=setTimeout(async()=>{try{state.projects=await api('/projects');renderProjects();await refreshUser();}catch(e){toast(e.message);}},500);});

 for(const type of ['image','video'])pageReferenceWidgets[type]=mountReference($(type+'Prompt'),$(type+'RefName').parentElement,()=>pageReferences[type],ref=>{pageReferences[type]=ref;},()=>$(type+'RefName').textContent=pageReferences[type]?.name||'');
document.querySelectorAll('[data-view]').forEach(btn=>btn.addEventListener('click',()=>setView(btn.dataset.view)));document.querySelectorAll('[data-add]').forEach(btn=>btn.addEventListener('click',()=>addNode(btn.dataset.add)));document.querySelectorAll('[data-kind]').forEach(btn=>btn.addEventListener('click',()=>setKind(btn.dataset.kind)));
 $('newProjectBtn').onclick=async()=>{let title=prompt('新画布名称','未命名画布');if(title===null)return;title=title.trim()||'未命名画布';try{const p=await api('/projects',{method:'POST',body:JSON.stringify({title})});state.projects=await api('/projects');await openProject(p.id);toast('新建成功');}catch(e){toast(e.message);}};
 $('renameProjectBtn').onclick=()=>{if(!state.project)return;let title=prompt('修改画布名称',state.project.title);if(title!==null&&title.trim()){state.project.title=title.trim().slice(0,80);$('projectTitle').textContent=state.project.title;state.projects.find(p=>p.id===state.project.id).title=state.project.title;renderProjects();queueSave();}};
 $('uploadShortcut').onclick=()=>{if(state.activeView==='canvas'&&$('infiniteCanvasFrame')){$('infiniteCanvasFrame').contentWindow.postMessage({type:'studio-canvas-upload'},location.origin);return;}state.uploadTarget=state.activeView==='canvas'?'canvas':state.activeView==='video'?'video':'image';if(state.uploadTarget==='image')setView('image');upload();};
 $('nodeUploadBtn').onclick=()=>{state.uploadTarget='canvas';upload();};
 $('fileInput').addEventListener('change',e=>onFile(e.target.files[0],state.uploadTarget||'canvas'));
 $('imageModel').onchange=()=>{$('imageCost').textContent='≈ '+(state.models.find(m=>m.id===+$('imageModel').value)?.cost||0)+' 积分';};$('videoModel').onchange=()=>{$('videoCost').textContent='≈ '+(state.models.find(m=>m.id===+$('videoModel').value)?.cost||0)+' 积分';refreshVideoDurationPicker();};$('imageGenerate').onclick=$('videoGenerate').onclick=generate;$('centerCanvasBtn').onclick=centerCanvas;$('fitCanvasBtn').onclick=fitCanvas;
 $('imageReference').onclick=()=>{state.uploadTarget='image';upload();};$('videoReference').onclick=()=>{state.uploadTarget='video';upload();};
 $('zoomOutBtn').onclick=()=>{state.zoom=Math.max(.4,Math.round((state.zoom-.1)*10)/10);applyTransform();};$('zoomInBtn').onclick=()=>{state.zoom=Math.min(1.8,Math.round((state.zoom+.1)*10)/10);applyTransform();};
 $('stage').addEventListener('pointerdown',evt=>{if(evt.target.closest('.node,.composer,.canvas-toolbar,.canvas-tools,.canvas-zoom,.canvas-topright'))return;state.drag={kind:'pan',startX:evt.clientX,startY:evt.clientY,panX:state.panX,panY:state.panY};$('stage').setPointerCapture(evt.pointerId);});
 window.addEventListener('pointermove',evt=>{if(!state.drag)return;const d=state.drag;if(Math.hypot(evt.clientX-d.startX,evt.clientY-d.startY)>4)d.moved=true;if(d.kind==='pan'){state.panX=d.panX+evt.clientX-d.startX;state.panY=d.panY+evt.clientY-d.startY;applyTransform();}else{const n=state.nodes.find(n=>n.id===d.id);if(!n)return;n.x=Math.round(d.x+(evt.clientX-d.startX)/state.zoom);n.y=Math.round(d.y+(evt.clientY-d.startY)/state.zoom);const el=Array.from(document.querySelectorAll('.node')).find(e=>e.dataset.id===d.id);if(el){el.style.left=n.x+'px';el.style.top=n.y+'px';}renderEdges();}});
 window.addEventListener('pointerup',()=>{if(state.drag?.kind==='node'){if(state.drag.moved){state.suppressNodeClick=true;setTimeout(()=>state.suppressNodeClick=false,0);}queueSave();}state.drag=null;});
 $('stage').addEventListener('wheel',evt=>{if(evt.target.closest('.composer'))return;if(evt.ctrlKey){evt.preventDefault();state.zoom=Math.max(.4,Math.min(1.8,state.zoom+(evt.deltaY>0?-.05:.05)));applyTransform();}}, {passive:false});
 $('mobileMenu').onclick=()=>$('sidebar').classList.add('open');$('closeSidebar').onclick=()=>$('sidebar').classList.remove('open');
 $('logoutBtn').onclick=async()=>{await api('/auth/logout',{method:'POST'});location.reload();};
 $('refreshHistory').onclick=loadHistory;$('addModelBtn').onclick=()=>openModel();$('closeModelModal').onclick=$('cancelModelModal').onclick=()=>$('modelOverlay').hidden=true;$('modelForm').onsubmit=submitModel;
 document.querySelectorAll('[data-admin-tab]').forEach(btn=>btn.onclick=()=>{state.adminTab=btn.dataset.adminTab;document.querySelectorAll('.admin-tab').forEach(t=>t.classList.toggle('active',t===btn));renderAdminTable().catch(e=>toast(e.message));});
 document.querySelectorAll('[data-login-tab]').forEach(btn=>btn.onclick=()=>{state.loginMode=btn.dataset.loginTab;$('invitationLabel').hidden=state.loginMode!=='register';$('invitationCode').required=state.loginMode==='register';$('loginPassword').autocomplete=state.loginMode==='register'?'new-password':'current-password';document.querySelectorAll('[data-login-tab]').forEach(t=>t.classList.toggle('active',t===btn));$('submitAuth').textContent=state.loginMode==='login'?'登录工作台 →':'创建账户 →';$('authError').textContent='';});$('loginForm').onsubmit=authenticate;
 window.addEventListener('keydown',ev=>{if((ev.ctrlKey||ev.metaKey)&&ev.key.toLowerCase()==='k'){ev.preventDefault();setView('image');$('imagePrompt').focus();}if(ev.key==='Escape'){state.connectedFrom=null;$('modelOverlay').hidden=true;renderNodes();}if(ev.key==='Delete'&&state.activeView==='canvas'&&state.selected&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){state.nodes=state.nodes.filter(n=>n.id!==state.selected);state.edges=state.edges.filter(e=>e.source!==state.selected&&e.target!==state.selected);state.selected=null;renderNodes();queueSave();}});
}
async function init(){wireEvents();try{await bootstrap();$('loginOverlay').hidden=true;}catch(e){$('loginOverlay').hidden=false;if(!e.message.includes('请先登录')&&!e.message.includes('登录已过期'))toast(e.message);}}
init();
})();
