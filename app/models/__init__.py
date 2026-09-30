"""SQLAlchemy ORM models."""

from app.models.enums import AvailabilityType
from app.models.genre import Genre, movie_genres, tv_show_genres
from app.models.language import Language, movie_languages, tv_show_languages
from app.models.movie import Movie
from app.models.content_safety import ContentSafetyAudit
from app.models.community_rating import CommunityRating, User, UserAuthToken
from app.models.discovery import MovieDiscoveryCandidate, MovieDiscoveryRun
from app.models.movie_metadata import (
    AlternativeTitle,
    ExternalId,
    Keyword,
    MovieCredit,
    MovieImage,
    MovieTrailer,
    MovieKeyword,
    MovieProductionCompany,
    MovieProductionCountry,
    MovieRating,
    MovieReleaseDate,
    Person,
    ProductionCompany,
    ProductionCountry,
)
from app.models.movie_ott import MovieOtt
from app.models.ott_availability import OttAvailability
from app.models.ott_platform import OttPlatform
from app.models.tv_show import TVShow
from app.models.tv_show_ott import TVShowOtt
from app.models.operations import AdminAuditLog, BackfillRecord, ContactRequest, DataQualityIssue, MaintenanceWindow, MovieComment, MovieRequest, NotificationLog, OperationState, OttEvidence, OttSourceRelease, RequestNotificationDelivery
from app.models.research import RequestCommunication, ResearchRun
from app.models.ott_intelligence import (
    OttAvailabilityObservation,
    OttGoldSetCase,
    OttProviderBudgetPeriod,
    OttProviderHealth,
    OttReconciliationDecision,
)
from app.models.pinterest import PinterestBoard, PinterestPin, PinterestOAuthToken
from app.models.editorial import EditorialArticle, MovieCorrection
from app.models.ai_research_audit import (
    AIResearchAuditRun,
    AIResearchEvent,
    AIResearchMovieActivity,
    AIResearchSource,
)
from app.models.support import (
    DiscordInteractionReceipt,
    SupportAuditLog,
    SupportContribution,
    SupportProfile,
)

__all__ = [
    "AvailabilityType",
    "Genre",
    "Language",
    "Movie",
    "ContentSafetyAudit",
    "CommunityRating",
    "User",
    "UserAuthToken",
    "MovieDiscoveryCandidate",
    "MovieDiscoveryRun",
    "AlternativeTitle",
    "ExternalId",
    "Keyword",
    "MovieCredit",
    "MovieImage",
    "MovieTrailer",
    "MovieKeyword",
    "MovieProductionCompany",
    "MovieProductionCountry",
    "MovieRating",
    "MovieReleaseDate",
    "Person",
    "ProductionCompany",
    "ProductionCountry",
    "MovieOtt",
    "OttAvailability",
    "OttPlatform",
    "TVShow",
    "TVShowOtt",
    "movie_genres",
    "movie_languages",
    "tv_show_genres",
    "tv_show_languages",
    "AdminAuditLog", "BackfillRecord", "ContactRequest", "MaintenanceWindow", "MovieComment", "MovieRequest", "OttEvidence", "OttSourceRelease", "DataQualityIssue", "NotificationLog", "OperationState", "RequestNotificationDelivery",
    "OttAvailabilityObservation", "OttReconciliationDecision", "OttProviderBudgetPeriod",
    "OttProviderHealth", "OttProviderCache", "OttGoldSetCase",
    "ResearchRun", "RequestCommunication",
    "PinterestBoard", "PinterestPin", "PinterestOAuthToken",
    "EditorialArticle", "MovieCorrection",
    "AIResearchAuditRun", "AIResearchMovieActivity", "AIResearchEvent", "AIResearchSource",
    "DiscordInteractionReceipt", "SupportAuditLog", "SupportContribution", "SupportProfile",
]
