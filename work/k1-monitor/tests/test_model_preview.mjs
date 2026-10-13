import {test} from 'node:test';
import assert from 'node:assert/strict';
import {modelFormat,meshBuffers,MAX_MODEL_BYTES,MAX_TRIANGLES} from '../k1monitor/static/model-preview-utils.js';
test('preview accepts STL/STEP/STP case-insensitively and rejects invalid or oversized files',()=>{
  for(const name of ['part.STL','часть.stl','part.STEP','part.stp'])assert.equal(modelFormat({name,size:10}),name.toLowerCase().endsWith('.stl')?'stl':'step');
  for(const file of [{name:'x.obj',size:10},{name:'x.stl',size:0},{name:'x.stl',size:MAX_MODEL_BYTES+1}])assert.throws(()=>modelFormat(file));
});
test('preview validates finite geometry and indices before allocating graphics resources',()=>{
  for(const positions of [[],[1,2],[0,0,Infinity,1,0,0,0,1,0]])assert.throws(()=>meshBuffers(positions));
  for(const indices of [[0,1,3],[0,1,-1],[0,1,.5],[0,1]])assert.throws(()=>meshBuffers([0,0,0,1,0,0,0,1,0],indices));
  const mesh=meshBuffers([0,0,0,1,0,0,0,1,0],[0,1,2],[.1,.2,.3]);
  assert.equal(mesh.triangles,1);assert.ok(mesh.positions instanceof Float32Array);assert.ok(mesh.indices instanceof Uint32Array);
});
