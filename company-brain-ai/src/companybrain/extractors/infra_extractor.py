"""
Infra-as-code extractor — Dockerfile / docker-compose / Makefile / Procfile / Terraform — ADR-0057.

Each format is parsed with a tiny deterministic parser. Terraform (.tf) gets
shallow extraction here; deep extraction is owned by ADR-0058.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from companybrain.extractors.base import Extractor
from companybrain.models.entities import (
    ContainerImage,
    ExtractedBatch,
    RuntimeStage,
    ServiceDefinition,
)


class InfraExtractor:
    kind = "infra"

    def supports(self, path: Path) -> bool:
        name = path.name
        if name == "Dockerfile" or name.startswith("Dockerfile."):
            return True
        if name in {"docker-compose.yml", "docker-compose.yaml"} or name.startswith("docker-compose."):
            return name.endswith(".yml") or name.endswith(".yaml")
        if name in {"Makefile", "GNUmakefile"} or name == "Procfile":
            return True
        if path.suffix.lower() == ".tf":
            return True
        return False

    def extract(self, path: Path, content: str, *, repo: str = "") -> ExtractedBatch:
        name = path.name
        batch = ExtractedBatch(file=str(path), repo=repo, extractor_kind=self.kind)

        if name == "Dockerfile" or name.startswith("Dockerfile."):
            images, stages = _parse_dockerfile(content, file=str(path), repo=repo)
            batch.container_images = images
            batch.runtime_stages = stages
        elif name.startswith("docker-compose"):
            batch.service_defs = _parse_compose(content, file=str(path), repo=repo)
        elif name in {"Makefile", "GNUmakefile"} or name == "Procfile" or path.suffix.lower() == ".tf":
            # Shallow only — full extraction lives in ADR-0058 / future ADRs.
            pass

        return batch


_DOCKER_FROM = re.compile(r"^\s*FROM\s+([^\s]+)(?:\s+AS\s+([^\s]+))?", re.IGNORECASE | re.MULTILINE)
_DOCKER_EXPOSE = re.compile(r"^\s*EXPOSE\s+(.+)$", re.IGNORECASE | re.MULTILINE)
_DOCKER_ENTRYPOINT = re.compile(r"^\s*ENTRYPOINT\s+(.+)$", re.IGNORECASE | re.MULTILINE)
_DOCKER_CMD = re.compile(r"^\s*CMD\s+(.+)$", re.IGNORECASE | re.MULTILINE)


def _parse_dockerfile(content: str, *, file: str, repo: str) -> tuple[list[ContainerImage], list[RuntimeStage]]:
    images: list[ContainerImage] = []
    stages: list[RuntimeStage] = []

    # Find FROM blocks; split content by FROM positions so per-stage EXPOSE/CMD attach correctly.
    from_matches = list(_DOCKER_FROM.finditer(content))
    if not from_matches:
        return images, stages

    for idx, m in enumerate(from_matches):
        image = m.group(1)
        alias = m.group(2)
        images.append(ContainerImage(file=file, repo=repo, name=image, stage_alias=alias))

        start = m.end()
        end = from_matches[idx + 1].start() if idx + 1 < len(from_matches) else len(content)
        stage_body = content[start:end]

        ports: list[int] = []
        for em in _DOCKER_EXPOSE.finditer(stage_body):
            for tok in em.group(1).split():
                port_part = tok.split("/")[0]
                if port_part.isdigit():
                    ports.append(int(port_part))

        entrypoint_match = _DOCKER_ENTRYPOINT.search(stage_body)
        cmd_match = _DOCKER_CMD.search(stage_body)

        stages.append(RuntimeStage(
            file=file, repo=repo,
            name=alias or f"stage_{idx}",
            base_image=image,
            exposed_ports=ports,
            entrypoint=entrypoint_match.group(1).strip() if entrypoint_match else None,
            cmd=cmd_match.group(1).strip() if cmd_match else None,
        ))

    return images, stages


def _parse_compose(content: str, *, file: str, repo: str) -> list[ServiceDefinition]:
    try:
        tree = yaml.safe_load(content) or {}
    except Exception:
        return []
    services = tree.get("services") if isinstance(tree, dict) else None
    if not isinstance(services, dict):
        return []

    out: list[ServiceDefinition] = []
    for name, spec in services.items():
        if not isinstance(spec, dict):
            continue
        env_raw = spec.get("environment") or {}
        env: dict[str, str] = {}
        if isinstance(env_raw, dict):
            env = {str(k): "" if v is None else str(v) for k, v in env_raw.items()}
        elif isinstance(env_raw, list):
            for item in env_raw:
                if isinstance(item, str) and "=" in item:
                    k, _, v = item.partition("=")
                    env[k.strip()] = v.strip()
        ports_raw = spec.get("ports") or []
        ports = [str(p) for p in ports_raw] if isinstance(ports_raw, list) else []
        depends_on_raw = spec.get("depends_on") or []
        if isinstance(depends_on_raw, dict):
            depends_on = list(depends_on_raw.keys())
        elif isinstance(depends_on_raw, list):
            depends_on = [str(d) for d in depends_on_raw]
        else:
            depends_on = []
        out.append(ServiceDefinition(
            file=file, repo=repo, name=str(name),
            image=spec.get("image"),
            ports=ports, env=env, depends_on=depends_on,
        ))
    return out
