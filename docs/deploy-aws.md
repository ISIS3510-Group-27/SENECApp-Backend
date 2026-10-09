# Deploying to AWS (single EC2 server)

This runs the same Docker Compose stack as local development on one EC2 server: PostgreSQL, the API with its scheduler, and **Caddy** for HTTPS. The result is a public `https://` URL that the mobile apps can use from any network.

Expected cost: about $20/month for a `t3.small` with a 20 GB disk and a public IPv4 address, covered by AWS credits.

```
Phone ──HTTPS──► Caddy (:443, Let's Encrypt) ──► API (:8000, private) ──► PostgreSQL (private)
```

Files used: `docker-compose.prod.yml`, `deploy/Caddyfile`, `.env.production.example`.

---

## 0. Before you start

- An AWS account with credits, and access to the AWS console.
- The Firebase service-account key: `secrets/firebase-service-account.json` on your laptop.
- The deployment files above committed and pushed to the branch you will deploy.

## 1. Set a budget alarm (2 minutes)

AWS console → **Billing and Cost Management** → **Budgets** → **Create budget** → *Monthly cost budget*. Set the amount (for example $25) and your email address. You'll be warned before credits run low.

## 2. Launch the server

AWS console → **EC2** → choose a region at the top right (`us-east-1` is cheapest; `sa-east-1` São Paulo is closer to Bogotá) → **Launch instance**:

| Setting | Value |
|---|---|
| Name | `senecapp-backend` |
| AMI | **Ubuntu Server 24.04 LTS** (64-bit x86) |
| Instance type | **t3.small** (2 GB RAM). `t3.micro` is too small to build the image and run the seed |
| Key pair | **Create new key pair** → name `senecapp-key`, type RSA, format `.pem` → it downloads. Keep it safe |
| Network settings → Firewall | **Create security group**: allow **SSH from My IP**, allow **HTTPS from the internet**, allow **HTTP from the internet** (Let's Encrypt needs port 80) |
| Storage | **20 GiB gp3** |

Click **Launch instance**.

## 3. Give it a fixed IP (Elastic IP)

EC2 → **Elastic IPs** → **Allocate Elastic IP address** → **Allocate**. Then **Actions → Associate Elastic IP address** → choose the instance `senecapp-backend` → **Associate**.

Write down the IP, for example `3.85.12.34`. Your domain will be **`3-85-12-34.sslip.io`**: sslip.io is a free service that resolves that name to that IP, so no domain purchase is needed.

## 4. Connect with SSH (from your laptop)

Windows PowerShell, in the folder where `senecapp-key.pem` was downloaded:

```powershell
# One-time: make the key private, or ssh will refuse it
icacls .\senecapp-key.pem /inheritance:r /grant:r "$($env:USERNAME):R"

ssh -i .\senecapp-key.pem ubuntu@3.85.12.34
```

(macOS/Linux: `chmod 400 senecapp-key.pem` and the same `ssh` command.)

## 5. Install Docker on the server

Inside the SSH session:

```bash
sudo apt-get update && sudo apt-get -y upgrade
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu

# 2 GB swap: headroom for image builds and the first seed
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

exit
```

Reconnect with the same `ssh` command so the `docker` group applies, then check that `docker compose version` works.

## 6. Get the code onto the server

The repository is private, so give the server a **read-only deploy key**:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github_deploy -N "" -C "senecapp-ec2"
cat ~/.ssh/github_deploy.pub
```

Copy the printed line. On GitHub: **SENECApp-Backend → Settings → Deploy keys → Add deploy key**, paste it, and leave *Allow write access* **unchecked**. Then:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/github_deploy
  IdentitiesOnly yes
EOF
ssh-keyscan github.com >> ~/.ssh/known_hosts

git clone git@github.com:ISIS3510-Group-27/SENECApp-Backend.git
cd SENECApp-Backend
git checkout main   # or the branch that has docker-compose.prod.yml
```

> No repo admin rights? Copy the folder from your laptop instead: `scp -i .\senecapp-key.pem -r .\SENECApp-Backend ubuntu@3.85.12.34:~/` (exclude `.venv` first, or it's slow).

## 7. Add the Firebase key

From your laptop, in a **new** PowerShell window:

```powershell
ssh -i .\senecapp-key.pem ubuntu@3.85.12.34 "mkdir -p ~/SENECApp-Backend/secrets"
scp -i .\senecapp-key.pem .\SENECApp-Backend\secrets\firebase-service-account.json ubuntu@3.85.12.34:~/SENECApp-Backend/secrets/
```

## 8. Create the production `.env`

Back on the server:

```bash
cd ~/SENECApp-Backend
cp .env.production.example .env
openssl rand -hex 24          # copy the output: it's the DB password
nano .env
```

Change:

- `DOMAIN=3-85-12-34.sslip.io` (your IP with dashes)
- `CORS_ORIGINS=https://3-85-12-34.sslip.io`
- `POSTGRES_PASSWORD=<the openssl output>`
- `ADMIN_EMAILS=` your Uniandes email (and teammates', comma-separated)

Save with `Ctrl+O`, `Enter`, `Ctrl+X`. Then `chmod 600 .env secrets/*.json`.

## 9. Start everything

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f api caddy
```

The first start builds the image, runs migrations, generates the demo data (1–3 minutes) and obtains the HTTPS certificate. It's ready when you see `Uvicorn running on http://0.0.0.0:8000` and Caddy logs `certificate obtained successfully`. Press `Ctrl+C` to stop following the logs; the containers keep running.

## 10. Check it

From any browser, including your phone on mobile data:

- `https://3-85-12-34.sslip.io/api/v1/health/db` → `{"status":"ok","database":"ok"}`
- `https://3-85-12-34.sslip.io/docs` → Swagger UI

## 11. Point the Flutter app to it

```bash
flutter run --dart-define=API_BASE_URL=https://3-85-12-34.sslip.io/api/v1 --dart-define=AUTH_MODE=firebase
```

Use the same `--dart-define` flags with `flutter build apk` to make an APK you can install on teammates' phones.

---

## Day-to-day operations

All commands run on the server, in `~/SENECApp-Backend`. Define a shortcut first:

```bash
alias dc='docker compose -f docker-compose.prod.yml'
```

| Task | Command |
|---|---|
| Deploy new code | `git pull && dc up -d --build` |
| Logs | `dc logs -f api` |
| Restart after editing `.env` | `dc up -d` |
| Status | `dc ps` |
| Database backup | `dc exec -T db pg_dump -U senecapp senecapp \| gzip > backup-$(date +%F).sql.gz` |
| Regenerate demo data (wipes everything) | `dc exec -e APP_ENV=maintenance api python -m app.seed --reset` |
| Stop (keep data) | `dc down` |

- `APP_ENV=production` refuses database resets on purpose, which is why the reset command overrides it for that one command.
- The simulated data is anchored to the day it was generated; reset it before a demo.
- **Saving credits:** stopping the EC2 instance stops the compute charge. The disk and the Elastic IP are still billed (a few dollars a month). Starting it again brings everything back, because the containers use `restart: unless-stopped`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Browser shows a certificate error / Caddy logs `challenge failed` | Port 80 must be open to the internet (security group), and `DOMAIN` must match the Elastic IP. Then `dc restart caddy` |
| `ssh: Connection timed out` | Your IP changed. EC2 → Security groups → edit the SSH rule → *My IP* |
| `401 Invalid or expired token` from the app | Run the app with `AUTH_MODE=firebase`. `dev:` tokens never work on this server |
| API container keeps restarting | `dc logs api`. Usually a typo in `.env`; keep comments on their own lines |
| Build killed / out of memory | Make sure the swap file exists (`free -h`), or use `t3.medium` |

## Security notes

- Only ports 80 and 443 are public; SSH is limited to your IP. PostgreSQL and the API port are reachable only inside Docker.
- `.env` and `secrets/` stay on the server and are git-ignored. Never commit them.
- `AUTH_PROVIDER=dev` must never be used here: anyone could act as any user.
- `/dashboard` and `/docs` are public pages, but every data endpoint requires a valid token; admin data needs an email listed in `ADMIN_EMAILS`.
