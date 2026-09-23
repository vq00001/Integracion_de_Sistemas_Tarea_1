FROM python:3.12-slim

WORKDIR /srv

# Se construye con context: . (raíz del monorepo), igual que cupos/ y
# matriculas/, para poder copiar proto/ además del código del servicio.
COPY matriculas-api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY matriculas-api/app ./app
COPY proto ./proto

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
