# RIFT EFFECT

Professional AI video rendering SaaS. Applies artistic ink and print effects to video using GPU acceleration.

---

## Stack

- **FastAPI** + **Uvicorn** — async web server
- **PostgreSQL** — primary database
- **Redis** + **Celery** — job queue and workers
- **PyTorch** + **Kornia** — GPU effects engine (all 11 effects zero Python loops)
- **RealESRGAN** — AI upscaling (2K / 4K / 8K)
- **FFmpeg** — video writing and audio processing
- **Stripe** — subscriptions + pay-per-video
- **Cloudflare R2** — zero-egress storage (optional)
- **Docker Compose** — one-command deployment

---

## Quick Start — Lightning.ai (SSH from VSCode or Windsurf)

### Step 1 — Open a Lightning.ai Studio

1. Go to [lightning.ai](https://lightning.ai) and sign in
2. Create a new Studio with an **A100** or **H100** GPU
3. Open the Studio — you get a VSCode-like interface with a terminal

### Step 2 — Connect from your local VSCode or Windsurf via SSH

In the Lightning.ai Studio:
```
Settings → SSH → Copy the SSH connection string
```

In VSCode:
```
Ctrl+Shift+P → Remote-SSH: Connect to Host → paste the Lightning SSH string
```

In Windsurf:
```
File → Open Remote → SSH → paste the Lightning SSH string
```

You now have the Lightning.ai GPU accessible directly from your local editor.

### Step 3 — Clone and set up

In the Lightning.ai terminal:
```bash
git clone https://github.com/YOUR_USERNAME/rift-effect.git
cd rift-effect
chmod +x scripts/setup.sh
./scripts/setup.sh
```

The setup script:
- Installs Docker and NVIDIA Container Toolkit
- Auto-generates all secrets in `.env`
- Builds Docker images (~8 minutes first time)
- Runs database migrations
- Creates the admin user
- Starts all services

### Step 4 — Fill in your Stripe keys

```bash
nano .env
```

Set these values (get them from [dashboard.stripe.com](https://dashboard.stripe.com)):
```
STRIPE_SECRET_KEY=sk_live_...
STRIPE_PUBLISHABLE_KEY=pk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_STARTER=price_...
STRIPE_PRICE_PRO=price_...
STRIPE_PRICE_STUDIO=price_...
```

Then restart:
```bash
docker compose restart web
```

### Step 5 — Expose publicly with Cloudflare Tunnel (free HTTPS URL)

```bash
# Download cloudflared
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o cloudflared
chmod +x cloudflared

# Start tunnel (prints a public HTTPS URL)
./cloudflared tunnel --url http://localhost:80
```

You get a URL like `https://something.trycloudflare.com` — this is your live HTTPS URL.

Copy it into `.env`:
```
FRONTEND_URL=https://something.trycloudflare.com
DOMAIN=something.trycloudflare.com
```

Add it to Stripe webhook endpoint:
```
https://something.trycloudflare.com/api/v1/billing/webhook
```

---

## Production Deployment (own domain)

### Cloudflare Setup

1. Buy domain at [Cloudflare Registrar](https://cloudflare.com/products/registrar/) (~$10/year)
2. Add site to Cloudflare (free plan)
3. Create R2 bucket for storage (free tier: 10GB, zero egress fees)
4. Add DNS A record pointing to your server IP

### Environment for production

```bash
ENV=production
DOMAIN=yourdomain.com
FRONTEND_URL=https://yourdomain.com
STORAGE_BACKEND=r2
R2_ACCOUNT_ID=your_account_id
R2_ACCESS_KEY=your_key
R2_SECRET_KEY=your_secret
R2_BUCKET=rift-renders
```

### SSL Certificate (Let's Encrypt)

```bash
docker compose run --rm certbot certonly \
  --webroot --webroot-path=/var/www/certbot \
  -d yourdomain.com -d www.yourdomain.com \
  --email your@email.com --agree-tos --no-eff-email
```

Then restart nginx:
```bash
docker compose restart nginx
```

---

## Project Structure

```
rift/
├── Dockerfile                 # CUDA 12.1, Python 3.11, FFmpeg
├── docker-compose.yml         # Full stack
├── nginx.conf                 # Reverse proxy, SSL, rate limits
├── requirements.txt           # All pinned deps
├── .env.example               # All env vars documented
├── alembic/                   # Database migrations
├── scripts/
│   ├── setup.sh               # One-command install
│   └── seed_admin.py          # Create admin user
└── rift/
    ├── core/                  # Config, DB, exceptions, schemas
    ├── models/                # SQLAlchemy models
    ├── services/              # Effects, overlays, upscaler, video, audio, storage, billing, email
    ├── api/                   # FastAPI routes + dependencies
    │   └── routes/            # auth, videos, jobs, billing, admin
    ├── worker/                # Celery app + tasks
    └── web/static/            # Frontend SPA (HTML/CSS/JS)
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/auth/register` | Create account |
| POST | `/api/v1/auth/login` | Get tokens |
| GET | `/api/v1/auth/me` | Current user |
| POST | `/api/v1/videos/upload` | Upload video (multipart) |
| POST | `/api/v1/jobs` | Create render job |
| GET | `/api/v1/jobs/{id}` | Job status + progress |
| GET | `/api/v1/jobs/{id}/download` | Download output |
| GET | `/api/v1/jobs/{id}/stream` | Stream output (range requests) |
| POST | `/api/v1/jobs/preview` | Preview effect on frame |
| GET | `/api/v1/billing/plans` | List plans |
| POST | `/api/v1/billing/checkout` | Create Stripe checkout |
| POST | `/api/v1/billing/webhook` | Stripe webhook handler |
| GET | `/api/v1/admin/metrics` | Admin metrics (admin only) |
| GET | `/api/v1/admin/users` | User list (admin only) |

Full docs at: `http://localhost:8000/api/docs` (development only)

---

## Effects (11 total — all GPU accelerated)

| Effect | Description |
|--------|-------------|
| `graphic_pen` | Directional ink strokes with variable density |
| `cross_hatch` | Two-angle hatching with edge detection |
| `pencil_sketch` | Soft pencil with paper texture |
| `stipple` | Density-mapped dot pattern |
| `woodcut` | Posterized with carved edges |
| `halftone` | Dot/line/circular screen patterns |
| `dither` | Floyd-Steinberg / Bayer / Threshold |
| `engraving` | Sine-wave line engraving |
| `color_bitmap` | Per-channel Bayer dithering |
| `grayscale_bitmap` | Single-channel Bayer dithering |
| `charcoal` | Multi-scale soft charcoal |

## Overlays (7 total)

`film_grain` · `scan_lines` · `cross_hatch` · `glass` · `ocean_ripple` · `bokeh` · `texture`

---

## Useful Commands

```bash
# View logs
docker compose logs -f web
docker compose logs -f worker_render

# Restart a service
docker compose restart web

# Run a migration
docker compose run --rm web alembic revision --autogenerate -m "description"
docker compose run --rm web alembic upgrade head

# Open a Django-style shell
docker compose run --rm web python -c "import asyncio; from rift.core.database import *; ..."

# Check GPU in container
docker compose run --rm worker_render python -c "import torch; print(torch.cuda.get_device_name(0))"

# Monitor Celery
open http://localhost:5555  # Flower dashboard
```

---

## Pricing Configuration

Edit `.env`:
```
PRICE_PER_VIDEO_CENTS=2500     # $25 per video
PLAN_STARTER_RENDERS=10        # renders per month
PLAN_PRO_RENDERS=50
PLAN_STUDIO_RENDERS=200
```

Create products in Stripe dashboard:
1. Product: **RIFT Starter** — Recurring, $19/month → copy Price ID to `STRIPE_PRICE_STARTER`
2. Product: **RIFT Pro** — Recurring, $49/month → copy to `STRIPE_PRICE_PRO`
3. Product: **RIFT Studio** — Recurring, $99/month → copy to `STRIPE_PRICE_STUDIO`

Pay-per-video credits are handled as one-time payments directly in code — no Stripe product needed.

---

## Admin Access

Default admin credentials (created by `seed_admin.py`):
```
Email:    admin@rifteffect.com
Password: Admin1rift!
```

**Change immediately after first login.**

Admin panel available at `/` → sign in → Admin Dashboard in sidebar (admin users only).

---

## License

Proprietary. All rights reserved.