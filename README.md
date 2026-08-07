# 🚀 SmartReco 2026 — Agentic Course Recommendation System

[![CI Checks](https://github.com/your-username/aura-smartreco/actions/workflows/smartreco-checks.yml/badge.svg)](https://github.com/your-username/aura-smartreco/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.6-009688.svg)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2.61-FF6F00.svg)](https://langchain-ai.github.io/langgraph/)
[![Mesh API Gateway](https://img.shields.io/badge/Mesh_API-100%25_Compliant-00E5FF.svg)](https://api.meshapi.ai)

An educational course platform that tracks user behavior (views, searches, clicks, dwell time) and uses a **LangGraph agent** routed through the **Mesh API Gateway** to generate personalized, persuasive course recommendations that update in real-time as user behavior changes.

---

## 🌟 Key Product Features

- **Mesh API Native Compliance**: 100% of LLM reasoning & narrative copywriting (`tencent/hy3`) and vector embeddings (`sentence-transformers/all-minilm-l6-v2`) pass through `https://api.meshapi.ai/v1`.
- **Transactional Dual-Write Engine**: Products are stored in **SQLite DB** (configured in WAL mode for concurrent performance) and embedded into **ChromaDB** simultaneously.
- **Non-Blocking Behavior Ingestion**: `tracker.js` batches event data in memory and flushes every 5s or 20 events using `navigator.sendBeacon`.
- **Smart Trigger Engine**: Evaluates 6 trigger conditions (cold-start, high-intent, session event threshold, search signal, staleness, manual refresh) guarded by a 10-minute cooldown and behavior hash comparison to optimize API calls.
- **Self-Correction Refetch Loop**: The 5-node LangGraph StateGraph automatically evaluates recommendation relevance; if quality score < 60, it loops back up to 2 times to broaden search queries.
- **Cold-Start Fallback**: Users with < 3 interactions instantly receive top popular & trending courses without LLM delay.
- **Daily Digest Scheduler**: APScheduler runs daily at 9:00 AM, processing active users in batches of 10 and dispatching email digests.
- **Modern Glassmorphism UI**: High-aesthetic responsive dark-mode dashboard built with Tailwind CSS, marked.js markdown rendering, and live behavior tracking feedback.

---

## 🏗 System Architecture

For complete architectural details, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/AGENT.md](docs/AGENT.md), and [docs/DATA_MODEL.md](docs/DATA_MODEL.md).

```
aura-smartreco/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── core/           # Database (WAL), Security, Mesh LLM, Embeddings, VectorStore, Cache
│   ├── models/         # User, Product, Event, Recommendation, UserProfile
│   ├── services/       # Dual-Write Product, Trigger Engine, Recommendation, Auth, Email
│   ├── agent/          # LangGraph 5-node StateGraph + Refetch loop + Prompts
│   ├── routers/        # Auth, Products, Events, Recommendations, Admin
│   ├── scheduler/      # APScheduler Daily 9 AM Digest
│   ├── static/         # tracker.js, style.css
│   └── templates/      # Jinja2 HTML templates
├── docs/               # ARCHITECTURE.md, AGENT.md, DATA_MODEL.md
├── scripts/            # seed_data.py, create_admin.py
└── tests/              # test_dual_write.py, test_agent.py, test_api.py
```

---

## ⚡ Quickstart Setup Guide

### 1. Clone Repository & Setup Environment
```bash
git clone https://github.com/your-username/aura-smartreco.git
cd aura-smartreco
cp .env.example .env
```

### 2. Install Dependencies
```bash
make setup
# OR
pip install -r requirements.txt
```

### 3. Seed Database & Vector Store
```bash
make seed
# Populates SQLite and ChromaDB with 25+ courses via Mesh API embeddings
```

### 4. Run Application
```bash
make run
# App starts at http://localhost:8000
```

---

## 🧪 Running Automated Tests

Run the test suite covering dual-write sync, agent refetch logic, and API endpoints:
```bash
make test
# OR
pytest tests/ -v
```

---

## 🔒 Mesh API Compliance Statement

All generative text calls and vector embeddings in this project strictly use Mesh API (`https://api.meshapi.ai/v1`).
- `tencent/hy3`: Behavior Analysis, Evaluation/Reranking & AIDA Persuasive Copywriting Narrative
- `sentence-transformers/all-minilm-l6-v2`: Custom `MeshEmbeddingFunction` for ChromaDB vector search

---

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details. Hello
