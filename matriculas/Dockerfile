# Este Dockerfile se construye con contexto en la RAIZ del repositorio
# (ver docker-compose.yml: context: ., dockerfile: matriculas/Dockerfile),
# exactamente igual que cupos/Dockerfile. Por eso los COPY llevan el
# prefijo "matriculas/".

FROM node:20-alpine

WORKDIR /usr/src/app

COPY matriculas/package*.json ./
RUN npm install --omit=dev

COPY matriculas/ .

CMD ["node", "src/index.js"]
