from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.app.models.entities import (
    CandidateIndustryExperience,
    CandidateProfile,
    CandidateSkill,
)
from apps.api.app.schemas.profile import ProfilePayload, ProfileResponse
from apps.api.app.services.errors import VersionConflictError
from apps.api.app.services.user_service import DEFAULT_USER_ID, ensure_default_user
from packages.job_parser.normalizers import normalize_skill


def get_profile(session: Session) -> ProfileResponse | None:
    profile = session.scalar(select(CandidateProfile).where(CandidateProfile.user_id == DEFAULT_USER_ID))
    return _response(profile) if profile else None


def save_profile(session: Session, payload: ProfilePayload) -> ProfileResponse:
    ensure_default_user(session)
    profile = session.scalar(select(CandidateProfile).where(CandidateProfile.user_id == DEFAULT_USER_ID))
    if profile is None:
        if payload.version is not None:
            raise VersionConflictError("新建资料时不应提供 version")
        profile = CandidateProfile(user_id=DEFAULT_USER_ID, name=payload.name)
        session.add(profile)
    elif payload.version != profile.version:
        raise VersionConflictError("候选人资料版本已变化")
    else:
        profile.version += 1
        profile.skills.clear()
        profile.industries.clear()
        session.flush()
    profile.name = payload.name
    profile.total_years = payload.total_years
    profile.management_years = payload.management_years
    profile.has_architecture_experience = payload.has_architecture_experience
    profile.has_core_system_experience = payload.has_core_system_experience
    profile.skills = [CandidateSkill(name=item.name, normalized_name=normalize_skill(item.name).lower(),
                                     years=item.years, proficiency=item.proficiency,
                                     source=item.source, is_core=item.is_core) for item in payload.skills]
    profile.industries = [CandidateIndustryExperience(industry_code=item.industry_code,
                                                      years=item.years, source=item.source)
                          for item in payload.industry_experiences]
    session.commit()
    session.refresh(profile)
    return _response(profile)


def _response(profile: CandidateProfile) -> ProfileResponse:
    return ProfileResponse(
        id=profile.id, name=profile.name, total_years=profile.total_years,
        management_years=profile.management_years,
        has_architecture_experience=profile.has_architecture_experience,
        has_core_system_experience=profile.has_core_system_experience, version=profile.version,
        skills=[{"name": item.name, "years": item.years, "proficiency": item.proficiency,
                 "source": item.source, "is_core": item.is_core} for item in profile.skills],
        industry_experiences=[{"industry_code": item.industry_code, "years": item.years,
                               "source": item.source} for item in profile.industries],
    )
