Place production TLS certificates in this directory before starting `docker-compose.prod.yml`.

Required filenames:

- `fullchain.pem`
- `privkey.pem`

These files are mounted into the gateway container at `/etc/nginx/certs`.
