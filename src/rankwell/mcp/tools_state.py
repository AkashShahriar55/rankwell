"""Tools: the app repo's .aso/ folder (context, decisions, progress, experiments) and the code audit."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from .. import apps, render, state
from .. import code_audit as audit_mod
from ..errors import RankwellError
from ..util import today
from .common import db, safe

SEV_MARK = {"high": "● high", "medium": "◐ medium", "low": "○ low"}


def _app_summary(a: apps.App) -> dict[str, Any]:
    return {k: a.get(k) for k in ("id", "slug", "name", "stage", "store_id", "bundle_id", "repo_path", "countries")}


def _bullets(entries: list[dict[str, Any]], limit: int) -> str:
    if not entries:
        return "_(none yet)_"
    return "\n".join(f"- {e.get('date') or '?'}: {e['text'].splitlines()[0]}" for e in entries[:limit])


def _exp_rows(exps: list[state.Experiment]) -> list[dict[str, Any]]:
    return [{"id": e["id"], "title": e.get("title"), "status": e["status"], "check_after": e.get("check_after"),
             "metric": e.get("metric"), "verdict": e.get("verdict") or "–"} for e in exps]


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @safe
    def aso_context(app: str) -> dict[str, Any]:
        """Everything Rankwell remembers about an app: the app row and its repo's .aso/ files (app.md context,
        recent decisions, progress and open items, experiments). Read this before advising on an app."""
        a = apps.get(db(), app)
        out: dict[str, Any] = {"app": _app_summary(a),
                               "keywords": len(apps.keywords(db(), a["id"])),
                               "competitors": [c["name"] for c in apps.competitors(db(), a["id"])]}
        head = f"**{a['name']}** · {a['stage']} · {', '.join(a['countries'])} · " \
               f"{out['keywords']} keywords · {len(out['competitors'])} competitors"
        try:
            repo = state.repo_for(a)
        except RankwellError as e:
            out["aso"] = None
            out["note"] = str(e)
            out["display"] = f"{head}\n\n{e}"
            return out
        s = state.read(repo)
        if not s["exists"]:
            out["aso"] = None
            out["note"] = f"No .aso/ folder in {repo} yet. Create it with aso_init('{a['slug']}')."
            out["display"] = f"{head}\n\n{out['note']}"
            return out
        exps = s["experiments"]
        open_exps = [e for e in exps if e["status"] == "open"]
        out["aso"] = {
            "folder": s["folder"], "app_md": s["files"].get("app.md", ""),
            "release_toml": s["files"].get("release.toml"),
            "decisions": s["decisions"][:15], "progress": s["progress"][:15], "open_items": s["open_items"],
            "experiments_open": open_exps, "experiments_closed": [e for e in exps if e["status"] == "closed"][:10],
        }
        due_ids = {e["id"] for e in state.due(repo)}
        parts = [head, f"Repo: `{repo}`"]
        if open_exps:
            rows = [{**r, "due": "due now" if r["id"] in due_ids else ""} for r in _exp_rows(open_exps)]
            parts.append("**Open experiments**\n\n" + render.table(rows, ["id", "title", "check_after", "metric",
                                                                         "due"]))
        if s["open_items"]:
            parts.append("**Open items**\n\n" + "\n".join(f"- [ ] {t.splitlines()[0]}" for t in s["open_items"][:10]))
        parts.append("**Recent decisions**\n\n" + _bullets(s["decisions"], 5))
        parts.append("**Recent progress**\n\n" + _bullets([p for p in s["progress"] if not p.get("open")], 5))
        out["display"] = "\n\n".join(parts)
        return out

    @mcp.tool()
    @safe
    def aso_init(app: str, repo_path: str | None = None) -> dict[str, Any]:
        """Create the app repo's .aso/ folder (app.md, decisions.md, experiments.md, progress.md, release.toml).
        Existing files are kept. Pass repo_path to set or change where the app's code lives."""
        a = apps.get(db(), app)
        if repo_path:
            p = Path(repo_path).expanduser().resolve()
            if not p.is_dir():
                raise RankwellError(f"Not a folder: {p}")
            a = apps.update(db(), a["id"], repo_path=str(p))
        repo = state.repo_for(a)
        r = state.init(repo, a)
        shown = [{"file": f".aso/{n}", "status": "created"} for n in r["created"]]
        shown += [{"file": f".aso/{n}", "status": "kept (already there)"} for n in r["kept"]]
        return {**r, "repo": str(repo), "display": render.table(shown, ["file", "status"])
                + "\n\nNext: fill in `.aso/app.md` (what the app is, who it is for, what not to claim)."}

    @mcp.tool()
    @safe
    def aso_note(app: str, kind: str, text: str) -> dict[str, Any]:
        """Remember something in the app repo's .aso/ files, dated today.

        kind: decision (what was decided and why → decisions.md), progress (what was done → progress.md),
        todo (an open item → progress.md), done (ticks the matching open item and logs it)."""
        a = apps.get(db(), app)
        r = state.note(state.repo_for(a), kind, text)
        return {**r, "display": f"Noted in `{Path(r['file']).name}` ({r['date']}): {text.strip().splitlines()[0]}"}

    @mcp.tool()
    @safe
    def experiment_open(app: str, hypothesis: str, change: str, metric: str, baseline: str,
                        title: str | None = None, check_after: str | None = None) -> dict[str, Any]:
        """Start an ASO experiment in .aso/experiments.md. hypothesis: 'If <change>, <metric> improves because
        <reason>'. baseline: the metric's value before the change with its date range. check_after: YYYY-MM-DD
        (default 14 days from today). Open it when the change goes live."""
        a = apps.get(db(), app)
        e = state.open_experiment(state.repo_for(a), hypothesis, change, metric, baseline, title, check_after)
        return {"experiment": e, "display": render.table(_exp_rows([e]), ["id", "title", "check_after", "metric"])
                + f"\n\nBaseline: {e.get('baseline')}"}

    @mcp.tool()
    @safe
    def experiment_close(app: str, experiment_id: str, result: str, verdict: str) -> dict[str, Any]:
        """Close an experiment with its result (the metric after, next to the baseline) and a verdict:
        confirmed, failed or inconclusive. Hand-written lines in the entry are kept."""
        a = apps.get(db(), app)
        e = state.close_experiment(state.repo_for(a), experiment_id, result, verdict)
        return {"experiment": e, "display": f"**{e['id']}** {e.get('title')}: **{e['verdict']}**\n\n"
                f"- Baseline: {e.get('baseline')}\n- Result: {e.get('result')}"}

    @mcp.tool()
    @safe
    def experiments_due(app: str | None = None) -> dict[str, Any]:
        """Open experiments whose check-after date has come, for one app or all apps with a repo folder."""
        targets = [apps.get(db(), app)] if app else apps.list_apps(db())
        rows: list[dict[str, Any]] = []
        skipped: list[str] = []
        day = today()
        for a in targets:
            try:
                repo = state.repo_for(a)
            except RankwellError:
                skipped.append(a["name"])
                continue
            for e in state.due(repo, day):
                rows.append({"app": a["name"], "app_slug": a["slug"], "id": e["id"], "title": e.get("title"),
                             "check_after": e.get("check_after"), "days_overdue": e["days_overdue"],
                             "metric": e.get("metric"), "baseline": e.get("baseline"),
                             "hypothesis": e.get("hypothesis"), "change": e.get("change")})
        display = render.table(rows, ["app", "id", "title", "check_after", "metric", "baseline"]) if rows \
            else "No experiments are due."
        if skipped:
            display += f"\n\nNo repo folder set for: {', '.join(skipped)} (aso_init with repo_path)."
        return {"due": rows, "skipped": skipped, "display": display}

    @mcp.tool()
    @safe
    def code_audit(app_or_path: str) -> dict[str, Any]:
        """Check an app's source code for ASO and App Review problems: permission texts, rating prompt timing,
        home screen name vs store name, localizations, privacy manifest, deep links, app icon, in-app purchase
        ids, placeholder text, tracking prompt vs ads SDKs. Takes an app (uses its repo folder) or a path.
        Each finding has severity, file:line, why it matters, the fix and knowledge rule ids."""
        p = Path(app_or_path).expanduser()
        listing_name = None
        if p.is_dir():
            repo = p
        else:
            a = apps.get(db(), app_or_path)
            repo = state.repo_for(a)
            snap = db().one("SELECT title FROM snapshots WHERE store_id = ? AND title IS NOT NULL "
                            "ORDER BY date DESC LIMIT 1", (a["store_id"],)) if a.get("store_id") else None
            listing_name = snap["title"] if snap else a["name"]
        r = audit_mod.audit(repo, listing_name)
        rows = [{"severity": SEV_MARK[f["severity"]], "finding": f["title"], "where": f["where"],
                 "rules": ", ".join(f["rules"]) or "–"} for f in r["findings"][:40]]
        c = r["counts"]
        head = (f"**Code audit** · {r['kind']} · {r['files_scanned']} files · "
                f"{c['high']} high, {c['medium']} medium, {c['low']} low")
        more = f"\n\n…and {len(r['findings']) - 40} more." if len(r["findings"]) > 40 else ""
        r["display"] = head + "\n\n" + (render.table(rows, ["severity", "finding", "where", "rules"])
                                        if rows else "Nothing found.") + more
        return r
