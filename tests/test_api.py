"""Pruebas del servidor contra un PostgreSQL de verdad.

Se corren como el usuario postgres (crea y borra la base veterinaria_test):
    sudo -u postgres venv/bin/python -m pytest -q tests
"""
import json
import os
import subprocess

import pytest

BASE = 'veterinaria_test'
os.environ['VET_ADMIN_DSN'] = f'dbname={BASE}'
# La app entra como el rol "veterinaria", igual que en el server: así se
# prueban también las reglas de seguridad por fila.
os.environ['VET_DSN'] = f"dbname={BASE} options='-c role=veterinaria'"

from fastapi.testclient import TestClient  # noqa: E402

from app import admin  # noqa: E402
from app.main import app  # noqa: E402

H = {'X-Vet': '1'}


@pytest.fixture(scope='session', autouse=True)
def base(tmp_path_factory):
    os.environ['VET_ARCHIVOS'] = str(tmp_path_factory.mktemp('archivos'))
    import app.main as m
    m.ARCHIVOS = m.Path(os.environ['VET_ARCHIVOS'])
    subprocess.run(['dropdb', '--if-exists', BASE], check=True)
    subprocess.run(['createdb', BASE], check=True)
    subprocess.run(['psql', '-q', BASE, '-c',
                    "DO $$BEGIN CREATE ROLE veterinaria LOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END$$"],
                   check=True)
    admin.migrar(None)
    m.pool.open()
    with admin.conectar() as c:
        a = admin.crear_veterinaria(c, 'Patitas', 'Mitre 1', '11 1111-1111', 'patitas.mp')
        b = admin.crear_veterinaria(c, 'Huellas')
        for usuario, nombre, vets in [('ana', 'Dra. Ana', [(a, 'admin', True)]),
                                      ('rita', 'Rita', [(a, 'recepcion', False)]),
                                      ('vero', 'Dra. Vero', [(a, 'veterinario', True), (b, 'admin', True)]),
                                      ('hugo', 'Hugo', [(b, 'admin', False)])]:
            uid = admin.crear_usuario(c, usuario, nombre, 'clave-segura')
            for vid, rol, atiende in vets:
                admin.asignar(c, uid, vid, rol, atiende)
    yield {'a': a, 'b': b}
    m.pool.close()


def entrar(usuario, clave='clave-segura'):
    c = TestClient(app)
    r = c.post('/api/login', json={'usuario': usuario, 'clave': clave}, headers=H)
    assert r.status_code == 200, r.text
    return c


def test_login_incorrecto_y_freno():
    if c := TestClient(app):
        for _ in range(5):
            assert c.post('/api/login', json={'usuario': 'nadie', 'clave': 'x'}, headers=H).status_code == 401
        assert c.post('/api/login', json={'usuario': 'nadie', 'clave': 'x'}, headers=H).status_code == 429


def test_sin_sesion_y_sin_encabezado():
    if c := TestClient(app):
        assert c.get('/api/datos').status_code == 401
        assert c.post('/api/login', json={'usuario': 'ana', 'clave': 'clave-segura'}).status_code == 403


def test_sesion_y_datos_por_veterinaria(base):
    ana = entrar('ana')
    s = ana.get('/api/sesion').json()
    assert s['rol'] == 'admin' and s['veterinaria']['nombre'] == 'Patitas'
    assert s['profesionales'] == ['Dra. Ana', 'Dra. Vero']
    r = ana.post('/api/cambios', headers=H, json={'guardar': {
        'clientes': [{'id': 'c1', 'nombre': 'Lucía', 'apellido': 'Fernández'}],
        'mascotas': [{'id': 'm1', 'cliente': 'c1', 'nombre': 'Toby'}],
        'precios': [{'id': 'p1', 'nombre': 'Consulta', 'precio': 15000}]}})
    assert r.status_code == 200, r.text
    d = ana.get('/api/datos').json()
    assert d['clientes'] == [{'id': 'c1', 'nombre': 'Lucía', 'apellido': 'Fernández'}]
    assert d['mascotas'][0]['nombre'] == 'Toby'

    # Otra veterinaria con el mismo id de cliente no se mezcla.
    hugo = entrar('hugo')
    assert hugo.get('/api/datos').json()['clientes'] == []
    hugo.post('/api/cambios', headers=H, json={'guardar': {'clientes': [{'id': 'c1', 'nombre': 'Otro'}]}})
    hugo.post('/api/cambios', headers=H, json={'borrar': {'mascotas': ['m1']}})
    assert ana.get('/api/datos').json()['clientes'][0]['nombre'] == 'Lucía'
    assert ana.get('/api/datos').json()['mascotas'][0]['nombre'] == 'Toby'

    # Editar y borrar.
    ana.post('/api/cambios', headers=H, json={'guardar': {'clientes': [{'id': 'c1', 'nombre': 'Lucía', 'apellido': 'Paz'}]},
                                               'borrar': {'precios': ['p1']}})
    d = ana.get('/api/datos').json()
    assert d['clientes'][0]['apellido'] == 'Paz' and d['precios'] == []


def test_permisos_por_rol():
    rita = entrar('rita')
    assert rita.post('/api/cambios', headers=H, json={'guardar': {'turnos': [{'id': 't1', 'fecha': '2026-11-02'}]}}).status_code == 200
    assert rita.post('/api/cambios', headers=H, json={'guardar': {'visitas': [{'id': 'v1'}]}}).status_code == 403
    assert rita.post('/api/cambios', headers=H, json={'guardar': {'precios': [{'id': 'p9'}]}}).status_code == 403
    assert rita.post('/api/cambios', headers=H, json={'guardar': {'cualquiera': [{'id': 'x'}]}}).status_code == 400
    assert rita.get('/api/sesion').json()['permisos'] == ['clientes', 'estudios', 'mascotas', 'mensajes', 'turnos']


def test_varias_veterinarias(base):
    vero = entrar('vero')
    s = vero.get('/api/sesion').json()
    assert s['veterinaria'] is None and len(s['veterinarias']) == 2
    assert vero.get('/api/datos').status_code == 409
    assert vero.post('/api/elegir-veterinaria', headers=H, json={'veterinaria_id': 999}).status_code == 403
    assert vero.post('/api/elegir-veterinaria', headers=H, json={'veterinaria_id': base['b']}).status_code == 200
    s = vero.get('/api/sesion').json()
    assert s['veterinaria']['nombre'] == 'Huellas' and s['rol'] == 'admin'


def test_archivos(base):
    ana = entrar('ana')
    r = ana.post('/api/archivos', headers=H, files={'archivo': ('eco.pdf', b'%PDF-1.4 hola', 'application/pdf')})
    assert r.status_code == 200, r.text
    fid = r.json()['id']
    r = ana.get(f'/api/archivos/{fid}')
    assert r.status_code == 200 and r.content == b'%PDF-1.4 hola'
    assert entrar('hugo').get(f'/api/archivos/{fid}').status_code == 404
    r = ana.post('/api/archivos', headers=H, files={'archivo': ('x.svg', b'<svg/>', 'image/svg+xml')})
    assert r.status_code == 400


def test_logout_y_cambio_de_clave():
    c = entrar('hugo')
    otra = entrar('hugo')
    assert c.post('/api/cambiar-clave', headers=H, json={'actual': 'mal', 'nueva': 'nueva-clave'}).status_code == 400
    assert c.post('/api/cambiar-clave', headers=H, json={'actual': 'clave-segura', 'nueva': 'nueva-clave'}).status_code == 200
    assert otra.get('/api/sesion').status_code == 401
    assert c.get('/api/sesion').status_code == 200
    c.post('/api/logout', headers=H)
    assert c.get('/api/sesion').status_code == 401
    entrar('hugo', 'nueva-clave')


def test_rls_sin_veterinaria_no_ve_nada():
    import psycopg
    with psycopg.connect(os.environ['VET_DSN']) as c:
        assert c.execute('SELECT count(*) FROM clientes').fetchone()[0] == 0


def test_importar_db_json(base, tmp_path, capsys):
    archivo = tmp_path / 'db.json'
    archivo.write_text(json.dumps({
        'clientes': [{'id': 'c1', 'nombre': 'Jorge'}, {'id': 'c2', 'nombre': 'Carla'}],
        'turnos': [{'id': 't1', 'fecha': '2026-10-30', 'items': [{'id': 'i1', 'precio': 100, 'cant': 1}]}],
        'vacunas': [], 'precios': [{'nombre': 'sin id'}]}), encoding='utf-8')
    hugo_vet = base['b']

    class A:
        veterinaria = hugo_vet
        reemplazar = True
    A.archivo = str(archivo)
    admin.importar(A)
    salida = capsys.readouterr().out
    assert 'clientes            2       2' in salida
    assert 'revisar' in salida  # el precio sin id
    vero = entrar('vero')
    vero.post('/api/elegir-veterinaria', headers=H, json={'veterinaria_id': hugo_vet})
    d = vero.get('/api/datos').json()
    assert {c['nombre'] for c in d['clientes']} == {'Jorge', 'Carla'}
    assert d['turnos'][0]['items'][0]['precio'] == 100
