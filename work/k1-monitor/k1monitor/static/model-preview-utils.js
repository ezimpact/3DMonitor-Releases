import {t,tm,m,trMarkup,locale} from './i18n.js?v=0.102.1';
export const MAX_MODEL_BYTES=100*1024*1024;
export const MAX_TRIANGLES=2_000_000;
export function modelFormat(file){
  const extension=file.name?.split('.').pop().toLowerCase();
  if(!['stl','step','stp'].includes(extension))throw new Error(t('Выберите файл STL, STEP или STP.'));
  if(!Number.isFinite(file.size)||file.size<=0)throw new Error(t('Файл пуст. Выберите другую модель.'));
  if(file.size>MAX_MODEL_BYTES)throw new Error(t('Файл больше 100 МБ. Для просмотра выберите модель меньшего размера.'));
  return extension==='stl'?'stl':'step';
}
export function meshBuffers(positions,indices=null,color=null){
  if(!positions||!positions.length||positions.length%3)throw new Error(t('В файле не найдена корректная геометрия.'));
  if(positions.length>MAX_TRIANGLES*9)throw new Error(t('Модель слишком подробная: предел — 2 миллиона треугольников.'));
  const vertices=Float32Array.from(positions);
  for(const value of vertices)if(!Number.isFinite(value))throw new Error(t('Файл содержит некорректные координаты.'));
  let faces=null;
  if(indices){
    if(!indices.length||indices.length%3||indices.length>MAX_TRIANGLES*3)throw new Error(t('Некорректная или слишком подробная геометрия модели.'));
    for(const value of indices)if(!Number.isInteger(value)||value<0||value>=vertices.length/3)throw new Error(t('В файле повреждена геометрия.'));
    faces=Uint32Array.from(indices);
  }else if(vertices.length%9)throw new Error(t('В файле повреждены треугольники.'));
  const rgb=Array.isArray(color)&&color.length===3&&color.every(value=>Number.isFinite(value)&&value>=0&&value<=1)?color:null;
  return {positions:vertices,indices:faces,color:rgb,triangles:faces?faces.length/3:vertices.length/9};
}
