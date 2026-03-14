#!/bin/bash
set -e
echo "╔═══════════════════════════════════════╗"
echo "║     RIFT EFFECT — Setup Script        ║"
echo "╚═══════════════════════════════════════╝"

# Check we're in the right directory
if [ ! -f "docker-compose.yml" ]; then
    echo "ERROR: Run this from the rift/ project root"
    exit 1
fi

# Install Docker if missing
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    echo "Docker installed. You may need to re-login for group changes."
fi

# Install Docker Compose plugin if missing
if ! docker compose version &> /dev/null 2>&1; then
    echo "Installing Docker Compose..."
    sudo apt-get update -qq
    sudo apt-get install -y docker-compose-plugin
fi

# Install NVIDIA Container Toolkit if GPU present
if command -v nvidia-smi &> /dev/null; then
    echo "GPU detected: $(nvidia-smi --query-gpu=name --format=csv,noheader,nounits | head -1)"
    if ! dpkg -l | grep -q nvidia-container-toolkit 2>/dev/null; then
        echo "Installing NVIDIA Container Toolkit..."
        distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
        curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
        curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
            sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
            sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
        sudo apt-get update -qq
        sudo apt-get install -y nvidia-container-toolkit
        sudo systemctl restart docker
    fi
else
    echo "No GPU detected — running in CPU mode"
fi

# Create .env if not exists
if [ ! -f ".env" ]; then
    cp .env.example .env
    # Generate secure secrets
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    PG_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")
    REDIS_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")
    sed -i "s/SECRET_KEY=.*/SECRET_KEY=$SECRET_KEY/" .env
    sed -i "s/JWT_SECRET=.*/JWT_SECRET=$JWT_SECRET/" .env
    sed -i "s/POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$PG_PASS/" .env
    sed -i "s/REDIS_PASSWORD=.*/REDIS_PASSWORD=$REDIS_PASS/" .env
    sed -i "s|CELERY_BROKER=.*|CELERY_BROKER=redis://:$REDIS_PASS@redis:6379/1|" .env
    sed -i "s|CELERY_BACKEND=.*|CELERY_BACKEND=redis://:$REDIS_PASS@redis:6379/2|" .env
    sed -i "s|REDIS_URL=.*|REDIS_URL=redis://:$REDIS_PASS@redis:6379/0|" .env
    sed -i "s|DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://rift:$PG_PASS@db:5432/rift|" .env
    echo ""
    echo "✓ .env created with secure random secrets"
    echo "→ Edit .env and add your STRIPE keys before starting"
fi

# Create storage directories
mkdir -p /data/storage/{uploads,renders,temp,weights,previews}
echo "✓ Storage directories created"

# Pull/build images
echo "Building Docker images (this takes 5-10 minutes first time)..."
docker compose build

# Start database and redis first
echo "Starting database and Redis..."
docker compose up -d db redis
echo "Waiting for database to be ready..."
sleep 10

# Run migrations
echo "Running database migrations..."
docker compose run --rm web alembic upgrade head

# Create admin user
echo ""
echo "Creating admin user..."
docker compose run --rm web python scripts/seed_admin.py

# Start everything
echo "Starting all services..."
docker compose up -d

echo ""
echo "╔═══════════════════════════════════════╗"
echo "║         RIFT EFFECT is running!       ║"
echo "╚═══════════════════════════════════════╝"
echo ""
echo "Application: http://$(hostname -I | awk '{print $1}'):80"
echo "API docs:    http://$(hostname -I | awk '{print $1}'):8000/api/docs"
echo "Flower:      http://$(hostname -I | awk '{print $1}'):5555"
echo ""
echo "To expose publicly with Cloudflare Tunnel:"
echo "  curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o cloudflared"
echo "  chmod +x cloudflared"
echo "  ./cloudflared tunnel --url http://localhost:80"
echo ""
echo "To view logs:    docker compose logs -f web"
echo "To stop:         docker compose down"
echo "To update:       git pull && docker compose build && docker compose up -d"