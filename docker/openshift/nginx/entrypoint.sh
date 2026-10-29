#!/bin/sh
set -eu

: "${BACKEND_URL:?BACKEND_URL is required}"
: "${BACKEND_PORT:?BACKEND_PORT is required}"
DNS_RESOLVER="${DNS_RESOLVER:-$(awk '$1 == "nameserver" {print $2; exit}' /etc/resolv.conf)}"
: "${DNS_RESOLVER:?DNS_RESOLVER or /etc/resolv.conf nameserver is required}"
export DNS_RESOLVER
if [ "${FRONTEND_BASE_PATH:-}" = hub ]; then
  template=/opt/skillhub/nginx-hub.template
elif [ -z "${FRONTEND_BASE_PATH:-}" ]; then
  template=/opt/skillhub/nginx-root.template
else
  echo 'FRONTEND_BASE_PATH must be empty or hub' >&2
  exit 1
fi

mkdir -p /tmp/nginx/conf.d /tmp/nginx/client_temp /tmp/nginx/proxy_temp
envsubst '${BACKEND_URL} ${BACKEND_PORT} ${DNS_RESOLVER}' < "$template" > /tmp/nginx/conf.d/default.conf
exec nginx -g 'daemon off;'
