FROM node:22-alpine

WORKDIR /app

COPY package.json ./
RUN npm install --omit=dev

COPY server.js ./

ENV PORT=8080
ENV LOG_DIR=/logs
EXPOSE 8080
VOLUME ["/logs"]

CMD ["node", "server.js"]
