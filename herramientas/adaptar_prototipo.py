"""Convierte la pantalla del prototipo en la de la app con servidor.

El prototipo se sigue mejorando por separado (un solo HTML que guarda todo
junto). Este script toma ese HTML y le cambia solo las partes que hablan con
el servidor, así cada mejora del prototipo se pasa a la app corriendo:

    python3 herramientas/adaptar_prototipo.py prototipo.html web/index.html

Si el prototipo cambió justo en una de estas partes, el script avisa cuál
no encontró en vez de generar algo roto.
"""
import re
import sys

# Guardado: en vez de mandar todo el JSON, se manda solo lo que cambió.
ALMACEN = r"""// Datos en el servidor (PostgreSQL). Se traen todos los de la veterinaria
// y se mandan solo los registros que cambiaron.
const COLS=['clientes','mascotas','turnos','visitas','vacunas','estudios','documentos','precios','mensajes'];
const ROLES={admin:'Administración',veterinario:'Veterinario/a',recepcion:'Recepción'};
let db=Object.fromEntries(COLS.map(c=>[c,[]])),sesion=null,base={},ultimo='',pushTimer=null,enviando=null;
async function api(url,opt={}){
  const r=await fetch(url,{cache:'no-store',credentials:'same-origin',...opt,headers:{'X-Vet':'1',...(opt.headers||{})}});
  if(r.status===401||r.status===409){location.href='login.html';throw new Error('sin sesión')}
  return r;
}
const porId=c=>new Map((db[c]||[]).map(r=>[r.id,JSON.stringify(r)]));
const instantanea=()=>Object.fromEntries(COLS.map(c=>[c,porId(c)]));
function cambiosPendientes(){
  const guardar={},borrar={};let hay=false;
  for(const c of COLS){
    const ahora=porId(c),antes=base[c]||new Map();
    const g=[...ahora].filter(([id,j])=>antes.get(id)!==j).map(([,j])=>JSON.parse(j));
    const b=[...antes.keys()].filter(id=>!ahora.has(id));
    if(g.length){guardar[c]=g;hay=true}
    if(b.length){borrar[c]=b;hay=true}
  }
  return hay?{guardar,borrar}:null;
}
function save(){clearTimeout(pushTimer);pushTimer=setTimeout(push,300)}
async function push(){
  pushTimer=null;
  if(enviando){save();return}
  const p=cambiosPendientes();if(!p) return;
  const snap=instantanea();
  enviando=(async()=>{
    try{
      const r=await api('api/cambios',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
      if(r.ok) base=snap;
      else if(r.status===403){toast((await r.json().catch(()=>({}))).detail||'Tu usuario no tiene permiso para eso');enviando=null;await pull(true);render()}
      else{toast('No se pudo guardar en el servidor, reintentando…');setTimeout(save,5000)}
    }catch(e){if(e.message!=='sin sesión'){toast('Sin conexión con el servidor, reintentando…');setTimeout(save,5000)}}
    finally{enviando=null}
  })();
}
const ocupado=()=>pushTimer||enviando||cambiosPendientes();
async function pull(forzar){
  if(!forzar&&ocupado()) return false;
  const r=await api('api/datos');if(!r.ok) return false;
  const txt=await r.text();
  if(!forzar&&(txt===ultimo||ocupado())) return false;
  const d=JSON.parse(txt);
  for(const c of COLS) if(!Array.isArray(d[c])) d[c]=[];
  db=d;ultimo=txt;seedCobros();base=instantanea();
  return true;
}
async function refresh(){
  if(!document.getElementById('overlay').hidden||state.q) return;
  try{if(await pull()) render()}catch(e){}
}
addEventListener('pagehide',()=>{
  const p=cambiosPendientes();
  if(p) fetch('api/cambios',{method:'POST',keepalive:true,credentials:'same-origin',headers:{'X-Vet':'1','Content-Type':'application/json'},body:JSON.stringify(p)});
});
async function iniciar(){
  let r;
  try{r=await api('api/sesion')}catch(e){if(e.message!=='sin sesión') toast('No se pudo conectar con el servidor');return false}
  if(!r.ok){toast('No se pudo conectar con el servidor');return false}
  sesion=await r.json();
  const v=sesion.veterinaria;
  if(!v){location.href='login.html';return false}
  CLINICA={nombre:v.nombre,dir:v.direccion,tel:v.telefono};ALIAS=v.alias_pago||'';
  PROFS=sesion.profesionales.length?sesion.profesionales:[sesion.usuario.nombre];
  document.title=v.nombre+' · Gestión veterinaria';
  document.getElementById('brand-nombre').textContent=v.nombre;
  document.getElementById('quien').textContent=sesion.usuario.nombre+' · '+ROLES[sesion.rol];
  document.body.dataset.rol=sesion.rol;
  return await pull(true);
}
"""

# Archivos de estudios: se suben al servidor en vez de quedar en el navegador.
ARCHIVOS = r"""/* Estudios: los archivos se guardan en el servidor */
const files={
  async put(f){
    const fd=new FormData();fd.append('archivo',f);
    const r=await api('api/archivos',{method:'POST',body:fd});
    if(!r.ok) throw new Error((await r.json().catch(()=>({}))).detail||`No se pudo subir "${f.name}".`);
    return (await r.json()).id;
  }
};
const fileUrl=k=>'api/archivos/'+encodeURIComponent(k);
function hydrateThumbs(root){
  root.querySelectorAll('img[data-file]').forEach(img=>{
    img.onerror=()=>img.replaceWith(Object.assign(document.createElement('span'),{className:'fic',textContent:'Archivo no disponible'}));
    img.src=fileUrl(img.dataset.file);
  });
}
"""

CUENTA = r"""function formCuenta(){
  const otras=sesion.veterinarias.filter(v=>v.id!==sesion.veterinaria.id);
  openModal(`<h2>Mi cuenta</h2><form class="form" novalidate>
    <p class="full">${esc(sesion.usuario.nombre)} · ${esc(ROLES[sesion.rol])} en <b>${esc(CLINICA.nombre)}</b><br><span class="hint">Usuario: ${esc(sesion.usuario.usuario)}</span></p>
    ${otras.length?`<div class="full">${otras.map(v=>`<button type="button" class="btn small" data-elegir-vet="${v.id}">Pasar a ${esc(v.nombre)}</button>`).join(' ')}</div>`:''}
    <p class="full"><b>Cambiar contraseña</b></p>
    ${field('cu-a','Contraseña actual','<input id="cu-a" type="password" name="actual" autocomplete="current-password">')}
    ${field('cu-n','Nueva (mínimo 8 caracteres)','<input id="cu-n" type="password" name="nueva" autocomplete="new-password">')}
    <div class="err full" role="alert"></div>
    <div class="mact full"><button type="button" class="btn" data-salir>Salir</button><button type="button" class="btn" data-close>Cerrar</button><button class="btn primary">Cambiar contraseña</button></div></form>`,
  async fd=>{
    if(String(fd.get('nueva')).length<8) return 'La nueva contraseña tiene que tener al menos 8 caracteres.';
    const r=await api('api/cambiar-clave',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({actual:fd.get('actual'),nueva:fd.get('nueva')})});
    if(!r.ok) return (await r.json().catch(()=>({}))).detail||'No se pudo cambiar la contraseña.';
    toast('Contraseña cambiada');
  },f=>{
    f.querySelector('[data-salir]').addEventListener('click',async()=>{await push();await api('api/logout',{method:'POST'});location.href='login.html'});
    f.querySelectorAll('[data-elegir-vet]').forEach(b=>b.addEventListener('click',async()=>{
      await push();
      await api('api/elegir-veterinaria',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({veterinaria_id:Number(b.dataset.elegirVet)})});
      location.reload();
    }));
  });
}
document.addEventListener('click',e=>{if(e.target.closest('#cuenta,[data-cuenta]')) formCuenta()});
"""

INICIO = r"""(async()=>{
  if(!await iniciar()) return;
  addEventListener('hashchange',route);
  route();
"""

REEMPLAZOS = [
    ('título', '<title>Prototipo Patitas</title>', '<title>Gestión veterinaria</title>'),
    ('marca', '<div class="brand">Patitas<small>', '<div class="brand"><span id="brand-nombre">Veterinaria</span><small>'),
    ('pie del menú',
     '<span>Prototipo para revisar. Los datos son de ejemplo.</span>\n      <button class="btn small" id="reset">Restaurar ejemplos</button>',
     '<span id="quien"></span>\n      <button class="btn small" id="cuenta">Mi cuenta</button>'),
    ('estilos de la barra de cuenta en celular', '.tabs{display:none}',
     '.tabs{display:none}\n.cuentabar{display:none}\n'
     '@media (max-width:760px){.cuentabar{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:12px}}'),
    ('cartel de prototipo',
     """'<div class="banner">Prototipo para probar. Los datos son inventados y los cambios quedan solo en este navegador.</div>'+""",
     """`<div class="cuentabar"><b>${esc(CLINICA.nombre)}</b><button class="btn small" data-cuenta>Mi cuenta</button></div>`+"""),
    ('profesionales', "const PROFS=['Dra. Paula Ríos','Dr. Martín Sosa'];", 'let PROFS=[];'),
    ('alias', "const ALIAS='patitas.vet.mp';", "let ALIAS='';"),
    ('datos de la clínica',
     "const CLINICA={nombre:'Veterinaria Patitas',dir:'Av. Ejemplo 123 · Buenos Aires',tel:'11 0000-0000'};",
     "let CLINICA={nombre:'',dir:'',tel:''};"),
    ('pie de los PDF', "'Documento generado con el prototipo Patitas. Datos de ejemplo.'",
     "'Documento generado por '+CLINICA.nombre+'.'"),
    ('subida de archivos',
     'for(const f of fs){const id=uid();await files.put(id,f);archivos.push(',
     'for(const f of fs){let id;try{id=await files.put(f)}catch(e){return e.message}archivos.push('),
    ('vista previa de PDF',
     '<div class="empty">Vista previa de PDF disponible en la versión real.</div>',
     '<iframe src="${fileUrl(f.id)}" title="${esc(f.nombre)}" style="width:100%;height:65vh;border:0"></iframe>'),
    ('enlace para abrir el archivo',
     '<p class="hint full">${esc(f.nombre)} · ${sizeTxt(f.size)}</p>',
     '<p class="hint full">${esc(f.nombre)} · ${sizeTxt(f.size)} · <a href="${fileUrl(f.id)}" target="_blank" rel="noopener" style="color:var(--accent);font-weight:600">Abrir o descargar</a></p>'),
    ('botón de restaurar ejemplos',
     "document.getElementById('reset').addEventListener('click',()=>{db=sampleData();seedCobros();save();state.detail=null;state.day=TODAY;render()});\n",
     CUENTA),
    ('arranque', '(async()=>{\n  await pull();\n  seedCobros();save();\n  addEventListener(\'hashchange\',route);\n  route();\n', INICIO),
]

# Partes que se reemplazan enteras, de una marca de inicio a una de fin.
BLOQUES = [
    ('guardado en el servidor', r"const KEY='patitas-proto-v1';\n.*?\n  if\(await pull\(\)\)\{seedCobros\(\);render\(\)\}\n\}\n", ALMACEN),
    ('archivos de estudios', r"/\* Estudios: los archivos se guardan en IndexedDB de este navegador \*/\n.*?\n(?=const sizeTxt=)", ARCHIVOS),
    # Con datos reales no se inventan cobros: solo se completa la lista vacía.
    ('cobros de ejemplo', r"function seedCobros\(\)\{\n.*?\n\}\n(?=const totalDe=)",
     'function seedCobros(){\n  for(const t of db.turnos) if(!t.items) t.items=[];\n}\n'),
]


def adaptar(html: str) -> str:
    faltan = []
    for nombre, viejo, nuevo in REEMPLAZOS:
        if html.count(viejo) != 1:
            faltan.append(nombre)
        html = html.replace(viejo, nuevo)
    for nombre, patron, nuevo in BLOQUES:
        html, n = re.subn(patron, lambda _: nuevo, html, count=1, flags=re.S)
        if n != 1:
            faltan.append(nombre)
    # Textos que firmaban como la veterinaria de ejemplo.
    html = re.sub(r'\\nVeterinaria Patitas(?=`|\\n)', lambda _: '\\n${CLINICA.nombre}', html)
    if 'Patitas' in html.replace('patitas-archivos', ''):
        faltan.append('textos con "Patitas"')
    if faltan:
        sys.exit('No encontré estas partes del prototipo (cambiaron): ' + ', '.join(faltan))
    return html


if __name__ == '__main__':
    origen, destino = sys.argv[1:3]
    with open(origen, encoding='utf-8') as f:
        html = f.read()
    with open(destino, 'w', encoding='utf-8') as f:
        f.write(adaptar(html))
    print(f'Listo: {destino}')
