FROM python:3.12-slim
WORKDIR /app

# instalar requerimientos (versiones fijas: build reproducible)
COPY cupos/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# copiar codigo fuente a container
COPY cupos/src ./src
COPY proto ./proto

# genera cupos_pb2.py y cupos_pb2_grpc.py dentro de src/
RUN python src/generar.py

# Usuario sin privilegios. /data pertenece a ese usuario: un volumen nombrado nuevo hereda
# ese dueño. (Si vienes de una version anterior con un volumen creado por root: `docker compose
# down -v` o `chown -R 10001 /data` dentro del volumen.)
RUN useradd --system --uid 10001 --no-create-home app \
 && mkdir -p /data && chown app:app /data
USER app

# exponer el puerto
EXPOSE 50051

# ejecutar el archivos
CMD ["python", "src/server.py"]
