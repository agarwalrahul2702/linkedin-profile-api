"""Flatten LinkedIn's normalized Voyager profile graph into our API schema."""

from typing import Any, Optional


def _by_type(included: list[dict], type_suffix: str) -> list[dict]:
    return [
        item
        for item in included
        if isinstance(item, dict)
        and isinstance(item.get("$type"), str)
        and item["$type"].endswith(type_suffix)
    ]


def _text(value: Any) -> Optional[str]:
    """Unwrap Voyager AttributedText while still accepting legacy strings."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return value["text"]
    return None


def _entity_index(included: list[dict]) -> dict[str, dict]:
    return {
        item["entityUrn"]: item
        for item in included
        if isinstance(item, dict) and isinstance(item.get("entityUrn"), str)
    }


def _find_target_profile_urn(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "*elements" and isinstance(child, list):
                for candidate in child:
                    if isinstance(candidate, str) and candidate.startswith(
                        "urn:li:fsd_profile:"
                    ):
                        return candidate
            found = _find_target_profile_urn(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_target_profile_urn(child)
            if found:
                return found
    return None


def _referenced_entities(
    profile: dict,
    index: dict[str, dict],
    reference_fragments: tuple[str, ...],
    type_suffix: str,
) -> list[dict]:
    """Walk the target profile's URN graph instead of mixing global entities."""
    seeds = [
        value
        for key, value in profile.items()
        if key.startswith("*")
        and any(fragment.lower() in key.lower() for fragment in reference_fragments)
    ]
    found: list[dict] = []
    seen_urns: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            if value in seen_urns:
                return
            seen_urns.add(value)
            entity = index.get(value)
            if entity is not None:
                walk(entity)
            return
        if isinstance(value, list):
            for child in value:
                walk(child)
            return
        if not isinstance(value, dict):
            return

        entity_type = value.get("$type")
        if isinstance(entity_type, str) and entity_type.endswith(type_suffix):
            found.append(value)
            return

        for key, child in value.items():
            if key.startswith("*") or key == "elements":
                walk(child)

    walk(seeds)
    return found


def _resolved_text_from_reference(
    value: Any, index: dict[str, dict], *field_names: str
) -> Optional[str]:
    if isinstance(value, str):
        value = index.get(value)
    if not isinstance(value, dict):
        return None
    for field_name in field_names:
        text = _text(value.get(field_name))
        if text:
            return text
    return None


def _date_range_to_str(date_range: Optional[dict]) -> Optional[str]:
    if not date_range:
        return None

    def fmt(date: Optional[dict]) -> Optional[str]:
        if not date:
            return None
        month = date.get("month")
        year = date.get("year")
        if year and month:
            return f"{month:02d}/{year}"
        if year:
            return str(year)
        return None

    start = fmt(date_range.get("start") or date_range.get("startDate"))
    end = fmt(date_range.get("end") or date_range.get("endDate"))
    if start and end:
        return f"{start} - {end}"
    if start:
        return f"{start} - Present"
    return end


def _vector_image_url(value: Any) -> Optional[str]:
    if not isinstance(value, dict):
        return None

    vector_image = value.get("vectorImage")
    if not isinstance(vector_image, dict):
        display_reference = value.get("displayImageReference")
        if isinstance(display_reference, dict):
            vector_image = display_reference.get("vectorImage")
    if not isinstance(vector_image, dict):
        return None

    root_url = vector_image.get("rootUrl")
    artifacts = vector_image.get("artifacts") or []
    if not root_url or not artifacts:
        return None

    valid_artifacts = [artifact for artifact in artifacts if isinstance(artifact, dict)]
    if not valid_artifacts:
        return None
    best = max(
        valid_artifacts,
        key=lambda artifact: (artifact.get("width") or 0)
        * (artifact.get("height") or 0),
    )
    segment = best.get("fileIdentifyingUrlPathSegment", "")
    return f"{root_url}{segment}" if segment else None


def _first_image_url(*values: Any) -> Optional[str]:
    for value in values:
        url = _vector_image_url(value)
        if url:
            return url
    return None


def _profile_score(profile: dict) -> int:
    fields = ("firstName", "lastName", "headline", "summary", "publicIdentifier")
    return sum(profile.get(field) is not None for field in fields)


def _profile_location(profile: dict, index: dict[str, dict]) -> Optional[str]:
    direct = _text(profile.get("geoLocationName")) or _text(profile.get("locationName"))
    if direct:
        return direct

    geo_location = profile.get("geoLocation") or {}
    geo_reference = (
        geo_location.get("geoUrn") if isinstance(geo_location, dict) else None
    )
    geo_reference = geo_reference or profile.get("*geoLocation")
    return _resolved_text_from_reference(
        geo_reference, index, "defaultLocalizedName", "name"
    )


def _elements(value: Any) -> list[dict]:
    """Read LinkedIn's standard REST.li collection wrapper."""
    if not isinstance(value, dict):
        return []
    elements = value.get("elements")
    if not isinstance(elements, list):
        return []
    return [element for element in elements if isinstance(element, dict)]


def _parse_standard_profile(raw: dict) -> Optional[dict]:
    """Parse FullProfileWithEntities-109's non-normalized elements[] shape."""
    top_level = raw.get("elements")
    if not isinstance(top_level, list):
        return None
    profile = next((item for item in top_level if isinstance(item, dict)), None)
    if profile is None:
        return None

    positions = []
    for group in _elements(profile.get("profilePositionGroups")):
        positions.extend(
            _elements(group.get("profilePositionInPositionGroup"))
        )
    # Some decoration revisions expose an ungrouped collection.
    positions.extend(_elements(profile.get("profilePositions")))

    educations = _elements(profile.get("profileEducations"))
    skills = _elements(profile.get("profileSkills"))
    certifications = _elements(profile.get("profileCertifications"))
    languages = _elements(profile.get("profileLanguages"))

    public_identifier = _text(profile.get("publicIdentifier"))
    full_name = (
        " ".join(
            part
            for part in (
                _text(profile.get("firstName")),
                _text(profile.get("lastName")),
            )
            if part
        ).strip()
        or None
    )
    location = (
        _text(profile.get("geoLocationName"))
        or _text(profile.get("locationName"))
        or _text(profile.get("geoCountryName"))
    )

    return {
        "public_identifier": public_identifier,
        "profile_url": (
            f"https://www.linkedin.com/in/{public_identifier}/"
            if public_identifier
            else None
        ),
        "name": full_name,
        "headline": _text(profile.get("headline")),
        "location": location,
        "about": _text(profile.get("summary")),
        "profile_image_url": _first_image_url(
            profile.get("profilePicture"), profile.get("picture")
        ),
        "background_image_url": _first_image_url(
            profile.get("backgroundPicture"), profile.get("backgroundImage")
        ),
        "experience": [
            {
                "title": _text(position.get("title")),
                "company": _text(position.get("companyName"))
                or _text((position.get("company") or {}).get("name"))
                if isinstance(position.get("company") or {}, dict)
                else _text(position.get("companyName")),
                "location": _text(position.get("locationName"))
                or _text(position.get("geoLocationName")),
                "date_range": _date_range_to_str(
                    position.get("timePeriod") or position.get("dateRange")
                ),
                "description": _text(position.get("description")),
            }
            for position in positions
        ],
        "education": [
            {
                "school": _text(education.get("schoolName")),
                "degree": _text(education.get("degreeName")),
                "field_of_study": _text(education.get("fieldOfStudy")),
                "date_range": _date_range_to_str(
                    education.get("timePeriod") or education.get("dateRange")
                ),
            }
            for education in educations
        ],
        "skills": [
            name for skill in skills if (name := _text(skill.get("name")))
        ],
        "certifications": [
            {
                "name": _text(certification.get("name")),
                "authority": _text(certification.get("authority")),
                "date_range": _date_range_to_str(
                    certification.get("timePeriod")
                    or certification.get("dateRange")
                ),
            }
            for certification in certifications
        ],
        "languages": [
            {
                "name": _text(language.get("name")),
                "proficiency": _text(language.get("proficiency")),
            }
            for language in languages
        ],
    }


def parse_profile(raw: dict) -> dict:
    standard_profile = _parse_standard_profile(raw)
    if standard_profile is not None:
        return standard_profile

    included = raw.get("included", [])
    if not isinstance(included, list):
        included = []
    index = _entity_index(included)

    profiles = _by_type(included, "identity.profile.Profile")
    target_profile_urn = _find_target_profile_urn(raw.get("data"))
    profile = index.get(target_profile_urn, {}) if target_profile_urn else {}
    if not profile:
        profile = max(profiles, key=_profile_score) if profiles else {}

    positions = _referenced_entities(
        profile, index, ("profilePosition",), "identity.profile.Position"
    ) or _by_type(included, "identity.profile.Position")
    educations = _referenced_entities(
        profile, index, ("profileEducation",), "identity.profile.Education"
    ) or _by_type(included, "identity.profile.Education")
    skills = _referenced_entities(
        profile, index, ("profileSkill",), "identity.profile.Skill"
    ) or _by_type(included, "identity.profile.Skill")
    certifications = _referenced_entities(
        profile, index, ("profileCertification",), "identity.profile.Certification"
    ) or _by_type(included, "identity.profile.Certification")
    languages = _referenced_entities(
        profile, index, ("profileLanguage",), "identity.profile.Language"
    ) or _by_type(included, "identity.profile.Language")

    full_name = (
        " ".join(
            part
            for part in (
                _text(profile.get("firstName")),
                _text(profile.get("lastName")),
            )
            if part
        ).strip()
        or None
    )
    public_identifier = _text(profile.get("publicIdentifier"))
    mini_profile = profile.get("miniProfile") or {}

    return {
        "public_identifier": public_identifier,
        "profile_url": (
            f"https://www.linkedin.com/in/{public_identifier}/"
            if public_identifier
            else None
        ),
        "name": full_name,
        "headline": _text(profile.get("headline")),
        "location": _profile_location(profile, index),
        "about": _text(profile.get("summary")),
        "profile_image_url": _first_image_url(
            profile.get("profilePicture"),
            mini_profile.get("picture"),
            profile.get("picture"),
        ),
        "background_image_url": _first_image_url(
            profile.get("backgroundPicture"),
            profile.get("backgroundImage"),
        ),
        "experience": [
            {
                "title": _text(position.get("title")),
                "company": _text(position.get("companyName"))
                or _resolved_text_from_reference(
                    position.get("*company"), index, "name"
                ),
                "location": _text(position.get("locationName")),
                "date_range": _date_range_to_str(
                    position.get("timePeriod") or position.get("dateRange")
                ),
                "description": _text(position.get("description")),
            }
            for position in positions
        ],
        "education": [
            {
                "school": _text(education.get("schoolName")),
                "degree": _text(education.get("degreeName")),
                "field_of_study": _text(education.get("fieldOfStudy")),
                "date_range": _date_range_to_str(
                    education.get("timePeriod") or education.get("dateRange")
                ),
            }
            for education in educations
        ],
        "skills": [name for skill in skills if (name := _text(skill.get("name")))],
        "certifications": [
            {
                "name": _text(certification.get("name")),
                "authority": _text(certification.get("authority")),
                "date_range": _date_range_to_str(
                    certification.get("timePeriod") or certification.get("dateRange")
                ),
            }
            for certification in certifications
        ],
        "languages": [
            {
                "name": _text(language.get("name")),
                "proficiency": _text(language.get("proficiency")),
            }
            for language in languages
        ],
    }
