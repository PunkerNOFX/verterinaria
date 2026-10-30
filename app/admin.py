"""Tareas de administración desde la terminal del server.

Se corre como el usuario postgres, por ejemplo:
    sudo veterinaria-admin usuarios
(el instalador crea ese atajo). Ver "--help" para la lista de comandos.
"""
import argparse
import getpass
import json
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .main import COLECCIONES
from .seguridad import hashear

DSN = os.environ.get('VET_ADMIN_DSN', 'dbname=veterinaria')
ROL_APP = os.environ.get('VET_ROL_APP', 'veterinaria')
MIGRACIONES = Path(__file__).parent / 'migraciones'
ROLES = ('admin', 'veterinario', 'recepcion')


def conectar():
    return psycopg.connect(DSN, row_factory=dict_row)


def pedir(texto, defecto=''):
    r = input(f'{texto}{f" [{defecto}]" if defecto else ""}: ').strip()
    return r or defecto


def pedir_clave():
    while True:
        clave = getpass.getpass('Contraseña (mínimo 8 caracteres): ')
        if len(clave) < 8:
            print('Muy corta, probá de nuevo.')
        elif clave != getpass.getpass('Repetila: '):
            print('No coinciden, probá de nuevo.')
        else:
            return clave


def migrar(_):
    with conectar() as c:
        c.execute('CREATE TABLE IF NOT EXISTS esquema_version (nombre text PRIMARY KEY, aplicada timestamptz NOT NULL DEFAULT now())')
        hechas = {f['nombre'] for f in c.execute('SELECT nombre FROM esquema_version')}
        for archivo in sorted(MIGRACIONES.glob('*.sql')):
            if archivo.name not in hechas:
                print(f'Aplicando {archivo.name}')
                c.execute(archivo.read_text())
                c.execute('INSERT INTO esquema_version (nombre) VALUES (%s)', (archivo.name,))
        rol = sql.Identifier(ROL_APP)
        c.execute(sql.SQL('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}').format(rol))
        c.execute(sql.SQL('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}').format(rol))
    print('Base de datos al día.')


def crear_veterinaria(c, nombre, direccion='', telefono='', alias=''):
    return c.execute('INSERT INTO veterinarias (nombre, direccion, telefono, alias_pago) VALUES (%s, %s, %s, %s) RETURNING id',
                     (nombre, direccion, telefono, alias)).fetchone()['id']


def crear_usuario(c, usuario, nombre, clave, superadmin=False):
    return c.execute('INSERT INTO usuarios (usuario, nombre, clave_hash, superadmin) VALUES (%s, %s, %s, %s) RETURNING id',
                     (usuario.strip().lower(), nombre, hashear(clave), superadmin)).fetchone()['id']


def asignar(c, usuario_id, veterinaria_id, rol, atiende):
    c.execute("""INSERT INTO membresias (usuario_id, veterinaria_id, rol, atiende) VALUES (%s, %s, %s, %s)
                 ON CONFLICT (usuario_id, veterinaria_id) DO UPDATE SET rol = EXCLUDED.rol, atiende = EXCLUDED.atiende""",
              (usuario_id, veterinaria_id, rol, atiende))


def id_usuario(c, usuario):
    f = c.execute('SELECT id FROM usuarios WHERE usuario = %s', (usuario.strip().lower(),)).fetchone()
    if not f:
        sys.exit(f'No existe el usuario "{usuario}".')
    return f['id']


def nueva_veterinaria(a):
    with conectar() as c:
        vid = crear_veterinaria(c, a.nombre, a.direccion, a.telefono, a.alias)
    print(f'Veterinaria "{a.nombre}" creada con número {vid}.')


def editar_veterinaria(a):
    campos = {k: v for k, v in (('nombre', a.nombre), ('direccion', a.direccion),
                                ('telefono', a.telefono), ('alias_pago', a.alias)) if v is not None}
    if a.activa is not None:
        campos['activa'] = a.activa == 'si'
    if not campos:
        sys.exit('No indicaste nada para cambiar.')
    with conectar() as c:
        sets = sql.SQL(', ').join(sql.SQL('{} = %s').format(sql.Identifier(k)) for k in campos)
        n = c.execute(sql.SQL('UPDATE veterinarias SET {} WHERE id = %s').format(sets),
                      (*campos.values(), a.id)).rowcount
    print('Listo.' if n else f'No existe la veterinaria {a.id}.')


def veterinarias(_):
    with conectar() as c:
        for v in c.execute('SELECT id, nombre, telefono, activa FROM veterinarias ORDER BY id'):
            print(f"{v['id']:>4}  {v['nombre']}{'' if v['activa'] else '  (desactivada)'}")


def nuevo_usuario(a):
    with conectar() as c:
        uid = crear_usuario(c, a.usuario, a.nombre, pedir_clave(), a.superadmin)
        if a.veterinaria:
            asignar(c, uid, a.veterinaria, a.rol, a.atiende)
    print(f'Usuario "{a.usuario.lower()}" creado.')


def asignar_cmd(a):
    with conectar() as c:
        asignar(c, id_usuario(c, a.usuario), a.veterinaria, a.rol, a.atiende)
    print('Listo.')


def quitar(a):
    with conectar() as c:
        c.execute('DELETE FROM membresias WHERE usuario_id = %s AND veterinaria_id = %s',
                  (id_usuario(c, a.usuario), a.veterinaria))
    print('Listo.')


def usuarios(_):
    with conectar() as c:
        filas = c.execute("""
            SELECT u.usuario, u.nombre, u.activo, u.superadmin, v.id AS vid, v.nombre AS vet, m.rol, m.atiende
              FROM usuarios u
              LEFT JOIN membresias m ON m.usuario_id = u.id
              LEFT JOIN veterinarias v ON v.id = m.veterinaria_id
             ORDER BY u.usuario, v.id""").fetchall()
    for f in filas:
        extra = ' superadmin' if f['superadmin'] else ''
        extra += '' if f['activo'] else ' (desactivado)'
        donde = f"{f['vid']} {f['vet']} · {f['rol']}{' · atiende' if f['atiende'] else ''}" if f['vid'] else 'sin veterinaria'
        print(f"{f['usuario']:<16} {f['nombre']:<24} {donde}{extra}")


def clave(a):
    with conectar() as c:
        uid = id_usuario(c, a.usuario)
        c.execute('UPDATE usuarios SET clave_hash = %s WHERE id = %s', (hashear(pedir_clave()), uid))
        c.execute('DELETE FROM sesiones WHERE usuario_id = %s', (uid,))
    print('Contraseña cambiada.')


def activar(a, activo):
    with conectar() as c:
        uid = id_usuario(c, a.usuario)
        c.execute('UPDATE usuarios SET activo = %s WHERE id = %s', (activo, uid))
        if not activo:
            c.execute('DELETE FROM sesiones WHERE usuario_id = %s', (uid,))
    print('Listo.')


def importar(a):
    """Carga el db.json del prototipo en una veterinaria."""
    texto = sys.stdin.read() if a.archivo == '-' else Path(a.archivo).read_text(encoding='utf-8')
    datos = json.loads(texto)
    if not isinstance(datos, dict):
        sys.exit('El archivo no tiene el formato del prototipo.')
    desconocidas = [k for k in datos if k not in COLECCIONES]
    with conectar() as c:
        if not c.execute('SELECT 1 FROM veterinarias WHERE id = %s', (a.veterinaria,)).fetchone():
            sys.exit(f'No existe la veterinaria {a.veterinaria}.')
        c.execute("SELECT set_config('app.veterinaria_id', %s, true)", (str(a.veterinaria),))
        print(f"{'':<12}{'archivo':>9}{'base':>8}")
        problemas = 0
        for col in COLECCIONES:
            regs = datos.get(col) or []
            validos = [r for r in regs if isinstance(r, dict) and isinstance(r.get('id'), str) and r['id']]
            if a.reemplazar:
                c.execute(f'DELETE FROM {col} WHERE veterinaria_id = %s', (a.veterinaria,))
            for r in validos:
                c.execute(f"""INSERT INTO {col} (veterinaria_id, id, datos) VALUES (%s, %s, %s)
                              ON CONFLICT (veterinaria_id, id) DO UPDATE SET datos = EXCLUDED.datos, actualizado = now()""",
                          (a.veterinaria, r['id'], Jsonb(r)))
            ids = list({r['id'] for r in validos})
            en_base = c.execute(f'SELECT count(*) AS n FROM {col} WHERE veterinaria_id = %s AND id = ANY(%s)',
                                (a.veterinaria, ids)).fetchone()['n']
            ok = en_base == len(ids) and len(validos) == len(regs)
            problemas += not ok
            nota = '' if ok else f'  <- revisar ({len(regs) - len(validos)} sin id, {len(validos) - len(ids)} repetidos)'
            print(f'{col:<12}{len(regs):>9}{en_base:>8}{nota}')
    if desconocidas:
        print(f'Ojo: el archivo trae datos que la base todavía no guarda: {", ".join(desconocidas)}')
    print('Importación terminada.' if not problemas else 'Importación terminada con diferencias (ver arriba).')


def configurar(_):
    """Primera puesta en marcha: crea la primera veterinaria y su administrador."""
    with conectar() as c:
        if c.execute('SELECT 1 FROM veterinarias').fetchone():
            print('Ya hay veterinarias cargadas, no hace falta configurar.')
            return
        print('\nDatos de la primera veterinaria (salen en recetas y mensajes):')
        nombre = pedir('Nombre', 'Veterinaria')
        vid = crear_veterinaria(c, nombre, pedir('Dirección'), pedir('Teléfono'), pedir('Alias para transferencias'))
        print('\nUsuario administrador (para la dueña de la veterinaria):')
        usuario = pedir('Usuario para entrar (sin espacios)', 'admin')
        nombre_u = pedir('Nombre como aparece en los turnos (ej.: Dra. Paula Ríos)', usuario)
        uid = crear_usuario(c, usuario, nombre_u, pedir_clave())
        asignar(c, uid, vid, 'admin', True)
    print(f'\nListo: veterinaria "{nombre}" (número {vid}) con el usuario "{usuario.lower()}".')


def main():
    p = argparse.ArgumentParser(prog='veterinaria-admin', description='Administración de la app de la veterinaria')
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('migrar', help='crea o actualiza las tablas').set_defaults(f=migrar)
    sub.add_parser('configurar', help='primera veterinaria y su administrador').set_defaults(f=configurar)

    s = sub.add_parser('nueva-veterinaria', help='da de alta una veterinaria')
    s.add_argument('nombre')
    s.add_argument('--direccion', default='')
    s.add_argument('--telefono', default='')
    s.add_argument('--alias', default='', help='alias para transferencias')
    s.set_defaults(f=nueva_veterinaria)

    s = sub.add_parser('editar-veterinaria', help='cambia datos de una veterinaria')
    s.add_argument('id', type=int)
    s.add_argument('--nombre')
    s.add_argument('--direccion')
    s.add_argument('--telefono')
    s.add_argument('--alias')
    s.add_argument('--activa', choices=('si', 'no'))
    s.set_defaults(f=editar_veterinaria)

    sub.add_parser('veterinarias', help='lista las veterinarias').set_defaults(f=veterinarias)

    s = sub.add_parser('nuevo-usuario', help='crea un usuario (pide la contraseña)')
    s.add_argument('usuario')
    s.add_argument('nombre', help='nombre que se muestra, ej.: "Dra. Paula Ríos"')
    s.add_argument('--veterinaria', type=int, help='número de veterinaria')
    s.add_argument('--rol', choices=ROLES, default='recepcion')
    s.add_argument('--atiende', action='store_true', help='aparece en la lista de profesionales')
    s.add_argument('--superadmin', action='store_true')
    s.set_defaults(f=nuevo_usuario)

    s = sub.add_parser('asignar', help='da acceso a un usuario a una veterinaria o le cambia el rol')
    s.add_argument('usuario')
    s.add_argument('veterinaria', type=int)
    s.add_argument('--rol', choices=ROLES, required=True)
    s.add_argument('--atiende', action='store_true')
    s.set_defaults(f=asignar_cmd)

    s = sub.add_parser('quitar', help='saca a un usuario de una veterinaria')
    s.add_argument('usuario')
    s.add_argument('veterinaria', type=int)
    s.set_defaults(f=quitar)

    sub.add_parser('usuarios', help='lista los usuarios').set_defaults(f=usuarios)

    s = sub.add_parser('clave', help='cambia la contraseña de un usuario')
    s.add_argument('usuario')
    s.set_defaults(f=clave)

    s = sub.add_parser('desactivar', help='bloquea a un usuario')
    s.add_argument('usuario')
    s.set_defaults(f=lambda a: activar(a, False))

    s = sub.add_parser('activar', help='desbloquea a un usuario')
    s.add_argument('usuario')
    s.set_defaults(f=lambda a: activar(a, True))

    s = sub.add_parser('importar', help='carga el db.json del prototipo')
    s.add_argument('archivo', help='ruta del db.json, o - para leerlo de la entrada')
    s.add_argument('--veterinaria', type=int, required=True)
    s.add_argument('--reemplazar', action='store_true', help='borra antes los datos de esa veterinaria')
    s.set_defaults(f=importar)

    a = p.parse_args()
    a.f(a)


if __name__ == '__main__':
    main()
