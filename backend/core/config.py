from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    PROJECT_NAME: str = "Browser Agent API"
    # Supabase/Postgres only — set in backend/.env, e.g.
    # postgresql+psycopg://postgres.<ref>:<password>@<region>.pooler.supabase.com:5432/postgres
    # SQLite is no longer supported (see core/database.py).
    DATABASE_URL: str = ""

    # Shared secret the extension must send (?token=...) to open the WebSocket.
    # Empty = no auth (fine for local dev). Set it in production so a public URL
    # can't be used by strangers to run up the LLM bill. NOTE: this token is
    # baked into the distributed extension build, so it deters casual/bot abuse
    # but is not a per-user secret.
    AGENT_TOKEN: str = ""

    # LLM provider selection: "nvidia" | "openai" | "gemini"
    LLM_PROVIDER: str = "nvidia"
    # 70B follows the plan-format rules and reasons through complex sites
    # (Spotify, YouTube) far more reliably than 8B, which looped and mis-
    # targeted on real tasks. Reverted to 70B for reliability; override with
    # LLM_MODEL in .env (e.g. meta/llama-3.1-8b-instruct) if you need speed.
    LLM_MODEL: str = "meta/llama-3.3-70b-instruct"
    NVIDIA_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    GEMINI_API_KEY: str = ""

    # Vision fallback (Phase 3): when a DOM action fails, a vision model
    # locates the target on a screenshot and the action is retried at those
    # coordinates. Provider: "nvidia" | "openai".
    # Enabled with Qwen2.5-VL-72B for accuracy: it locates elements far more
    # reliably than the small llama-3.2-11b-vision (which mislocated and
    # reported wrong clicks as success), and it natively uses the 0-1000 grid
    # the locate prompt asks for. Slower per call; for speed over accuracy,
    # switch VISION_MODEL back to meta/llama-3.2-11b-vision-instruct.
    VISION_ENABLED: bool = True
    VISION_PROVIDER: str = "nvidia"
    VISION_MODEL: str = "qwen/qwen2.5-vl-72b-instruct"

    # Agent loop limits
    MAX_PLAN_CYCLES: int = 8           # observe -> plan -> execute rounds per task
    MAX_ACTIONS_PER_PLAN: int = 5      # actions accepted from a single plan
    # A second LLM call checks whether the goal is already achieved and stops
    # the agent — catches planners that forget to set done=true. Costs one
    # extra LLM call, so it only runs from VERIFY_AFTER_CYCLE onward (the
    # planner sets done itself on simple/early cycles; the verifier is a
    # late-loop safety net, not an every-cycle tax).
    VERIFY_COMPLETION: bool = True
    VERIFY_AFTER_CYCLE: int = 1        # 0-indexed cycle to start verifying from
                                       # (low because the 8B planner can loop;
                                       # the verifier stops it early)
    # When the agent gets stuck repeating an action that keeps failing, it
    # abandons that approach and re-plans a different next step instead of
    # quitting. This caps how many such recoveries it attempts before it
    # really does stop.
    MAX_RECOVERY_ATTEMPTS: int = 2
    TOOL_TIMEOUT_SECONDS: float = 30.0
    CONFIRMATION_TIMEOUT_SECONDS: float = 120.0

    # Context budgets for the planner prompt
    SNAPSHOT_TEXT_CHARS: int = 1500
    SNAPSHOT_MAX_ELEMENTS: int = 60
    HISTORY_MESSAGES: int = 10

    LOG_LEVEL: str = "INFO"


settings = Settings()
