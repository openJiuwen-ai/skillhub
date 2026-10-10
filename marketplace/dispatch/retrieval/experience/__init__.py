"""Experience-based knowledge base for fast-path skill retrieval.

Non-invasive wrapper: wraps the existing Retriever and adds an experience
cache layer before the progressive tree search.
"""

__all__ = [
    "ExperienceBank",
    "ExperienceRetriever",
    "ExperienceAwareRetriever",
    "EmbeddingClient",
    "SkillKnowledgeBuilder",
    "TraceRecord",
    "DistilledPattern",
    "TraceDistiller",
    "cluster_traces",
    "ClusteredQuery",
]

from .bank import ExperienceBank
from .retriever import ExperienceRetriever
from .collector import SkillKnowledgeBuilder
from .wrapper import ExperienceAwareRetriever
from .models import TraceRecord, DistilledPattern
from .cluster import cluster_traces, ClusteredQuery
from .distiller import TraceDistiller
from .embed import EmbeddingClient
