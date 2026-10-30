"""Conexión a PostgreSQL y transacciones atadas a una veterinaria."""
import os
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# Por defecto se conecta por el socket local con el usuario del sistema
# (autenticación "peer"), así no hay contraseñas guardadas en el server.
DSN = os.environ.get('VET_DSN', 'dbname=veterinaria')

pool = ConnectionPool(DSN, min_size=1, max_size=10, open=False,
                      kwargs={'row_factory': dict_row})


@contextmanager
def tx(veterinaria_id=None):
    """Abre una transacción. Con veterinaria_id, las reglas de seguridad por
    fila de PostgreSQL limitan todo a esa veterinaria."""
    with pool.connection() as conn, conn.transaction():
        if veterinaria_id is not None:
            conn.execute("SELECT set_config('app.veterinaria_id', %s, true)",
                         (str(veterinaria_id),))
        yield conn
