window.createJimengPanel = function({$, api, esc, state, toast, renderModels}) {
let jimengPoll = null;
let jimengSession = null;
let noticesBusy = false;
const hasPermissionIssue = a => a.generation_issue?.code === 'model_permission_required';
const hasDurationIssue = a => a.generation_issue?.code === 'video_duration_unsupported';
const hasGenerationIssue = a => hasPermissionIssue(a) || hasDurationIssue(a);
const issueAction = a => a.verification?.pending ? (a.verification.has_challenge_url ? '打开验证页面' : '生成诊断') : hasDurationIssue(a) ? '视频时长' : hasPermissionIssue(a) ? '模型权益' : '生成诊断';
const openIssue = a => a.verification?.pending ? (a.verification.has_challenge_url ? openVerification(a) : openDiagnostics(a)) : hasGenerationIssue(a) ? openPermissionIssue(a) : openDiagnostics(a);
function openPermissionIssue(account) {
  if (document.getElementById('jimengPermissionIssue')) return;
  const issue = account.generation_issue;
  const durationIssue = hasDurationIssue(account);
  const dialog = document.createElement('dialog');
  dialog.id = 'jimengPermissionIssue'; dialog.className = 'node-editor';
  dialog.style.cssText = 'width:min(620px,96vw);max-height:90vh;overflow:auto';
  dialog.innerHTML = `<h3>${esc(account.name || '账号 '+account.id)} · ${durationIssue?'视频时长':'模型权益'}</h3><p>${esc(issue.message)}</p><p>上次失败模型：${esc(issue.model)}</p>${durationIssue?'<p>请按该账号所选模型及生成模式支持的时长重新设置。会员等级和积分余额不代表所有模型都支持相同时长。</p>':'<p>请用该账号在即梦官网选择相同模型和分辨率，核对可用权益。开通对应权益或接入已具备权益的账号后，再手动生成。</p><p>登录检测和积分余额不代表模型权限已经通过。本次异常没有登录验证码。</p>'}<div class="editor-actions"><button data-close>关闭</button></div>`;
  const close = () => { dialog.close(); dialog.remove(); };
  dialog.querySelector('[data-close]').onclick = close;
  dialog.oncancel = e => { e.preventDefault(); close(); };
  document.body.append(dialog); dialog.showModal();
}
function updateMessageBadge(count) {
  const badge = document.getElementById('adminMessageBadge');
  if (!badge) return;
  badge.textContent = String(count || 0);
  badge.hidden = !count;
}
async function refreshNotices() {
  if (state.user?.role !== 'admin') { updateMessageBadge(0); return; }
  if (noticesBusy) return;
  noticesBusy = true;
  try {
    const data = await api('/admin/jimeng/messages');
    if (state.user?.role !== 'admin') return;
    updateMessageBadge(data.unread_count);
    if (state.adminTab === 'messages' && state.activeView === 'admin') await renderMessages($('adminTable'), data, false);
  } catch (_) { /* A gateway outage should not interrupt other admin work. */ }
  finally { noticesBusy = false; }
}
setInterval(() => refreshNotices(), 15000);

async function renderMessages(host, data = null, showLoading = true) {
  if (showLoading) host.innerHTML = '<div class="empty-note">正在读取后台消息...</div>';
  try {
    data = data || await api('/admin/jimeng/messages');
    updateMessageBadge(data.unread_count);
    const unread = data.items.filter(message => !message.read_at).length;
    host.innerHTML = `<div class="admin-user-actions"><span>未读 ${unread} 条</span><button class="small-btn" id="readAllMessages" ${unread ? '' : 'disabled'}>全部标记已读</button></div>${data.gateway_error?`<div class="empty-note">账号池暂时未连接，显示已保存的消息。${esc(data.gateway_error)}</div>`:''}${data.items.length?`<table class="data-table"><thead><tr><th>状态</th><th>账号</th><th>消息</th><th>发生时间</th><th>操作</th></tr></thead><tbody>${data.items.map(message=>`<tr><td><span class="status-pill ${message.read_at?'completed':'failed'}">${message.read_at?'已读':'未读'}</span></td><td>${esc(message.account_name||'账号 '+message.account_id)}</td><td><strong>${esc(message.title)}</strong><br><small style="white-space:pre-line">${esc(message.body)}</small></td><td>${esc(String(message.event_at||message.created_at).slice(0,16).replace('T',' '))}</td><td>${message.read_at?'':`<button class="small-btn" data-read-message="${message.id}">标记已读</button>`}</td></tr>`).join('')}</tbody></table>`:'<div class="empty-note">暂无即梦异常消息</div>'}`;
    host.querySelector('#readAllMessages')?.addEventListener('click', async event => {
      event.currentTarget.disabled = true;
      try { await api('/admin/jimeng/messages/read-all', {method:'POST',body:'{}'}); await renderMessages(host); }
      catch (error) { event.currentTarget.disabled = false; toast(error.message); }
    });
    host.querySelectorAll('[data-read-message]').forEach(button => button.onclick = async () => {
      button.disabled = true;
      try { await api(`/admin/jimeng/messages/${button.dataset.readMessage}/read`, {method:'POST',body:'{}'}); await renderMessages(host); }
      catch (error) { button.disabled = false; toast(error.message); }
    });
  } catch (error) { host.innerHTML = `<div class="empty-note">${esc(error.message)}</div>`; }
}

async function openDiagnostics(account) {
  if (document.getElementById('jimengDiagnostics')) return;
  const dialog = document.createElement('dialog');
  dialog.id='jimengDiagnostics';dialog.className='node-editor';
  dialog.style.cssText='width:min(680px,96vw);max-height:90vh;overflow:auto';
  dialog.innerHTML=`<h3>${esc(account.name || '账号 '+account.id)} · 生成诊断</h3><p data-summary role="status">正在读取失败记录...</p><div data-details></div><p data-check role="status"></p><section data-consent hidden style="margin:20px 0;border-top:1px solid #ddd;padding-top:12px"><h4>即梦 · 素材使用确认</h4><p data-consent-text style="line-height:1.7"></p><label style="display:flex;gap:8px;align-items:flex-start"><input type="checkbox" data-consent-agree style="width:18px;flex:0 0 18px;margin-top:3px"><span>我已阅读并确认上述内容，同意提交给该即梦账号</span></label><button class="primary-btn" data-consent-submit disabled>确认并提交至即梦</button><p data-consent-result role="status"></p></section><div class="editor-actions"><button data-check-login>重新检测</button><button data-challenge hidden>打开验证页面</button><button data-account-page>查看对应账号页面</button><button data-retry hidden>允许手动重试</button><button data-close>关闭</button></div>`;
  document.body.append(dialog);dialog.showModal();
  let info;
  const endpoint='/admin/jimeng/accounts/'+account.id+'/verification/';
  const close=()=>{dialog.close();dialog.remove();};
  const show=r=>{
    if(!dialog.isConnected)return;
    info=r;
    dialog.querySelector('[data-summary]').textContent=r.has_challenge_url
      ? '即梦接口返回了可打开的验证地址。'
      : '4010 对应生成前的素材使用确认，不是登录验证码。官网首页不会显示该确认，请在下方阅读并提交确认内容。';
    const request=r.request;
    const evidence='即梦官网的生成流程会先检查内容使用确认；仅登录账号不会完成这一步。';
    dialog.querySelector('[data-details]').innerHTML=`<p>${esc(evidence)}</p><p>错误码：${esc(r.ret ?? '未记录')} · 生成调度：${r.pending?'已暂停':'允许手动重试'}</p>${request?`<p>模型：${esc(request.model)}<br>参考素材：${Number(request.reference_count)} 张 · 时长：${esc(request.duration_s ?? '未记录')} 秒<br>任务：<span style="overflow-wrap:anywhere">${esc(request.task_id)}</span></p>`:''}`;
    const d=r.diagnostics;
    dialog.querySelector('[data-check]').textContent=d
      ? ({valid:'登录检测通过，余额接口可用；生成权限尚未验证。',invalid:'登录已失效，请更新账号登录状态。',unknown:'登录检测未完成，请稍后重试。'}[d.login]||'检测结果待确认')
      : '尚未检测登录状态。检测不提交生成任务，不扣积分。';
    if(d?.content_consent_confirmed===false)dialog.querySelector('[data-check]').textContent+=' 即梦返回：素材使用确认尚未完成。';
    if(d?.content_consent_confirmed===true)dialog.querySelector('[data-check]').textContent+=' 即梦返回：素材使用确认已完成。';
    dialog.querySelector('[data-consent]').hidden=!r.pending || !r.consent?.version;
    dialog.querySelector('[data-consent-text]').textContent=r.consent?.text||'';
    dialog.querySelector('[data-consent-agree]').checked=false;
    dialog.querySelector('[data-consent-submit]').disabled=true;
    dialog.querySelector('[data-challenge]').hidden=!r.has_challenge_url || !r.pending;
    dialog.querySelector('[data-retry]').disabled=!r.pending;
    dialog.querySelector('[data-retry]').hidden=d?.content_consent_confirmed!==true || !r.pending;
    dialog.querySelector('[data-account-page]').disabled=!r.pending;
  };
  dialog.querySelector('[data-close]').onclick=close;
  dialog.oncancel=e=>{e.preventDefault();close();};
  dialog.querySelector('[data-challenge]').onclick=()=>{close();openVerification(account);};
  dialog.querySelector('[data-account-page]').onclick=()=>{close();openVerification(account,'account_page');};
  dialog.querySelector('[data-check-login]').onclick=async e=>{
    e.target.disabled=true;dialog.querySelector('[data-check]').textContent='正在检测登录状态，不提交生成任务...';
    try{show(await api(endpoint+'diagnose',{method:'POST',body:'{}'}));}
    catch(error){if(dialog.isConnected)dialog.querySelector('[data-check]').textContent=error.message;}
    finally{e.target.disabled=false;}
  };
  dialog.querySelector('[data-consent-agree]').onchange=e=>{dialog.querySelector('[data-consent-submit]').disabled=!e.target.checked || !info?.pending;};
  dialog.querySelector('[data-consent-submit]').onclick=async e=>{
    if(!info?.pending || !dialog.querySelector('[data-consent-agree]').checked)return;
    e.target.disabled=true;
    dialog.querySelector('[data-consent-agree]').disabled=true;
    dialog.querySelector('[data-consent-result]').textContent='正在提交确认并读取即梦的实际状态...';
    try{
      const r=await api(endpoint+'consent',{method:'POST',body:JSON.stringify({event_id:info.id,consent_version:info.consent.version,confirmed:true})});
      toast(r.message);close();await refreshNotices();if(state.adminTab==='jimeng')await renderJimeng($('adminTable'));
    }catch(error){if(dialog.isConnected){dialog.querySelector('[data-consent-result]').textContent=error.message;dialog.querySelector('[data-consent-agree]').disabled=false;e.target.disabled=false;}}
  };
  dialog.querySelector('[data-retry]').onclick=async e=>{
    if(!info?.pending || !confirm('允许该账号接收手动提交的生成任务？这不会标记验证通过，也不会自动重发失败任务。接口问题未解决时仍可能失败。'))return;
    e.target.disabled=true;
    try{const r=await api(endpoint+'resume',{method:'POST',body:JSON.stringify({event_id:info.id,confirmed:true})});toast(r.message);close();await refreshNotices();if(state.adminTab==='jimeng')await renderJimeng($('adminTable'));}
    catch(error){if(dialog.isConnected){dialog.querySelector('[data-check]').textContent=error.message;e.target.disabled=false;}}
  };
  try{show(await api(endpoint+'details'));}catch(e){if(dialog.isConnected)dialog.querySelector('[data-summary]').textContent=e.message;}
}

async function openVerification(account, purpose='challenge') {
  if (document.getElementById('jimengVerification')) return;
  const dialog = document.createElement('dialog');
  dialog.id = 'jimengVerification'; dialog.className = 'node-editor';
  dialog.style.cssText = 'width:min(1320px,96vw);max-width:96vw;max-height:94vh;overflow:auto';
  dialog.innerHTML = `<h3>${esc(account.name || '账号 '+account.id)} · ${purpose==='challenge'?'验证页面':'账号页面'}</h3><p data-status role="status">正在连接页面...</p><p data-detail></p><img data-screen tabindex="0" alt="对应即梦账号页面" style="width:100%;aspect-ratio:1.6;object-fit:contain;background:#eee;touch-action:none" draggable="false"><div class="editor-actions"><input data-text aria-label="输入到即梦页面的文字" placeholder="输入文字" autocomplete="off"><button data-send>输入</button><button data-reload>刷新页面</button><button data-diagnostics>返回生成诊断</button><button data-close>关闭</button></div>`;
  document.body.append(dialog); dialog.showModal();
  const screen = dialog.querySelector('[data-screen]'), status = dialog.querySelector('[data-status]');
  screen.src = 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=';
  let session, timer, closed = false, meta = {width:1280,height:800}, chain = Promise.resolve();
  const endpoint = '/admin/jimeng/accounts/'+account.id+'/verification/';
  const close = async () => {
    closed = true; clearTimeout(timer); dialog.close(); dialog.remove();
    if (session) { try { await api(endpoint+'close',{method:'POST',body:JSON.stringify({session_id:session.session_id})}); } catch (_) {} }
    refreshNotices();
  };
  dialog.querySelector('[data-close]').onclick = close;
  dialog.oncancel = e => { e.preventDefault(); close(); };
  const send = command => {
    if (!session || closed) return;
    chain = chain.then(() => closed ? null : api(endpoint+'input',{method:'POST',body:JSON.stringify({session_id:session.session_id,command})})).catch(e => {status.textContent=e.message;});
  };
  const coords = e => {const r=screen.getBoundingClientRect();return {x:(e.clientX-r.left)*meta.width/r.width,y:(e.clientY-r.top)*meta.height/r.height};};
  let lastMove = 0;
  screen.onpointerdown = e => {e.preventDefault();screen.focus();screen.setPointerCapture(e.pointerId);send({t:'mouse',type:'mousePressed',...coords(e),button:'left',buttons:1,clickCount:1});};
  screen.onpointermove = e => {if(e.buttons && Date.now()-lastMove>60){lastMove=Date.now();send({t:'mouse',type:'mouseMoved',...coords(e),button:'left',buttons:1});}};
  screen.onpointerup = screen.onpointercancel = e => send({t:'mouse',type:'mouseReleased',...coords(e),button:'left',buttons:0,clickCount:1});
  screen.addEventListener('wheel',e=>{e.preventDefault();send({t:'wheel',...coords(e),deltaY:e.deltaY});},{passive:false});
  screen.onkeydown=e=>{if(e.key==='Escape'||e.key==='Tab')return;e.preventDefault();if(e.key.length===1)send({t:'text',text:e.key});else send({t:'key',type:'keyDown',key:e.key,code:e.code,keyCode:e.keyCode});};
  screen.onkeyup=e=>{if(e.key.length>1 && !['Escape','Tab'].includes(e.key))send({t:'key',type:'keyUp',key:e.key,code:e.code,keyCode:e.keyCode});};
  dialog.querySelector('[data-send]').onclick=()=>{const input=dialog.querySelector('[data-text]');send({t:'text',text:input.value});input.value='';};
  dialog.querySelector('[data-reload]').onclick=()=>send({t:'reload'});
  dialog.querySelector('[data-diagnostics]').onclick=async()=>{await close();openDiagnostics(account);};
  try {
    session=await api(endpoint+'start',{method:'POST',body:JSON.stringify({purpose})});
    if(closed){await api(endpoint+'close',{method:'POST',body:JSON.stringify({session_id:session.session_id})});return;}
    dialog.querySelector('[data-detail]').textContent=session.has_challenge_url ? '已打开上游返回的验证地址，请亲自完成页面中的确认。' : '这是该账号的官网页面，不是验证页面。生成是否恢复尚未确认，关闭此窗口不会解除暂停。';
    const poll=async()=>{
      if(closed)return;
      try {const r=await api(endpoint+session.session_id);if(closed)return;meta=r.meta||meta;if(r.frame)screen.src='data:image/jpeg;base64,'+r.frame;status.textContent=r.error||({starting:'正在连接即梦...',live:session.has_challenge_url?'验证页面已打开':'对应账号的官网页面已打开',closed:'会话已结束，请关闭后重新打开',error:'页面打开失败'}[r.state]||r.state);if(['closed','error'].includes(r.state))return;}
      catch(e){if(!closed)status.textContent=e.message;return;}
      timer=setTimeout(poll,600);
    };poll();
  } catch(e){status.textContent=e.message;}
}
async function renderJimeng(host) {
  clearTimeout(jimengPoll);
  host.innerHTML = '<div class="empty-note">正在连接即梦账号池...</div>';
  try {
    const data = await api('/admin/jimeng/accounts');
    if (state.adminTab !== 'jimeng') return;
    const labels = {ok:'正常',confirmation:'生成异常（已暂停）',invalid:'登录失效',cooling:'冷却中',quota:'额度不足',disabled:'已停用',unknown:'待检测'};
    host.innerHTML = `<div class="admin-user-actions" style="display:flex;gap:12px;flex-wrap:wrap">
      <button class="primary-btn" id="jimengLogin">扫码添加账号</button>
      <button class="small-btn" id="jimengImport">导入登录凭据</button>
      <button class="small-btn" id="jimengRefresh">刷新</button>
      <button class="small-btn" id="jimengModel">添加即梦视频模型</button>
      <span>账号 ${data.stats.total} · 可用 ${data.stats.ok} · 生成中 ${data.stats.inflight}</span></div>
      <form id="jimengLimits" class="admin-user-actions" style="display:flex;gap:12px;flex-wrap:wrap;align-items:center">
      <label>总并发 <input name="concurrency" type="number" min="1" max="32" value="${data.config.concurrency}" required style="width:70px"></label>
      <label>每账号并发 <input name="per_account" type="number" min="1" step="1" value="${data.config.per_account}" required style="width:70px"></label>
      <button class="small-btn">保存</button></form>
      <div id="jimengLoginArea"></div>
      <table class="data-table"><thead><tr><th>账号 / 备注</th><th>状态</th><th>即梦积分</th><th>生成中</th><th>操作</th></tr></thead><tbody>${data.items.map(a=>`<tr><td>${esc(a.name||'账号 '+a.id)}<br>${esc(a.remark||'')}<br><small>${esc(a.session_hint)}</small></td><td>${esc(labels[a.state]||a.state)}${a.cooldown_left?` (${a.cooldown_left}s)`:''}</td><td>${a.credits??'-'}</td><td>${a.inflight}</td><td><button class="small-btn" data-jm-probe="${a.id}">检测</button> <button class="small-btn" data-jm-toggle="${a.id}" data-enabled="${a.enabled?0:1}">${a.enabled?'停用':'启用'}</button> <button class="small-btn danger-btn" data-jm-delete="${a.id}">删除</button></td></tr>`).join('')||'<tr><td colspan="5">暂无账号</td></tr>'}</tbody></table>`;
    refreshNotices();
    for (const account of data.items.filter(a => a.verification?.pending)) {
      const cell = host.querySelector(`[data-jm-probe="${account.id}"]`)?.parentElement;
      if (cell) { const button = document.createElement('button');button.className='small-btn';button.textContent=issueAction(account);button.onclick=()=>openIssue(account);cell.prepend(button); }
    }
    const run = async (button, action) => {button.disabled=true;try{await action();await renderJimeng(host);}catch(e){button.disabled=false;toast(e.message);}};
    $('jimengRefresh').onclick=()=>renderJimeng(host);
    $('jimengModel').onclick=e=>run(e.target,async()=>{await api('/admin/jimeng/install-model',{method:'POST',body:'{}'});state.models=await api('/models');renderModels();toast('即梦视频模型已就绪');});
    $('jimengLimits').onsubmit=e=>{e.preventDefault();const f=e.target;run(f.querySelector('button'),()=>api('/admin/jimeng/settings',{method:'PUT',body:JSON.stringify({concurrency:+f.elements.concurrency.value,per_account:+f.elements.per_account.value})}));};
    host.querySelectorAll('[data-jm-probe]').forEach(b=>b.onclick=()=>run(b,async()=>{const r=await api(`/admin/jimeng/accounts/${b.dataset.jmProbe}/probe`,{method:'POST',body:'{}'});toast(r.ok?'登录检测通过；模型权益请查看生成记录':r.error||'检测失败');}));
    host.querySelectorAll('[data-jm-toggle]').forEach(b=>b.onclick=()=>run(b,()=>api('/admin/jimeng/accounts/'+b.dataset.jmToggle,{method:'PATCH',body:JSON.stringify({enabled:!!+b.dataset.enabled})})));
    host.querySelectorAll('[data-jm-delete]').forEach(b=>b.onclick=()=>{if(confirm('确定从号池删除这个账号？'))run(b,()=>api('/admin/jimeng/accounts/'+b.dataset.jmDelete,{method:'DELETE'}));});
    $('jimengImport').onclick=()=>{
      const area=$('jimengLoginArea');
      area.innerHTML='<form id="jimengImportForm" class="admin-user-actions"><label>备注 <input name="remark" maxlength="100"></label><label>Cookie / Session <input name="sessionid" type="password" autocomplete="off" required></label><button class="primary-btn">导入</button><button type="button" class="small-btn" id="jmCloseImport">取消</button></form>';
      $('jmCloseImport').onclick=()=>area.replaceChildren();
      $('jimengImportForm').onsubmit=e=>{e.preventDefault();const f=e.target;const payload={sessionid:f.elements.sessionid.value,remark:f.elements.remark.value,probe:true};f.elements.sessionid.value='';run(f.querySelector('button'),async()=>{const r=await api('/admin/jimeng/accounts',{method:'POST',body:JSON.stringify(payload)});toast(r.ok?'账号已导入':r.error||'已导入，检测未通过');});};
    };
    $('jimengLogin').onclick=async e=>{
      e.target.disabled=true;
      try{const session=await api('/admin/jimeng/login/qr/start',{method:'POST',body:JSON.stringify({provider:'douyin',remark:'扫码账号'})});jimengSession=session.session_id;showJimengLogin(host);}
      catch(error){e.target.disabled=false;toast(error.message);}
    };
    if(jimengSession)showJimengLogin(host);
  } catch(e) {host.innerHTML='<div class="empty-note">'+esc(e.message)+' <button class="small-btn" id="jmRetry">重试</button></div>';$('jmRetry').onclick=()=>renderJimeng(host);}
}

function showJimengLogin(host){
  clearTimeout(jimengPoll);
  const area=$('jimengLoginArea');
  area.innerHTML='<div style="padding:16px"><p id="jmLoginStatus" role="status">正在准备抖音登录二维码...</p><img id="jmQr" alt="抖音登录二维码" hidden style="width:280px;max-width:100%;height:auto;display:block"><button class="small-btn" id="jmCancel">取消登录</button></div>';
  $('jmCancel').onclick=async()=>{try{await api('/admin/jimeng/login/qr/'+jimengSession+'/cancel',{method:'POST',body:'{}'});jimengSession=null;clearTimeout(jimengPoll);await renderJimeng(host);}catch(e){toast(e.message);}};
  const poll=async()=>{
    if(state.adminTab!=='jimeng'||state.activeView!=='admin'||!jimengSession||!area.isConnected)return;
    try{
      const r=await api('/admin/jimeng/login/qr/'+jimengSession);
      if(!area.isConnected||!$('jmLoginStatus'))return;
      $('jmLoginStatus').textContent=r.error||r.message;
      if(r.qr){$('jmQr').src=r.qr;$('jmQr').hidden=false;}
      if(['success','error','expired','cancelled'].includes(r.state)){
        jimengSession=null;$('jimengLogin').disabled=false;$('jmCancel').hidden=true;
        if(r.state==='success'){toast('账号登录成功，已加入号池');await renderJimeng(host);}return;
      }
      jimengPoll=setTimeout(poll,2000);
    }catch(e){$('jmLoginStatus').textContent=e.message;jimengPoll=setTimeout(poll,5000);}
  };poll();
}
renderJimeng.renderMessages = renderMessages;
renderJimeng.refreshNotices = refreshNotices;
return renderJimeng;
};
