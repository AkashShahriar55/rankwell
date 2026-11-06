"""Tools: the user's apps, countries, keywords, competitors."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .. import apps, render
from ..stores import appstore
from .common import db, safe


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @safe
    def list_apps() -> dict[str, Any]:
        """The user's apps: id, slug, name, live or pre-launch, countries, keyword and competitor counts."""
        rows = []
        for a in apps.list_apps(db()):
            rows.append({
                "id": a["id"], "slug": a["slug"], "name": a["name"], "stage": a["stage"],
                "store_id": a["store_id"], "countries": ", ".join(a["countries"]),
                "keywords": len(apps.keywords(db(), a["id"])), "competitors": len(apps.competitors(db(), a["id"])),
                "repo": a["repo_path"],
            })
        return {"apps": rows, "display": render.table(
            rows, ["name", "stage", "countries", "keywords", "competitors"], align_right=["keywords", "competitors"])}

    @mcp.tool()
    @safe
    def add_app(name: str, store_url_or_id: str | None = None, bundle_id: str | None = None,
                repo_path: str | None = None, countries: list[str] | None = None) -> dict[str, Any]:
        """Track one of the user's own apps. Leave store_url_or_id empty for an app that is not released yet."""
        store_id = appstore.store_id_from(store_url_or_id) if store_url_or_id else None
        genre = None
        if store_id:
            found = appstore.lookup([store_id], (countries or ["us"])[0]).get(store_id)
            if found:
                genre = int(found["primaryGenreId"]) if found.get("primaryGenreId") else None
                bundle_id = bundle_id or found.get("bundleId")
        a = apps.add(db(), name, store_id=store_id, bundle_id=bundle_id, repo_path=repo_path,
                     countries=countries, genre_id=genre)
        return {"app": a}

    @mcp.tool()
    @safe
    def update_app(app: str, name: str | None = None, store_url_or_id: str | None = None,
                   bundle_id: str | None = None, repo_path: str | None = None,
                   countries: list[str] | None = None) -> dict[str, Any]:
        """Change an app's name, App Store link (e.g. when it goes live), bundle id, repo path or countries."""
        a = apps.get(db(), app)
        fields: dict[str, Any] = {}
        if name:
            fields["name"] = name
        if store_url_or_id:
            fields["store_id"] = appstore.store_id_from(store_url_or_id)
        if bundle_id:
            fields["bundle_id"] = bundle_id
        if repo_path:
            fields["repo_path"] = repo_path
        a = apps.update(db(), a["id"], **fields)
        if countries:
            apps.set_countries(db(), a["id"], countries)
            a = apps.get(db(), a["id"])
        return {"app": a}

    @mcp.tool()
    @safe
    def keywords_add(app: str, terms: list[str], country: str | None = None) -> dict[str, Any]:
        """Track keywords for an app in a country (default: the app's first country)."""
        a = apps.get(db(), app)
        c = country or a["countries"][0]
        n = apps.add_keywords(db(), a["id"], terms, c)
        return {"added": n, "country": c, "tracked": len(apps.keywords(db(), a["id"], c))}

    @mcp.tool()
    @safe
    def keywords_remove(app: str, terms: list[str], country: str | None = None) -> dict[str, Any]:
        """Stop tracking keywords (history stays in the database)."""
        a = apps.get(db(), app)
        c = country or a["countries"][0]
        return {"removed": apps.remove_keywords(db(), a["id"], terms, c), "country": c}

    @mcp.tool()
    @safe
    def competitors_add(app: str, store_url_or_id: str) -> dict[str, Any]:
        """Watch a competitor app (its listing, ranks, reviews)."""
        a = apps.get(db(), app)
        sid = appstore.store_id_from(store_url_or_id)
        found = appstore.lookup([sid], a["countries"][0]).get(sid)
        name = found["trackName"] if found else str(sid)
        apps.add_competitor(db(), a["id"], sid, name)
        return {"added": {"store_id": sid, "name": name}}

    @mcp.tool()
    @safe
    def competitors_remove(app: str, store_url_or_id: str) -> dict[str, Any]:
        """Stop watching a competitor."""
        a = apps.get(db(), app)
        return {"removed": apps.remove_competitor(db(), a["id"], appstore.store_id_from(store_url_or_id))}
