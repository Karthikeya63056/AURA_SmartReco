# SmartReco 2026 — System Architecture

SmartReco 2026 is an agentic, personalized course recommendation platform designed to turn user behavior (clicks, dwell time, searches, views) into real-time persuasive recommendation narratives.

```mermaid
flowchart TD
    subgraph Layer 1: Client & Ingestion
        A[Browser Client / tracker.js] -->|Batched sendBeacon / 5s| B[FastAPI Ingestion Endpoint]
    end

    subgraph Layer 2: Core Storage & Dual-Write
        B --> C[SQLite DB WAL Mode]
        C -->|Dual-Write Product Service| D[ChromaDB Vector Store]
        D -->|MeshEmbeddingFunction| E[Mesh API Gateway]
    end

    subgraph Layer 3: Smart Trigger Engine
        B --> F{Smart Trigger Engine}
        F -->|Cold-Start < 3 events| G[Popular/Trending Fallback]
        F -->|Condition Met| H[LangGraph Recommendation Agent]
        F -->|Cooldown / Unchanged| I[Serve Active Cached Rec]
    end

    subgraph Layer 4: LangGraph Agentic Workflow
        H --> N1[1. Analyze Behavior Node]
        N1 -->|gpt-4o-mini| N2[2. Retrieve Candidates Node]
        N2 -->|Chroma + Mesh Embedding| N3[3. Evaluate & Rerank Node]
        N3 -->|Quality Score < 60 & Count < 2| N4[Refetch Edge Broaden Query]
        N4 --> N2
        N3 -->|Quality Score >= 60| N5[4. Generate AIDA Narrative Node]
        N5 -->|gpt-4o| N6[5. Store & Invalidate Cache Node]
    end

    subgraph Layer 5: Presentation & Scheduler
        N6 --> J[Jinja2 + Tailwind Frontend Dashboard]
        K[APScheduler 9 AM Daily Digest] -->|Batch Size 10| H
    end
```

## Architectural Rationale

1. **Mesh API Gateway Compliance**:
   All LLM calls (`openai/gpt-4o-mini`, `openai/gpt-4o`) and Embedding queries/indexing (`openai/text-embedding-3-small`) pass strictly through `https://api.meshapi.ai/v1`.
2. **Dual-Write Integrity**:
   Product modifications insert/update SQLite via SQLAlchemy transactions first, followed by ChromaDB upsert via custom `MeshEmbeddingFunction`.
3. **Non-Blocking Client Tracking**:
   `tracker.js` batches events in-memory, flushing every 5 seconds or 20 events. `navigator.sendBeacon` guarantees delivery even when navigating across pages.
4. **Smart Triggering & Cooldowns**:
   Prevents unnecessary LLM costs by enforcing a 10-minute cooldown and behavior hash comparison, evaluating 6 trigger conditions before invoking the 5-node agent graph.
5. **Self-Correction Refetch Loop**:
   If candidate quality score evaluates below 60, the agent loops back up to 2 times, broadening search queries and parameter bounds.
