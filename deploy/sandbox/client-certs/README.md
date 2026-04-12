Place the external sandbox daemon client TLS bundle in this directory.

Expected files typically include:

- `ca.pem`
- `cert.pem`
- `key.pem`

Set `SANDBOX_DOCKER_HOST` in `configs/.env.production` to the external daemon
address, for example `sandbox.internal.example:2376`.
