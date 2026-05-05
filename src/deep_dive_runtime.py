"""Technology deep-dive runtime — isolated agent sessions for tech research.

Each deep-dive query runs in a **separate** AgentSession so the research
context never pollutes the main development agent's context window.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .hook_policy import RuntimeBase
from .session_naming import make_session_id

if TYPE_CHECKING:
    from .agent_runtime import LocalCodingAgent


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DeepDiveQuery:
    """A single technology deep-dive query."""

    id: str                          # "dd-abc123"
    technology: str                  # "PostgreSQL"
    context: str = ""                # "proposed as primary database"
    result: Optional[str] = None     # Full Markdown analysis
    status: str = "pending"          # pending | in_progress | completed | failed
    agent_session_id: Optional[str] = None
    created_at: float = 0.0
    completed_at: Optional[float] = None

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "technology": self.technology,
            "context": self.context,
            "result": self.result,
            "status": self.status,
            "agent_session_id": self.agent_session_id,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DeepDiveQuery:
        return cls(
            id=data.get("id", ""),
            technology=data.get("technology", ""),
            context=data.get("context", ""),
            result=data.get("result"),
            status=data.get("status", "pending"),
            agent_session_id=data.get("agent_session_id"),
            created_at=data.get("created_at", 0.0),
            completed_at=data.get("completed_at"),
        )


@dataclass
class DeepDiveSession:
    """A deep-dive session containing multiple queries."""

    session_id: str
    parent_phase: str               # "ARCHITECTURE"
    parent_session_id: str          # lifecycle / devflow session ID
    queries: List[DeepDiveQuery] = field(default_factory=list)
    created_at: float = 0.0

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "parent_phase": self.parent_phase,
            "parent_session_id": self.parent_session_id,
            "queries": [q.to_dict() for q in self.queries],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DeepDiveSession:
        return cls(
            session_id=data.get("session_id", ""),
            parent_phase=data.get("parent_phase", ""),
            parent_session_id=data.get("parent_session_id", ""),
            queries=[DeepDiveQuery.from_dict(q)
                     for q in data.get("queries", [])],
            created_at=data.get("created_at", 0.0),
        )


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

DEEP_DIVE_PROMPT = """You are a technology expert. Provide a comprehensive analysis.

## Technology
{technology}

## Context
{context}

## Instructions

Provide a detailed analysis:

### 1. What is {technology}?
Clear explanation for someone unfamiliar with it.

### 2. Key Features and Capabilities
What it does well, what it's designed for.

### 3. Pros and Cons
- **Advantages**: Why it might be a good choice
- **Disadvantages**: Limitations, drawbacks, gotchas

### 4. Alternatives
Compare with 2-3 alternatives. For each: description, when to use, key trade-offs.

### 5. Best Practices
Common patterns, anti-patterns, recommendations.

### 6. Suitability for This Project
Based on the context: is this technology a good fit?

Output as well-formatted Markdown."""

# Technology name patterns for extraction from architecture output
TECH_PATTERNS = [
    # Language / framework names (capitalized mixed-case words)
    r'\b(React|Angular|Vue\.?js|Svelte|Next\.?js|Nuxt\.?js|FastAPI|Flask|Django|Express\.?js|Spring\s+Boot|Rails|Laravel)\b',
    # Databases and stores
    r'\b(PostgreSQL|MySQL|MariaDB|SQLite|MongoDB|Redis|Memcached|Cassandra|CockroachDB|DynamoDB|Elasticsearch|ClickHouse|TimescaleDB)\b',
    # Cloud / infra
    r'\b(Kubernetes|Docker|AWS\s+Lambda|AWS\s+ECS|AWS\s+RDS|AWS\s+S3|Google\s+Cloud|Azure|Terraform|Pulumi|Helm)\b',
    # Message queues / streaming
    r'\b(Kafka|RabbitMQ|NATS|Pulsar|Celery|Bull\.?MQ|Sidekiq)\b',
    # ORM / data
    r'\b(SQLAlchemy|Prisma|TypeORM|Diesel|Entity\s+Framework|Hibernate)\b',
    # GraphQL
    r'\b(GraphQL|Apollo|Relay|Hasura)\b',
    # Misc tech
    r'\b(gRPC|REST|WebSocket|SSE|OAuth|JWT|SAML|OpenID\s+Connect)\b',
    # AI / ML
    r'\b(PyTorch|TensorFlow|scikit-learn|LangChain|LlamaIndex|Hugging\s+Face|Ollama)\b',
    # Build / CI
    r'\b(GitHub\s+Actions|GitLab\s+CI|Jenkins|CircleCI|ArgoCD|Flux)\b',
]


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------

class DeepDiveRuntime(RuntimeBase):
    """Manages isolated deep-dive sessions for technology exploration.

    Creates a **separate** ``LocalCodingAgent`` and ``AgentSession`` for
    each query so the research context never pollutes the main
    development agent's context window.
    """

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.session: Optional[DeepDiveSession] = None
        self._sessions_dir = os.path.join(cwd, ".port_sessions", "deepdive")
        os.makedirs(self._sessions_dir, exist_ok=True)
        self._agent_factory: Optional[callable] = None  # type: ignore[assignment]

    def set_agent_factory(self, factory: callable) -> None:  # type: ignore[assignment]
        """Provide a callable that returns a fresh ``LocalCodingAgent``."""
        self._agent_factory = factory

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def start_session(
        self, parent_phase: str, parent_session_id: str
    ) -> DeepDiveSession:
        session_id = make_session_id(parent_phase, "dd")
        self.session = DeepDiveSession(
            session_id=session_id,
            parent_phase=parent_phase,
            parent_session_id=parent_session_id,
        )
        self.save()
        return self.session

    def save(self) -> None:
        if not self.session:
            return
        path = self._session_path(self.session.session_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.session.to_dict(), f, indent=2, ensure_ascii=False)

    def load(self, session_id: str) -> Optional[DeepDiveSession]:
        path = self._session_path(session_id)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            self.session = DeepDiveSession.from_dict(json.load(f))
        return self.session

    def _session_path(self, session_id: str) -> str:
        return os.path.join(self._sessions_dir, f"{session_id}.json")

    # ------------------------------------------------------------------
    # Technology extraction
    # ------------------------------------------------------------------

    @staticmethod
    def extract_technologies(text: str) -> List[str]:
        """Parse technology names from architecture / design output."""
        found: List[str] = []
        seen = set()
        for pattern in TECH_PATTERNS:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                name = m.group(1).strip()
                if name.lower() not in seen:
                    found.append(name)
                    seen.add(name.lower())
        return found

    # ------------------------------------------------------------------
    # Query management
    # ------------------------------------------------------------------

    def add_query(self, technology: str, context: str = "") -> DeepDiveQuery:
        if not self.session:
            raise RuntimeError("No deep-dive session. Call start_session() first.")

        qid = f"dd-{str(uuid.uuid4())[:6]}"
        query = DeepDiveQuery(
            id=qid, technology=technology, context=context or "",
        )
        self.session.queries.append(query)
        self.save()
        return query

    def execute_query(self, query_id: str) -> str:
        """Run a deep-dive agent for the query.

        Creates a new AgentSession and LocalCodingAgent for isolated
        execution.  Returns the agent's final message.
        """
        query = self._find_query(query_id)
        if not query:
            raise RuntimeError(f"Query '{query_id}' not found.")

        if self._agent_factory is None:
            raise RuntimeError(
                "No agent factory set. Call set_agent_factory() first."
            )

        query.status = "in_progress"
        self.save()

        try:
            agent = self._agent_factory()
            prompt = DEEP_DIVE_PROMPT.format(
                technology=query.technology,
                context=query.context or "Proposed as part of a software architecture.",
            )
            result = agent.run(prompt=prompt, stream=False)
            query.result = result.final_message or ""
            query.status = "completed"
            query.completed_at = time.time()
            if agent.session:
                query.agent_session_id = agent.session.session_id
        except Exception as e:
            query.result = f"Error: {e}"
            query.status = "failed"
            query.completed_at = time.time()

        self.save()
        return query.result or ""

    def get_result(self, query_id: str) -> Optional[str]:
        query = self._find_query(query_id)
        return query.result if query else None

    def cancel_query(self, query_id: str) -> None:
        query = self._find_query(query_id)
        if query and query.status in ("pending", "in_progress"):
            query.status = "failed"
            query.result = "Cancelled by user."
            self.save()

    def format_for_parent(self, query_id: Optional[str] = None) -> str:
        """Format completed deep-dive results as structured context.

        If *query_id* is given, only that query is included.  Otherwise
        all completed queries are summarised.
        """
        if not self.session:
            return ""

        parts = ["## Technology Deep-Dive Results\n"]
        for q in self.session.queries:
            if query_id and q.id != query_id:
                continue
            if q.status != "completed" or not q.result:
                continue
            # Truncate each result to a short summary
            summary = q.result
            if len(summary) > 600:
                summary = summary[:600] + "\n\n... [full result available: /deep-dive view " + q.id + "]"
            parts.append(f"### {q.technology}\n{summary}\n")

        return "\n".join(parts) if len(parts) > 1 else ""

    def _find_query(self, query_id: str) -> Optional[DeepDiveQuery]:
        if not self.session:
            return None
        for q in self.session.queries:
            if q.id == query_id:
                return q
        return None

    # ------------------------------------------------------------------
    # RuntimeBase
    # ------------------------------------------------------------------

    def get_state(self) -> Optional[Dict[str, Any]]:
        if not self.session:
            return None
        return self.session.to_dict()

    def render_summary(self) -> str:
        if not self.session:
            return "[DeepDive] No active session"
        completed = sum(1 for q in self.session.queries
                        if q.status == "completed")
        total = len(self.session.queries)
        return f"[DeepDive] {completed}/{total} queries complete"

    def get_prompt_guidance(self) -> str:
        return ""
