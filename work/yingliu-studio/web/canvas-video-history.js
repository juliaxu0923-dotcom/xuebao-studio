// Runs in the canvas bundle so history selection uses the normal node save flow.
const studioVideoSubmissions = new Set();
async function studioRunVideoGeneration(nodeId, mode, nodesRef, generate) {
  if (mode !== 'video') return generate();
  if (studioVideoSubmissions.has(nodeId) || nodesRef.current.find(node => node.id === nodeId)?.metadata?.status === 'loading') return;
  studioVideoSubmissions.add(nodeId);
  try { return await generate(); }
  finally { studioVideoSubmissions.delete(nodeId); }
}

function StudioVideoPromptPanel(props) {
  const {jsx} = e.i(843476);
  const {useState, useLayoutEffect, useRef} = e.i(271645);
  const {createPortal} = e.i(174080);
  const container = useRef(null);
  const [position, setPosition] = useState({left: 12, top: 72});
  useLayoutEffect(() => {
    if (props.node.type !== 'video') return;
    const update = () => {
      const anchor = document.querySelector(`[data-node-id="${CSS.escape(props.node.id)}"]`);
      if (!anchor || !container.current) return;
      const rect = anchor.getBoundingClientRect();
      const width = Math.min(622, window.innerWidth - 24);
      const height = container.current.getBoundingClientRect().height;
      const left = Math.max(12, Math.min(window.innerWidth - width - 12, rect.left + rect.width / 2 - width / 2));
      const top = Math.max(72, Math.min(rect.bottom + 16, window.innerHeight - height - 100));
      setPosition(current => current.left === left && current.top === top ? current : {left, top});
    };
    const observer = new ResizeObserver(update);
    observer.observe(container.current);
    update();
    window.addEventListener('resize', update);
    return () => { observer.disconnect(); window.removeEventListener('resize', update); };
  }, [props.node.id, props.node.type, props.node.position.x, props.node.position.y, props.node.width, props.node.height]);
  if (props.node.type !== 'video') return jsx(xl, props);
  return createPortal(jsx('div', {ref: container, className: 'studio-video-panel-anchor',
    style: position, onClick: event => event.stopPropagation(), children: jsx(xl, props)}), document.body);
}

function StudioVideoHistory({node, busy, onConfigChange, theme}) {
  const {jsx, jsxs} = e.i(843476);
  const {useState, useEffect, useRef} = e.i(271645);
  const {Button} = e.i(464571);
  const {Modal} = e.i(212931);
  const {App} = e.i(158962);
  const {Trash2} = e.i(573474);
  const {Download} = e.i(762368);
  const {message, modal} = App.useApp();
  const [items, setItems] = useState([]);
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [preview, setPreview] = useState(null);
  const pendingBilling = useRef(new Set());
  const canvasId = location.pathname.split('/').pop();
  const url = `/api/canvas/${encodeURIComponent(canvasId)}/nodes/${encodeURIComponent(node.id)}/videos`;

  useEffect(() => {
    const controller = new AbortController();
    let timer;
    const load = async () => {
      try {
        const response = await fetch(url, {signal: controller.signal});
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || '生成记录加载失败');
        if (!controller.signal.aborted) {
          if (parent !== window && result.some(item => item.billing_status === 'settled' && pendingBilling.current.has(item.id))) {
            parent.postMessage({type: 'studio-canvas-credits-changed'}, location.origin);
          }
          pendingBilling.current = new Set(result.filter(item => item.billing_status === 'pending').map(item => item.id));
          setItems(result); setError(''); setLoaded(true);
        }
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure.message || '生成记录加载失败');
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(load, busy ? 1500 : 5000);
      }
    };
    load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [url, node.metadata?.videoTaskId, node.metadata?.status, busy, refresh]);

  const pending = busy || items.some(item => !['completed', 'failed'].includes(item.status));
  const selected = item => item.status === 'completed' && !!item.result_url && node.metadata?.status === 'success' &&
    (node.metadata?.videoTaskId ? node.metadata.videoTaskId === item.id : node.metadata?.content === item.result_url);
  useEffect(() => {
    const current = items.find(item => item.id === node.metadata?.videoTaskId && item.status === 'completed' && item.result_url);
    if (current && node.metadata?.status === 'success' && node.metadata?.content !== current.result_url) {
      onConfigChange(node.id, {content: current.result_url, storageKey: '', studioSelectedVideoId: current.id});
    }
  }, [items, node.id, node.metadata?.videoTaskId, node.metadata?.status, node.metadata?.content, onConfigChange]);
  const select = item => {
    const options = item.options || {};
    onConfigChange(node.id, {
      content: item.result_url, storageKey: '', videoTaskId: item.id, videoTaskVideoId: undefined,
      studioSelectedVideoId: item.id, status: 'success', progress: 100,
      errorDetails: undefined, downloadError: undefined, mimeType: 'video/mp4',
      prompt: item.prompt, model: item.model_code, channelId: String(item.model_id),
      seconds: String(options.duration), vquality: options.resolution, size: options.ratio
    });
  };
  const remove = item => modal.confirm({
    title: '删除生成记录',
    content: selected(item) ? '删除当前记录后，画布将切换到其他可用视频；没有其他视频时保留空视频模块。' : '确定删除这条生成记录？',
    okText: '删除', cancelText: '取消', okButtonProps: {danger: true},
    onOk: async () => {
      try {
        const response = await fetch('/api/jobs/' + encodeURIComponent(item.id), {method: 'DELETE'});
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || '删除失败');
        const remaining = items.filter(record => record.id !== item.id);
        if (selected(item) || node.metadata?.videoTaskId === item.id) {
          const fallback = remaining.find(record => record.status === 'completed' && record.result_url);
          if (fallback) select(fallback);
          else onConfigChange(node.id, {content: '', storageKey: '', videoTaskId: undefined,
            videoTaskVideoId: undefined, studioSelectedVideoId: undefined,
            status: 'idle', progress: 0, errorDetails: undefined, downloadError: undefined});
        }
        setItems(remaining);
        setRefresh(value => value + 1);
        message.success('生成记录已删除');
      } catch (failure) { message.error(failure.message || '删除失败，请重试'); }
    }
  });
  const statusText = {completed: '已完成', failed: '生成失败', queued: '排队中', running: '生成中'};
  return jsxs('section', {
    className: 'studio-video-history', 'aria-label': '视频生成记录',
    style: {'--history-border': theme.node.stroke, '--history-fill': theme.node.fill,
      '--history-active': theme.node.activeStroke, '--history-muted': theme.node.muted},
    children: [
      jsxs('div', {className: 'studio-video-history-head', children: [
        jsx('strong', {children: `生成记录 (${items.length})`}),
        pending ? jsx('span', {role: 'status', children: '生成中...'}) : null
      ]}),
      error ? jsxs('div', {className: 'studio-video-history-error', role: 'alert', children: [
        error, jsx(Button, {size: 'small', onClick: () => setRefresh(value => value + 1), children: '重试'})
      ]}) : null,
      !items.length ? jsx('div', {className: 'studio-video-history-empty', children: loaded ? '暂无生成记录' : '正在加载记录...'}) : null,
      jsx('div', {className: 'studio-video-history-list', children: items.map(item => jsxs('article', {
        className: 'studio-video-history-item' + (selected(item) ? ' is-selected' : ''),
        'data-video-job-id': item.id,
        children: [
          jsx('button', {type: 'button', className: 'studio-video-history-preview',
            disabled: item.status !== 'completed' || !item.result_url,
            'aria-label': '预览视频', title: '预览视频', onClick: () => setPreview(item),
            children: item.status === 'completed' && item.result_url
              ? jsx('video', {src: item.result_url, preload: 'metadata', muted: true, playsInline: true})
              : jsx('span', {children: statusText[item.status] || item.status})}),
          jsx('div', {className: 'studio-video-history-prompt', title: item.error || item.prompt,
            children: item.status === 'failed' ? item.error || '生成失败' : item.prompt}),
          jsx('div', {className: 'studio-video-history-details', title: item.model_name,
            children: [
              `${item.options?.resolution || ''} · ${item.options?.duration || ''}s · ${new Date(item.created_at).toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'})}`,
              item.billing_status === 'pending' ? '积分待即梦确认' : null,
              item.billing_status === 'settled' ? `即梦实扣 ${item.charged_points} 积分` : null
            ].filter(Boolean).join(' · ')}),
          jsxs('div', {className: 'studio-video-history-actions', children: [
            jsx(Button, {size: 'small', type: selected(item) ? 'primary' : 'default',
              disabled: pending || item.status !== 'completed' || !item.result_url || selected(item),
              onClick: () => select(item), children: selected(item) ? '已选用' : '放到画布'}),
            jsx(Button, {size: 'small', danger: true, disabled: pending,
              title: '删除记录', 'aria-label': '删除记录', icon: jsx(Trash2, {size: 14}), onClick: () => remove(item)})
          ]})
        ]
      }, item.id))}),
      jsx(Modal, {title: '视频预览', open: !!preview, width: 760, footer: null, destroyOnHidden: true,
        onCancel: () => setPreview(null), children: preview ? jsxs('div', {className: 'studio-video-history-modal', children: [
          jsx('video', {src: preview.result_url, controls: true, autoPlay: false, playsInline: true}),
          jsx('p', {children: preview.prompt}),
          jsxs('div', {className: 'studio-video-history-actions', children: [
            jsx(Button, {type: 'primary', disabled: pending, onClick: () => { select(preview); setPreview(null); }, children: '放到画布'}),
            jsx(Button, {href: preview.result_url, download: `video-${preview.id}.mp4`, icon: jsx(Download, {size: 16}), children: '下载'})
          ]})
        ]}) : null})
    ]
  });
}
