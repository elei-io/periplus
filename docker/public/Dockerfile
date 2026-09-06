FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
COPY packages/periplus-console-core/package.json packages/periplus-console-core/package.json
COPY packages/periplus-terminal-shell/package.json packages/periplus-terminal-shell/package.json
COPY packages/periplus-web-shell/package.json packages/periplus-web-shell/package.json
COPY packages/periplus-admin/package.json packages/periplus-admin/package.json
COPY packages/periplus-public/package.json packages/periplus-public/package.json
RUN npm ci
COPY packages/periplus-console-core/ packages/periplus-console-core/
COPY packages/periplus-web-shell/ packages/periplus-web-shell/
COPY packages/periplus-public/ packages/periplus-public/
RUN npm run build:public

FROM node:24-alpine
WORKDIR /app
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build --chown=node:node /app/packages/periplus-public/.next/standalone/ ./
COPY --from=build --chown=node:node /app/packages/periplus-public/.next/static/ ./packages/periplus-public/.next/static/
COPY --from=build --chown=node:node /app/packages/periplus-public/public/ ./packages/periplus-public/public/
USER node
EXPOSE 3000
HEALTHCHECK --interval=5s --timeout=3s --retries=12 --start-period=5s \
  CMD wget -q --spider http://127.0.0.1:3000/api/healthz || exit 1
CMD ["node", "packages/periplus-public/server.js"]
