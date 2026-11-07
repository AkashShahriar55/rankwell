"""Read-only checks against the real Apple endpoints, to notice when Apple changes something.

Not part of the normal run: `uv run pytest -m live`. Public endpoints need nothing; the App Store Connect
checks run only when RANKWELL_ASC_ISSUER_ID, RANKWELL_ASC_KEY_ID and RANKWELL_ASC_KEY_PATH are set. Nothing here
writes anywhere. Printed output is counts and field names, never account data.
"""

import os

import pytest

from rankwell.stores import appstore
from rankwell.stores.asc import ASC

pytestmark = pytest.mark.live
APPLE_STORE_ID = 1108187390  # Apple's own "Apple Store" app: stable, always live
HAS_KEY = all(os.environ.get(f"RANKWELL_ASC_{n}") for n in ("ISSUER_ID", "KEY_ID", "KEY_PATH"))
needs_key = pytest.mark.skipif(not HAS_KEY, reason="no App Store Connect key in the environment")


def test_search_and_lookup() -> None:
    found = appstore.search("photo editor", "us", limit=10)
    assert len(found) >= 5 and {"trackId", "trackName", "averageUserRating"} <= set(found[0])
    one = appstore.lookup([APPLE_STORE_ID], "us")[APPLE_STORE_ID]
    assert one["trackName"] and one.get("primaryGenreId")


def test_autocomplete() -> None:
    assert any("photo" in s for s in appstore.autocomplete("phot", "us"))


def test_chart_feed() -> None:
    assert len(appstore.chart(6008, "us", "free", limit=25)) >= 10  # Photo & Video


@pytest.mark.xfail(reason="since November 2026 the review feed answers empty to non-browser clients; an XPASS "
                          "means Apple serves it again", strict=False)
def test_reviews_feed() -> None:
    rows = appstore.reviews(APPLE_STORE_ID, "us")
    assert rows and {"rating", "content"} <= set(rows[0])


@needs_key
def test_asc_read_only() -> None:
    # Checks are plain booleans so a failure never prints account data into a CI log.
    asc = ASC()
    apps = asc.apps()
    assert bool(apps), "the key sees no apps"
    sid = apps[0]["store_id"]
    versions = asc.get(f"/v1/apps/{sid}/appStoreVersions", limit=5).get("data") or []
    has_state = all("appVersionState" in v["attributes"] or "appStoreState" in v["attributes"] for v in versions)
    assert has_state, "appStoreVersions lost its state attribute"
    assert "data" in asc.get(f"/v1/apps/{sid}/appInfos"), "appInfos changed shape"
    reviews = asc.get(f"/v1/apps/{sid}/customerReviews", limit=5, include="response")
    assert "data" in reviews, "customerReviews changed shape"
    assert "data" in asc.get(f"/v1/apps/{sid}/analyticsReportRequests"), "analyticsReportRequests changed shape"
    print(f"ASC ok: {len(apps)} apps, {len(versions)} versions, {len(reviews['data'])} reviews read")
