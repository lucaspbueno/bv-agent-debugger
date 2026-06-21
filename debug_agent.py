#!/usr/bin/env python3
"""
debug_agent.py — Pipeline de debug do agente Jabuti/BV

Fluxo: BVID -> customer_id -> conversation_id -> logs Loki -> análise Claude (Anthropic API)

REQUISITOS:
    pip install requests anthropic

VARIÁVEIS DE AMBIENTE:
    GRAFANA_TOKEN       Token do service account do Grafana
    ANTHROPIC_API_KEY   Chave da API do Claude
    API_AUTH_TOKEN      Token de autenticação das APIs do Jabuti/BV

USO:
    python debug_agent.py <BVID>
    python debug_agent.py <BVID> --hours 48
    python debug_agent.py <BVID> --save
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta, timezone

import requests
from anthropic import Anthropic

# ============================================================
# CONFIGURAÇÃO — ajuste os campos marcados com # AJUSTE
# ============================================================

GRAFANA_BASE        = "https://jabutiagi.grafana.net"
LOKI_DATASOURCE_UID = "grafanacloud-logs"
LOKI_APP_FILTER     = 'app_kubernetes_io_name="worker-chart"'

API_CUSTOMER_URL    = "https://SUA-API/customer"   # AJUSTE
API_CUSTOMER_METHOD = "GET"
API_CUSTOMER_PARAM  = "bv_id"
API_CUSTOMER_FIELD  = "customer_id"

API_CONVERSATION_URL    = "https://SUA-API/conversation"  # AJUSTE
API_CONVERSATION_METHOD = "GET"
API_CONVERSATION_PARAM  = "customer_id"
API_CONVERSATION_FIELD  = "conversation_id"

DEFAULT_HOURS = 24
DEFAULT_MODEL = "claude-sonnet-4-6"
LOG_LIMIT     = 2000

GRAFANA_TOKEN     = os.environ.get("GRAFANA_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
API_AUTH_TOKEN    = os.environ.get("API_AUTH_TOKEN")

SYSTEM_PROMPT = """Você é um especialista em debug de agentes de IA conversacionais.
Recebe os logs brutos de uma conversa e produz um diagnóstico técnico claro.

Estruture sua resposta:
1. RESUMO — o que aconteceu, em 2-3 linhas.
2. FLUXO — tools/chamadas executadas, em ordem cronológica.
3. PONTO DE FALHA — onde e quando quebrou (com timestamp).
4. CAUSA PROVÁVEL — hipótese técnica para a raiz do problema.
5. EVIDÊNCIAS — linhas de log que sustentam o diagnóstico.
6. PRÓXIMOS PASSOS — o que investigar ou corrigir."""


def _step(msg): print(f"  {msg}...".ljust(55), end="", flush=True)
def _ok(d=""):  print(f"  ✓  {d}" if d else "  ✓")
def _fail(msg):
    print("  ✗")
    print(f"\nERRO: {msg}", file=sys.stderr)
    sys.exit(1)


def check_secrets():
    missing = [n for n, v in [
        ("GRAFANA_TOKEN", GRAFANA_TOKEN),
        ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
        ("API_AUTH_TOKEN", API_AUTH_TOKEN),
    ] if not v]
    if missing:
        _fail(f"Variáveis de ambiente faltando: {', '.join(missing)}")


def _request(url, method, params, field, label):
    headers = {"Authorization": f"Bearer {API_AUTH_TOKEN}"}
    try:
        r = (requests.get(url, headers=headers, params=params, timeout=30)
             if method == "GET"
             else requests.post(url, headers=headers, json=params, timeout=30))
        r.raise_for_status()
    except requests.RequestException as e:
        _fail(f"[{label}] {e}")
    data  = r.json()
    value = data.get(field) or (data.get("data") or {}).get(field)
    if not value:
        _fail(f"[{label}] campo '{field}' não encontrado: {json.dumps(data)[:300]}")
    return value


def resolve_customer_id(bvid):
    _step("Buscando customer_id (API 1)")
    cid = _request(API_CUSTOMER_URL, API_CUSTOMER_METHOD,
                   {API_CUSTOMER_PARAM: bvid}, API_CUSTOMER_FIELD, "API customer")
    _ok(cid)
    return cid


def resolve_conversation_id(customer_id):
    _step("Buscando conversation_id (API 2)")
    cid = _request(API_CONVERSATION_URL, API_CONVERSATION_METHOD,
                   {API_CONVERSATION_PARAM: customer_id},
                   API_CONVERSATION_FIELD, "API conversation")
    _ok(cid)
    return cid


def fetch_logs(conversation_id, hours):
    end, start = datetime.now(timezone.utc), datetime.now(timezone.utc) - timedelta(hours=hours)
    query = f'{{{LOKI_APP_FILTER}}} |= `{conversation_id}`'
    url   = f"{GRAFANA_BASE}/api/datasources/proxy/uid/{LOKI_DATASOURCE_UID}/loki/api/v1/query_range"
    _step(f"Buscando logs no Grafana (últimas {hours}h)")
    try:
        r = requests.get(url,
            headers={"Authorization": f"Bearer {GRAFANA_TOKEN}"},
            params={"query": query,
                    "start": str(int(start.timestamp() * 1e9)),
                    "end":   str(int(end.timestamp()   * 1e9)),
                    "limit": LOG_LIMIT, "direction": "backward"},
            timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        _fail(f"[Loki] {e}")
    streams = r.json().get("data", {}).get("result", [])
    lines   = sorted([(int(ts), ln) for s in streams for ts, ln in s.get("values", [])],
                     key=lambda x: x[0])
    fmt = [f"[{datetime.fromtimestamp(ts/1e9, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] {ln}"
           for ts, ln in lines]
    _ok(f"{len(lines)} linhas")
    return "\n".join(fmt), len(lines)


def analyze_logs(logs, bvid, conversation_id, model):
    client  = Anthropic()
    _step(f"Analisando com Claude ({model})")
    resp = client.messages.create(
        model=model, max_tokens=2000, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content":
                   f"BVID: {bvid}\nconversation_id: {conversation_id}\n\nLOGS:\n{'-'*60}\n{logs}"}])
    _ok()
    return resp.content[0].text


def main():
    parser = argparse.ArgumentParser(description="Debug do agente Jabuti/BV")
    parser.add_argument("bvid")
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--save",  action="store_true")
    args = parser.parse_args()

    print(f"\n{\'═\'*60}")
    print(f"  Debug Jabuti/BV  |  BVID: {args.bvid}")
    print(f"{\'═\'*60}\n")

    check_secrets()
    customer_id     = resolve_customer_id(args.bvid)
    conversation_id = resolve_conversation_id(customer_id)
    logs, n         = fetch_logs(conversation_id, args.hours)
    if n == 0:
        _fail(f"Nenhum log encontrado. Tente --hours {args.hours * 3}.")
    diagnostico = analyze_logs(logs, args.bvid, conversation_id, args.model)

    print(f"\n{\'═\'*60}\n  DIAGNÓSTICO\n{\'═\'*60}\n")
    print(diagnostico)

    if args.save:
        base = f"debug_{conversation_id}"
        with open(f"{base}.log", "w") as f: f.write(logs)
        with open(f"{base}.md",  "w") as f: f.write(f"# Debug {args.bvid}\n\n{diagnostico}\n")
        print(f"\n  Salvos: {base}.log e {base}.md")


if __name__ == "__main__":
    main()
