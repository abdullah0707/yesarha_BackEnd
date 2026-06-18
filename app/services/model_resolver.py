from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.responses import AppError, ErrorCodes
from app.models.ai import AIModel


def resolve_model(db: Session, model_name: str | None = None) -> AIModel:
    """
    Resolve which model to use:
    - If model_name is given, look it up (must be active).
    - Otherwise use the model marked is_default=True.
    - If nothing is registered yet, fall back to settings.DEFAULT_MODEL
      with settings.OLLAMA_BASE_URL (keeps the system usable before seeding).
    """
    query = db.query(AIModel).filter(AIModel.status == "active")

    if model_name:
        model = query.filter(AIModel.name == model_name).first()
        if not model:
            raise AppError(ErrorCodes.MODEL_UNAVAILABLE, f"Model '{model_name}' not found or inactive", 404)
        return model

    model = query.filter(AIModel.is_default == True).first()  # noqa: E712
    if model:
        return model

    model = query.first()
    if model:
        return model

    # Fallback virtual model (not persisted) when registry is empty
    return AIModel(
        id=None,
        name=settings.DEFAULT_MODEL,
        version=None,
        type="general",
        status="active",
        is_default=True,
        endpoint_url=settings.OLLAMA_BASE_URL
    )
