FROM node:24-alpine AS build

COPY packages/atlas-console-core/ /app/packages/atlas-console-core/
RUN cd /app/packages/atlas-console-core \
    && npm install --no-package-lock --no-save typescript@~6 @types/node@^24 \
    && npm run build

WORKDIR /app/packages/atlas-web-shell

COPY packages/atlas-web-shell/package.json packages/atlas-web-shell/package-lock.json ./
RUN npm ci

COPY packages/atlas-web-shell/ ./
RUN npm run build
RUN find dist/assets -name '*.wasm' -exec gzip -9 -k '{}' ';'


FROM nginx:1.27-alpine

COPY docker/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/packages/atlas-web-shell/dist/ /usr/share/nginx/html/

EXPOSE 80

HEALTHCHECK --interval=5s --timeout=3s --retries=12 --start-period=5s \
  CMD wget -q --spider http://127.0.0.1/api/openapi.json || exit 1
