// A dedicated worker per file: closing, replacing or cancelling terminates parsing.
const vendor=new URL('./vendor/model-preview/',self.location.href);
self.onmessage=async({data:{format,buffer}})=>{
  try{
    const {meshBuffers,MAX_TRIANGLES}=await import('./model-preview-utils.js');
    let meshes;
    if(format==='stl'){
      self.postMessage({phase:'parsing',message:'Чтение STL…'});
      if(buffer.byteLength>=84){
        const count=new DataView(buffer).getUint32(80,true);
        if(84+count*50===buffer.byteLength&&count>MAX_TRIANGLES)throw new Error('Модель слишком подробная: предел — 2 миллиона треугольников.');
      }
      const {STLLoader}=await import(new URL('STLLoader.js',vendor).href);
      const geometry=new STLLoader().parse(buffer);
      meshes=[meshBuffers(geometry.getAttribute('position')?.array)];
      geometry.dispose();
    }else if(format==='step'){
      self.postMessage({phase:'parsing',message:'Подготовка просмотра STEP…'});
      if(!new TextDecoder().decode(buffer.slice(0,512)).includes('ISO-10303-21'))throw new Error('Это не файл STEP, либо файл повреждён.');
      importScripts(new URL('occt-import-js.js',vendor).href);
      const occt=await occtimportjs({locateFile:name=>new URL(name,vendor).href});
      self.postMessage({phase:'parsing',message:'Преобразование STEP для просмотра…'});
      const result=occt.ReadStepFile(new Uint8Array(buffer),{linearUnit:'millimeter',linearDeflectionType:'bounding_box_ratio',linearDeflection:0.001,angularDeflection:0.5});
      if(!result.success||!result.meshes?.length)throw new Error('Не удалось прочитать геометрию STEP. Возможно, файл повреждён или использует неподдерживаемые данные.');
      if(result.meshes.length>2000)throw new Error('В сборке слишком много деталей для просмотра. Откройте отдельную деталь.');
      let count=0;
      meshes=result.meshes.map(mesh=>{
        const result=meshBuffers(mesh.attributes?.position?.array,mesh.index?.array,mesh.color);
        count+=result.triangles;
        if(count>MAX_TRIANGLES)throw new Error('Модель слишком подробная: предел — 2 миллиона треугольников.');
        return result;
      });
    }else throw new Error('Формат модели не поддерживается.');
    const transfer=meshes.flatMap(mesh=>[mesh.positions.buffer,...(mesh.indices?[mesh.indices.buffer]:[])]);
    self.postMessage({phase:'ready',meshes,format},transfer);
  }catch(error){
    self.postMessage({phase:'error',message:error instanceof Error?error.message:'Не удалось открыть модель.'});
  }
};
