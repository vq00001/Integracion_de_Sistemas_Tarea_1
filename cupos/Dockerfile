FROM python:3.12-slim
WORKDIR /app

# instalar requerimientos
COPY cupos/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# copiar codigo fuente a container
COPY cupos/src ./src
COPY proto ./proto

# genera cupos_pb2.py y cupos_pb2_grpc.py dentro de src/
RUN python src/generar.py

# exponer el puerto
EXPOSE 50051

# ejecutar el archivos
CMD ["python", "src/server.py"]
