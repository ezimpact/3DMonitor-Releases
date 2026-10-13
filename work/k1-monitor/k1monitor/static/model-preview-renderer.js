import {t,tm,m,trMarkup,locale} from './i18n.js?v=0.101';
import * as THREE from './vendor/model-preview/three.module.js';
import {OrbitControls} from './vendor/model-preview/OrbitControls.js';

export function createModelRenderer(stage){
  const renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio||1,2));
  renderer.setClearColor(0,0);stage.append(renderer.domElement);
  renderer.domElement.setAttribute('aria-label',t('3D-модель: перетаскивание вращает, колесо приближает'));
  renderer.domElement.tabIndex=0;
  const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(35,1,0.1,10000);
  camera.up.set(0,0,1);
  const controls=new OrbitControls(camera,renderer.domElement);
  controls.enableDamping=false;
  scene.add(new THREE.HemisphereLight(0xdbeaff,0x526078,2.2));
  const light=new THREE.DirectionalLight(0xffffff,3.5);light.position.set(1,-2,3);scene.add(light);
  const rim=new THREE.DirectionalLight(0x5ba5ff,2);rim.position.set(-2,1,1);scene.add(rim);
  let group=null,grid=null,radius=1,distance=5,disposed=false,contextLost=false;
  function draw(){if(!disposed&&!contextLost)renderer.render(scene,camera);}
  function view(kind='perspective'){
    camera.up.set(0,0,1);
    if(kind==='top'){camera.up.set(0,1,0);camera.position.set(0,0,distance);}
    else if(kind==='front')camera.position.set(0,-distance,0);
    else camera.position.set(distance*.65,-distance*.65,distance*.4);
    controls.target.set(0,0,0);controls.update();draw();
  }
  function clear(){
    if(group){scene.remove(group);group.traverse(item=>{item.geometry?.dispose();if(item.material)for(const material of [].concat(item.material))material.dispose();});group=null;}
    if(grid){scene.remove(grid);grid.geometry.dispose();for(const material of [].concat(grid.material))material.dispose();grid=null;}
  }
  function resize(){
    const {width,height}=stage.getBoundingClientRect();if(!width||!height||disposed)return;
    renderer.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix();draw();
  }
  const observer=new ResizeObserver(resize);observer.observe(stage);controls.addEventListener('change',draw);
  function onLost(event){event.preventDefault();contextLost=true;stage.dispatchEvent(new CustomEvent('model-context-lost'));}
  renderer.domElement.addEventListener('webglcontextlost',onLost);
  resize();view();
  return {
    load(meshes){
      clear();group=new THREE.Group();
      let triangles=0;
      for(const mesh of meshes){
        const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.BufferAttribute(mesh.positions,3));
        if(mesh.indices)geometry.setIndex(new THREE.BufferAttribute(mesh.indices,1));
        geometry.computeVertexNormals();
        const material=new THREE.MeshStandardMaterial({color:mesh.color?new THREE.Color().setRGB(...mesh.color):0x4298ef,roughness:.48,metalness:.12,side:THREE.DoubleSide});
        group.add(new THREE.Mesh(geometry,material));triangles+=mesh.triangles;
      }
      const box=new THREE.Box3().setFromObject(group),size=box.getSize(new THREE.Vector3()),center=box.getCenter(new THREE.Vector3());
      if(![size.x,size.y,size.z,center.x,center.y,center.z].every(Number.isFinite)||Math.max(size.x,size.y,size.z)<=0){clear();throw new Error(t('Модель не содержит видимой геометрии.'));}
      group.position.sub(center);scene.add(group);radius=size.length()/2;
      const halfFov=camera.fov*Math.PI/360,aspect=Math.min(1,camera.aspect);
      distance=radius/Math.sin(Math.atan(Math.tan(halfFov)*aspect))*1.2;
      camera.near=Math.max(radius/1000,1e-7);camera.far=radius*1000;camera.updateProjectionMatrix();
      controls.minDistance=radius*.02;controls.maxDistance=radius*100;
      grid=new THREE.GridHelper(Math.max(size.x,size.y,size.z)*1.5,12,0x52647b,0x34404b);grid.rotation.x=Math.PI/2;grid.position.z=-size.z/2-radius*.01;
      for(const material of [].concat(grid.material)){material.transparent=true;material.opacity=.3;}
      scene.add(grid);view();
      return {size:[size.x,size.y,size.z],triangles,parts:meshes.length};
    },
    view,
    wireframe(value){if(group)group.traverse(item=>{if(item.material)item.material.wireframe=value;});draw();},
    dispose(){if(disposed)return;disposed=true;observer.disconnect();controls.dispose();clear();renderer.domElement.removeEventListener('webglcontextlost',onLost);renderer.dispose();renderer.forceContextLoss();renderer.domElement.remove();},
  };
}
