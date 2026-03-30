from pydantic import BaseModel, Field, model_validator

ALLOWED_PLATFORMS = {"zhihu", "xiaohongshu", "baijiahao"}


class GenerateRequest(BaseModel):
    topic: str = Field(..., min_length=2, max_length=200)
    platform: str | None = Field(default=None, min_length=2, max_length=32)
    # Backward compatibility for old clients. Prefer `platform`.
    platforms: list[str] | None = Field(default=None)
    enable_llm_stream: bool = Field(default=False)

    @model_validator(mode="after")
    def validate_single_platform(self) -> "GenerateRequest":
        normalized: str | None = None

        if self.platform:
            normalized = self.platform.strip().lower()

        if self.platforms is not None:
            cleaned = [x.strip().lower() for x in self.platforms if isinstance(x, str) and x.strip()]
            if len(cleaned) != 1:
                raise ValueError(
                    "Only one platform is allowed per request. Use platform='zhihu' (preferred) or platforms=['zhihu']."
                )
            if normalized and cleaned[0] != normalized:
                raise ValueError("platform and platforms[0] must be the same value when both are provided.")
            normalized = cleaned[0]

        if not normalized:
            raise ValueError("Missing platform. Use platform='zhihu'.")
        if normalized not in ALLOWED_PLATFORMS:
            allowed = ", ".join(sorted(ALLOWED_PLATFORMS))
            raise ValueError(f"Invalid platform '{normalized}'. Allowed values: {allowed}.")

        self.platform = normalized
        self.platforms = [normalized]
        return self


class TaskPlan(BaseModel):
    outline: list[str]
    keywords: list[str]
    visual_placeholders: list[str]
    platform_style: dict[str, str]
