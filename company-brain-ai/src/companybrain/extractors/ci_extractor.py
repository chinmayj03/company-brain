"""
CI/CD workflow extractor — GitHub Actions / GitLab CI / Jenkinsfile — ADR-0057.

GitHub Actions and GitLab CI are YAML; Jenkinsfile is Groovy DSL — keep parsing
shallow for the latter (we only need job names, not the full pipeline DAG).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from companybrain.extractors.base import Extractor
from companybrain.models.entities import ExtractedBatch, WorkflowJob


class CIExtractor:
    kind = "ci"

    def supports(self, path: Path) -> bool:
        name = path.name
        parts = path.parts
        # GitHub Actions
        if ".github" in parts and "workflows" in parts and (name.endswith(".yml") or name.endswith(".yaml")):
            return True
        # GitLab CI
        if name == ".gitlab-ci.yml":
            return True
        # CircleCI
        if name == "config.yml" and "circleci" in [p.lower() for p in parts]:
            return True
        # Bitbucket pipelines
        if name == "bitbucket-pipelines.yml":
            return True
        # Jenkins
        if name == "Jenkinsfile":
            return True
        return False

    def extract(self, path: Path, content: str, *, repo: str = "") -> ExtractedBatch:
        name = path.name
        parts = path.parts
        batch = ExtractedBatch(file=str(path), repo=repo, extractor_kind=self.kind)

        if ".github" in parts and "workflows" in parts:
            batch.workflow_jobs = _parse_github_actions(content, file=str(path), repo=repo)
        elif name == ".gitlab-ci.yml":
            batch.workflow_jobs = _parse_gitlab(content, file=str(path), repo=repo)
        elif name == "Jenkinsfile":
            batch.workflow_jobs = _parse_jenkinsfile(content, file=str(path), repo=repo)
        elif name in {"bitbucket-pipelines.yml"} or (name == "config.yml" and "circleci" in [p.lower() for p in parts]):
            batch.workflow_jobs = _parse_simple_yaml_ci(content, file=str(path), repo=repo, ci="circle" if "circleci" in [p.lower() for p in parts] else "bitbucket")

        return batch


def _parse_github_actions(content: str, *, file: str, repo: str) -> list[WorkflowJob]:
    try:
        tree = yaml.safe_load(content) or {}
    except Exception:
        return []
    if not isinstance(tree, dict):
        return []

    # ``on:`` may be a string, list, or dict (YAML quirk: ``on: push``)
    on_field = tree.get(True, tree.get("on"))   # PyYAML can parse "on:" as boolean True
    triggers: list[str] = []
    if isinstance(on_field, str):
        triggers = [on_field]
    elif isinstance(on_field, list):
        triggers = [str(t) for t in on_field]
    elif isinstance(on_field, dict):
        triggers = [str(k) for k in on_field.keys()]

    jobs_field = tree.get("jobs") or {}
    if not isinstance(jobs_field, dict):
        return []

    out: list[WorkflowJob] = []
    for job_id, job in jobs_field.items():
        if not isinstance(job, dict):
            continue
        steps: list[str] = []
        for step in (job.get("steps") or []):
            if not isinstance(step, dict):
                continue
            if step.get("name"):
                steps.append(str(step["name"]))
            elif step.get("uses"):
                steps.append(f"uses: {step['uses']}")
            elif step.get("run"):
                first_line = str(step["run"]).strip().splitlines()[0] if step.get("run") else ""
                steps.append(f"run: {first_line}")
        out.append(WorkflowJob(
            file=file, repo=repo,
            name=str(job.get("name") or job_id),
            triggers=triggers,
            runs_on=str(job["runs-on"]) if job.get("runs-on") else None,
            steps=steps,
            ci_system="github",
        ))
    return out


def _parse_gitlab(content: str, *, file: str, repo: str) -> list[WorkflowJob]:
    try:
        tree = yaml.safe_load(content) or {}
    except Exception:
        return []
    if not isinstance(tree, dict):
        return []
    # GitLab top-level keys are jobs unless they're reserved words.
    reserved = {"stages", "variables", "default", "include", "workflow", "image",
                "services", "before_script", "after_script", "cache"}
    out: list[WorkflowJob] = []
    for key, val in tree.items():
        if str(key).startswith(".") or key in reserved or not isinstance(val, dict):
            continue
        scripts = val.get("script") or []
        if isinstance(scripts, str):
            scripts = [scripts]
        steps = [str(s).splitlines()[0] for s in scripts if s]
        out.append(WorkflowJob(
            file=file, repo=repo,
            name=str(key),
            triggers=[],
            runs_on=str(val["image"]) if val.get("image") else None,
            steps=steps,
            ci_system="gitlab",
        ))
    return out


_JENKINS_STAGE = re.compile(r"stage\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", re.IGNORECASE)


def _parse_jenkinsfile(content: str, *, file: str, repo: str) -> list[WorkflowJob]:
    stages = _JENKINS_STAGE.findall(content)
    if not stages:
        return []
    return [
        WorkflowJob(
            file=file, repo=repo, name=name,
            triggers=[], runs_on=None, steps=[],
            ci_system="jenkins",
        )
        for name in stages
    ]


def _parse_simple_yaml_ci(content: str, *, file: str, repo: str, ci: str) -> list[WorkflowJob]:
    try:
        tree = yaml.safe_load(content) or {}
    except Exception:
        return []
    if not isinstance(tree, dict):
        return []
    out: list[WorkflowJob] = []
    if ci == "circle":
        jobs = (tree.get("jobs") or {})
        for name, spec in (jobs.items() if isinstance(jobs, dict) else []):
            steps: list[str] = []
            if isinstance(spec, dict):
                for s in (spec.get("steps") or []):
                    if isinstance(s, str):
                        steps.append(s)
                    elif isinstance(s, dict):
                        steps.append(next(iter(s.keys()), ""))
            out.append(WorkflowJob(
                file=file, repo=repo, name=str(name), triggers=[],
                runs_on=None, steps=steps, ci_system="circle",
            ))
    elif ci == "bitbucket":
        # bitbucket-pipelines: pipelines.{default,branches.X,pull-requests}.steps
        pipelines = (tree.get("pipelines") or {})
        if isinstance(pipelines, dict):
            for trigger_key, trigger_val in pipelines.items():
                if isinstance(trigger_val, list):
                    for idx, step in enumerate(trigger_val):
                        if isinstance(step, dict) and "step" in step:
                            inner = step["step"] or {}
                            out.append(WorkflowJob(
                                file=file, repo=repo,
                                name=str(inner.get("name") or f"{trigger_key}_{idx}"),
                                triggers=[str(trigger_key)],
                                runs_on=str(inner.get("image")) if inner.get("image") else None,
                                steps=[str(s) for s in (inner.get("script") or [])],
                                ci_system="bitbucket",
                            ))
    return out
