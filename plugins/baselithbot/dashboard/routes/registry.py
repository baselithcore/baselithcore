"""Read-only registry routes (channels, skills, crons, nodes)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ...policies import RateLimiter
from ...skills import (
    ClawHubConfig,
    OpenClawDraft,
    OpenClawRequiresDraft,
    SkillDraft,
    SkillScope,
)
from ..bus import _BUS
from ...cron_custom import (
    ACTION_CATALOG,
    CronActionSpec,
    CustomCronSpec,
)
from ..schemas import (
    ClawHubConfigRequest,
    CronCustomCreateRequest,
    CronCustomUpdateRequest,
    CronIntervalRequest,
    CronToggleRequest,
    PairingTokenRequest,
    WorkspaceSkillCreateRequest,
)
from ..security import enforce

if TYPE_CHECKING:
    from ...plugin import BaselithbotPlugin


def register_registry_routes(
    router: APIRouter,
    plugin: "BaselithbotPlugin",
    *,
    guard: Any,
    token_rate_limit: RateLimiter,
    delete_rate_limit: RateLimiter,
) -> None:
    @router.get("/channels")
    async def list_channels() -> dict[str, Any]:
        known = plugin.channels.known()
        live = set(plugin.channels.live_names())
        inbound_stats = plugin.inbound_dispatcher.stats()
        channels: list[dict[str, Any]] = []
        for name in known:
            required = plugin.channels.required_credentials(name)
            snap = plugin.channel_configs.snapshot_entry(name, required)
            channels.append(
                {
                    "name": name,
                    "live": name in live,
                    "configured": snap["configured"],
                    "enabled": snap["enabled"],
                    "required_fields": snap["required_fields"],
                    "missing_fields": snap["missing_fields"],
                    "inbound_events": inbound_stats.get(name, 0),
                    "updated_at": snap["updated_at"],
                }
            )
        return {"channels": channels}

    @router.get("/skills")
    async def list_skills(scope: str | None = None) -> dict[str, Any]:
        skills = plugin.skills.list()
        if scope:
            skills = [s for s in skills if s.scope.value == scope]
        return {"skills": [s.model_dump(mode="json") for s in skills]}

    @router.get("/skills/workspace/validate")
    async def validate_workspace_skills() -> dict[str, Any]:
        reports = plugin.workspace_skill_reports()
        counts = {"verified": 0, "provisional": 0, "invalid": 0}
        for report in reports:
            validation = report.get("validation") if isinstance(report, dict) else None
            status = validation.get("status") if isinstance(validation, dict) else None
            if isinstance(status, str) and status in counts:
                counts[status] += 1
        return {"reports": reports, "counts": counts}

    @router.get("/skills/clawhub")
    async def clawhub_status() -> dict[str, Any]:
        cfg = plugin.clawhub.config
        return {
            "base_url": cfg.base_url,
            "convex_url": cfg.convex_url,
            "install_dir": cfg.install_dir,
            "timeout_seconds": cfg.timeout_seconds,
            "auth_token_set": bool(cfg.auth_token),
        }

    @router.put("/skills/clawhub", dependencies=[Depends(guard)])
    async def configure_clawhub(
        req: ClawHubConfigRequest, request: Request
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "clawhub_config")
        current = plugin.clawhub.config
        merged = ClawHubConfig(
            base_url=req.base_url if req.base_url is not None else current.base_url,
            convex_url=(
                req.convex_url if req.convex_url is not None else current.convex_url
            ),
            auth_token=(
                req.auth_token if req.auth_token is not None else current.auth_token
            ),
            install_dir=(
                req.install_dir if req.install_dir is not None else current.install_dir
            ),
            timeout_seconds=(
                req.timeout_seconds
                if req.timeout_seconds is not None
                else current.timeout_seconds
            ),
        )
        plugin.configure_clawhub(merged)
        _BUS.publish(
            "skill.clawhub_configured",
            {
                "base_url": merged.base_url,
                "convex_url": merged.convex_url,
                "install_dir": merged.install_dir,
            },
        )
        return {
            "base_url": merged.base_url,
            "convex_url": merged.convex_url,
            "install_dir": merged.install_dir,
            "timeout_seconds": merged.timeout_seconds,
            "auth_token_set": bool(merged.auth_token),
        }

    @router.get("/skills/clawhub/catalog")
    async def clawhub_catalog() -> dict[str, Any]:
        return {"entries": await plugin.clawhub.list_skills()}

    @router.post("/skills/clawhub/sync", dependencies=[Depends(guard)])
    async def clawhub_sync(request: Request) -> dict[str, Any]:
        enforce(token_rate_limit, request, "clawhub_sync")
        result = await plugin.clawhub.sync(plugin.skills)
        _BUS.publish("skill.clawhub_synced", {"installed": result.get("installed", 0)})
        return result

    @router.post("/skills/clawhub/install/{name}", dependencies=[Depends(guard)])
    async def clawhub_install(name: str, request: Request) -> dict[str, Any]:
        enforce(token_rate_limit, request, "clawhub_install")
        result = await plugin.clawhub.install(name, registry=plugin.skills)
        if result.get("status") == "error":
            raise HTTPException(status_code=502, detail=result)
        _BUS.publish("skill.installed", {"name": name, "source": "clawhub"})
        return result

    @router.post("/skills/workspace", dependencies=[Depends(guard)])
    async def create_workspace_skill(
        req: WorkspaceSkillCreateRequest, request: Request
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "skills_create")
        try:
            openclaw_draft: OpenClawDraft | None = None
            if req.openclaw is not None:
                openclaw_draft = OpenClawDraft(
                    homepage=req.openclaw.homepage,
                    user_invocable=req.openclaw.user_invocable,
                    disable_model_invocation=req.openclaw.disable_model_invocation,
                    command_dispatch=req.openclaw.command_dispatch,
                    command_tool=req.openclaw.command_tool,
                    command_arg_mode=req.openclaw.command_arg_mode,
                    always=req.openclaw.always,
                    emoji=req.openclaw.emoji,
                    os=req.openclaw.os,
                    primary_env=req.openclaw.primary_env,
                    skill_key=req.openclaw.skill_key,
                    requires=OpenClawRequiresDraft(
                        bins=req.openclaw.requires.bins,
                        any_bins=req.openclaw.requires.any_bins,
                        env=req.openclaw.requires.env,
                        config=req.openclaw.requires.config,
                    ),
                    install=req.openclaw.install,
                )
            draft = SkillDraft(
                slug=req.slug,
                name=req.name,
                description=req.description,
                version=req.version,
                instructions=req.instructions,
                surfaces=req.surfaces,
                tags=req.tags,
                openclaw=openclaw_draft,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            spec = plugin.create_workspace_skill(
                draft, workspace=req.workspace, overwrite=req.overwrite
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="workspace not found") from exc
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _BUS.publish(
            "skill.workspace_created",
            {"slug": spec.slug, "workspace": req.workspace, "overwrite": req.overwrite},
        )
        return {"status": "created", "spec": spec.model_dump(mode="json")}

    @router.post("/skills/rescan", dependencies=[Depends(guard)])
    async def rescan_skills(request: Request) -> dict[str, Any]:
        enforce(token_rate_limit, request, "skills_rescan")
        removed = plugin.rescan_workspace_skills()
        current = [
            s.model_dump(mode="json") for s in plugin.skills.list(SkillScope.WORKSPACE)
        ]
        _BUS.publish(
            "skill.rescanned", {"removed": removed, "registered": len(current)}
        )
        return {"removed": removed, "workspace_skills": current}

    @router.delete("/skills/{name}", dependencies=[Depends(guard)])
    async def remove_skill(name: str, request: Request) -> dict[str, Any]:
        enforce(delete_rate_limit, request, "skill_remove")
        skill = plugin.skills.get(name)
        if skill is None:
            raise HTTPException(status_code=404, detail="skill not found")
        if skill.scope == SkillScope.BUNDLED:
            raise HTTPException(
                status_code=409, detail="bundled skills cannot be removed"
            )
        purged_files = False
        try:
            purged_files = plugin.purge_skill_on_disk(skill)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        plugin.skills.remove(name)
        # Rescan so workspace skills that live on disk aren't re-registered
        # from a stale mirror, and so managed/clawhub bundles re-sync state.
        if purged_files:
            plugin.rescan_workspace_skills()
        _BUS.publish(
            "skill.removed",
            {"name": name, "scope": skill.scope.value, "purged_files": purged_files},
        )
        return {
            "status": "removed",
            "name": name,
            "scope": skill.scope.value,
            "purged_files": purged_files,
        }

    @router.get("/crons")
    async def list_crons() -> dict[str, Any]:
        custom_names = {spec.name for spec in plugin.custom_crons.list()}
        jobs = plugin.cron.list()
        for entry in jobs:
            entry["custom"] = entry["name"] in custom_names
        return {
            "backend": plugin.cron.backend,
            "jobs": jobs,
        }

    @router.get("/crons/catalog")
    async def cron_catalog() -> dict[str, Any]:
        return {
            "actions": [
                {
                    "type": desc.type,
                    "label": desc.label,
                    "description": desc.description,
                    "params_schema": desc.params_schema,
                }
                for desc in ACTION_CATALOG.values()
            ],
            "name_prefix": "custom.",
        }

    @router.get("/crons/custom")
    async def list_custom_crons() -> dict[str, Any]:
        return {
            "jobs": [
                spec.model_dump(mode="json") for spec in plugin.custom_crons.list()
            ]
        }

    @router.post("/crons", dependencies=[Depends(guard)])
    async def create_custom_cron(
        req: CronCustomCreateRequest, request: Request
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "cron_create")
        spec = CustomCronSpec(
            name=req.name,
            interval_seconds=req.interval_seconds,
            action=CronActionSpec(type=req.action.type, params=req.action.params),
            description=req.description,
            enabled=req.enabled,
        )
        try:
            stored = plugin.custom_crons.register(spec)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _BUS.publish("cron.custom_registered", {"name": stored.name})
        return {"status": "created", "job": stored.model_dump(mode="json")}

    @router.put("/crons/{name}/custom", dependencies=[Depends(guard)])
    async def update_custom_cron(
        name: str, req: CronCustomUpdateRequest, request: Request
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "cron_custom_update")
        spec = CustomCronSpec(
            name=name,
            interval_seconds=req.interval_seconds,
            action=CronActionSpec(type=req.action.type, params=req.action.params),
            description=req.description,
            enabled=req.enabled,
        )
        try:
            stored = plugin.custom_crons.update(name, spec)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="custom cron not found"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _BUS.publish("cron.custom_updated", {"name": stored.name})
        return {"status": "updated", "job": stored.model_dump(mode="json")}

    @router.post("/crons/{name}/remove", dependencies=[Depends(guard)])
    async def remove_cron(name: str, request: Request) -> dict[str, Any]:
        enforce(delete_rate_limit, request, "cron_remove")
        if plugin.custom_crons.is_custom(name):
            plugin.custom_crons.delete(name)
            _BUS.publish("cron.removed", {"name": name, "custom": True})
            return {"status": "removed", "name": name, "custom": True}
        removed = plugin.cron.remove(name)
        if not removed:
            raise HTTPException(status_code=404, detail="cron job not found")
        _BUS.publish("cron.removed", {"name": name, "custom": False})
        return {"status": "removed", "name": name, "custom": False}

    @router.post("/crons/{name}/toggle", dependencies=[Depends(guard)])
    async def toggle_cron(
        name: str, req: CronToggleRequest, request: Request
    ) -> dict[str, Any]:
        enforce(delete_rate_limit, request, "cron_toggle")
        ok = plugin.cron.set_enabled(name, req.enabled)
        if not ok:
            raise HTTPException(status_code=404, detail="cron job not found")
        event = "cron.enabled" if req.enabled else "cron.paused"
        _BUS.publish(event, {"name": name})
        job = plugin.cron.get(name)
        return {"status": "ok", "name": name, "job": job}

    @router.post("/crons/{name}/run", dependencies=[Depends(guard)])
    async def run_cron(name: str, request: Request) -> dict[str, Any]:
        enforce(delete_rate_limit, request, "cron_run")
        ok = plugin.cron.trigger(name)
        if not ok:
            raise HTTPException(status_code=404, detail="cron job not found")
        _BUS.publish("cron.triggered", {"name": name})
        return {"status": "triggered", "name": name}

    @router.patch("/crons/{name}", dependencies=[Depends(guard)])
    async def update_cron_interval(
        name: str, req: CronIntervalRequest, request: Request
    ) -> dict[str, Any]:
        enforce(delete_rate_limit, request, "cron_update")
        try:
            ok = plugin.cron.set_interval(name, req.interval_seconds)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not ok:
            raise HTTPException(status_code=404, detail="cron job not found")
        _BUS.publish(
            "cron.interval_updated",
            {"name": name, "interval_seconds": req.interval_seconds},
        )
        job = plugin.cron.get(name)
        return {"status": "updated", "name": name, "job": job}

    @router.get("/nodes")
    async def list_nodes() -> dict[str, Any]:
        return {
            "paired": [n.model_dump() for n in plugin.pairing.list_paired()],
            "status": plugin.pairing.status(),
        }

    @router.post("/nodes/token", dependencies=[Depends(guard)])
    async def issue_pairing_token(
        req: PairingTokenRequest, request: Request
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "node_token")
        token = plugin.pairing.issue_token(platform=req.platform)
        _BUS.publish("node.token_issued", {"platform": req.platform})
        return {"token": token, "platform": req.platform}

    @router.delete("/nodes/{node_id}", dependencies=[Depends(guard)])
    async def revoke_node(node_id: str, request: Request) -> dict[str, Any]:
        enforce(delete_rate_limit, request, "node_revoke")
        revoked = plugin.pairing.revoke(node_id)
        if not revoked:
            raise HTTPException(status_code=404, detail="node not paired")
        _BUS.publish("node.revoked", {"node_id": node_id})
        return {"status": "revoked", "node_id": node_id}


__all__ = ["register_registry_routes"]
