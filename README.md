# Gestión veterinaria

App web para veterinarias (agenda, clientes, mascotas, historia clínica, vacunas,
estudios, documentos, precios y cobros), pensada para que la usen varias
veterinarias desde una sola instalación. Funciona en PC y celular.

## Cómo está armada

| Parte | Qué es |
|---|---|
| `web/index.html` | La pantalla de la app. Sale del prototipo con `herramientas/adaptar_prototipo.py`. |
| `web/login.html` | Pantalla para entrar y elegir veterinaria. |
| `app/main.py` | Servidor (Python + FastAPI): login, permisos, datos y archivos. |
| `app/migraciones/` | Estructura de la base de datos PostgreSQL. |
| `app/admin.py` | Comandos para administrar veterinarias y usuarios desde la terminal. |
| `instalar.sh` | Instala o actualiza todo en un server Debian 12 o posterior. |

### Varias veterinarias

Cada registro (cliente, mascota, turno, etc.) lleva el número de su veterinaria.
El servidor siempre filtra por la veterinaria de quien entró, y además PostgreSQL
tiene reglas de seguridad por fila (`ROW LEVEL SECURITY`) que no dejan ver ni
modificar datos de otra veterinaria aunque el programa se equivoque.

Cada tabla guarda el registro completo en una columna `datos` (jsonb) y expone
como columnas los campos que se usan para buscar y relacionar (fecha, mascota,
cliente…). Así la pantalla puede sumar campos sin tocar la base.

### Usuarios y roles

| Rol | Puede modificar |
|---|---|
| `admin` | Todo, incluida la lista de precios |
| `veterinario` | Todo menos la lista de precios |
| `recepcion` | Clientes, mascotas, turnos (y cobros), estudios y mensajes. Ve la historia clínica pero no la modifica. |

Una persona puede tener acceso a varias veterinarias, con un rol en cada una.
Los usuarios marcados con "atiende" aparecen en la lista de profesionales de los
turnos. Las contraseñas se guardan con Argon2.

## Instalar o actualizar en el server

```bash
sudo bash instalar.sh
```

La primera vez pide los datos de la veterinaria y del usuario administrador, y
ofrece pasar los datos del prototipo anterior (`/var/lib/veterinaria/db.json`).
Las siguientes veces solo actualiza el programa; los datos no se tocan.

Queda en `http://IP-DEL-SERVER:8092`. Todas las noches se guarda una copia de la
base y de los archivos en `/var/backups/veterinaria-pg` (últimos 14 días).

## Administrar veterinarias y usuarios

```bash
sudo veterinaria-admin usuarios                     # lista usuarios y veterinarias
sudo veterinaria-admin veterinarias
sudo veterinaria-admin nuevo-usuario martin "Dr. Martín Sosa" --veterinaria 1 --rol veterinario --atiende
sudo veterinaria-admin asignar martin 2 --rol veterinario --atiende   # acceso a otra veterinaria
sudo veterinaria-admin clave martin                 # cambiar contraseña
sudo veterinaria-admin desactivar martin
sudo veterinaria-admin nueva-veterinaria "Veterinaria Huellas" --telefono "11 2222-3333" --alias huellas.mp
sudo veterinaria-admin editar-veterinaria 2 --direccion "Mitre 100"
sudo veterinaria-admin importar - --veterinaria 1 < db.json
```

## Pasar mejoras del prototipo a la app

```bash
python3 herramientas/adaptar_prototipo.py server3/veterinaria/index.html web/index.html
```

Se parte de la copia del prototipo que usa api/db (la de server3). El script reemplaza solo las partes que hablan con el servidor (guardado,
archivos, login y datos de la veterinaria). Si el prototipo cambió justo ahí,
avisa qué parte no encontró.

## Pruebas

```bash
python3 -m venv venv && venv/bin/pip install -r requirements-dev.txt
sudo -u postgres venv/bin/python -m pytest -q tests
```

Crean y borran una base `veterinaria_test` y entran con el rol `veterinaria`,
así también prueban que una veterinaria no vea los datos de otra.
