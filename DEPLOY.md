# Deploying to a Contabo VPS

Target: a fresh Contabo VPS (Ubuntu 22.04/24.04). The app is a single
container behind Caddy, which handles TLS automatically.

## Before you start — pick the generation provider deliberately

This is the one decision that can embarrass you in front of a compliance
buyer, so make it consciously rather than inheriting the demo default.

| Provider | Trains on your data? |
|---|---|
| **Groq** | **No** — Services Agreement §4.2 bars training on inputs/outputs |
| Anthropic | No — API data not used for training by default |
| Gemini free tier | **Yes** — Google's terms allow product improvement *and human review* |
| NVIDIA NIM | Unverified — trial terms not confirmed |

The repo currently ships `LLM_PROVIDER=gemini`, which is the right call for
local development on synthetic data and the wrong call for anything a client
will type into. **Set `LLM_PROVIDER=groq` for any client-facing deployment**,
or move Gemini to a paid tier (adding billing removes the training clause).

If you leave it on Gemini, you must tell the client not to enter real customer
information — see the email template in `CLIENT_EMAIL.md`.

## Sizing

- **RAM:** 1 GB minimum, 2 GB comfortable. The embedding model runs in-process.
- **Disk:** ~2 GB for the image; the index itself is only ~3 MB.
- **CPU:** any Contabo tier. Embedding a single query is milliseconds.

Contabo's smallest VPS is comfortably enough.

## 1. Server setup

```bash
ssh root@YOUR_VPS_IP

adduser --disabled-password --gecos "" deploy
usermod -aG sudo deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy

apt update && apt install -y docker.io docker-compose-v2 git
systemctl enable --now docker
usermod -aG docker deploy
```

Lock down SSH (`/etc/ssh/sshd_config`): `PasswordAuthentication no`,
`PermitRootLogin no`, then `systemctl restart ssh`.

Firewall — note port 8000 is deliberately **not** opened. Compose binds it to
loopback so the app is only reachable through Caddy:

```bash
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
```

## 2. DNS

Point an A record at the VPS IP and wait for it to resolve. Caddy cannot issue
a certificate until it does:

```
aml-demo.yourdomain.com.   A   YOUR_VPS_IP
```

Then set that hostname as the first line of `Caddyfile`.

## 3. Deploy

```bash
su - deploy
git clone YOUR_REPO_URL aml-demo && cd aml-demo
```

Create `.env` on the server — never commit it:

```bash
cat > .env <<'EOF'
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_...
EMBED_PROVIDER=local
COHERE_API_KEY=...
ADMIN_USER=admin
ADMIN_TOKEN=PUT_A_LONG_RANDOM_STRING_HERE
DEMO_SEGMENT=accountant
MIN_RERANK_SCORE=0
EOF
chmod 600 .env
```

Generate the admin token with `openssl rand -base64 32`.

In `Caddyfile`, replace `203.0.113.4` with your own IP (`curl ifconfig.me`) so
the internal routes are restricted at the proxy as well as by password. If you
need to reach them from anywhere, delete the `@internal` block.

```bash
docker compose up -d --build
docker compose logs -f app
```

First build takes several minutes: it installs dependencies, bakes in the
embedding model, and downloads and indexes the legislation.

## 4. Verify

```bash
curl -o /dev/null -w '%{http_code}\n' https://aml-demo.yourdomain.com/            # 200
curl -o /dev/null -w '%{http_code}\n' https://aml-demo.yourdomain.com/evaluation  # 401
curl -o /dev/null -w '%{http_code}\n' -u admin:TOKEN \
     https://aml-demo.yourdomain.com/evaluation                                    # 200
```

Confirm tenant isolation is intact — the second call must be 404:

```bash
KEY='meridian%2Fsop-14-onboarding-trust-clients%23b3'
curl -o /dev/null -w '%{http_code}\n' "https://.../api/source/$KEY?tenant_id=meridian"  # 200
curl -o /dev/null -w '%{http_code}\n' "https://.../api/source/$KEY?tenant_id=corvid"    # 404
```

## Routes

| Route | Access |
|---|---|
| `/` | Public — chat only, no link to anything internal |
| `/api/ask`, `/api/config`, `/api/source` | Public (source is tenant-scoped) |
| `/evaluation` | HTTP Basic |
| `/corpus` | HTTP Basic |
| `/api/metrics`, `/api/corpus` | HTTP Basic |

With `ADMIN_TOKEN` unset the internal routes return **404**, not 401 — an
unconfigured deployment should not advertise that a dashboard exists.

## Updating

```bash
cd ~/aml-demo && git pull && docker compose up -d --build
```

The index lives in the `index-data` volume and survives rebuilds. To force a
re-index: `docker compose run --rm app python -m index.build`.

## Operational notes

- **Backups:** nothing here is a source of truth. The corpus rebuilds from
  legislation.gov.au; tenant documents come from the repo. Back up `.env`.
- **Cost:** Contabo VPS only. Groq and local embeddings are free; Cohere
  reranking is capped at 1,000 calls/month on the trial key, and retrieval
  degrades gracefully without it.
- **Logs:** `docker compose logs -f app`. Questions are printed; treat the log
  as sensitive if a client is using it for real.
- **Not included:** no authentication on the chat itself, no per-user audit
  trail, no rate limiting beyond Caddy defaults. Fine for a demo, not for
  production use with real customer data.
