"""Importing this package registers all Phase 0 baseline policies
(B0-B4) into somaos.broker.policy.POLICY_REGISTRY. Policy S lives here
too (WP-06) once implemented."""
from somaos.broker.policies import b0_full  # noqa: F401
from somaos.broker.policies import b1_window  # noqa: F401
from somaos.broker.policies import b2_rag  # noqa: F401
from somaos.broker.policies import b3_summarize  # noqa: F401
from somaos.broker.policies import b4_paging  # noqa: F401
