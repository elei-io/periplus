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
ARG PERIPLUS_PUBLIC_ORIGIN
ENV PERIPLUS_PUBLIC_ORIGIN=$PERIPLUS_PUBLIC_ORIGIN
ARG NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN
ARG NEXT_PUBLIC_POSTHOG_HOST
ENV NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN=$NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN
ENV NEXT_PUBLIC_POSTHOG_HOST=$NEXT_PUBLIC_POSTHOG_HOST
ARG NEXT_PUBLIC_ANALYTICS_ENVIRONMENT=development
ARG NEXT_PUBLIC_RELEASE=local
ARG POSTHOG_PROJECT_ID
ENV NEXT_PUBLIC_ANALYTICS_ENVIRONMENT=$NEXT_PUBLIC_ANALYTICS_ENVIRONMENT
ENV NEXT_PUBLIC_RELEASE=$NEXT_PUBLIC_RELEASE
ENV POSTHOG_PROJECT_ID=$POSTHOG_PROJECT_ID
RUN --mount=type=secret,id=posthog_api_key \
    if [ -f /run/secrets/posthog_api_key ]; then export POSTHOG_API_KEY="$(cat /run/secrets/posthog_api_key)"; fi; \
    npm run build:public

FROM node:24-alpine
WORKDIR /app
COPY LICENSE NOTICE LICENSING.md THIRD_PARTY_NOTICES.md /usr/share/licenses/periplus/
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build --chown=node:node /app/packages/periplus-public/.next/standalone/ ./
COPY --from=build --chown=node:node /app/packages/periplus-public/.next/static/ ./packages/periplus-public/.next/static/
USER node
EXPOSE 3000
HEALTHCHECK --interval=5s --timeout=3s --retries=12 --start-period=5s \
  CMD wget -q --spider http://127.0.0.1:3000/api/healthz || exit 1
CMD ["node", "packages/periplus-public/server.js"]
