const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const sandbox = {structuredClone};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, 'yingliu-studio/web/canvas-copy.js'), 'utf8'), sandbox);
let counter = 0;
const clone = (nodes, live = nodes) => JSON.parse(JSON.stringify(
  sandbox.studioCopyCanvasNodes(nodes, live, {x:36, y:36}, () => String(++counter))));
const makeNode = (id, type, metadata = {}) => ({id, type, title:id,
  position:{x:10,y:20},width:200,height:120,metadata});
const photo = makeNode('photo', 'image', {content:'/api/media/test.png'});
const voice = makeNode('voice', 'audio', {content:'/api/media/test.wav'});
const video = makeNode('video', 'video', {prompt:'Animate @[node:photo] with @[node:voice]',
  model:'jimeng-video-25',channelId:'12',seconds:'12',vquality:'480p',size:'16:9',
  firstFrameNodeId:'photo',references:['/api/media/test.png'],cameraControl:{enabled:true,focalLength:35},
  klingImageNodeIds:['photo'],klingElementList:[{nodeIds:['photo','voice']}],
  klingMultiPrompt:[{textNodeId:'script',duration:'5'}]});
const script = makeNode('script', 'text', {content:'Scene text'});
const output = makeNode('output', 'video');
const live = [photo, voice, video, script, output];
const original = structuredClone(live);
const single = clone([video],live);
assert.deepEqual(single.nodes[0].metadata,video.metadata);
assert.deepEqual(single.connections.map(e=>[e.fromNodeId,e.toNodeId]),[
  ['video',single.nodes[0].id],
]);
single.nodes[0].metadata.cameraControl.focalLength=80;
assert.equal(video.metadata.cameraControl.focalLength,35);

const multiple=clone([photo,video],live);
const photoCopy=multiple.nodes[0],videoCopy=multiple.nodes[1];
assert.deepEqual(videoCopy.metadata,video.metadata);
assert.equal(videoCopy.metadata.model,video.metadata.model);
assert.equal(videoCopy.metadata.channelId,video.metadata.channelId);
assert.deepEqual(multiple.connections.map(e=>[e.fromNodeId,e.toNodeId]),[
  ['photo',photoCopy.id],['video',videoCopy.id],
]);
assert.equal(new Set(multiple.connections.map(e=>e.id)).size,2);

const deletedSource=clone([video],live.filter(n=>n.id!=='video'));
assert.equal(deletedSource.connections.length,0);
assert.deepEqual(deletedSource.nodes[0].metadata,video.metadata);
const secondCopy=clone(single.nodes,[...live,...single.nodes]);
assert.deepEqual(secondCopy.connections.map(e=>[e.fromNodeId,e.toNodeId]),[
  [single.nodes[0].id,secondCopy.nodes[0].id],
]);
const group=makeNode('group','group');
const grouped=structuredClone(photo);grouped.metadata.groupId='group';
const groupCopy=clone([group,grouped],[group,grouped]);
assert.equal(groupCopy.nodes[1].metadata.groupId,groupCopy.nodes[0].id);
assert.equal(clone([grouped],[group,grouped]).nodes[0].metadata.groupId,undefined);
assert.deepEqual(live,original);
console.log('PASS: direct source-to-copy links, repeated copies, unchanged settings, groups, independent metadata, deleted source');
