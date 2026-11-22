(() => {
  const user = document.currentScript.dataset.user;
  for (const method of ['open','deleteDatabase']) {
    const original = IDBFactory.prototype[method];
    IDBFactory.prototype[method] = function(name,...args) {return original.call(this,'studio-'+user+'-'+name,...args);};
  }
  localStorage.setItem('infinite-canvas-auth-token-v1', JSON.stringify({state:{token:'studio-'+user},version:0}));
  const key = 'infinite-canvas:ai_config_store';
  const stored = JSON.parse(localStorage.getItem(key) || '{}');
  stored.state = {...stored.state, config:{...stored.state?.config, channelMode:'remote'}};
  localStorage.setItem(key, JSON.stringify(stored));
  const notify = () => {if (parent !== window) parent.postMessage({type:'studio-canvas-changed'}, location.origin);};
  window.addEventListener('message', event => {
    if(event.origin !== location.origin || event.source !== parent)return;
    if(event.data?.type === 'studio-canvas-upload')document.querySelector('button[aria-label="上传素材"]')?.click();
  });
  const originalFetch = window.fetch;
  window.fetch = async (...args) => {
    const target = new URL(args[0] instanceof Request ? args[0].url : String(args[0]), location.href);
    if(target.origin === location.origin && target.pathname.startsWith('/api/v1/')) {
      const headers=new Headers(args[1]?.headers || (args[0] instanceof Request ? args[0].headers : undefined));
      const id=location.pathname.match(/^\/canvas\/([^/]+)$/)?.[1];
      if(id)headers.set('X-Studio-Project-ID',id);
      args[1]={...args[1],headers};
    }
    const response = await originalFetch(...args);
    const url = String(args[0] instanceof Request ? args[0].url : args[0]);
    if (response.ok && url.includes('/api/v1/')) notify();
    return response;
  };
  const send = XMLHttpRequest.prototype.send;
  const open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method,url,...args) {this.studioTarget=new URL(url,location.href);return open.call(this,method,url,...args);};
  XMLHttpRequest.prototype.send = function(...args) {
    const id=location.pathname.match(/^\/canvas\/([^/]+)$/)?.[1];
    if(id && this.studioTarget?.origin===location.origin && this.studioTarget.pathname.startsWith('/api/v1/'))this.setRequestHeader('X-Studio-Project-ID',id);
    this.addEventListener('load', () => {if(this.status >= 200 && this.status < 300)notify();});
    return send.apply(this,args);
  };
})();
