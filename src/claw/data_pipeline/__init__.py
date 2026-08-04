"""Versioned data contracts between Claw and external data frameworks."""

from .manifest import SilverDatasetManifest
from .episode_source import (
    EpisodeSourceError,
    load_episode_batch,
    load_episode_dataset_record,
)
from .collection import (
    TRAINING_COLLECTION_SCHEMA_VERSION,
    LocalAgentTrainingAdapter,
    TrainingEpisodeCollectionManifest,
    TrainingEpisodeCollectionResult,
    collect_local_training_episodes,
)
from .swe_bench_collection import collect_swe_bench_lite_dev_episode
from .gold import (
    AgentSFTGovernancePipeline,
    ClawRecordReader,
    GoldBuildResult,
    GoldDatasetManifest,
)
from .operators import (
    DomainDifficultyBalancer,
    FailureTaxonomyAnnotator,
    LeakageGuardOperator,
    QualityPolicy,
    ToolAlignmentValidator,
    TrajectoryQualityScorer,
)
from .schemas import (
    AGENT_TRAINING_RECORD_SCHEMA_VERSION,
    AgentTrainingRecord,
    AgentTrainingRecordError,
)
from .silver import SilverBuildResult, SilverDatasetBuilder, to_training_record

__all__ = [
    "AGENT_TRAINING_RECORD_SCHEMA_VERSION",
    "AgentTrainingRecord",
    "AgentTrainingRecordError",
    "AgentSFTGovernancePipeline",
    "ClawRecordReader",
    "TRAINING_COLLECTION_SCHEMA_VERSION",
    "DomainDifficultyBalancer",
    "EpisodeSourceError",
    "FailureTaxonomyAnnotator",
    "GoldBuildResult",
    "GoldDatasetManifest",
    "LeakageGuardOperator",
    "LocalAgentTrainingAdapter",
    "QualityPolicy",
    "SilverBuildResult",
    "SilverDatasetBuilder",
    "SilverDatasetManifest",
    "ToolAlignmentValidator",
    "TrajectoryQualityScorer",
    "TrainingEpisodeCollectionManifest",
    "TrainingEpisodeCollectionResult",
    "collect_local_training_episodes",
    "collect_swe_bench_lite_dev_episode",
    "load_episode_batch",
    "load_episode_dataset_record",
    "to_training_record",
]
