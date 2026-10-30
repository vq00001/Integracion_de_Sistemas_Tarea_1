#!/usr/bin/env python3
"""Medición para D2 (gRPC vs REST): tamaño del mensaje y costo de (de)serialización.

D2 afirmaba que gRPC reduce el tamaño de los mensajes citando un "caso de referencia con datos de
producción" sin fuente. Este script lo reemplaza por una MEDICIÓN reproducible, sin Docker, con las
mismas respuestas que Cupos entrega (mensajes protobuf del contrato) frente a su equivalente JSON
compacto y con gzip.

Qué mide:
  - bytes del cuerpo (payload) en protobuf vs JSON vs JSON+gzip, para tres respuestas reales del
    contrato: obtenerCupo, ocuparCupo y listarCursos (5 cursos de la semilla).
  - tiempo medio de serializar+deserializar (microsegundos) en este equipo: es un orden de magnitud,
    no una garantía; depende del equipo y de la versión de protobuf.

Qué NO mide (y por tanto no se afirma): latencia de red de extremo a extremo (HTTP/2 + multiplexación
frente a HTTP/1.1 + keep-alive) ni el costo de cabeceras HTTP. Para eso hay que medir sobre el sistema
levantado; en este sistema el cuello de botella medido (experimento 3) es la escritura serializada en
SQLite, no el formato del mensaje.

Uso:  python experiment/scripts/tamano_mensaje.py     → experiment/data/raw/tamano_mensaje_<fecha>.csv
"""
import csv
import gzip
import json
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def cargar_stubs():
    from grpc_tools import protoc
    out = Path(tempfile.mkdtemp(prefix="stubs_"))
    rc = protoc.main(["protoc", f"-I{ROOT / 'proto'}", f"--python_out={out}", str(ROOT / "proto" / "cupos.proto")])
    if rc != 0:
        raise SystemExit("no se pudo compilar el .proto")
    sys.path.insert(0, str(out))
    import cupos_pb2
    return cupos_pb2


SEMILLA = [(1, "Programación Básica", 30), (2, "Bases de Datos", 25), (3, "Redes de Computadores", 20),
           (4, "Ingeniería de Software", 15), (5, "Ciberseguridad Básica", 2)]


def tiempo_us(fn, reps=20000):
    muestras = []
    for _ in range(5):
        t0 = time.perf_counter()
        for _ in range(reps // 5):
            fn()
        muestras.append((time.perf_counter() - t0) / (reps // 5) * 1e6)
    return statistics.median(muestras)


def main():
    pb = cargar_stubs()
    casos = {
        "obtenerCupo": (pb.ObtenerResponse(cupos_libres=17), {"cupos_libres": 17}, pb.ObtenerResponse),
        "ocuparCupo": (pb.OcuparResponse(estado=pb.CUPO_OCUPADO), {"estado": "CUPO_OCUPADO"}, pb.OcuparResponse),
        "listarCursos(5)": (
            pb.ListarCursosResponse(cursos=[pb.Curso(curso_id=i, nombre=n, cupos_totales=t, cupos_libres=t - 1)
                                            for i, n, t in SEMILLA]),
            {"items": [{"curso_id": i, "nombre": n, "cupos_totales": t, "cupos_libres": t - 1} for i, n, t in SEMILLA]},
            pb.ListarCursosResponse),
    }
    filas = []
    print(f"{'respuesta':18}{'protobuf':>10}{'JSON':>8}{'JSON+gzip':>11}{'JSON/proto':>12}{'us proto':>10}{'us JSON':>9}")
    for nombre, (msg, obj, clase) in casos.items():
        p = msg.SerializeToString()
        j = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        z = gzip.compress(j)
        us_p = tiempo_us(lambda: clase.FromString(msg.SerializeToString()))
        us_j = tiempo_us(lambda: json.loads(json.dumps(obj, ensure_ascii=False)))
        filas.append([nombre, len(p), len(j), len(z), f"{len(j) / len(p):.2f}", f"{us_p:.2f}", f"{us_j:.2f}"])
        print(f"{nombre:18}{len(p):>10}{len(j):>8}{len(z):>11}{len(j) / len(p):>12.2f}{us_p:>10.2f}{us_j:>9.2f}")
    out = ROOT / "experiment" / "data" / "raw" / f"tamano_mensaje_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["respuesta", "bytes_protobuf", "bytes_json", "bytes_json_gzip", "json_sobre_protobuf",
                    "us_protobuf_ida_vuelta", "us_json_ida_vuelta"])
        w.writerows(filas)
    print("datos:", out)


if __name__ == "__main__":
    main()
