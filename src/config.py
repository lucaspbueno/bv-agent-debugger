"""
config.py — Carregamento centralizado do .env e constantes do projeto.

Toda variável de ambiente é lida aqui uma única vez no import.
Outros módulos importam as constantes daqui, nunca direto do os.environ.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Carrega .env do diretório raiz do projeto
_PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# ============================================================
# AMBIENTES (UAT / PROD)
# ============================================================

ENVS = {
    "uat": {
        "host":      "api.bancobv.jabuti.ai",
        "api_key":   os.environ.get("BV_API_KEY", "DEFINA_VIA_ENV_BV_API_KEY"),
        "worker_id": os.environ.get("BV_WORKER_ID", "ca3afe48-39ec-4fb2-a365-29859aeab69f"),
    },
    "prod": {
        "host":      "PREENCHER",
        "api_key":   os.environ.get("BV_API_KEY_PROD",   "PREENCHER"),
        "worker_id": os.environ.get("BV_WORKER_ID_PROD", "PREENCHER"),
    },
}

# ============================================================
# GRAFANA / LOKI
# ============================================================

GRAFANA_BASE        = "https://jabutiagi.grafana.net"
LOKI_DATASOURCE_UID = "grafanacloud-logs"
LOKI_APP_FILTER     = 'app_kubernetes_io_name="worker-chart"'
GRAFANA_TOKEN       = os.environ.get("GRAFANA_TOKEN")
GRAFANA_SESSION     = os.environ.get("GRAFANA_SESSION")

# ============================================================
# ANTHROPIC (primário)
# ============================================================

ANTHROPIC_API_KEY     = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL       = os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-6"
ANTHROPIC_MAX_TOKENS  = 2000

# ============================================================
# OLLAMA (fallback)
# ============================================================

OLLAMA_URL            = "http://localhost:11434"
OLLAMA_FALLBACK_MODEL = os.environ.get("OLLAMA_MODEL") or "llama3.1"

# ============================================================
# DEFAULTS DE EXECUÇÃO
# ============================================================

DEFAULT_HOURS  = 24
LOG_LIMIT      = 2000
VPN_TIMEOUT    = 8
API_RETRIES    = 3
API_RETRY_WAIT = 5
