// Production Vault server config (referenced by deploy/compose/docker-compose.prod.yml).
//
// Notes for operators:
//   - This config uses the `file` storage backend; sufficient for single-node
//     production. For HA, switch to `storage "raft" { ... }` and run ≥3 nodes.
//   - TLS is terminated at the upstream nginx in the SMAP stack. Vault
//     listens HTTP on the internal Docker network; do **not** expose port
//     8200 outside the host.
//   - `disable_mlock = true` is acceptable inside containers where the
//     IPC_LOCK capability is unavailable on some kernels; verify your
//     deployment satisfies the trade-off.
//
// First-run bootstrap (`vault operator init` + unseal) is documented in
// deploy/vault/README.md §2. The dev-mode root token is **not** used in prod.

ui            = false
disable_mlock = true

storage "file" {
  path = "/vault/file"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = "true"
}

api_addr     = "http://vault:8200"
cluster_addr = "http://vault:8201"

// Audit log — every secret read, token mint, and transit op is recorded.
// Mount to a persistent volume so logs survive restarts; rotate externally.
audit "file" {
  file_path = "/vault/logs/audit.log"
  log_raw   = false
}
