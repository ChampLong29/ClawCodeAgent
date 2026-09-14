"""Independent benchmark execution, metrics, and four-group reporting."""

from .metrics import (
    BenchmarkEpisodeResult,
    bad_case_distribution,
    compute_metrics,
    cost_summary,
)
from .local_agent_adapter import LocalAgentBenchmarkAdapter
from .local_command import run_local_benchmark
from .pi_rpc_adapter import (
    PiDockerRpcClient,
    PiRpcAgent,
    PiRpcBenchmarkAdapter,
    PiRpcClient,
    PiRpcError,
)
from .pi_command import run_pi_benchmark
from .report import AblationReportGenerator
from .runtime_comparison import (
    RuntimeComparisonReportGenerator,
    load_benchmark_run,
)
from .runner import (
    ABLATION_GROUPS,
    BenchmarkConfig,
    BenchmarkError,
    BenchmarkRunResult,
    BenchmarkRunner,
    ModelAdapter,
)
from .swe_bench_lite_adapter import (
    LocalSweBenchLiteCalibrationRunner,
    MaterializedSweBenchLiteEpisodeTask,
    SweBenchLiteCandidateEvaluation,
    SweBenchLiteAgentTask,
    SweBenchLiteDevAdapter,
    SweBenchLiteEpisodeTaskMaterializer,
    SweBenchLiteEvaluationBundle,
    SweBenchLiteLocalCalibrationResult,
    SweBenchLiteTestExecution,
    evaluate_swe_bench_lite_candidate,
)
from .swe_bench_official import (
    OfficialSweBenchHarness,
    OfficialSweBenchHarnessConfig,
    OfficialSweBenchHarnessError,
    PreparedOfficialSweBenchRun,
)

__all__ = [
    "ABLATION_GROUPS",
    "AblationReportGenerator",
    "BenchmarkConfig",
    "BenchmarkEpisodeResult",
    "BenchmarkError",
    "BenchmarkRunResult",
    "BenchmarkRunner",
    "LocalAgentBenchmarkAdapter",
    "LocalSweBenchLiteCalibrationRunner",
    "MaterializedSweBenchLiteEpisodeTask",
    "ModelAdapter",
    "OfficialSweBenchHarness",
    "OfficialSweBenchHarnessConfig",
    "OfficialSweBenchHarnessError",
    "PreparedOfficialSweBenchRun",
    "PiRpcAgent",
    "PiRpcBenchmarkAdapter",
    "PiRpcClient",
    "PiDockerRpcClient",
    "PiRpcError",
    "RuntimeComparisonReportGenerator",
    "SweBenchLiteAgentTask",
    "SweBenchLiteCandidateEvaluation",
    "SweBenchLiteDevAdapter",
    "SweBenchLiteEpisodeTaskMaterializer",
    "SweBenchLiteEvaluationBundle",
    "SweBenchLiteLocalCalibrationResult",
    "SweBenchLiteTestExecution",
    "bad_case_distribution",
    "compute_metrics",
    "cost_summary",
    "evaluate_swe_bench_lite_candidate",
    "run_local_benchmark",
    "run_pi_benchmark",
    "load_benchmark_run",
]
