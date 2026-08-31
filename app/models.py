from typing import Optional

from pydantic import BaseModel, Field, SecretStr


class ProfileRequest(BaseModel):
    linkedin_url: str = Field(..., examples=["https://www.linkedin.com/in/someone/"])


class ExperienceItem(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    date_range: Optional[str] = None
    description: Optional[str] = None


class EducationItem(BaseModel):
    school: Optional[str] = None
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    date_range: Optional[str] = None


class CertificationItem(BaseModel):
    name: Optional[str] = None
    authority: Optional[str] = None
    date_range: Optional[str] = None


class LanguageItem(BaseModel):
    name: Optional[str] = None
    proficiency: Optional[str] = None


class ProfileResponse(BaseModel):
    public_identifier: Optional[str] = None
    profile_url: Optional[str] = None
    name: Optional[str] = None
    headline: Optional[str] = None
    location: Optional[str] = None
    about: Optional[str] = None
    profile_image_url: Optional[str] = None
    background_image_url: Optional[str] = None
    experience: list[ExperienceItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    certifications: list[CertificationItem] = Field(default_factory=list)
    languages: list[LanguageItem] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: str
    detail: str


class SessionHealthResponse(BaseModel):
    valid: bool
    detail: str


class SessionLoginRequest(BaseModel):
    linkedin_id: str = Field(..., min_length=3, max_length=320)
    password: SecretStr
    user_agent: str = Field(..., min_length=10, max_length=1000)


class SessionLoginResponse(BaseModel):
    authenticated: bool
    detail: str


class BatchProfileRequest(BaseModel):
    linkedin_urls: list[str] = Field(..., min_length=1)


class BatchProfileResultItem(BaseModel):
    linkedin_url: str
    profile: Optional[ProfileResponse] = None
    error: Optional[str] = None
    cached: bool = False


class BatchProfileResponse(BaseModel):
    results: list[BatchProfileResultItem]
