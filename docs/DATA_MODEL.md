# SmartReco 2026 — Data Models & Schemas

SmartReco 2026 utilizes a relational SQLite schema for structured data and a ChromaDB vector store schema for semantic course matching.

## 1. SQLite Relational Schema

### `users` Table
- `id` (INTEGER, Primary Key)
- `email` (VARCHAR, Unique, Indexed)
- `hashed_password` (VARCHAR)
- `full_name` (VARCHAR)
- `is_admin` (BOOLEAN)
- `created_at` (DATETIME)
- `updated_at` (DATETIME)

### `products` Table
- `id` (INTEGER, Primary Key)
- `title` (VARCHAR, Indexed)
- `category` (VARCHAR, Indexed)
- `level` (VARCHAR, Indexed)
- `price` (FLOAT)
- `rating` (FLOAT)
- `description` (TEXT)
- `tags` (JSON)
- `syllabus` (JSON)
- `metadata_json` (JSON)
- `needs_reindex` (BOOLEAN)
- `is_popular` (BOOLEAN)
- `is_trending` (BOOLEAN)

### `events` Table
- `id` (INTEGER, Primary Key)
- `user_id` (INTEGER, Foreign Key `users.id`, Indexed)
- `session_id` (VARCHAR, Indexed)
- `event_type` (VARCHAR, Indexed)
- `payload_json` (JSON)
- `idempotency_key` (VARCHAR, Unique, Indexed)
- `created_at` (DATETIME, Indexed)

### `recommendations` Table
- `id` (INTEGER, Primary Key)
- `user_id` (INTEGER, Foreign Key `users.id`, Indexed)
- `narrative` (TEXT)
- `product_ids_json` (JSON)
- `quality_score` (INTEGER)
- `trigger_reason` (VARCHAR)
- `is_active` (BOOLEAN)
- `refetch_count` (INTEGER)
- `metadata_json` (JSON)
- `created_at` (DATETIME, Indexed)

### `user_profiles` Table
- `id` (INTEGER, Primary Key)
- `user_id` (INTEGER, Foreign Key `users.id`, Unique)
- `interests_json` (JSON)
- `skill_level` (VARCHAR)
- `intent` (VARCHAR)
- `behavior_hash` (VARCHAR)
- `last_calculated_at` (DATETIME)

---

## 2. ChromaDB Vector Store Schema

- **Collection Name**: `products`
- **Embedding Function**: Custom `MeshEmbeddingFunction` calling `sentence-transformers/all-minilm-l6-v2` via `api.meshapi.ai/v1`.
- **Document Text Format**:
  `Title: {title}. Category: {category}. Level: {level}. Description: {description}. Tags: {tags}`
- **Metadata Fields**:
  - `product_id`: int
  - `title`: str
  - `category`: str
  - `level`: str
  - `price`: float
  - `rating`: float
  - `is_popular`: bool
  - `is_trending`: bool
