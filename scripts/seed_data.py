import logging
from sqlalchemy.orm import Session
from app.core.database import Base, engine, SessionLocal
from app.models import User, Product
from app.core.security import get_password_hash
from app.services.product_service import create_product

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

COURSES_DATA = [
    {
        "title": "Master Class: Agentic AI Systems with LangGraph & LangChain",
        "category": "AI & Agents",
        "level": "Advanced",
        "price": 149.99,
        "rating": 4.9,
        "description": "Learn to design, evaluate, and deploy autonomous multi-agent state machines using LangGraph, Mesh API, and memory persistence.",
        "tags": ["langgraph", "langchain", "agents", "python", "ai"],
        "syllabus": ["StateGraph Architecture", "Conditional Edges & Refetch Loops", "Human-in-the-Loop", "Production Deployment"],
        "is_popular": True,
        "is_trending": True
    },
    {
        "title": "Production RAG Architecture & Vector DB Optimization",
        "category": "AI & Agents",
        "level": "Intermediate",
        "price": 129.99,
        "rating": 4.8,
        "description": "Build high-accuracy Retrieval-Augmented Generation systems. Master hybrid search, reranking, ChromaDB, and query rewriting.",
        "tags": ["rag", "chromadb", "embeddings", "vector-search", "ai"],
        "syllabus": ["Chunking Strategies", "Dense & Sparse Retrieval", "Cross-Encoder Reranking", "Evaluation Metrics"],
        "is_popular": True,
        "is_trending": False
    },
    {
        "title": "Generative AI Application Development with Python",
        "category": "AI & Agents",
        "level": "Beginner",
        "price": 89.99,
        "rating": 4.7,
        "description": "Comprehensive introduction to constructing GenAI powered web apps using Python, OpenAI/Mesh API, and modern frameworks.",
        "tags": ["python", "genai", "llm", "api", "beginner"],
        "syllabus": ["Prompt Engineering Basics", "API Integration", "Streaming Responses", "Simple Chatbots"],
        "is_popular": True,
        "is_trending": False
    },
    {
        "title": "MLOps Bootcamp: CI/CD, Model Monitoring & Kubernetes",
        "category": "MLOps & Cloud",
        "level": "Advanced",
        "price": 169.99,
        "rating": 4.9,
        "description": "Automate machine learning lifecycles with Docker, Kubernetes, MLflow, Prometheus, and automated model retrain pipelines.",
        "tags": ["mlops", "kubernetes", "docker", "ci-cd", "monitoring"],
        "syllabus": ["Pipeline Automation", "Model Drift Detection", "Container Orchestration", "Feature Stores"],
        "is_popular": False,
        "is_trending": True
    },
    {
        "title": "Full-Stack LLM Applications with FastAPI & React",
        "category": "Web Dev & AI",
        "level": "Intermediate",
        "price": 119.99,
        "rating": 4.8,
        "description": "Combine FastAPI backend services with React frontend user interfaces to build responsive real-time AI products.",
        "tags": ["fastapi", "react", "fullstack", "python", "javascript"],
        "syllabus": ["Async FastAPI Routes", "JWT Authentication", "WebSockets & Event Streams", "React UI Components"],
        "is_popular": True,
        "is_trending": False
    },
    {
        "title": "Deep Learning & Neural Networks Fundamentals",
        "category": "AI & Machine Learning",
        "level": "Beginner",
        "price": 79.99,
        "rating": 4.6,
        "description": "Understand core mathematics and PyTorch implementation of backpropagation, convolutional networks, and transformers.",
        "tags": ["pytorch", "deep-learning", "neural-networks", "math"],
        "syllabus": ["Tensors & Gradient Descent", "CNNs", "RNNs", "Introduction to Transformers"],
        "is_popular": False,
        "is_trending": False
    },
    {
        "title": "Advanced Prompt Engineering & Agent Design Patterns",
        "category": "AI & Agents",
        "level": "Intermediate",
        "price": 99.99,
        "rating": 4.9,
        "description": "Master Chain-of-Thought, Tree-of-Thought, ReAct framing, structured outputs, and guardrails for trustworthy AI systems.",
        "tags": ["prompt-engineering", "react", "structured-output", "guardrails"],
        "syllabus": ["Zero-shot & Few-shot Tactics", "ReAct Paradigm", "JSON Schema Output Control", "Safety Filters"],
        "is_popular": False,
        "is_trending": True
    },
    {
        "title": "Python for Data Science & Machine Learning Masterclass",
        "category": "Python & Data",
        "level": "Beginner",
        "price": 69.99,
        "rating": 4.8,
        "description": "Master NumPy, Pandas, Matplotlib, Seaborn, and Scikit-Learn for end-to-end data analysis and predictive modeling.",
        "tags": ["python", "pandas", "numpy", "scikit-learn", "data-science"],
        "syllabus": ["Data Cleaning & Wrangling", "Exploratory Data Analysis", "Supervised Learning", "Unsupervised Clustering"],
        "is_popular": True,
        "is_trending": False
    },
    {
        "title": "Fine-Tuning Open Source LLMs: LLaMA & Mistral",
        "category": "AI & Agents",
        "level": "Advanced",
        "price": 189.99,
        "rating": 4.9,
        "description": "Learn LoRA, QLoRA, and Unsloth techniques to efficiently adapt open-source foundation models on custom domain datasets.",
        "tags": ["fine-tuning", "llama", "qlora", "huggingface", "gpu"],
        "syllabus": ["Dataset Preparation", "PEFT & LoRA Fundamentals", "Quantization", "Model Evaluation & Export"],
        "is_popular": False,
        "is_trending": True
    },
    {
        "title": "Building Autonomous Multi-Agent Workflows",
        "category": "AI & Agents",
        "level": "Advanced",
        "price": 159.99,
        "rating": 4.9,
        "description": "Orchestrate agent swarms, consensus mechanisms, task allocation, and specialized subagents for complex workflows.",
        "tags": ["multi-agent", "agents", "langgraph", "orchestration"],
        "syllabus": ["Agent Communication Protocols", "Supervisors vs Hierarchical Swarms", "Tool Calling", "Resilience"],
        "is_popular": True,
        "is_trending": False
    },
    {
        "title": "FastAPI Microservices Masterclass",
        "category": "Backend Dev",
        "level": "Intermediate",
        "price": 94.99,
        "rating": 4.7,
        "description": "Architect clean, scalable backend services with Pydantic v2, SQLAlchemy 2.0, Dependency Injection, and Async SQL.",
        "tags": ["fastapi", "backend", "python", "microservices", "async"],
        "syllabus": ["Dependency Injection", "Database Migrations", "API Versioning", "Rate Limiting & Security"],
        "is_popular": False,
        "is_trending": False
    },
    {
        "title": "ChromaDB & Vector Store Deep Dive for AI Engineers",
        "category": "Database & AI",
        "level": "Intermediate",
        "price": 79.99,
        "rating": 4.8,
        "description": "Master indexing strategies, similarity metrics, metadata filtering, distance algorithms, and custom embedding functions.",
        "tags": ["chromadb", "vector-db", "embeddings", "similarity-search"],
        "syllabus": ["HNSW Indexing", "Distance Metrics (Cosine vs L2)", "Metadata Filtering", "Persistent DB Maintenance"],
        "is_popular": False,
        "is_trending": False
    },
    {
        "title": "Reinforcement Learning from Human Feedback (RLHF)",
        "category": "AI & Machine Learning",
        "level": "Advanced",
        "price": 199.99,
        "rating": 4.9,
        "description": "Master reward model training, PPO optimization, DPO (Direct Preference Optimization), and alignment techniques for LLMs.",
        "tags": ["rlhf", "dpo", "alignment", "ppo", "ai"],
        "syllabus": ["Reward Modeling", "Policy Optimization", "Direct Preference Optimization", "Safety Alignment"],
        "is_popular": False,
        "is_trending": False
    },
    {
        "title": "Modern JavaScript & TypeScript for AI Engineers",
        "category": "Web Dev",
        "level": "Beginner",
        "price": 59.99,
        "rating": 4.6,
        "description": "Learn ES6+, TypeScript types, async programming, Fetch API, and DOM manipulation to construct AI dashboard UIs.",
        "tags": ["javascript", "typescript", "frontend", "web"],
        "syllabus": ["Async/Await & Promises", "TypeScript Generics & Interfaces", "DOM Event Handling", "State Management"],
        "is_popular": False,
        "is_trending": False
    },
    {
        "title": "Data Engineering Pipelines with Apache Airflow & Spark",
        "category": "Data Engineering",
        "level": "Advanced",
        "price": 139.99,
        "rating": 4.7,
        "description": "Build robust ETL pipelines, orchestrate workflows, process big data with PySpark, and manage data lakes.",
        "tags": ["data-engineering", "airflow", "spark", "etl", "python"],
        "syllabus": ["DAG Design Principles", "PySpark Transformations", "Data Quality Auditing", "Cloud Lakehouses"],
        "is_popular": False,
        "is_trending": False
    },
    {
        "title": "AI Security, Safety & Red Teaming for Enterprise",
        "category": "AI Security",
        "level": "Intermediate",
        "price": 149.99,
        "rating": 4.8,
        "description": "Protect GenAI applications against prompt injection, jailbreaks, data exfiltration, model poisoning, and privacy leaks.",
        "tags": ["security", "red-teaming", "prompt-injection", "ai-safety"],
        "syllabus": ["Indirect Prompt Injections", "Output Sanitization", "Vulnerability Scanning", "Compliance Standards"],
        "is_popular": False,
        "is_trending": True
    },
    {
        "title": "Async Python Programming & High-Performance AsyncIO",
        "category": "Python & Systems",
        "level": "Intermediate",
        "price": 84.99,
        "rating": 4.7,
        "description": "Unlock high throughput in Python using event loops, coroutines, tasks, queues, semaphores, and async HTTP clients.",
        "tags": ["python", "asyncio", "concurrency", "performance"],
        "syllabus": ["Event Loop Deep Dive", "Tasks vs Futures", "Async Generators", "Concurreny Pitfalls & Threadpools"],
        "is_popular": False,
        "is_trending": False
    },
    {
        "title": "Docker & Kubernetes for Machine Learning Engineers",
        "category": "MLOps & Cloud",
        "level": "Intermediate",
        "price": 109.99,
        "rating": 4.8,
        "description": "Containerize PyTorch/TensorFlow training scripts, manage GPU nodes, write Kubernetes manifests, and deploy services.",
        "tags": ["docker", "kubernetes", "containers", "mlops"],
        "syllabus": ["Multi-stage Dockerfiles", "K8s Deployments & Services", "GPU Passthrough", "Helm Charts"],
        "is_popular": False,
        "is_trending": False
    },
    {
        "title": "Computer Vision with PyTorch & OpenCV",
        "category": "AI & Vision",
        "level": "Intermediate",
        "price": 119.99,
        "rating": 4.7,
        "description": "Implement object detection, semantic segmentation, image generation, and pose estimation using modern vision models.",
        "tags": ["computer-vision", "pytorch", "opencv", "yolo"],
        "syllabus": ["Image Processing Operations", "YOLO Object Detection", "Segment Anything (SAM)", "Diffusion Models"],
        "is_popular": False,
        "is_trending": False
    },
    {
        "title": "Natural Language Processing from Scratch with Transformers",
        "category": "AI & NLP",
        "level": "Advanced",
        "price": 149.99,
        "rating": 4.9,
        "description": "Build self-attention mechanisms, multi-head attention blocks, and GPT/BERT transformer architectures from raw Python.",
        "tags": ["nlp", "transformers", "attention", "pytorch", "deep-learning"],
        "syllabus": ["Tokenization Algorithms", "Self-Attention Mathematics", "Encoder-Decoder Architecture", "Pretraining"],
        "is_popular": False,
        "is_trending": False
    },
    {
        "title": "Building Production Recommendation Systems with AI",
        "category": "Recommendation AI",
        "level": "Advanced",
        "price": 159.99,
        "rating": 4.9,
        "description": "Design hybrid recommendation engines combining collaborative filtering, graph neural networks, vector similarity, and LLM re-ranking.",
        "tags": ["recommendation-system", "llm-reranking", "vector-search", "ai"],
        "syllabus": ["Candidate Generation", "Feature Engineering", "LLM Evaluation & Reranking", "A/B Testing"],
        "is_popular": True,
        "is_trending": True
    },
    {
        "title": "GraphQL & REST API Architecture for Enterprise",
        "category": "Backend Dev",
        "level": "Intermediate",
        "price": 89.99,
        "rating": 4.6,
        "description": "Design resilient RESTful APIs and GraphQL services with pagination, rate limiting, caching, and OpenAPI documentation.",
        "tags": ["rest", "graphql", "api-design", "backend"],
        "syllabus": ["REST Best Practices", "GraphQL Schemas & Resolvers", "N+1 Problem Solutions", "OpenAPI Spec"],
        "is_popular": False,
        "is_trending": False
    },
    {
        "title": "AI Product Management & Strategic Implementation",
        "category": "Product & AI",
        "level": "Beginner",
        "price": 99.99,
        "rating": 4.8,
        "description": "Bridge product strategy and artificial intelligence. Evaluate ROI, user experience patterns, risk mitigation, and launch roadmaps.",
        "tags": ["product-management", "ai-strategy", "ux", "business"],
        "syllabus": ["AI Product Lifecycle", "Defining Quality Metrics", "Evaluating AI Vendors", "User Feedback Loops"],
        "is_popular": False,
        "is_trending": False
    },
    {
        "title": "Edge AI & On-Device Model Deployment",
        "category": "AI & Edge",
        "level": "Advanced",
        "price": 139.99,
        "rating": 4.7,
        "description": "Optimize and quantize models using ONNX Runtime, TensorRT, and llama.cpp for ultra-fast local inference on mobile and IoT.",
        "tags": ["edge-ai", "onnx", "quantization", "tensorrt"],
        "syllabus": ["Model Pruning & Compression", "ONNX Conversion", "TensorRT Engine Build", "Local Microcontrollers"],
        "is_popular": False,
        "is_trending": False
    },
    {
        "title": "Kubernetes & Helm Masterclass for DevOps",
        "category": "MLOps & Cloud",
        "level": "Advanced",
        "price": 129.99,
        "rating": 4.7,
        "description": "Master cluster setup, ingress controllers, persistent volumes, RBAC policies, and Helm chart deployment management.",
        "tags": ["kubernetes", "helm", "devops", "cloud"],
        "syllabus": ["Cluster Architecture", "Ingress Controllers & TLS", "Helm Chart Templates", "Production Hardening"],
        "is_popular": False,
        "is_trending": False
    },
    {
        "title": "Python Testing & Quality Assurance with Pytest",
        "category": "Python & Testing",
        "level": "Beginner",
        "price": 49.99,
        "rating": 4.8,
        "description": "Write reliable unit tests, integration tests, mock external APIs, generate coverage reports, and configure CI test workflows.",
        "tags": ["python", "pytest", "testing", "quality-assurance"],
        "syllabus": ["Pytest Fixtures & Parametrization", "Mocking & Monkeypatching", "Coverage Analysis", "CI Integration"],
        "is_popular": False,
        "is_trending": False
    }
]


def seed():
    """Seed SQLite database and ChromaDB vector store."""
    logger.info("Initializing database tables...")
    Base.metadata.create_all(bind=engine)

    db: Session = SessionLocal()
    try:
        # Check existing users
        admin_user = db.query(User).filter(User.email == "admin@smartreco.ai").first()
        if not admin_user:
            admin_user = User(
                email="admin@smartreco.ai",
                full_name="SmartReco Admin",
                hashed_password=get_password_hash("admin123456"),
                is_admin=True
            )
            db.add(admin_user)
            logger.info("Created default admin user: admin@smartreco.ai / admin123456")

        demo_user = db.query(User).filter(User.email == "demo@smartreco.ai").first()
        if not demo_user:
            demo_user = User(
                email="demo@smartreco.ai",
                full_name="Demo Learner",
                hashed_password=get_password_hash("demo123456"),
                is_admin=False
            )
            db.add(demo_user)
            logger.info("Created default demo user: demo@smartreco.ai / demo123456")

        db.commit()

        # Seed products if empty
        existing_count = db.query(Product).count()
        if existing_count == 0:
            logger.info(f"Seeding {len(COURSES_DATA)} courses into SQLite and ChromaDB (via Mesh API embeddings)...")
            for course_data in COURSES_DATA:
                create_product(db, course_data)
            logger.info("Successfully seeded course catalog and dual-write vector store!")
        else:
            logger.info(f"Database already contains {existing_count} products. Skipping product seeding.")

    except Exception as e:
        logger.error(f"Error seeding data: {str(e)}")
        db.rollback()
        raise e
    finally:
        db.close()


if __name__ == "__main__":
    seed()
