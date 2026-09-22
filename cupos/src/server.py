import logging
import os
import sqlite3
import time
from concurrent import futures
from contextlib import contextmanager

import grpc

import cupos_pb2
import cupos_pb2_grpc

# VARIABLES DE CONFIGURACION
DB_PATH = os.getenv("DB_PATH", "/data/cupos.db")
PORT = os.getenv("GRPC_PORT", "50051")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "10"))

# para experimento
LATENCIA_MS = int(os.getenv("SIMULAR_LATENCIA_MS", "0"))


# datos para base de datos
CURSOS_INICIALES = [
    (1, "Programación en Python", 30),
    (2, "Bases de Datos", 25),
    (3, "Redes y Comunicaciones", 20),
    (4, "Diseño Web", 15),
    (5, "Ciberseguridad Básica", 2),
]

# documentar los accesos y operaciones en la base de datos
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cupos")


# BASE DE DATOS


@contextmanager
def conexion():
    """Una conexión por llamada: seguro con el ThreadPool de gRPC."""
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with conexion() as con:
        con.execute("PRAGMA journal_mode=WAL")

        # crear base de datos minima para el microservicio
        con.execute(
            """CREATE TABLE IF NOT EXISTS cursos (
                curso_id      INTEGER PRIMARY KEY,
                nombre        TEXT    NOT NULL,
                cupos_totales INTEGER NOT NULL CHECK (cupos_totales >= 0),
                cupos_libres  INTEGER NOT NULL,
                CHECK (cupos_libres >= 0 AND cupos_libres <= cupos_totales)
            )"""
        )

        # crear tabla para asegurar idempotencia
        con.execute(
            """CREATE TABLE IF NOT EXISTS operaciones_idempotentes (
                clave      TEXT PRIMARY KEY,
                operacion  TEXT NOT NULL,
                curso_id   INTEGER NOT NULL,
                estado     INTEGER NOT NULL,
                creado_en  TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )

        ## si no hay datos en la tabla, insertarlos
        if con.execute("SELECT COUNT(*) FROM cursos").fetchone()[0] == 0:
            con.executemany(
                "INSERT INTO cursos VALUES (?, ?, ?, ?)",
                [(i, n, t, t) for i, n, t in CURSOS_INICIALES],
            )
            log.info("Base inicializada con %d cursos", len(CURSOS_INICIALES))


def a_curso(row: sqlite3.Row) -> cupos_pb2.Curso:
    return cupos_pb2.Curso(
        curso_id=row["curso_id"],
        nombre=row["nombre"],
        cupos_totales=row["cupos_totales"],
        cupos_libres=row["cupos_libres"],
    )


# MICROSERVICIO CUPOS


class CuposService(cupos_pb2_grpc.CuposServicer):
    def _simular_latencia(self):
        if LATENCIA_MS > 0:
            time.sleep(LATENCIA_MS / 1000)

    # seleccionar los cupos libres de un curso
    def obtenerCupo(self, request, context):
        self._simular_latencia()
        with conexion() as con:
            row = con.execute(
                "SELECT cupos_libres FROM cursos WHERE curso_id = ?",
                (request.curso_id,),
            ).fetchone()
        if row is None:
            context.abort(
                grpc.StatusCode.NOT_FOUND, f"Curso {request.curso_id} no existe"
            )
        return cupos_pb2.ObtenerResponse(cupos_libres=row["cupos_libres"])

    # devolver todos los cursos en la base de datos
    def listarCursos(self, request, context):
        self._simular_latencia()
        with conexion() as con:
            rows = con.execute("SELECT * FROM cursos ORDER BY curso_id").fetchall()
        return cupos_pb2.ListarCursosResponse(cursos=[a_curso(r) for r in rows])

    # devolver todos los cursos en la base de datos
    def listarCursosDisponibles(self, request, context):
        self._simular_latencia()
        with conexion() as con:
            rows = con.execute(
                "SELECT * FROM cursos WHERE cupos_libres > 0 ORDER BY curso_id"
            ).fetchall()
        return cupos_pb2.ListarCursosResponse(cursos=[a_curso(r) for r in rows])

    def ocuparCupo(self, request, context):
        self._simular_latencia()

        clave = request.idempotency_key
        with conexion() as con:
            if clave:  # revisar que no sea un request repetido:with
                previa = con.execute(
                    "SELECT estado FROM operaciones_idempotentes "
                    "WHERE clave = ? AND operacion = 'ocupar' AND curso_id = ?",
                    (clave, request.curso_id),
                ).fetchone()

                # si se repite, devuelve el resultado del primero
                if previa is not None:
                    log.info(
                        "ocuparCupo repetido, clave=%s -> resultado cacheado", clave
                    )
                    return cupos_pb2.OcuparResponse(estado=previa["estado"])

            # resta uno a la cantidad de cupos libres, si es que quedan
            cur = con.execute(
                "UPDATE cursos SET cupos_libres = cupos_libres - 1 "
                "WHERE curso_id = ? AND cupos_libres > 0",
                (request.curso_id,),
            )
            if cur.rowcount == 1:
                estado = cupos_pb2.CUPO_OCUPADO
            else:
                # si no puede ocupar el cupo, revisa si existe el curso y si esta completo
                existe = con.execute(
                    "SELECT 1 FROM cursos WHERE curso_id = ?", (request.curso_id,)
                ).fetchone()
                estado = (
                    cupos_pb2.SIN_CUPOS if existe else cupos_pb2.CURSO_NO_ENCONTRADO
                )

            # registrar operacion
            if clave:
                con.execute(
                    "INSERT INTO operaciones_idempotentes (clave, operacion, curso_id, estado) "
                    "VALUES (?, 'ocupar', ?, ?)",
                    (clave, request.curso_id, estado),
                )

        log.info(
            "ocupar cupo curso=%s -> %s",
            request.curso_id,
            cupos_pb2.Estado.Name(estado),
        )
        return cupos_pb2.OcuparResponse(estado=estado)

    def liberarCupo(self, request, context):
        self._simular_latencia()

        clave = request.idempotency_key
        with conexion() as con:
            if clave:  # checkear que no sea un mensaje repetido
                previa = con.execute(
                    "SELECT estado FROM operaciones_idempotentes "
                    "WHERE clave = ? AND operacion = 'liberar' AND curso_id = ?",
                    (clave, request.curso_id),
                ).fetchone()
                if previa is not None:
                    log.info(
                        "ocuparCupo repetido, clave=%s -> resultado cacheado", clave
                    )
                    return cupos_pb2.OcuparResponse(estado=previa["estado"])

            cur = con.execute(
                "UPDATE cursos SET cupos_libres = cupos_libres + 1 "
                "WHERE curso_id = ? AND cupos_libres < cupos_totales",
                (request.curso_id,),
            )
            if cur.rowcount == 1:
                estado = cupos_pb2.CUPO_LIBERADO
            else:
                existe = con.execute(
                    "SELECT 1 FROM cursos WHERE curso_id = ?", (request.curso_id,)
                ).fetchone()
                if not existe:
                    estado = cupos_pb2.CURSO_NO_ENCONTRADO
                else:
                    # El curso ya tiene todos sus cupos libres: liberar sería inconsistente.
                    context.abort(
                        grpc.StatusCode.FAILED_PRECONDITION,
                        "El curso ya tiene todos los cupos libres",
                    )

            # registrar operacion
            if clave:
                con.execute(
                    "INSERT INTO operaciones_idempotentes (clave, operacion, curso_id, estado) "
                    "VALUES (?, 'ocupar', ?, ?)",
                    (clave, request.curso_id, estado),
                )

        log.info(
            "liberar cupo curso=%s -> %s",
            request.curso_id,
            cupos_pb2.Estado.Name(estado),
        )
        return cupos_pb2.LiberarResponse(estado=estado)


def serve() -> None:
    init_db()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=MAX_WORKERS))
    cupos_pb2_grpc.add_CuposServicer_to_server(CuposService(), server)
    server.add_insecure_port(f"[::]:{PORT}")
    server.start()
    log.info(
        "Servicio Cupos escuchando en :%s (latencia simulada: %d ms)", PORT, LATENCIA_MS
    )
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
