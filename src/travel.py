"""Travel time estimation via 9292.nl API or Google Maps."""

import requests
from datetime import datetime, timedelta
from urllib.parse import quote


def estimate_travel_time_9292(origin="Hoofddorp", destination_city="Amsterdam", arrival_time="09:00"):
    """Estimate public transport travel time using 9292.nl.

    Note: 9292.nl doesn't have a public API. This uses their web search URL
    to construct a link the user can click. For actual travel times,
    we fall back to Google Maps Directions API or a simple distance estimate.
    """
    # Construct 9292 link for user reference
    tomorrow = datetime.now() + timedelta(days=1)
    # Find next weekday
    while tomorrow.weekday() >= 5:  # Skip weekends
        tomorrow += timedelta(days=1)

    date_str = tomorrow.strftime("%d-%m-%Y")
    link = (
        f"https://9292.nl/en/journey/"
        f"station-{origin.lower()}/station-{destination_city.lower()}/"
        f"arrive/{date_str}/{arrival_time}"
    )

    return {
        "origin": origin,
        "destination": destination_city,
        "arrival_time": arrival_time,
        "travel_link_9292": link,
        "estimated_minutes": _estimate_by_distance(origin, destination_city),
    }


# Simple lookup table for common NL cities from Hoofddorp (public transport minutes)
_TRAVEL_ESTIMATES_FROM_HOOFDDORP = {
    "amsterdam": 25,
    "schiphol": 5,
    "haarlem": 20,
    "leiden": 30,
    "den haag": 45,
    "the hague": 45,
    "rotterdam": 55,
    "utrecht": 40,
    "eindhoven": 80,
    "delft": 45,
    "almere": 35,
    "amstelveen": 20,
    "hilversum": 45,
    "amersfoort": 50,
    "diemen": 30,
    "zaandam": 30,
}


def _estimate_by_distance(origin, destination):
    """Rough travel time estimate based on known routes from Hoofddorp."""
    if origin.lower() == "hoofddorp":
        dest = destination.lower().strip()
        for city, minutes in _TRAVEL_ESTIMATES_FROM_HOOFDDORP.items():
            if city in dest or dest in city:
                return minutes
    return ""  # Unknown route


def enrich_with_travel_time(df, origin="Hoofddorp", arrival_time="09:00"):
    """Add travel time estimates to job DataFrame."""
    travel_times = []
    travel_links = []

    for _, row in df.iterrows():
        location = str(row.get("location", ""))
        result = estimate_travel_time_9292(origin, location, arrival_time)
        travel_times.append(result["estimated_minutes"])
        travel_links.append(result["travel_link_9292"])

    df = df.copy()
    df["travel_minutes"] = travel_times
    df["travel_link"] = travel_links
    return df
