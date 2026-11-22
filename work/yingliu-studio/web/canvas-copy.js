// Each copy takes its source node as its direct input, without inheriting edges.
function studioCopyCanvasNodes(sources, liveNodes, offset, makeId) {
  const idMap = new Map(sources.map(node => [node.id, `${node.type}-${makeId()}`]));
  const liveIds = new Set(liveNodes.map(node => node.id));
  const nodes = sources.map(source => {
    const node = structuredClone(source);
    node.id = idMap.get(source.id);
    node.title = source.title.endsWith(' Copy') ? source.title : `${source.title} Copy`;
    node.position = {x: source.position.x + offset.x, y: source.position.y + offset.y};
    if (node.metadata?.groupId) {
      const groupId = idMap.get(node.metadata.groupId);
      if (groupId) node.metadata.groupId = groupId;
      else delete node.metadata.groupId;
    }
    return node;
  });
  const copiedConnections = sources.flatMap(source => {
    if (!liveIds.has(source.id)) return [];
    return [{id: `conn-${makeId()}`, fromNodeId: source.id, toNodeId: idMap.get(source.id)}];
  });
  return {nodes, connections: copiedConnections};
}
