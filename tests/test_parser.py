from app.parser import parse_profile


def test_parse_profile_extracts_requested_fields(profile_payload):
    profile = parse_profile(profile_payload)

    assert profile["public_identifier"] == "jane-doe-123"
    assert profile["profile_url"] == "https://www.linkedin.com/in/jane-doe-123/"
    assert profile["name"] == "Jane Doe"
    assert profile["headline"] == "Senior Engineer at Example Co."
    assert profile["location"] == "Bengaluru, Karnataka, India"
    assert profile["about"] == "Builds dependable distributed systems."
    assert profile["profile_image_url"].endswith("large.jpg")
    assert profile["background_image_url"].endswith("cover.jpg")

    assert profile["experience"] == [
        {
            "title": "Senior Engineer",
            "company": "Example Co.",
            "location": "Bengaluru",
            "date_range": "06/2022 - Present",
            "description": "Led the platform team.",
        }
    ]
    assert profile["education"][0]["date_range"] == "2016 - 2020"
    assert profile["skills"] == ["Python"]
    assert profile["certifications"][0]["date_range"] == "2021 - 2024"
    assert profile["languages"] == [
        {"name": "English", "proficiency": "NATIVE_OR_BILINGUAL"}
    ]


def test_parse_profile_tolerates_an_empty_payload():
    profile = parse_profile({})

    assert profile["name"] is None
    assert profile["experience"] == []
    assert profile["education"] == []
    assert profile["skills"] == []


def test_parse_dash_profile_resolves_target_urn_graph(dash_profile_payload):
    profile = parse_profile(dash_profile_payload)

    assert profile["public_identifier"] == "target-person"
    assert profile["name"] == "Target Person"
    assert profile["headline"] == "Platform Engineer"
    assert profile["about"] == "About target"
    assert profile["location"] == "Pune, India"
    assert profile["experience"] == [
        {
            "title": "Senior Engineer",
            "company": "Target Company",
            "location": None,
            "date_range": "01/2024 - Present",
            "description": None,
        }
    ]
    assert profile["education"][0]["school"] == "Target University"
    assert "Unrelated stale position" not in {
        item["title"] for item in profile["experience"]
    }


def test_parse_current_standard_elements_profile():
    payload = {
        "elements": [
            {
                "publicIdentifier": "jane-current",
                "firstName": "Jane",
                "lastName": "Current",
                "headline": "Staff Engineer",
                "summary": "Builds APIs.",
                "geoLocationName": "Mumbai, India",
                "profilePicture": {
                    "displayImageReference": {
                        "vectorImage": {
                            "rootUrl": "https://media.example/",
                            "artifacts": [
                                {
                                    "width": 100,
                                    "height": 100,
                                    "fileIdentifyingUrlPathSegment": "small.jpg",
                                },
                                {
                                    "width": 400,
                                    "height": 400,
                                    "fileIdentifyingUrlPathSegment": "large.jpg",
                                },
                            ],
                        }
                    }
                },
                "profilePositionGroups": {
                    "elements": [
                        {
                            "profilePositionInPositionGroup": {
                                "elements": [
                                    {
                                        "title": "Staff Engineer",
                                        "companyName": "Example",
                                        "locationName": "Mumbai",
                                        "timePeriod": {
                                            "startDate": {"month": 2, "year": 2023}
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
                "profileEducations": {
                    "elements": [
                        {
                            "schoolName": "Example University",
                            "degreeName": "B.Tech",
                            "timePeriod": {
                                "startDate": {"year": 2015},
                                "endDate": {"year": 2019},
                            },
                        }
                    ]
                },
                "profileSkills": {"elements": [{"name": "Python"}]},
                "profileCertifications": {
                    "elements": [{"name": "Cloud Cert", "authority": "Vendor"}]
                },
                "profileLanguages": {
                    "elements": [
                        {"name": "English", "proficiency": "NATIVE_OR_BILINGUAL"}
                    ]
                },
            }
        ]
    }

    profile = parse_profile(payload)

    assert profile["public_identifier"] == "jane-current"
    assert profile["name"] == "Jane Current"
    assert profile["profile_image_url"] == "https://media.example/large.jpg"
    assert profile["experience"][0]["company"] == "Example"
    assert profile["experience"][0]["date_range"] == "02/2023 - Present"
    assert profile["education"][0]["date_range"] == "2015 - 2019"
    assert profile["skills"] == ["Python"]
    assert profile["certifications"][0]["authority"] == "Vendor"
    assert profile["languages"][0]["name"] == "English"
