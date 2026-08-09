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
        "prerequisites": ["Python Basics", "RAG", "LLM APIs"],
        "skills_taught": ["LangGraph", "Multi-Agent Architectures", "State Machines"],
        "syllabus": ["StateGraph Architecture", "Conditional Edges & Refetch Loops", "Human-in-the-Loop", "Production Deployment"],
        "is_popular": True,
        "is_trending": True,
        "metadata_json": {
            "instructor_name": "Dr. Ananya Rao",
            "instructor_bio": "Ex-FAANG applied scientist specializing in multi-agent systems and production LangGraph deployments.",
            "learner_count": 18420,
            "duration_weeks": 6,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "Do I need prior LangGraph experience?", "a": "Basic Python and LLM API experience is enough; LangGraph is taught from first principles."},
                {"q": "Is this project-based?", "a": "Yes. You build a multi-agent workflow with conditional edges and a refetch loop."},
                {"q": "Does it cover Mesh API?", "a": "Yes. Chat and embedding calls are designed around an OpenAI-compatible Mesh gateway."},
            ],
        },
    },
    {
        "title": "Production RAG Architecture & Vector DB Optimization",
        "category": "AI & Agents",
        "level": "Intermediate",
        "price": 129.99,
        "rating": 4.8,
        "description": "Build high-accuracy Retrieval-Augmented Generation systems. Master hybrid search, reranking, ChromaDB, and query rewriting.",
        "tags": ["rag", "chromadb", "embeddings", "vector-search", "ai"],
        "prerequisites": ["Python Basics"],
        "skills_taught": ["RAG", "ChromaDB", "Vector Search"],
        "syllabus": ["Chunking Strategies", "Dense & Sparse Retrieval", "Cross-Encoder Reranking", "Evaluation Metrics"],
        "is_popular": True,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Marcus Chen",
            "instructor_bio": "Search infrastructure engineer focused on hybrid retrieval and evaluation-driven RAG.",
            "learner_count": 22150,
            "duration_weeks": 5,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "Which vector DB is used?", "a": "ChromaDB is primary, with patterns that transfer to other stores."},
                {"q": "Will I learn evaluation?", "a": "Yes — recall, precision, and groundedness-style checks for RAG pipelines."},
                {"q": "Is GPU required?", "a": "Not required for the core labs; optional for larger embedding batches."},
            ],
        },
    },
    {
        "title": "Generative AI Application Development with Python",
        "category": "AI & Agents",
        "level": "Beginner",
        "price": 89.99,
        "rating": 4.7,
        "description": "Comprehensive introduction to constructing GenAI powered web apps using Python, OpenAI/Mesh API, and modern frameworks.",
        "tags": ["python", "genai", "llm", "api", "beginner"],
        "prerequisites": [],
        "skills_taught": ["Python Basics", "LLM APIs", "Prompt Engineering"],
        "syllabus": ["Prompt Engineering Basics", "API Integration", "Streaming Responses", "Simple Chatbots"],
        "is_popular": True,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Priya Nair",
            "instructor_bio": "Educator and engineer helping beginners ship their first LLM-powered apps.",
            "learner_count": 31200,
            "duration_weeks": 4,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "Is this beginner friendly?", "a": "Yes. Assumes basic programming comfort, not prior ML research experience."},
                {"q": "Do I need a paid API key?", "a": "You need Mesh (or compatible) credentials for live LLM calls."},
                {"q": "What will I build?", "a": "A streaming chatbot and a small GenAI feature inside a Python app."},
            ],
        },
    },
    {
        "title": "MLOps Bootcamp: CI/CD, Model Monitoring & Kubernetes",
        "category": "MLOps & Cloud",
        "level": "Advanced",
        "price": 169.99,
        "rating": 4.9,
        "description": "Automate machine learning lifecycles with Docker, Kubernetes, MLflow, Prometheus, and automated model retrain pipelines.",
        "tags": ["mlops", "kubernetes", "docker", "ci-cd", "monitoring"],
        "prerequisites": ["Docker Basics", "Python Basics"],
        "skills_taught": ["MLOps", "Kubernetes", "Model Monitoring"],
        "syllabus": ["Pipeline Automation", "Model Drift Detection", "Container Orchestration", "Feature Stores"],
        "is_popular": False,
        "is_trending": True,
        "metadata_json": {
            "instructor_name": "Elena Volkov",
            "instructor_bio": "Platform engineer specializing in ML CI/CD, drift detection, and Kubernetes for models.",
            "learner_count": 9800,
            "duration_weeks": 8,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "Do I need a cloud account?", "a": "Local Docker/K8s is enough to start; cloud is optional for stretch labs."},
                {"q": "Is MLflow covered?", "a": "Yes — tracking, registry patterns, and promotion workflows."},
                {"q": "How advanced is Kubernetes content?", "a": "Intermediate-to-advanced; Docker basics are assumed."},
            ],
        },
    },
    {
        "title": "Full-Stack LLM Applications with FastAPI & React",
        "category": "Web Dev & AI",
        "level": "Intermediate",
        "price": 119.99,
        "rating": 4.8,
        "description": "Combine FastAPI backend services with React frontend user interfaces to build responsive real-time AI products.",
        "tags": ["fastapi", "react", "fullstack", "python", "javascript"],
        "prerequisites": ["Python Basics", "JavaScript Basics"],
        "skills_taught": ["FastAPI", "React UI", "REST APIs"],
        "syllabus": ["Async FastAPI Routes", "JWT Authentication", "WebSockets & Event Streams", "React UI Components"],
        "is_popular": True,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Jordan Hale",
            "instructor_bio": "Full-stack engineer shipping LLM products with FastAPI, React, and real-time UX.",
            "learner_count": 15640,
            "duration_weeks": 6,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "React experience required?", "a": "Basic JS/React helps; components are taught in context."},
                {"q": "Auth included?", "a": "Yes — JWT patterns suitable for AI product backends."},
                {"q": "Streaming responses?", "a": "Covered with async FastAPI and frontend consumption patterns."},
            ],
        },
    },
    {
        "title": "Deep Learning & Neural Networks Fundamentals",
        "category": "AI & Machine Learning",
        "level": "Beginner",
        "price": 79.99,
        "rating": 4.6,
        "description": "Understand core mathematics and PyTorch implementation of backpropagation, convolutional networks, and transformers.",
        "tags": ["pytorch", "deep-learning", "neural-networks", "math"],
        "prerequisites": ["Python Basics"],
        "skills_taught": ["PyTorch", "Neural Networks", "Deep Learning"],
        "syllabus": ["Tensors & Gradient Descent", "CNNs", "RNNs", "Introduction to Transformers"],
        "is_popular": False,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Sam Okonkwo",
            "instructor_bio": "ML educator focused on clear math-to-code paths in PyTorch.",
            "learner_count": 27400,
            "duration_weeks": 5,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "How much math is required?", "a": "High-school calculus and linear algebra intuition; formulas are derived step by step."},
                {"q": "GPU needed?", "a": "Helpful but not mandatory for the fundamentals labs."},
                {"q": "PyTorch only?", "a": "Yes — implementation focus is PyTorch."},
            ],
        },
    },
    {
        "title": "Advanced Prompt Engineering & Agent Design Patterns",
        "category": "AI & Agents",
        "level": "Intermediate",
        "price": 99.99,
        "rating": 4.9,
        "description": "Master Chain-of-Thought, Tree-of-Thought, ReAct framing, structured outputs, and guardrails for trustworthy AI systems.",
        "tags": ["prompt-engineering", "react", "structured-output", "guardrails"],
        "prerequisites": ["Prompt Engineering"],
        "skills_taught": ["ReAct Pattern", "Structured Outputs", "AI Guardrails"],
        "syllabus": ["Zero-shot & Few-shot Tactics", "ReAct Paradigm", "JSON Schema Output Control", "Safety Filters"],
        "is_popular": False,
        "is_trending": True,
        "metadata_json": {
            "instructor_name": "Nina Patel",
            "instructor_bio": "Applied LLM engineer working on agent patterns, structured outputs, and safety filters.",
            "learner_count": 14330,
            "duration_weeks": 3,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "Is this only prompting?", "a": "No — it connects prompting to agent loops, tools, and guardrails."},
                {"q": "Structured outputs?", "a": "Yes — JSON schema style control and validation patterns."},
                {"q": "Safety covered?", "a": "Includes practical filters and failure-mode thinking."},
            ],
        },
    },
    {
        "title": "Python for Data Science & Machine Learning Masterclass",
        "category": "Python & Data",
        "level": "Beginner",
        "price": 69.99,
        "rating": 4.8,
        "description": "Master NumPy, Pandas, Matplotlib, Seaborn, and Scikit-Learn for end-to-end data analysis and predictive modeling.",
        "tags": ["python", "pandas", "numpy", "scikit-learn", "data-science"],
        "prerequisites": [],
        "skills_taught": ["Python Basics", "Pandas & Data Analysis", "Scikit-Learn"],
        "syllabus": ["Data Cleaning & Wrangling", "Exploratory Data Analysis", "Supervised Learning", "Unsupervised Clustering"],
        "is_popular": True,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Chris Alvarez",
            "instructor_bio": "Data scientist and instructor specializing in practical Pandas and Scikit-Learn workflows.",
            "learner_count": 40120,
            "duration_weeks": 6,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "Absolute beginners OK?", "a": "Yes if you can write basic Python; the course builds the DS stack from there."},
                {"q": "Deep learning included?", "a": "Focus is classical ML with Scikit-Learn; DL is a later path."},
                {"q": "Projects?", "a": "Yes — EDA and supervised modeling mini-projects."},
            ],
        },
    },
    {
        "title": "Fine-Tuning Open Source LLMs: LLaMA & Mistral",
        "category": "AI & Agents",
        "level": "Advanced",
        "price": 189.99,
        "rating": 4.9,
        "description": "Learn LoRA, QLoRA, and Unsloth techniques to efficiently adapt open-source foundation models on custom domain datasets.",
        "tags": ["fine-tuning", "llama", "qlora", "huggingface", "gpu"],
        "prerequisites": ["PyTorch", "Deep Learning"],
        "skills_taught": ["LoRA & QLoRA", "LLM Fine-Tuning", "PEFT"],
        "syllabus": ["Dataset Preparation", "PEFT & LoRA Fundamentals", "Quantization", "Model Evaluation & Export"],
        "is_popular": False,
        "is_trending": True,
        "metadata_json": {
            "instructor_name": "Hana Suzuki",
            "instructor_bio": "LLM fine-tuning specialist focused on PEFT, QLoRA, and efficient training.",
            "learner_count": 7600,
            "duration_weeks": 5,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "GPU required?", "a": "Strongly recommended for hands-on fine-tuning labs."},
                {"q": "Which models?", "a": "LLaMA- and Mistral-family workflows with PEFT methods."},
                {"q": "Full fine-tune or LoRA?", "a": "Primarily LoRA/QLoRA for practical efficiency."},
            ],
        },
    },
    {
        "title": "Building Autonomous Multi-Agent Workflows",
        "category": "AI & Agents",
        "level": "Advanced",
        "price": 159.99,
        "rating": 4.9,
        "description": "Orchestrate agent swarms, consensus mechanisms, task allocation, and specialized subagents for complex workflows.",
        "tags": ["multi-agent", "agents", "langgraph", "orchestration"],
        "prerequisites": ["LangGraph", "Multi-Agent Architectures"],
        "skills_taught": ["Agent Swarms", "Consensus Protocols", "Hierarchical Agents"],
        "syllabus": ["Agent Communication Protocols", "Supervisors vs Hierarchical Swarms", "Tool Calling", "Resilience"],
        "is_popular": True,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Omar Farouk",
            "instructor_bio": "Systems designer for hierarchical multi-agent orchestration and tool-using agents.",
            "learner_count": 11250,
            "duration_weeks": 5,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "Is LangGraph required first?", "a": "Recommended. This course assumes graph/agent fundamentals."},
                {"q": "Production concerns?", "a": "Includes resilience, tool failures, and supervisor patterns."},
                {"q": "Single agent vs swarm?", "a": "Focus is multi-agent coordination, not single-chatbots."},
            ],
        },
    },
    {
        "title": "FastAPI Microservices Masterclass",
        "category": "Backend Dev",
        "level": "Intermediate",
        "price": 94.99,
        "rating": 4.7,
        "description": "Architect clean, scalable backend services with Pydantic v2, SQLAlchemy 2.0, Dependency Injection, and Async SQL.",
        "tags": ["fastapi", "backend", "python", "microservices", "async"],
        "prerequisites": ["Python Basics"],
        "skills_taught": ["FastAPI", "SQLAlchemy", "Async Python"],
        "syllabus": ["Dependency Injection", "Database Migrations", "API Versioning", "Rate Limiting & Security"],
        "is_popular": False,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Lisa Berg",
            "instructor_bio": "Backend architect teaching FastAPI, SQLAlchemy 2.0, and service boundaries.",
            "learner_count": 19880,
            "duration_weeks": 5,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "Async-first?", "a": "Yes — async routes and SQL patterns are core."},
                {"q": "Migrations covered?", "a": "Yes — practical migration workflows for evolving schemas."},
                {"q": "Auth?", "a": "Security and rate-limiting patterns are included."},
            ],
        },
    },
    {
        "title": "ChromaDB & Vector Store Deep Dive for AI Engineers",
        "category": "Database & AI",
        "level": "Intermediate",
        "price": 79.99,
        "rating": 4.8,
        "description": "Master indexing strategies, similarity metrics, metadata filtering, distance algorithms, and custom embedding functions.",
        "tags": ["chromadb", "vector-db", "embeddings", "similarity-search"],
        "prerequisites": ["Python Basics"],
        "skills_taught": ["ChromaDB", "Vector Search", "Embeddings"],
        "syllabus": ["HNSW Indexing", "Distance Metrics (Cosine vs L2)", "Metadata Filtering", "Persistent DB Maintenance"],
        "is_popular": False,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Dev Kapoor",
            "instructor_bio": "Vector search practitioner focused on Chroma, metrics, and embedding pipelines.",
            "learner_count": 8650,
            "duration_weeks": 3,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "Only Chroma?", "a": "Chroma-first, with concepts that map to other vector DBs."},
                {"q": "Custom embeddings?", "a": "Yes — wiring custom embedding functions is covered."},
                {"q": "Persistence?", "a": "Includes practical maintenance of persistent collections."},
            ],
        },
    },
    {
        "title": "Reinforcement Learning from Human Feedback (RLHF)",
        "category": "AI & Machine Learning",
        "level": "Advanced",
        "price": 199.99,
        "rating": 4.9,
        "description": "Master reward model training, PPO optimization, DPO (Direct Preference Optimization), and alignment techniques for LLMs.",
        "tags": ["rlhf", "dpo", "alignment", "ppo", "ai"],
        "prerequisites": ["PyTorch", "Deep Learning"],
        "skills_taught": ["RLHF", "DPO", "Reward Modeling"],
        "syllabus": ["Reward Modeling", "Policy Optimization", "Direct Preference Optimization", "Safety Alignment"],
        "is_popular": False,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Dr. Emily Frost",
            "instructor_bio": "Alignment researcher teaching RLHF, DPO, and practical preference optimization.",
            "learner_count": 5420,
            "duration_weeks": 7,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "Math heavy?", "a": "Moderate — intuition first, then formal updates for PPO/DPO."},
                {"q": "GPU required?", "a": "Yes for realistic preference-training experiments."},
                {"q": "DPO vs PPO?", "a": "Both are covered with when-to-use guidance."},
            ],
        },
    },
    {
        "title": "Modern JavaScript & TypeScript for AI Engineers",
        "category": "Web Dev",
        "level": "Beginner",
        "price": 59.99,
        "rating": 4.6,
        "description": "Learn ES6+, TypeScript types, async programming, Fetch API, and DOM manipulation to construct AI dashboard UIs.",
        "tags": ["javascript", "typescript", "frontend", "web"],
        "prerequisites": [],
        "skills_taught": ["JavaScript Basics", "TypeScript", "Frontend Dev"],
        "syllabus": ["Async/Await & Promises", "TypeScript Generics & Interfaces", "DOM Event Handling", "State Management"],
        "is_popular": False,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Alex Rivera",
            "instructor_bio": "Frontend engineer teaching JS/TS specifically for AI product dashboards.",
            "learner_count": 16790,
            "duration_weeks": 4,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "For ML engineers?", "a": "Yes — aimed at engineers who need solid UI skills for AI apps."},
                {"q": "React included?", "a": "Foundation is JS/TS/DOM; React is a natural next step."},
                {"q": "TypeScript strictness?", "a": "Practical types and interfaces, not academic type theory."},
            ],
        },
    },
    {
        "title": "Data Engineering Pipelines with Apache Airflow & Spark",
        "category": "Data Engineering",
        "level": "Advanced",
        "price": 139.99,
        "rating": 4.7,
        "description": "Build robust ETL pipelines, orchestrate workflows, process big data with PySpark, and manage data lakes.",
        "tags": ["data-engineering", "airflow", "spark", "etl", "python"],
        "prerequisites": ["Python Basics", "Pandas & Data Analysis"],
        "skills_taught": ["ETL Pipelines", "Apache Airflow", "PySpark"],
        "syllabus": ["DAG Design Principles", "PySpark Transformations", "Data Quality Auditing", "Cloud Lakehouses"],
        "is_popular": False,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Rachel Kim",
            "instructor_bio": "Data platform engineer specializing in Airflow orchestration and Spark ETL.",
            "learner_count": 12100,
            "duration_weeks": 7,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "Local setup possible?", "a": "Yes for core DAGs; cluster-scale Spark is optional/cloud."},
                {"q": "Pandas enough prerequisite?", "a": "Yes — plus comfort with Python scripting."},
                {"q": "Lakehouse topics?", "a": "Introduced as an architectural destination for pipelines."},
            ],
        },
    },
    {
        "title": "AI Security, Safety & Red Teaming for Enterprise",
        "category": "AI Security",
        "level": "Intermediate",
        "price": 149.99,
        "rating": 4.8,
        "description": "Protect GenAI applications against prompt injection, jailbreaks, data exfiltration, model poisoning, and privacy leaks.",
        "tags": ["security", "red-teaming", "prompt-injection", "ai-safety"],
        "prerequisites": ["LLM APIs"],
        "skills_taught": ["AI Security", "Red Teaming", "Prompt Injection Defense"],
        "syllabus": ["Indirect Prompt Injections", "Output Sanitization", "Vulnerability Scanning", "Compliance Standards"],
        "is_popular": False,
        "is_trending": True,
        "metadata_json": {
            "instructor_name": "Victor Lang",
            "instructor_bio": "Security engineer red-teaming LLM apps for prompt injection and data leakage.",
            "learner_count": 6900,
            "duration_weeks": 4,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "Hands-on attacks?", "a": "Yes — controlled labs on injection and exfiltration patterns."},
                {"q": "Enterprise compliance?", "a": "High-level mapping to common compliance expectations."},
                {"q": "Defenses included?", "a": "Sanitization, monitoring, and architectural mitigations."},
            ],
        },
    },
    {
        "title": "Async Python Programming & High-Performance AsyncIO",
        "category": "Python & Systems",
        "level": "Intermediate",
        "price": 84.99,
        "rating": 4.7,
        "description": "Unlock high throughput in Python using event loops, coroutines, tasks, queues, semaphores, and async HTTP clients.",
        "tags": ["python", "asyncio", "concurrency", "performance"],
        "prerequisites": ["Python Basics"],
        "skills_taught": ["Async Python", "AsyncIO", "Concurrency"],
        "syllabus": ["Event Loop Deep Dive", "Tasks vs Futures", "Async Generators", "Concurreny Pitfalls & Threadpools"],
        "is_popular": False,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Tomás Silva",
            "instructor_bio": "Python systems engineer focused on asyncio performance and concurrency pitfalls.",
            "learner_count": 13450,
            "duration_weeks": 3,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "Threading vs asyncio?", "a": "Yes — when to use threads, processes, or the event loop."},
                {"q": "HTTP clients?", "a": "Async HTTP patterns are included."},
                {"q": "Production pitfalls?", "a": "Cancellation, backpressure, and threadpool offloading are covered."},
            ],
        },
    },
    {
        "title": "Docker & Kubernetes for Machine Learning Engineers",
        "category": "MLOps & Cloud",
        "level": "Intermediate",
        "price": 109.99,
        "rating": 4.8,
        "description": "Containerize PyTorch/TensorFlow training scripts, manage GPU nodes, write Kubernetes manifests, and deploy services.",
        "tags": ["docker", "kubernetes", "containers", "mlops"],
        "prerequisites": [],
        "skills_taught": ["Docker Basics", "Kubernetes", "Containerization"],
        "syllabus": ["Multi-stage Dockerfiles", "K8s Deployments & Services", "GPU Passthrough", "Helm Charts"],
        "is_popular": False,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Grace Okada",
            "instructor_bio": "ML platform engineer teaching containers and Kubernetes for model workloads.",
            "learner_count": 15220,
            "duration_weeks": 5,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "ML-specific?", "a": "Yes — training jobs, GPU notes, and model service deploys."},
                {"q": "Helm included?", "a": "Introductory Helm chart usage is included."},
                {"q": "Local cluster?", "a": "Kind/minikube-style local workflows are supported."},
            ],
        },
    },
    {
        "title": "Computer Vision with PyTorch & OpenCV",
        "category": "AI & Vision",
        "level": "Intermediate",
        "price": 119.99,
        "rating": 4.7,
        "description": "Implement object detection, semantic segmentation, image generation, and pose estimation using modern vision models.",
        "tags": ["computer-vision", "pytorch", "opencv", "yolo"],
        "prerequisites": ["PyTorch"],
        "skills_taught": ["Computer Vision", "OpenCV", "YOLO"],
        "syllabus": ["Image Processing Operations", "YOLO Object Detection", "Segment Anything (SAM)", "Diffusion Models"],
        "is_popular": False,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Diego Morales",
            "instructor_bio": "CV engineer working on detection, segmentation, and practical OpenCV pipelines.",
            "learner_count": 11870,
            "duration_weeks": 6,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "YOLO versions?", "a": "Modern YOLO-style detection workflows are used in labs."},
                {"q": "GPU recommended?", "a": "Yes for training/finetuning; inference demos can be lighter."},
                {"q": "OpenCV vs deep models?", "a": "Both classical processing and deep models are covered."},
            ],
        },
    },
    {
        "title": "Natural Language Processing from Scratch with Transformers",
        "category": "AI & NLP",
        "level": "Advanced",
        "price": 149.99,
        "rating": 4.9,
        "description": "Build self-attention mechanisms, multi-head attention blocks, and GPT/BERT transformer architectures from raw Python.",
        "tags": ["nlp", "transformers", "attention", "pytorch", "deep-learning"],
        "prerequisites": ["PyTorch", "Deep Learning"],
        "skills_taught": ["Transformers", "Self-Attention", "NLP"],
        "syllabus": ["Tokenization Algorithms", "Self-Attention Mathematics", "Encoder-Decoder Architecture", "Pretraining"],
        "is_popular": False,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Dr. Leah Moss",
            "instructor_bio": "NLP researcher teaching transformers from attention math to pretraining loops.",
            "learner_count": 9340,
            "duration_weeks": 8,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "From scratch meaning?", "a": "Implement core pieces in PyTorch; not only HF one-liners."},
                {"q": "BERT and GPT?", "a": "Encoder and decoder-style stacks are both discussed."},
                {"q": "Math intensity?", "a": "High — expect attention equations with code."},
            ],
        },
    },
    {
        "title": "Building Production Recommendation Systems with AI",
        "category": "Recommendation AI",
        "level": "Advanced",
        "price": 159.99,
        "rating": 4.9,
        "description": "Design hybrid recommendation engines combining collaborative filtering, graph neural networks, vector similarity, and LLM re-ranking.",
        "tags": ["recommendation-system", "llm-reranking", "vector-search", "ai"],
        "prerequisites": ["Python Basics", "Vector Search"],
        "skills_taught": ["Recommendation Engines", "Collaborative Filtering", "LLM Reranking"],
        "syllabus": ["Candidate Generation", "Feature Engineering", "LLM Evaluation & Reranking", "A/B Testing"],
        "is_popular": True,
        "is_trending": True,
        "metadata_json": {
            "instructor_name": "Sofia Mendes",
            "instructor_bio": "RecSys engineer blending classical ranking with vector retrieval and LLM rerankers.",
            "learner_count": 8750,
            "duration_weeks": 6,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "Collaborative filtering included?", "a": "Yes — as part of hybrid candidate generation."},
                {"q": "LLM reranking?", "a": "Yes — evaluation and rerank stages are core modules."},
                {"q": "Online metrics?", "a": "A/B testing and feedback loops are covered."},
            ],
        },
    },
    {
        "title": "GraphQL & REST API Architecture for Enterprise",
        "category": "Backend Dev",
        "level": "Intermediate",
        "price": 89.99,
        "rating": 4.6,
        "description": "Design resilient RESTful APIs and GraphQL services with pagination, rate limiting, caching, and OpenAPI documentation.",
        "tags": ["rest", "graphql", "api-design", "backend"],
        "prerequisites": ["REST APIs"],
        "skills_taught": ["GraphQL", "API Design", "OpenAPI"],
        "syllabus": ["REST Best Practices", "GraphQL Schemas & Resolvers", "N+1 Problem Solutions", "OpenAPI Spec"],
        "is_popular": False,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Ben Carter",
            "instructor_bio": "API architect focused on REST/GraphQL tradeoffs and enterprise-scale design.",
            "learner_count": 10240,
            "duration_weeks": 4,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "REST or GraphQL?", "a": "Both — with guidance on when each fits."},
                {"q": "N+1 problem?", "a": "Yes — practical mitigations for GraphQL resolvers."},
                {"q": "OpenAPI?", "a": "Documenting and evolving APIs with OpenAPI is included."},
            ],
        },
    },
    {
        "title": "AI Product Management & Strategic Implementation",
        "category": "Product & AI",
        "level": "Beginner",
        "price": 99.99,
        "rating": 4.8,
        "description": "Bridge product strategy and artificial intelligence. Evaluate ROI, user experience patterns, risk mitigation, and launch roadmaps.",
        "tags": ["product-management", "ai-strategy", "ux", "business"],
        "prerequisites": [],
        "skills_taught": ["AI Strategy", "Product Management", "AI Metrics"],
        "syllabus": ["AI Product Lifecycle", "Defining Quality Metrics", "Evaluating AI Vendors", "User Feedback Loops"],
        "is_popular": False,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Maya Thompson",
            "instructor_bio": "AI product leader teaching metrics, risk, and go-to-market for ML features.",
            "learner_count": 14600,
            "duration_weeks": 3,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "Coding required?", "a": "No — strategic and product execution focus."},
                {"q": "Metrics?", "a": "Quality, UX, and business metrics for AI features."},
                {"q": "Vendor evaluation?", "a": "Yes — build vs buy and risk framing."},
            ],
        },
    },
    {
        "title": "Edge AI & On-Device Model Deployment",
        "category": "AI & Edge",
        "level": "Advanced",
        "price": 139.99,
        "rating": 4.7,
        "description": "Optimize and quantize models using ONNX Runtime, TensorRT, and llama.cpp for ultra-fast local inference on mobile and IoT.",
        "tags": ["edge-ai", "onnx", "quantization", "tensorrt"],
        "prerequisites": ["Deep Learning"],
        "skills_taught": ["Edge AI", "ONNX", "Quantization"],
        "syllabus": ["Model Pruning & Compression", "ONNX Conversion", "TensorRT Engine Build", "Local Microcontrollers"],
        "is_popular": False,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Kenji Sato",
            "instructor_bio": "Edge ML engineer specializing in quantization, ONNX, and on-device inference.",
            "learner_count": 4810,
            "duration_weeks": 5,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "Hardware needed?", "a": "A modern laptop is enough for many labs; device kits are optional."},
                {"q": "TensorRT?", "a": "Covered at a practical introduction level."},
                {"q": "LLMs on device?", "a": "Includes local LLM runtime patterns such as llama.cpp-style flows."},
            ],
        },
    },
    {
        "title": "Kubernetes & Helm Masterclass for DevOps",
        "category": "MLOps & Cloud",
        "level": "Advanced",
        "price": 129.99,
        "rating": 4.7,
        "description": "Master cluster setup, ingress controllers, persistent volumes, RBAC policies, and Helm chart deployment management.",
        "tags": ["kubernetes", "helm", "devops", "cloud"],
        "prerequisites": ["Docker Basics"],
        "skills_taught": ["Kubernetes", "Helm", "Cloud Infrastructure"],
        "syllabus": ["Cluster Architecture", "Ingress Controllers & TLS", "Helm Chart Templates", "Production Hardening"],
        "is_popular": False,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Irene Walsh",
            "instructor_bio": "DevOps engineer focused on production Kubernetes hardening and Helm packaging.",
            "learner_count": 13990,
            "duration_weeks": 6,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "Helm depth?", "a": "Templates, values, and release workflows are core."},
                {"q": "RBAC?", "a": "Yes — practical RBAC and hardening topics."},
                {"q": "Cloud vendor locked?", "a": "Concepts are vendor-neutral with portable manifests."},
            ],
        },
    },
    {
        "title": "Python Testing & Quality Assurance with Pytest",
        "category": "Python & Testing",
        "level": "Beginner",
        "price": 49.99,
        "rating": 4.8,
        "description": "Write reliable unit tests, integration tests, mock external APIs, generate coverage reports, and configure CI test workflows.",
        "tags": ["python", "pytest", "testing", "quality-assurance"],
        "prerequisites": ["Python Basics"],
        "skills_taught": ["Pytest", "Unit Testing", "CI Test Workflows"],
        "syllabus": ["Pytest Fixtures & Parametrization", "Mocking & Monkeypatching", "Coverage Analysis", "CI Integration"],
        "is_popular": False,
        "is_trending": False,
        "metadata_json": {
            "instructor_name": "Nora Quinn",
            "instructor_bio": "QA-focused Python engineer teaching pytest, mocks, and CI test pipelines.",
            "learner_count": 22340,
            "duration_weeks": 3,
            "language": "English",
            "certificate": True,
            "faq": [
                {"q": "Only unit tests?", "a": "Unit and integration patterns, plus CI wiring."},
                {"q": "Coverage tools?", "a": "Yes — reading and improving coverage reports."},
                {"q": "Mocks?", "a": "Monkeypatching and API mocks are included."},
            ],
        },
    },
]


def _enrich_existing_products(db: Session) -> int:
    """
    For DBs that already have products, fill missing display metadata
    (instructor, learners, duration, FAQ) matched by title.
    Returns number of rows updated.
    """
    updated = 0
    for course_data in COURSES_DATA:
        title = course_data.get("title")
        new_meta = course_data.get("metadata_json") or {}
        if not title or not new_meta:
            continue
        product = db.query(Product).filter(Product.title == title).first()
        if not product:
            continue
        current = product.metadata_json if isinstance(product.metadata_json, dict) else {}
        # Only fill keys that are missing so we don't clobber manual edits
        merged = dict(current)
        changed = False
        for key, value in new_meta.items():
            if key not in merged or merged.get(key) in (None, "", [], {}):
                merged[key] = value
                changed = True
        if changed:
            product.metadata_json = merged
            # Keep skills/prereqs/syllabus in sync if empty
            if not product.skills_taught and course_data.get("skills_taught"):
                product.skills_taught = course_data["skills_taught"]
            if not product.prerequisites and course_data.get("prerequisites"):
                product.prerequisites = course_data["prerequisites"]
            if not product.syllabus and course_data.get("syllabus"):
                product.syllabus = course_data["syllabus"]
            updated += 1
    if updated:
        db.commit()
    return updated


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
                is_admin=True,
            )
            db.add(admin_user)
            logger.info("Created default admin user: admin@smartreco.ai / admin123456")

        demo_user = db.query(User).filter(User.email == "demo@smartreco.ai").first()
        if not demo_user:
            demo_user = User(
                email="demo@smartreco.ai",
                full_name="Demo Learner",
                hashed_password=get_password_hash("demo123456"),
                is_admin=False,
            )
            db.add(demo_user)
            logger.info("Created default demo user: demo@smartreco.ai / demo123456")

        guest_user = db.query(User).filter(User.id == 2).first()
        if not guest_user:
            guest_user = User(
                id=2,
                email="guest@example.com",
                full_name="Guest Demo User",
                hashed_password=get_password_hash("guest123456"),
                is_admin=False,
            )
            db.add(guest_user)
            logger.info("Created default guest user ID 2: guest@example.com")

        db.commit()

        # Seed products if empty
        existing_count = db.query(Product).count()
        if existing_count == 0:
            logger.info(
                f"Seeding {len(COURSES_DATA)} courses into SQLite and ChromaDB "
                "(via Mesh API embeddings)..."
            )
            for course_data in COURSES_DATA:
                create_product(db, course_data)
            logger.info("Successfully seeded course catalog and dual-write vector store!")
        else:
            logger.info(
                f"Database already contains {existing_count} products. Skipping full product seed."
            )
            enriched = _enrich_existing_products(db)
            logger.info(
                f"Enriched metadata on {enriched} existing products "
                "(instructor, learners, duration, FAQ)."
            )

    except Exception as e:
        logger.error(f"Error seeding data: {str(e)}")
        db.rollback()
        raise e
    finally:
        db.close()


if __name__ == "__main__":
    seed()