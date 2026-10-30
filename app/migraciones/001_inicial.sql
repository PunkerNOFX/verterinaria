-- Estructura inicial: veterinarias, usuarios y los datos de cada veterinaria.
-- Cada registro guarda el objeto completo de la app en "datos" (jsonb); las
-- columnas generadas exponen los campos que se usan para buscar y relacionar.

CREATE TABLE veterinarias (
  id          serial PRIMARY KEY,
  nombre      text NOT NULL,
  direccion   text NOT NULL DEFAULT '',
  telefono    text NOT NULL DEFAULT '',
  alias_pago  text NOT NULL DEFAULT '',
  activa      boolean NOT NULL DEFAULT true,
  creada      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE usuarios (
  id          serial PRIMARY KEY,
  usuario     text NOT NULL UNIQUE CHECK (usuario = lower(usuario)),
  nombre      text NOT NULL,
  clave_hash  text NOT NULL,
  superadmin  boolean NOT NULL DEFAULT false,
  activo      boolean NOT NULL DEFAULT true,
  creado      timestamptz NOT NULL DEFAULT now()
);

-- Qué rol tiene cada usuario en cada veterinaria. "atiende" = aparece en la
-- lista de profesionales de los turnos.
CREATE TABLE membresias (
  usuario_id      int NOT NULL REFERENCES usuarios ON DELETE CASCADE,
  veterinaria_id  int NOT NULL REFERENCES veterinarias ON DELETE CASCADE,
  rol             text NOT NULL CHECK (rol IN ('admin', 'veterinario', 'recepcion')),
  atiende         boolean NOT NULL DEFAULT false,
  PRIMARY KEY (usuario_id, veterinaria_id)
);

CREATE TABLE sesiones (
  token_hash      bytea PRIMARY KEY,
  usuario_id      int NOT NULL REFERENCES usuarios ON DELETE CASCADE,
  veterinaria_id  int REFERENCES veterinarias ON DELETE CASCADE,
  creada          timestamptz NOT NULL DEFAULT now(),
  expira          timestamptz NOT NULL
);
CREATE INDEX ON sesiones (expira);

CREATE TABLE clientes (
  veterinaria_id  int NOT NULL REFERENCES veterinarias ON DELETE CASCADE,
  id              text NOT NULL,
  datos           jsonb NOT NULL,
  nombre          text GENERATED ALWAYS AS (datos->>'nombre') STORED,
  apellido        text GENERATED ALWAYS AS (datos->>'apellido') STORED,
  telefono        text GENERATED ALWAYS AS (datos->>'telefono') STORED,
  dni             text GENERATED ALWAYS AS (datos->>'dni') STORED,
  creado          timestamptz NOT NULL DEFAULT now(),
  actualizado     timestamptz NOT NULL DEFAULT now(),
  actualizado_por int REFERENCES usuarios ON DELETE SET NULL,
  PRIMARY KEY (veterinaria_id, id)
);

CREATE TABLE mascotas (
  veterinaria_id  int NOT NULL REFERENCES veterinarias ON DELETE CASCADE,
  id              text NOT NULL,
  datos           jsonb NOT NULL,
  cliente_id      text GENERATED ALWAYS AS (datos->>'cliente') STORED,
  nombre          text GENERATED ALWAYS AS (datos->>'nombre') STORED,
  especie         text GENERATED ALWAYS AS (datos->>'especie') STORED,
  creado          timestamptz NOT NULL DEFAULT now(),
  actualizado     timestamptz NOT NULL DEFAULT now(),
  actualizado_por int REFERENCES usuarios ON DELETE SET NULL,
  PRIMARY KEY (veterinaria_id, id)
);
CREATE INDEX ON mascotas (veterinaria_id, cliente_id);

CREATE TABLE turnos (
  veterinaria_id  int NOT NULL REFERENCES veterinarias ON DELETE CASCADE,
  id              text NOT NULL,
  datos           jsonb NOT NULL,
  mascota_id      text GENERATED ALWAYS AS (datos->>'mascota') STORED,
  fecha           text GENERATED ALWAYS AS (datos->>'fecha') STORED,
  hora            text GENERATED ALWAYS AS (datos->>'hora') STORED,
  estado          text GENERATED ALWAYS AS (datos->>'estado') STORED,
  profesional     text GENERATED ALWAYS AS (datos->>'prof') STORED,
  creado          timestamptz NOT NULL DEFAULT now(),
  actualizado     timestamptz NOT NULL DEFAULT now(),
  actualizado_por int REFERENCES usuarios ON DELETE SET NULL,
  PRIMARY KEY (veterinaria_id, id)
);
CREATE INDEX ON turnos (veterinaria_id, fecha);
CREATE INDEX ON turnos (veterinaria_id, mascota_id);

-- Historia clínica: una fila por consulta.
CREATE TABLE visitas (
  veterinaria_id  int NOT NULL REFERENCES veterinarias ON DELETE CASCADE,
  id              text NOT NULL,
  datos           jsonb NOT NULL,
  mascota_id      text GENERATED ALWAYS AS (datos->>'mascota') STORED,
  fecha           text GENERATED ALWAYS AS (datos->>'fecha') STORED,
  creado          timestamptz NOT NULL DEFAULT now(),
  actualizado     timestamptz NOT NULL DEFAULT now(),
  actualizado_por int REFERENCES usuarios ON DELETE SET NULL,
  PRIMARY KEY (veterinaria_id, id)
);
CREATE INDEX ON visitas (veterinaria_id, mascota_id);

CREATE TABLE vacunas (
  veterinaria_id  int NOT NULL REFERENCES veterinarias ON DELETE CASCADE,
  id              text NOT NULL,
  datos           jsonb NOT NULL,
  mascota_id      text GENERATED ALWAYS AS (datos->>'mascota') STORED,
  vacuna          text GENERATED ALWAYS AS (datos->>'vacuna') STORED,
  fecha           text GENERATED ALWAYS AS (datos->>'fecha') STORED,
  proxima         text GENERATED ALWAYS AS (datos->>'proxima') STORED,
  creado          timestamptz NOT NULL DEFAULT now(),
  actualizado     timestamptz NOT NULL DEFAULT now(),
  actualizado_por int REFERENCES usuarios ON DELETE SET NULL,
  PRIMARY KEY (veterinaria_id, id)
);
CREATE INDEX ON vacunas (veterinaria_id, mascota_id);
CREATE INDEX ON vacunas (veterinaria_id, proxima);

CREATE TABLE estudios (
  veterinaria_id  int NOT NULL REFERENCES veterinarias ON DELETE CASCADE,
  id              text NOT NULL,
  datos           jsonb NOT NULL,
  mascota_id      text GENERATED ALWAYS AS (datos->>'mascota') STORED,
  tipo            text GENERATED ALWAYS AS (datos->>'tipo') STORED,
  fecha           text GENERATED ALWAYS AS (datos->>'fecha') STORED,
  creado          timestamptz NOT NULL DEFAULT now(),
  actualizado     timestamptz NOT NULL DEFAULT now(),
  actualizado_por int REFERENCES usuarios ON DELETE SET NULL,
  PRIMARY KEY (veterinaria_id, id)
);
CREATE INDEX ON estudios (veterinaria_id, mascota_id);

-- Recetas, órdenes de estudio e indicaciones.
CREATE TABLE documentos (
  veterinaria_id  int NOT NULL REFERENCES veterinarias ON DELETE CASCADE,
  id              text NOT NULL,
  datos           jsonb NOT NULL,
  mascota_id      text GENERATED ALWAYS AS (datos->>'mascota') STORED,
  tipo            text GENERATED ALWAYS AS (datos->>'tipo') STORED,
  fecha           text GENERATED ALWAYS AS (datos->>'fecha') STORED,
  creado          timestamptz NOT NULL DEFAULT now(),
  actualizado     timestamptz NOT NULL DEFAULT now(),
  actualizado_por int REFERENCES usuarios ON DELETE SET NULL,
  PRIMARY KEY (veterinaria_id, id)
);
CREATE INDEX ON documentos (veterinaria_id, mascota_id);

CREATE TABLE precios (
  veterinaria_id  int NOT NULL REFERENCES veterinarias ON DELETE CASCADE,
  id              text NOT NULL,
  datos           jsonb NOT NULL,
  categoria       text GENERATED ALWAYS AS (datos->>'categoria') STORED,
  nombre          text GENERATED ALWAYS AS (datos->>'nombre') STORED,
  creado          timestamptz NOT NULL DEFAULT now(),
  actualizado     timestamptz NOT NULL DEFAULT now(),
  actualizado_por int REFERENCES usuarios ON DELETE SET NULL,
  PRIMARY KEY (veterinaria_id, id)
);

-- Mensajes de WhatsApp preparados desde la app.
CREATE TABLE mensajes (
  veterinaria_id  int NOT NULL REFERENCES veterinarias ON DELETE CASCADE,
  id              text NOT NULL,
  datos           jsonb NOT NULL,
  mascota_id      text GENERATED ALWAYS AS (datos->>'mascota') STORED,
  creado          timestamptz NOT NULL DEFAULT now(),
  actualizado     timestamptz NOT NULL DEFAULT now(),
  actualizado_por int REFERENCES usuarios ON DELETE SET NULL,
  PRIMARY KEY (veterinaria_id, id)
);

-- Archivos subidos (fotos y PDF de estudios). El contenido vive en disco.
CREATE TABLE archivos (
  veterinaria_id  int NOT NULL REFERENCES veterinarias ON DELETE CASCADE,
  id              text NOT NULL,
  nombre          text NOT NULL,
  mime            text NOT NULL,
  tamano          bigint NOT NULL,
  subido          timestamptz NOT NULL DEFAULT now(),
  subido_por      int REFERENCES usuarios ON DELETE SET NULL,
  PRIMARY KEY (veterinaria_id, id)
);

-- Segundo candado: aunque el programa se equivoque, PostgreSQL solo deja ver
-- y modificar las filas de la veterinaria elegida en la sesión.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['clientes','mascotas','turnos','visitas','vacunas',
                           'estudios','documentos','precios','mensajes','archivos']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format($p$CREATE POLICY por_veterinaria ON %I
      USING (veterinaria_id = nullif(current_setting('app.veterinaria_id', true), '')::int)
      WITH CHECK (veterinaria_id = nullif(current_setting('app.veterinaria_id', true), '')::int)$p$, t);
  END LOOP;
END $$;
