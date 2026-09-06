#!/bin/sh
set -eu
: "${PERIPLUS_ADMIN_API_TOKEN:?Set the administrative API credential}"
printf '%s\n' "$PERIPLUS_ADMIN_API_TOKEN" | htpasswd -niB admin > /etc/nginx/admin.htpasswd
