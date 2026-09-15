#!/bin/sh
set -eu
: "${PERIPLUS_ADMIN_API_TOKEN:?Set the administrative API credential}"

# Use the container DNS in Compose and Kubernetes; refresh replaced service IPs.
awk '/^nameserver / { print "resolver " $2 " valid=5s ipv6=off;"; exit }' /etc/resolv.conf > /etc/nginx/resolver.conf
test -s /etc/nginx/resolver.conf
