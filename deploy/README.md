# Déploiement AI Governor

## Cloud Run (Production)

### Prérequis

- `gcloud` CLI installé et authentifié (`gcloud auth login`)
- Projet GCP `yourart-governor` avec droits admin
- Secrets API (Anthropic, Voyage, LiteLLM, etc.) dans un fichier `.env`

### Premier déploiement

```bash
# 1. Migrer les secrets vers Secret Manager (avant infra — Cloud SQL a besoin de litellm-db-password)
./deploy/cloud-deploy.sh secrets

# 2. Créer l'infrastructure (Cloud SQL, bucket GCS, IAM)
./deploy/cloud-deploy.sh infra

# 3. Déployer tous les services
./deploy/cloud-deploy.sh all

# 4. Vérifier
./deploy/cloud-deploy.sh status
```

### Mise à jour

```bash
# Après modification du code — redéploie le Worker Pool
./deploy/cloud-deploy.sh receiver

# Ou redéployer tout
./deploy/cloud-deploy.sh all
```

### Dry-run (prévisualisation)

```bash
./deploy/cloud-deploy.sh receiver --dry-run
```

### Logs et monitoring

```bash
# Logs en temps réel
gcloud run services logs read ai-governor-receiver --region=europe-west1 --limit=50

# Statut des services
./deploy/cloud-deploy.sh status
```

### Rollback

```bash
# Lister les révisions
gcloud run revisions list --service=ai-governor-receiver --region=europe-west1

# Revenir à une révision précédente
gcloud run services update-traffic ai-governor-receiver \
  --to-revisions=REVISION_NAME=100 --region=europe-west1
```

### Coûts

~48-53$/mois (≈45-50€) : Worker Pool always-on (~$35-40), LiteLLM scale-to-zero (~$1), Cloud SQL micro (~$12), GCS (~$0.05).

---

## Mac Mini (Legacy/Dev)

## Prérequis

- macOS (Apple Silicon ou Intel)
- Docker Desktop ou OrbStack installé
- `cloudflared` (`brew install cloudflared`)
- Accès admin sur la machine (pour LaunchDaemon)

## Installation

### 1. Copier le projet

```bash
git clone git@github.com:YourArtOfficial/koan.git /opt/ai-governor
cd /opt/ai-governor
git checkout 007-integration-poc
```

### 2. Configurer les variables d'environnement

```bash
cp env.example .env
# Éditer .env avec les vraies valeurs :
# ANTHROPIC_API_KEY, LITELLM_MASTER_KEY, LITELLM_DB_PASSWORD, etc.
```

### 3. Configurer le tunnel Cloudflare

```bash
cloudflared tunnel create ai-governor
cloudflared tunnel route dns ai-governor governor.yourart.art

# Copier l'UUID du tunnel dans deploy/cloudflare-config.yml
# Remplacer <TUNNEL-UUID> par l'UUID réel
```

### 4. Installer le LaunchDaemon (démarrage automatique)

```bash
# Créer le répertoire de logs
sudo mkdir -p /var/log/ai-governor

# Copier le plist
sudo cp deploy/com.yourart.ai-governor.plist /Library/LaunchDaemons/

# Charger le daemon
sudo launchctl load /Library/LaunchDaemons/com.yourart.ai-governor.plist
```

### 5. Installer cloudflared comme service

```bash
sudo cloudflared service install
```

### 6. Vérifier

```bash
# Santé locale
curl http://localhost:5001/health

# Santé publique
curl https://governor.yourart.art/health
```

## Gestion

### Démarrer / Arrêter

```bash
# Démarrer manuellement
cd /opt/ai-governor && docker compose up -d

# Arrêter
cd /opt/ai-governor && docker compose down

# Voir les logs
docker compose logs -f koan

# Redémarrer un service
docker compose restart koan
```

### Mise à jour

```bash
cd /opt/ai-governor
git pull
docker compose up -d --build
```

### Logs

```bash
# Logs Docker
docker compose logs -f

# Logs launchd
tail -f /var/log/ai-governor/launchd.log
tail -f /var/log/ai-governor/launchd.err

# Logs agent
tail -f /opt/ai-governor/logs/awake.log
```

### Dépannage

| Problème | Diagnostic | Solution |
|----------|-----------|----------|
| Agent ne démarre pas | `docker compose ps` | Vérifier `.env`, `docker compose logs` |
| `/health` ne répond pas | `curl localhost:5001/health` | Vérifier que le port 5001 est exposé |
| Notifications non reçues | `/governor.status` | Vérifier Google Chat webhook dans GSM |
| Module en erreur | `/governor.status` | Consulter les logs du module |
| Mac Mini redémarré | `launchctl list com.yourart.ai-governor` | Le daemon relance automatiquement |
| Tunnel down | `cloudflared tunnel info ai-governor` | `sudo launchctl kickstart system/com.cloudflare.cloudflared` |
