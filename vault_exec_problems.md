# Wundervault `vault_exec` Integration Problem

## Goal

Query the listmonk API on Oracle Cloud for subscriber count using the ByteClawAPI secret stored in Wundervault.

## Environment

| Component | Value |
|-----------|-------|
| Oracle host | `129.146.202.158` |
| SSH user | `opc` |
| SSH key | `~/.ssh/id_ed25519` |
| Listmonk API | `http://localhost:9000/api/subscribers` |
| Auth method | HTTP Basic Auth — username empty, password = ByteClawAPI secret |
| Wundervault agent ID | `MT1aMIT6zpRaA_1RvQI8rPgT` (Byte 430) |
| ByteClawAPI vault entry ID | `x6BqVjR3LhLreQ-AUGmZ7cPy2AdBA9B0` |

## What Works

- Wundervault MCP server is installed and authenticated
- `vault_entries_list` returns the ByteClawAPI entry
- SSH access to Oracle works: `ssh -i ~/.ssh/id_ed25519 opc@129.146.202.158`
- Listmonk API is reachable and responds with 400 `empty 'api_key' or 'token'` when no credentials are provided

## The Core Problem

`vault_exec` cannot inject the ByteClawAPI secret into an SSH command because:

1. **SSH does not forward local environment variables to the remote host by default.** Even if the secret is injected as `WV_SECRET` locally, `echo $WV_SECRET` on the remote side returns nothing.

2. **Shell escape patterns are blocked before decryption.** The blocked patterns are: `$()`, backticks, `bash -c`, `sh -c`, `eval`. This prevents writing the secret to a temporary file on the remote side using common patterns like:
   ```bash
   ssh ... 'cat > /tmp/secret <<EOF
   $(cat /tmp/wv_secret)
   EOF'
   ```

3. **The vault entry has no `exec_config` set.** The `vault_exec` injection requires an `exec_config` to be defined on the vault entry in the Wundervault dashboard (via the 📨 SEND modal). Without it, the MCP server has no injection recipe to apply. The `inject_as` parameter can only override an existing recipe — it cannot create one from scratch.

## Attempted Approaches

### 1. Direct env var injection via `vault_exec`
```python
vault_exec(entry_id=ByteClawAPI, purpose="query listmonk",
           command="ssh -i ~/.ssh/id_ed25519 opc@129.146.202.158 "
                   "'curl -s http://localhost:9000/api/subscribers -u \\"$WV_SECRET:\\"'",
           inject_as={"env_key": "WV_SECRET"})
```
**Problem:** SSH reads `$WV_SECRET` on the **local** side before spawning the remote shell, so the remote `curl` gets an empty password. Result: `empty 'api_key' or 'token'`.

### 2. `SendEnv` approach — not configured
Setting `SendEnv WV_SECRET` in SSH config would forward the env var, but:
- The Oracle server's `AcceptEnv` configuration cannot be changed this way
- The SSH key authentication already works; adding env forwarding requires server-side config

### 3. File-based secret transfer via pre/post commands
```python
vault_exec(entry_id=ByteClawAPI, purpose="query listmonk",
           command="ssh -i ~/.ssh/id_ed25519 opc@129.146.202.158 "
                   "'curl -s http://localhost:9000/api/subscribers -u \\"$WV_SECRET:\\"'",
           inject_as={
               "pre_command": "scp /tmp/secret opc@129.146.202.158:/tmp/secret",
               "env_key": "WV_SECRET"
           })
```
**Problem:** `pre_command` still runs on the local side; the remote side still has no access to the secret. Also, any command chaining with `&&` or `;` would need shell escape patterns to pass the secret value through.

### 4. Remote command with `WV_SECRET` inline via heredoc
```bash
ssh -i ~/.ssh/id_ed25519 opc@129.146.202.158 'bash -s' <<'OUTER'
curl -s http://localhost:9000/api/subscribers -u "$WV_SECRET:"
OUTER
```
**Problem:** `bash -s` reads `WV_SECRET` from the remote environment, not the local one. No `SendEnv` means the variable is empty on the remote side regardless.

## Why This Fails Architecturally

`vault_exec` is designed for commands that run **locally** — the secret is injected as a local environment variable and the command executes on the same machine. SSH+remote-curl splits the process across two machines:

- The secret is available **locally** (injected by vault_exec)
- The command that needs it (`curl -u $WV_SECRET:`) runs **remotely**
- SSH does not bridge the gap — it doesn't forward env vars to the remote shell unless both `SendEnv` (client) and `AcceptEnv` (server) are configured

## Required Fix

The ByteClawAPI vault entry needs an **`exec_config`** set in the Wundervault dashboard (📨 SEND modal). The config should specify:

- **env key**: `WV_SECRET` (or similar)
- **pre_command** (optional): `echo $WV_SECRET > /tmp/wv_secret` — but this still only works locally
- **post_command** (optional): cleanup to remove the temp file

Once the `exec_config` is set on the vault entry, `vault_exec` can use it with any command — including an SSH chain — but the fundamental SSH env-forwarding problem remains.

**Alternative fix:** The Oracle SSH server's `sshd_config` could be updated to accept `AcceptEnv WV_SECRET`, and the local SSH client could be configured with `SendEnv WV_SECRET`. This would allow the injected env var to reach the remote shell. However, this requires write access to `/etc/ssh/sshd_config` on Oracle (likely `root`).

**Simplest workaround:** Have the Wundervault dashboard return the ByteClawAPI secret value to this chat, then use it directly in a terminal command. The secret would briefly pass through conversation context but would not be stored.

## Relevant Docs

- Wundervault `vault_exec` security model: `$()`, backticks, `bash -c`, `sh -c`, `eval` are rejected **before** decryption to prevent prompt injection from escalating to command execution with secret privileges.
- `inject_as` override requires an existing `exec_config` on the vault entry — it cannot bootstrap a new injection recipe.
