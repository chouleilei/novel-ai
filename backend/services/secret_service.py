from backend.config import get_settings


MISSING_ENCRYPTION_KEY_ERROR = "未配置 NOVEL_AI_MODEL_SECRET_ENCRYPTION_KEY，不能保存或读取项目级 API Key"
INVALID_ENCRYPTION_KEY_ERROR = "NOVEL_AI_MODEL_SECRET_ENCRYPTION_KEY 格式无效，应为 Fernet 32-byte urlsafe base64 key"
DECRYPTION_ERROR = "无法解密已保存的项目级 API Key，请检查 NOVEL_AI_MODEL_SECRET_ENCRYPTION_KEY 是否正确"


def has_configured_encryption_key() -> bool:
    return _get_encryption_key() is not None


def encrypt_model_api_key(plain: str) -> str:
    normalized = plain.strip()
    if not normalized:
        raise ValueError("API Key 不能为空")
    fernet = _build_fernet()
    return fernet.encrypt(normalized.encode("utf-8")).decode("utf-8")


def decrypt_model_api_key(ciphertext: str) -> str:
    normalized = ciphertext.strip()
    if not normalized:
        raise ValueError("缺少加密后的 API Key")
    fernet = _build_fernet()
    try:
        return fernet.decrypt(normalized.encode("utf-8")).decode("utf-8")
    except Exception as exc:
        invalid_token = _get_invalid_token_type()
        if invalid_token is not None and isinstance(exc, invalid_token):
            raise ValueError(DECRYPTION_ERROR) from exc
        raise


def _get_encryption_key() -> str | None:
    value = get_settings().model_secret_encryption_key
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _build_fernet():
    key = _get_encryption_key()
    if key is None:
        raise ValueError(MISSING_ENCRYPTION_KEY_ERROR)
    try:
        from cryptography.fernet import Fernet

        return Fernet(key.encode("utf-8"))
    except ImportError as exc:
        raise RuntimeError("缺少 cryptography 依赖，请先安装 backend/requirements.txt") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError(INVALID_ENCRYPTION_KEY_ERROR) from exc


def _get_invalid_token_type():
    try:
        from cryptography.fernet import InvalidToken
    except ImportError:
        return None
    return InvalidToken
