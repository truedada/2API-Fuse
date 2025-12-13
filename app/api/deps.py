from fastapi import Depends
from app.repositories.channel import ChannelRepository
from app.repositories.platform import PlatformRepository
from app.services.admin import AdminService
from app.services.geminicli_auth import GeminiCliAuthService
from app.services.antigravity_auth import AntigravityAuthService
from app.repositories.apikey import ApiKeyRepository
from app.repositories.usage_log import UsageLogRepository
def get_channel_repo() -> ChannelRepository:
    return ChannelRepository()

def get_apikey_repo() -> ApiKeyRepository:
    return ApiKeyRepository()
def get_platform_repo() -> PlatformRepository:
    return PlatformRepository()
def get_usage_log_repo() -> UsageLogRepository:
    return UsageLogRepository()
def get_admin_service(
    channel_repo: ChannelRepository = Depends(get_channel_repo),
    platform_repo: PlatformRepository = Depends(get_platform_repo),
    apikey_repo: ApiKeyRepository = Depends(get_apikey_repo),
    usage_log_repo : UsageLogRepository = Depends(get_usage_log_repo)
    
) -> AdminService:
    return AdminService(channel_repo, platform_repo, apikey_repo, usage_log_repo)


def get_geminicli_auth_service(
    channel_repo: ChannelRepository = Depends(get_channel_repo),
    platform_repo: PlatformRepository = Depends(get_platform_repo),
) -> GeminiCliAuthService:
    return GeminiCliAuthService(channel_repo, platform_repo)


# Dependency Helper
def get_antigravity_service(
    channel_repo: ChannelRepository = Depends(get_channel_repo),
    platform_repo: PlatformRepository = Depends(get_platform_repo),
) -> AntigravityAuthService:
    return AntigravityAuthService(channel_repo, platform_repo)