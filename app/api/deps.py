from fastapi import Depends
from app.repositories.channel import ChannelRepository
from app.repositories.platform import PlatformRepository
from app.services.admin import AdminService
from app.services.google_auth import GoogleAuthService
from app.repositories.apikey import ApiKeyRepository
def get_channel_repo() -> ChannelRepository:
    return ChannelRepository()

def get_apikey_repo() -> ApiKeyRepository:
    return ApiKeyRepository()
def get_platform_repo() -> PlatformRepository:
    return PlatformRepository()

def get_admin_service(
    channel_repo: ChannelRepository = Depends(get_channel_repo),
    platform_repo: PlatformRepository = Depends(get_platform_repo),
    apikey_repo: ApiKeyRepository = Depends(get_apikey_repo),
) -> AdminService:
    return AdminService(channel_repo, platform_repo, apikey_repo)


def get_google_auth_service(
    channel_repo: ChannelRepository = Depends(get_channel_repo),
    platform_repo: PlatformRepository = Depends(get_platform_repo),
) -> GoogleAuthService:
    return GoogleAuthService(channel_repo, platform_repo)