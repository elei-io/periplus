FROM node:24-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
COPY packages/periplus-console-core/package.json packages/periplus-console-core/package.json
COPY packages/periplus-terminal-shell/package.json packages/periplus-terminal-shell/package.json
COPY packages/periplus-web-shell/package.json packages/periplus-web-shell/package.json
COPY web/package.json web/package.json
RUN npm ci

COPY packages/periplus-console-core/ packages/periplus-console-core/
COPY packages/periplus-web-shell/ packages/periplus-web-shell/
COPY web/ web/
RUN npm run build:web
RUN find web/dist/assets -name '*.wasm' -exec gzip -9 -k '{}' ';'


FROM nginx:1.27-alpine

ENV PERIPLUS_API_HOST=periplus-api \
    PERIPLUS_API_PORT=8000

# The stock nginx entrypoint renders templates with envsubst at container start.
# This keeps the web image portable across Compose and Helm release names.
COPY docker/web/nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/web/dist/ /usr/share/nginx/html/

EXPOSE 80

HEALTHCHECK --interval=5s --timeout=3s --retries=12 --start-period=5s \
  CMD wget -q --spider http://127.0.0.1/api/openapi.json || exit 1
