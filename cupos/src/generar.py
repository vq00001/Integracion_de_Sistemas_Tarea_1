"""Genera los stubs de gRPC a partir de proto/cupos.proto.

Funciona tanto en Docker (/app/proto) como en local (<repo>/proto).
"""

import sys
from pathlib import Path

from grpc_tools import protoc

SRC_DIR = Path(__file__).resolve().parent
CANDIDATOS = [SRC_DIR.parent / "proto", SRC_DIR.parent.parent / "proto"]


def main() -> None:
    proto_dir = next((p for p in CANDIDATOS if (p / "cupos.proto").exists()), None)
    if proto_dir is None:
        sys.exit("No se encontró cupos.proto en: " + ", ".join(map(str, CANDIDATOS)))

    resultado = protoc.main(
        [
            "grpc_tools.protoc",
            f"-I{proto_dir}",
            f"--python_out={SRC_DIR}",
            f"--grpc_python_out={SRC_DIR}",
            str(proto_dir / "cupos.proto"),
        ]
    )
    if resultado != 0:
        sys.exit("Falló la generación de stubs")
    print(f"Stubs generados en {SRC_DIR}")


if __name__ == "__main__":
    main()
