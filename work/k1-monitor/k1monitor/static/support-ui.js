export function setupSupport(){
  const $=id=>document.getElementById(id);
  for(const id of ['about-open'])$(id).addEventListener('click',()=>$('about-dialog').showModal());
  $('donate-open').addEventListener('click',()=>$('donate-dialog').showModal());
  $('about-donate').addEventListener('click',()=>{$('about-dialog').close();$('donate-dialog').showModal();});
}
