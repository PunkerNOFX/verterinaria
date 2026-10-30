"""Servidor de la app de gestión veterinaria.

La pantalla (web/index.html) trae todos los datos de su veterinaria con
GET /api/datos y manda solo lo que cambió con POST /api/cambios.
"""
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import seguridad
from .db import pool, tx

ARCHIVOS = Path(os.environ.get('VET_ARCHIVOS', '/var/lib/veterinaria-pg/archivos'))
COOKIE_SEGURA = os.environ.get('VET_HTTPS') == '1'
MAX_ARCHIVO = 25 * 1024 * 1024
MIMES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/heic', 'image/heif',
         'application/pdf'}
ID_OK = re.compile(r'^[\w-]{1,64}$')
COOKIE = 'vet_sesion'

# Qué colecciones de la app existen y qué roles pueden modificarlas.
# Todos los roles pueden verlas.
TODOS = {'admin', 'veterinario', 'recepcion'}
CLINICOS = {'admin', 'veterinario'}
COLECCIONES = {
    'clientes': TODOS,
    'mascotas': TODOS,
    'turnos': TODOS,
    'mensajes': TODOS,
    'estudios': TODOS,
    'visitas': CLINICOS,
    'vacunas': CLINICOS,
    'documentos': CLINICOS,
    'precios': {'admin'},
}



@asynccontextmanager
async def ciclo(app):
    pool.open()
    yield
    pool.close()


app = FastAPI(title='Veterinaria', docs_url=None, redoc_url=None, openapi_url=None, lifespan=ciclo)
# Por usuario frena rápido; por IP es más permisivo porque toda una
# veterinaria puede salir a internet con la misma IP.
freno_usuario = seguridad.Freno(maximo=5)
freno_ip = seguridad.Freno(maximo=30)


@app.middleware('http')
async def proteger(request: Request, call_next):
    # Las cookies van con SameSite=Lax, y además todo pedido que modifica algo
    # tiene que traer este encabezado, que otro sitio no puede agregar.
    if request.method not in ('GET', 'HEAD') and request.headers.get('x-vet') != '1':
        return JSONResponse({'detail': 'Pedido no permitido'}, status_code=403)
    return await call_next(request)


class Sesion(BaseModel):
    usuario_id: int
    usuario: str
    nombre: str
    superadmin: bool
    veterinaria_id: int | None
    rol: str | None


def sesion_actual(request: Request) -> Sesion:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, 'Iniciá sesión')
    with tx() as c:
        fila = c.execute("""
            SELECT u.id AS usuario_id, u.usuario, u.nombre, u.superadmin,
                   s.veterinaria_id,
                   -- El superadministrador entra a cualquier veterinaria como admin.
                   COALESCE(m.rol, CASE WHEN u.superadmin AND v.id IS NOT NULL THEN 'admin' END) AS rol
              FROM sesiones s
              JOIN usuarios u ON u.id = s.usuario_id AND u.activo
              LEFT JOIN membresias m ON m.usuario_id = u.id AND m.veterinaria_id = s.veterinaria_id
              LEFT JOIN veterinarias v ON v.id = s.veterinaria_id
             WHERE s.token_hash = %s AND s.expira > now()
               AND (s.veterinaria_id IS NULL OR v.activa OR u.superadmin)""",
            (seguridad.hash_token(token),)).fetchone()
    if not fila:
        raise HTTPException(401, 'La sesión venció, volvé a entrar')
    return Sesion(**fila)


def con_veterinaria(s: Sesion = Depends(sesion_actual)) -> Sesion:
    if s.veterinaria_id is None or s.rol is None:
        raise HTTPException(409, 'Elegí una veterinaria')
    return s


def veterinarias_de(c, usuario_id, superadmin=False):
    return c.execute("""
        SELECT v.id, v.nombre, COALESCE(m.rol, 'admin') AS rol
          FROM veterinarias v
          LEFT JOIN membresias m ON m.veterinaria_id = v.id AND m.usuario_id = %s
         WHERE v.activa AND (m.usuario_id IS NOT NULL OR %s)
         ORDER BY v.nombre""", (usuario_id, superadmin)).fetchall()


# ---------- Login ----------

class Login(BaseModel):
    usuario: str = Field(max_length=100)
    clave: str = Field(max_length=200)


@app.post('/api/login')
def login(datos: Login, request: Request, response: Response):
    usuario = datos.usuario.strip().lower()
    ip = request.headers.get('x-real-ip') or (request.client.host if request.client else '')
    if freno_usuario.bloqueado(usuario) or freno_ip.bloqueado(ip):
        raise HTTPException(429, 'Demasiados intentos. Esperá 10 minutos y probá de nuevo.')
    with tx() as c:
        u = c.execute('SELECT id, clave_hash, superadmin FROM usuarios WHERE usuario = %s AND activo',
                      (usuario,)).fetchone()
        if not seguridad.verificar(u and u['clave_hash'], datos.clave):
            freno_usuario.fallo(usuario)
            freno_ip.fallo(ip)
            raise HTTPException(401, 'Usuario o contraseña incorrectos')
        freno_usuario.limpiar(usuario)
        if seguridad.ph.check_needs_rehash(u['clave_hash']):
            c.execute('UPDATE usuarios SET clave_hash = %s WHERE id = %s',
                      (seguridad.hashear(datos.clave), u['id']))
        vets = veterinarias_de(c, u['id'])
        token, token_hash = seguridad.nuevo_token()
        c.execute('DELETE FROM sesiones WHERE expira < now()')
        c.execute('INSERT INTO sesiones (token_hash, usuario_id, veterinaria_id, expira) VALUES (%s, %s, %s, %s)',
                  (token_hash, u['id'], vets[0]['id'] if len(vets) == 1 and not u['superadmin'] else None,
                   datetime.now(timezone.utc) + timedelta(seconds=seguridad.DURACION_SESION)))
    response.set_cookie(COOKIE, token, max_age=seguridad.DURACION_SESION, httponly=True,
                        samesite='lax', secure=COOKIE_SEGURA, path='/')
    return {'ok': True}


@app.post('/api/logout')
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with tx() as c:
            c.execute('DELETE FROM sesiones WHERE token_hash = %s', (seguridad.hash_token(token),))
    response.delete_cookie(COOKIE, path='/')
    return {'ok': True}


@app.get('/api/sesion')
def sesion(s: Sesion = Depends(sesion_actual)):
    with tx() as c:
        vets = veterinarias_de(c, s.usuario_id, s.superadmin)
        vet = profesionales = None
        if s.veterinaria_id is not None and s.rol:
            vet = c.execute('SELECT id, nombre, direccion, telefono, alias_pago FROM veterinarias WHERE id = %s',
                            (s.veterinaria_id,)).fetchone()
            profesionales = [f['nombre'] for f in c.execute("""
                SELECT u.nombre FROM membresias m JOIN usuarios u ON u.id = m.usuario_id AND u.activo
                 WHERE m.veterinaria_id = %s AND m.atiende ORDER BY u.nombre""",
                (s.veterinaria_id,))]
    return {
        'usuario': {'usuario': s.usuario, 'nombre': s.nombre, 'superadmin': s.superadmin},
        'rol': s.rol,
        'veterinaria': vet,
        'veterinarias': vets,
        'profesionales': profesionales,
        'permisos': sorted(k for k, roles in COLECCIONES.items() if s.rol in roles),
    }


class Elegir(BaseModel):
    veterinaria_id: int


@app.post('/api/elegir-veterinaria')
def elegir(datos: Elegir, request: Request, s: Sesion = Depends(sesion_actual)):
    with tx() as c:
        if not any(v['id'] == datos.veterinaria_id for v in veterinarias_de(c, s.usuario_id, s.superadmin)):
            raise HTTPException(403, 'No tenés acceso a esa veterinaria')
        c.execute('UPDATE sesiones SET veterinaria_id = %s WHERE token_hash = %s',
                  (datos.veterinaria_id, seguridad.hash_token(request.cookies[COOKIE])))
    return {'ok': True}


class CambioClave(BaseModel):
    actual: str = Field(max_length=200)
    nueva: str = Field(min_length=8, max_length=200)


@app.post('/api/cambiar-clave')
def cambiar_clave(datos: CambioClave, request: Request, s: Sesion = Depends(sesion_actual)):
    with tx() as c:
        u = c.execute('SELECT clave_hash FROM usuarios WHERE id = %s', (s.usuario_id,)).fetchone()
        if not seguridad.verificar(u['clave_hash'], datos.actual):
            raise HTTPException(400, 'La contraseña actual no es correcta')
        c.execute('UPDATE usuarios SET clave_hash = %s WHERE id = %s',
                  (seguridad.hashear(datos.nueva), s.usuario_id))
        # Cierra las otras sesiones abiertas con la contraseña anterior.
        c.execute('DELETE FROM sesiones WHERE usuario_id = %s AND token_hash <> %s',
                  (s.usuario_id, seguridad.hash_token(request.cookies[COOKIE])))
    return {'ok': True}


# ---------- Datos ----------

@app.get('/api/datos')
def datos(s: Sesion = Depends(con_veterinaria)):
    with tx(s.veterinaria_id) as c:
        return {col: [f['datos'] for f in c.execute(f'SELECT datos FROM {col} ORDER BY creado, id')]
                for col in COLECCIONES}


class Cambios(BaseModel):
    guardar: dict[str, list[dict]] = {}
    borrar: dict[str, list[str]] = {}


@app.post('/api/cambios')
def cambios(cambio: Cambios, s: Sesion = Depends(con_veterinaria)):
    for col in {*cambio.guardar, *cambio.borrar}:
        if col not in COLECCIONES:
            raise HTTPException(400, f'Colección desconocida: {col}')
        if s.rol not in COLECCIONES[col]:
            raise HTTPException(403, 'Tu usuario no tiene permiso para modificar eso')
    for regs in cambio.guardar.values():
        for r in regs:
            if not isinstance(r.get('id'), str) or not ID_OK.match(r['id']):
                raise HTTPException(400, 'Registro sin id válido')
    with tx(s.veterinaria_id) as c:
        for col, regs in cambio.guardar.items():
            for r in regs:
                c.execute(f"""
                    INSERT INTO {col} (veterinaria_id, id, datos, actualizado_por)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (veterinaria_id, id) DO UPDATE
                       SET datos = EXCLUDED.datos, actualizado = now(),
                           actualizado_por = EXCLUDED.actualizado_por""",
                    (s.veterinaria_id, r['id'], Jsonb(r), s.usuario_id))
        for col, ids in cambio.borrar.items():
            if ids:
                c.execute(f'DELETE FROM {col} WHERE id = ANY(%s)', (ids,))
    return {'ok': True}


# ---------- Archivos de estudios ----------

def ruta_archivo(veterinaria_id: int, archivo_id: str) -> Path:
    return ARCHIVOS / str(veterinaria_id) / archivo_id


@app.post('/api/archivos')
def subir(archivo: UploadFile = File(...), s: Sesion = Depends(con_veterinaria)):
    if s.rol not in COLECCIONES['estudios']:
        raise HTTPException(403, 'Tu usuario no tiene permiso para subir archivos')
    mime = archivo.content_type or ''
    if mime not in MIMES:
        raise HTTPException(400, 'Solo se aceptan imágenes y PDF')
    archivo_id = secrets.token_hex(16)
    destino = ruta_archivo(s.veterinaria_id, archivo_id)
    destino.parent.mkdir(parents=True, exist_ok=True)
    tamano = 0
    try:
        with open(destino, 'wb') as f:
            while bloque := archivo.file.read(1024 * 1024):
                tamano += len(bloque)
                if tamano > MAX_ARCHIVO:
                    raise HTTPException(413, 'El archivo pesa más de 25 MB')
                f.write(bloque)
        with tx(s.veterinaria_id) as c:
            c.execute('INSERT INTO archivos (veterinaria_id, id, nombre, mime, tamano, subido_por) VALUES (%s, %s, %s, %s, %s, %s)',
                      (s.veterinaria_id, archivo_id, (archivo.filename or 'archivo')[:200], mime, tamano, s.usuario_id))
    except BaseException:
        destino.unlink(missing_ok=True)
        raise
    return {'id': archivo_id, 'tamano': tamano}


@app.get('/api/archivos/{archivo_id}')
def bajar(archivo_id: str, s: Sesion = Depends(con_veterinaria)):
    if not re.fullmatch(r'[0-9a-f]{32}', archivo_id):
        raise HTTPException(404)
    with tx(s.veterinaria_id) as c:
        a = c.execute('SELECT nombre, mime FROM archivos WHERE id = %s', (archivo_id,)).fetchone()
    ruta = ruta_archivo(s.veterinaria_id, archivo_id)
    if not a or not ruta.exists():
        raise HTTPException(404, 'Archivo no encontrado')
    return FileResponse(ruta, media_type=a['mime'], filename=a['nombre'], content_disposition_type='inline',
                        headers={'Cache-Control': 'private, max-age=86400',
                                 'X-Content-Type-Options': 'nosniff'})


# ---------- Panel: usuarios y datos de la veterinaria (rol admin) ----------

USUARIO_OK = re.compile(r'^[a-z0-9._-]{3,40}$')


def solo_admin(s: Sesion = Depends(con_veterinaria)) -> Sesion:
    if s.rol != 'admin':
        raise HTTPException(403, 'Solo la administración de la veterinaria puede hacer esto')
    return s


def solo_superadmin(s: Sesion = Depends(sesion_actual)) -> Sesion:
    if not s.superadmin:
        raise HTTPException(403, 'Solo el administrador general puede hacer esto')
    return s


def validar_usuario(usuario: str) -> str:
    usuario = usuario.strip().lower()
    if not USUARIO_OK.match(usuario):
        raise HTTPException(400, 'El usuario tiene que tener entre 3 y 40 letras o números, sin espacios')
    return usuario


def crear_usuario(c, usuario, nombre, clave, superadmin=False):
    usuario = validar_usuario(usuario)
    if c.execute('SELECT 1 FROM usuarios WHERE usuario = %s', (usuario,)).fetchone():
        raise HTTPException(409, f'Ya existe el usuario "{usuario}". Elegí otro nombre de usuario.')
    return c.execute('INSERT INTO usuarios (usuario, nombre, clave_hash, superadmin) VALUES (%s, %s, %s, %s) RETURNING id',
                     (usuario, nombre.strip(), seguridad.hashear(clave), superadmin)).fetchone()['id']


def quedan_admins(c, veterinaria_id, sin_usuario):
    return c.execute("""SELECT 1 FROM membresias m JOIN usuarios u ON u.id = m.usuario_id AND u.activo
                         WHERE m.veterinaria_id = %s AND m.rol = 'admin' AND m.usuario_id <> %s""",
                     (veterinaria_id, sin_usuario)).fetchone() is not None


class NuevoMiembro(BaseModel):
    usuario: str = Field(max_length=40)
    nombre: str = Field(min_length=1, max_length=100)
    clave: str = Field(min_length=8, max_length=200)
    rol: str = Field(pattern='^(admin|veterinario|recepcion)$')
    atiende: bool = False


class CambioMiembro(BaseModel):
    nombre: str | None = Field(None, min_length=1, max_length=100)
    clave: str | None = Field(None, min_length=8, max_length=200)
    rol: str | None = Field(None, pattern='^(admin|veterinario|recepcion)$')
    atiende: bool | None = None
    activo: bool | None = None


@app.get('/api/equipo')
def equipo(s: Sesion = Depends(solo_admin)):
    with tx() as c:
        return c.execute("""
            SELECT u.id, u.usuario, u.nombre, u.activo, m.rol, m.atiende,
                   (SELECT count(*) FROM membresias o WHERE o.usuario_id = u.id) > 1 AS compartido
              FROM membresias m JOIN usuarios u ON u.id = m.usuario_id
             WHERE m.veterinaria_id = %s ORDER BY u.activo DESC, u.nombre""", (s.veterinaria_id,)).fetchall()


@app.post('/api/equipo')
def sumar_miembro(d: NuevoMiembro, s: Sesion = Depends(solo_admin)):
    with tx() as c:
        uid = crear_usuario(c, d.usuario, d.nombre, d.clave)
        c.execute('INSERT INTO membresias (usuario_id, veterinaria_id, rol, atiende) VALUES (%s, %s, %s, %s)',
                  (uid, s.veterinaria_id, d.rol, d.atiende))
    return {'ok': True}


@app.patch('/api/equipo/{usuario_id}')
def cambiar_miembro(usuario_id: int, d: CambioMiembro, s: Sesion = Depends(solo_admin)):
    with tx() as c:
        m = c.execute("""SELECT m.rol, (SELECT count(*) FROM membresias o WHERE o.usuario_id = m.usuario_id) > 1 AS compartido
                           FROM membresias m WHERE m.usuario_id = %s AND m.veterinaria_id = %s""",
                      (usuario_id, s.veterinaria_id)).fetchone()
        if not m:
            raise HTTPException(404, 'Ese usuario no es de esta veterinaria')
        # Nombre, contraseña y bloqueo valen para todas sus veterinarias: si
        # trabaja también en otra, solo lo puede cambiar el administrador general.
        if (d.nombre or d.clave or d.activo is not None) and m['compartido'] and not s.superadmin:
            raise HTTPException(403, 'Este usuario trabaja también en otra veterinaria. Pedile el cambio al administrador general.')
        deja_admin = (d.rol and d.rol != 'admin') or d.activo is False
        if m['rol'] == 'admin' and deja_admin and not quedan_admins(c, s.veterinaria_id, usuario_id):
            raise HTTPException(400, 'Tiene que quedar al menos una persona con rol de administración')
        if d.rol is not None or d.atiende is not None:
            c.execute('UPDATE membresias SET rol = COALESCE(%s, rol), atiende = COALESCE(%s, atiende) WHERE usuario_id = %s AND veterinaria_id = %s',
                      (d.rol, d.atiende, usuario_id, s.veterinaria_id))
        if d.nombre:
            c.execute('UPDATE usuarios SET nombre = %s WHERE id = %s', (d.nombre.strip(), usuario_id))
        if d.clave:
            c.execute('UPDATE usuarios SET clave_hash = %s WHERE id = %s', (seguridad.hashear(d.clave), usuario_id))
        if d.activo is not None:
            c.execute('UPDATE usuarios SET activo = %s WHERE id = %s', (d.activo, usuario_id))
        if d.clave or d.activo is False:
            c.execute('DELETE FROM sesiones WHERE usuario_id = %s AND usuario_id <> %s', (usuario_id, s.usuario_id))
    return {'ok': True}


@app.delete('/api/equipo/{usuario_id}')
def quitar_miembro(usuario_id: int, s: Sesion = Depends(solo_admin)):
    with tx() as c:
        m = c.execute('SELECT rol FROM membresias WHERE usuario_id = %s AND veterinaria_id = %s',
                      (usuario_id, s.veterinaria_id)).fetchone()
        if not m:
            raise HTTPException(404, 'Ese usuario no es de esta veterinaria')
        if m['rol'] == 'admin' and not quedan_admins(c, s.veterinaria_id, usuario_id):
            raise HTTPException(400, 'Tiene que quedar al menos una persona con rol de administración')
        c.execute('DELETE FROM membresias WHERE usuario_id = %s AND veterinaria_id = %s', (usuario_id, s.veterinaria_id))
        c.execute('DELETE FROM sesiones WHERE usuario_id = %s AND veterinaria_id = %s', (usuario_id, s.veterinaria_id))
    return {'ok': True}


class DatosVeterinaria(BaseModel):
    nombre: str | None = Field(None, min_length=1, max_length=120)
    direccion: str | None = Field(None, max_length=200)
    telefono: str | None = Field(None, max_length=60)
    alias_pago: str | None = Field(None, max_length=100)


def actualizar_veterinaria(c, veterinaria_id, campos: dict):
    campos = {k: (v.strip() if isinstance(v, str) else v) for k, v in campos.items() if v is not None}
    if campos:
        sets = ', '.join(f'{k} = %s' for k in campos)  # claves fijas del modelo
        c.execute(f'UPDATE veterinarias SET {sets} WHERE id = %s', (*campos.values(), veterinaria_id))


@app.patch('/api/veterinaria')
def editar_mi_veterinaria(d: DatosVeterinaria, s: Sesion = Depends(solo_admin)):
    with tx() as c:
        actualizar_veterinaria(c, s.veterinaria_id, d.model_dump())
    return {'ok': True}


# ---------- Panel general (superadministrador) ----------

class NuevaVeterinaria(BaseModel):
    nombre: str = Field(min_length=1, max_length=120)
    direccion: str = Field('', max_length=200)
    telefono: str = Field('', max_length=60)
    alias_pago: str = Field('', max_length=100)


class CambioVeterinaria(DatosVeterinaria):
    activa: bool | None = None


class NuevoUsuario(BaseModel):
    usuario: str = Field(max_length=40)
    nombre: str = Field(min_length=1, max_length=100)
    clave: str = Field(min_length=8, max_length=200)
    superadmin: bool = False


class CambioUsuario(BaseModel):
    nombre: str | None = Field(None, min_length=1, max_length=100)
    clave: str | None = Field(None, min_length=8, max_length=200)
    activo: bool | None = None
    superadmin: bool | None = None


class Membresia(BaseModel):
    rol: str = Field(pattern='^(admin|veterinario|recepcion)$')
    atiende: bool = False


@app.get('/api/super/resumen')
def resumen(s: Sesion = Depends(solo_superadmin)):
    with tx() as c:
        vets = c.execute("""
            SELECT v.id, v.nombre, v.direccion, v.telefono, v.alias_pago, v.activa, v.creada,
                   (SELECT count(*) FROM membresias m WHERE m.veterinaria_id = v.id) AS usuarios
              FROM veterinarias v ORDER BY v.nombre""").fetchall()
        usuarios = c.execute("""
            SELECT u.id, u.usuario, u.nombre, u.activo, u.superadmin,
                   COALESCE(json_agg(json_build_object('veterinaria_id', m.veterinaria_id, 'rol', m.rol, 'atiende', m.atiende))
                            FILTER (WHERE m.usuario_id IS NOT NULL), '[]') AS membresias
              FROM usuarios u LEFT JOIN membresias m ON m.usuario_id = u.id
             GROUP BY u.id ORDER BY u.nombre""").fetchall()
    return {'veterinarias': vets, 'usuarios': usuarios}


@app.post('/api/super/veterinarias')
def super_nueva_veterinaria(d: NuevaVeterinaria, s: Sesion = Depends(solo_superadmin)):
    with tx() as c:
        vid = c.execute('INSERT INTO veterinarias (nombre, direccion, telefono, alias_pago) VALUES (%s, %s, %s, %s) RETURNING id',
                        (d.nombre.strip(), d.direccion.strip(), d.telefono.strip(), d.alias_pago.strip())).fetchone()['id']
    return {'id': vid}


@app.patch('/api/super/veterinarias/{veterinaria_id}')
def super_editar_veterinaria(veterinaria_id: int, d: CambioVeterinaria, s: Sesion = Depends(solo_superadmin)):
    with tx() as c:
        if not c.execute('SELECT 1 FROM veterinarias WHERE id = %s', (veterinaria_id,)).fetchone():
            raise HTTPException(404, 'No existe esa veterinaria')
        actualizar_veterinaria(c, veterinaria_id, d.model_dump())
    return {'ok': True}


@app.post('/api/super/usuarios')
def super_nuevo_usuario(d: NuevoUsuario, s: Sesion = Depends(solo_superadmin)):
    with tx() as c:
        return {'id': crear_usuario(c, d.usuario, d.nombre, d.clave, d.superadmin)}


@app.patch('/api/super/usuarios/{usuario_id}')
def super_editar_usuario(usuario_id: int, d: CambioUsuario, s: Sesion = Depends(solo_superadmin)):
    if usuario_id == s.usuario_id and (d.activo is False or d.superadmin is False):
        raise HTTPException(400, 'No podés bloquearte ni quitarte el acceso general a vos mismo')
    with tx() as c:
        if not c.execute('SELECT 1 FROM usuarios WHERE id = %s', (usuario_id,)).fetchone():
            raise HTTPException(404, 'No existe ese usuario')
        if d.nombre:
            c.execute('UPDATE usuarios SET nombre = %s WHERE id = %s', (d.nombre.strip(), usuario_id))
        if d.clave:
            c.execute('UPDATE usuarios SET clave_hash = %s WHERE id = %s', (seguridad.hashear(d.clave), usuario_id))
        if d.activo is not None:
            c.execute('UPDATE usuarios SET activo = %s WHERE id = %s', (d.activo, usuario_id))
        if d.superadmin is not None:
            c.execute('UPDATE usuarios SET superadmin = %s WHERE id = %s', (d.superadmin, usuario_id))
        if d.clave or d.activo is False or d.superadmin is False:
            c.execute('DELETE FROM sesiones WHERE usuario_id = %s', (usuario_id,))
    return {'ok': True}


@app.put('/api/super/usuarios/{usuario_id}/veterinarias/{veterinaria_id}')
def super_asignar(usuario_id: int, veterinaria_id: int, d: Membresia, s: Sesion = Depends(solo_superadmin)):
    with tx() as c:
        if not c.execute('SELECT 1 FROM usuarios WHERE id = %s', (usuario_id,)).fetchone() or \
           not c.execute('SELECT 1 FROM veterinarias WHERE id = %s', (veterinaria_id,)).fetchone():
            raise HTTPException(404, 'No existe ese usuario o esa veterinaria')
        c.execute("""INSERT INTO membresias (usuario_id, veterinaria_id, rol, atiende) VALUES (%s, %s, %s, %s)
                     ON CONFLICT (usuario_id, veterinaria_id) DO UPDATE SET rol = EXCLUDED.rol, atiende = EXCLUDED.atiende""",
                  (usuario_id, veterinaria_id, d.rol, d.atiende))
    return {'ok': True}


@app.delete('/api/super/usuarios/{usuario_id}/veterinarias/{veterinaria_id}')
def super_quitar(usuario_id: int, veterinaria_id: int, s: Sesion = Depends(solo_superadmin)):
    with tx() as c:
        c.execute('DELETE FROM membresias WHERE usuario_id = %s AND veterinaria_id = %s', (usuario_id, veterinaria_id))
        c.execute('DELETE FROM sesiones WHERE usuario_id = %s AND veterinaria_id = %s', (usuario_id, veterinaria_id))
    return {'ok': True}


@app.get('/api/salud')
def salud():
    with tx() as c:
        c.execute('SELECT 1')
    return {'ok': True}


# Para probar sin nginx: VET_WEB=web sirve también la pantalla.
if os.environ.get('VET_WEB'):
    from fastapi.staticfiles import StaticFiles
    app.mount('/', StaticFiles(directory=os.environ['VET_WEB'], html=True))
