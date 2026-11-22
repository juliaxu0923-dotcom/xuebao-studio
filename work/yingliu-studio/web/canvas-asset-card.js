// Injected into the bundled asset picker; uses its existing React, icons and store.
function StudioRemovableAssetCard({studioAsset: asset, studioCard: Card, ...props}) {
  const {jsx, jsxs} = e.i(843476);
  const {App} = e.i(158962);
  const {Button} = e.i(464571);
  const {Trash2} = e.i(573474);
  const {useAssetStore} = e.i(53703);
  const {modal, message} = App.useApp();

  const confirmRemove = event => {
    event.preventDefault();
    event.stopPropagation();
    modal.confirm({
      title: '移除素材',
      content: `确定从我的素材中移除“${asset.title}”？画布中已插入的内容会保留。`,
      okText: '移除',
      cancelText: '取消',
      okButtonProps: {danger: true},
      onOk: async () => {
        const store = useAssetStore.getState();
        const token = e.i(447769).useUserStore.getState().token;
        try {
          await e.i(827236).apiPost('/api/v1/user-data/assets', {
            data: {assets: store.assets.filter(item => item.id !== asset.id)}
          }, token);
          useAssetStore.getState().removeAsset(asset.id);
          message.success('素材已移除');
        } catch (error) {
          message.error(error instanceof Error ? error.message : '移除失败，请重试');
        }
      }
    });
  };

  return jsxs('div', {
    className: 'studio-asset-picker-card',
    'data-studio-asset-id': asset.id,
    children: [
      jsx(Card, props),
      jsx(Button, {
        className: 'studio-asset-remove',
        title: '移除素材',
        'aria-label': `移除素材：${asset.title}`,
        danger: true,
        icon: jsx(Trash2, {size: 16, 'aria-hidden': true}),
        onClick: confirmRemove
      })
    ]
  });
}
