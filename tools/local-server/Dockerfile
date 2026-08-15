FROM node:22-alpine

# openssl is used to generate the self-signed certificate on first run
RUN apk add --no-cache openssl

WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev
COPY mqtt.js server.js ./

ENV HTTP_PORT=8099 MQTT_PORT=8098 CERT_DIR=/certs
EXPOSE 8098 8099
VOLUME ["/certs"]
CMD ["node", "server.js"]
