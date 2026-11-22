(() => {
  window.studioPickReference = (nodes, options={}) => new Promise(resolve => {
    const dialog = document.createElement('dialog');
    dialog.className = 'studio-reference-picker';
    dialog.innerHTML = '<header><strong>添加参考</strong><button type="button" aria-label="关闭">×</button></header><div class="reference-actions"><button type="button" data-upload>上传参考</button><input type="file" accept="image/jpeg,image/png,image/webp" hidden></div><h3>画布中的素材</h3><div class="reference-items"></div><p role="status"></p>';
    const keydown = e => {e.stopImmediatePropagation();if(e.key==='Escape'){e.preventDefault();finish(null);}};
    window.addEventListener('keydown',keydown,true);
    const finish = value => {window.removeEventListener('keydown',keydown,true);dialog.close();dialog.remove();resolve(value);};
    dialog.querySelector('header button').onclick = () => finish(null);
    dialog.addEventListener('cancel', e => {e.preventDefault();finish(null);});
    dialog.addEventListener('click', e => {if(e.target===dialog) {const b=dialog.getBoundingClientRect();if(e.clientX<b.left||e.clientX>b.right||e.clientY<b.top||e.clientY>b.bottom)finish(null);}});
    const input = dialog.querySelector('input');
    input.multiple=true;
    if(options.video) input.accept='image/jpeg,image/png,image/webp,video/mp4,video/webm,audio/mpeg,audio/wav,.mp3,.wav';
    dialog.querySelector('[data-upload]').onclick = () => input.click();
    input.onchange = () => {
      const files=Array.from(input.files);if(!files.length)return;
      if(files.some(file=>file.size>30*1024*1024)){dialog.querySelector('[role=status]').textContent='每个参考素材不能超过 30MB';input.value='';return;}
      finish({file:files[0],files});
    };
    const items = dialog.querySelector('.reference-items');
    if(!nodes.length)dialog.querySelector('[role=status]').textContent='暂无可选素材';
    for(const node of nodes){
      const button=document.createElement('button');button.type='button';
      const kind=node.type==='video'?'video':node.type==='audio'?'audio':['image','panorama'].includes(node.type)?'img':null;
      if(kind){const media=document.createElement(kind);media.src=node.metadata.content;if(kind==='img')media.alt=node.title||'参考图片';else{media.muted=true;media.preload='metadata';}button.append(media);}
      const label=document.createElement('span');label.textContent=node.title||'未命名素材';button.append(label);
      button.onclick=()=>finish({nodeId:node.id});items.append(button);
    }
    document.body.append(dialog);dialog.showModal();
  });
})();
