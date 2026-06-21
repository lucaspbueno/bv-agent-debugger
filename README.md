# BV Agent Debugger

Pipeline de debug do agente Jabuti/BV. Recebe um BVID, busca o `conversation_id` e os logs no Grafana Loki, e gera um diagnóstico técnico estruturado da conversa.

## Pipeline

```
BVID  →  customer_id  →  conversation_id  →  logs Loki  →  análise (Claude API ou Ollama)
```

## Instalação

```bash
pip install -r requirements.txt
cp .env.example .env
# preencha os valores necessários no .env
```

## Uso

```bash
python debug_agent.py <BVID>
python debug_agent.py <BVID> --env prod
python debug_agent.py <BVID> --hours 48
python debug_agent.py <BVID> --save
```

## Estratégia de análise

O script tem dois provedores de análise:

| | Provedor | Quando é usado |
|---|---|---|
| **Primário** | Claude API | Se `ANTHROPIC_API_KEY` estiver no `.env` |
| **Fallback** | Ollama local | Se Claude falhar, ou se a key não estiver definida |

Toda decisão e fallback é logada no terminal.

## Estrutura

```
bv-agent-debugger/
├── debug_agent.py          # CLI orquestradora
├── src/
│   ├── config.py           # .env e constantes
│   ├── logger.py           # output formatado
│   ├── http_client.py      # GET com retry e tratamento de erro
│   ├── bv_api.py           # APIs Jabuti/BV
│   ├── grafana.py          # Loki + auth
│   ├── analyzer.py         # Claude API com fallback Ollama
│   └── prompts.py          # system prompt da análise
├── .env.example
├── .gitignore
└── requirements.txt
```
