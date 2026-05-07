"""Phase 7: Async HTTP client for the TypeScript tRPC structural API.

The tRPC server exposes Neo4j-backed structural queries (symbols, call graphs,
contracts, database schema, repo map) on a separate process / port from the
Spring Boot backend.

Protocol:
  POST {base_url}/{procedure}
  Body: {"input": <input-dict>}
  Success: {"result": {"data": <payload>}}
  Absent:  {"result": {"data": null}} or {"result": {"absent": true}}

Transport note: tRPC HTTP-batch queries use GET with a `batch` param;
we use the simpler single-procedure POST shape instead.

ADR-006 Phase 7 §§1–6.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

import httpx
import structlog

log = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT  = 15.0
_CONNECT_RETRY_DELAY = 1.0   # seconds to wait before one retry on ConnectError


class TrpcClient:
    """Async HTTP client for the tRPC structural API (TypeScript / Node.js).

    One instance should be created at server start-up and shared across all
    requests — httpx.AsyncClient maintains a connection pool.

    Configuration
    -------------
    TRPC_API_URL : str
        Base URL including the /trpc path segment.
        Default: ``http://cb-api:8090/trpc``
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base = (
            (base_url or os.getenv("TRPC_API_URL", "http://cb-api:8090/trpc"))
            .rstrip("/")
        )
        self._client = httpx.AsyncClient(
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=timeout,
        )
        log.info("TrpcClient initialised", base_url=self._base)

    async def close(self) -> None:
        """Release the underlying connection pool."""
        await self._client.aclose()

    # ── Core call primitive ───────────────────────────────────────────────────

    async def call(self, procedure: str, input: dict[str, Any]) -> dict[str, Any] | None:
        """Call a single tRPC procedure and return the unwrapped data payload.

        Returns ``None`` when:
          - The procedure indicates the resource is absent (``result.absent`` is
            ``True``, or ``result.data`` is ``null`` / missing).
          - Any HTTP or network error occurs (logged, never raised).

        Retries once on :class:`httpx.ConnectError` with a 1-second delay so
        that a brief tRPC server restart does not fail a tool call.

        Args:
            procedure: tRPC procedure name, e.g. ``"findSymbol"``.
            input:     Input dict forwarded verbatim inside ``{"input": ...}``.

        Returns:
            Unwrapped ``result.data`` dict, or ``None``.
        """
        url = f"{self._base}/{procedure}"
        body = {"input": input}
        _log = log.bind(procedure=procedure)

        for attempt in (1, 2):
            try:
                resp = await self._client.post(url, json=body)
                resp.raise_for_status()
                break
            except httpx.ConnectError as exc:
                if attempt == 1:
                    _log.warning(
                        "tRPC connect error — retrying in 1 s",
                        error=str(exc),
                    )
                    await asyncio.sleep(_CONNECT_RETRY_DELAY)
                    continue
                _log.error("tRPC connect error after retry", error=str(exc))
                return None
            except httpx.HTTPStatusError as exc:
                _log.error(
                    "tRPC HTTP error",
                    status_code=exc.response.status_code,
                    text=exc.response.text[:200],
                )
                return None
            except Exception as exc:
                _log.error("tRPC unexpected error", error=str(exc))
                return None

        try:
            envelope: dict[str, Any] = resp.json()
        except Exception as exc:
            _log.error("tRPC response parse error", error=str(exc))
            return None

        result = envelope.get("result", {})

        # Explicit absent marker
        if result.get("absent") is True:
            _log.debug("tRPC result absent", procedure=procedure)
            return None

        data = result.get("data")
        if data is None:
            _log.debug("tRPC result data is null", procedure=procedure)
            return None

        return data  # type: ignore[return-value]

    # ── Symbol queries ────────────────────────────────────────────────────────

    async def find_symbol(
        self,
        scope: str,
        pattern: str,
        kind: Optional[str] = None,
        limit: int = 20,
    ) -> dict[str, Any] | None:
        """Find classes, functions, methods, or types by name pattern.

        Args:
            scope:   Workspace / repo scope identifier.
            pattern: Glob-style or substring pattern.
            kind:    Optional symbol kind filter (e.g. ``"function"``, ``"class"``).
            limit:   Maximum number of results (default 20).
        """
        inp: dict[str, Any] = {"scope": scope, "pattern": pattern, "limit": limit}
        if kind is not None:
            inp["kind"] = kind
        log.debug("tRPC find_symbol", scope=scope, pattern=pattern, kind=kind)
        return await self.call("findSymbol", inp)

    async def find_callers(
        self,
        scope: str,
        symbol_id_or_name: str,
    ) -> dict[str, Any] | None:
        """Find all callers of a function or method.

        Args:
            scope:              Workspace / repo scope identifier.
            symbol_id_or_name:  Symbol UUID or qualified name.
        """
        log.debug("tRPC find_callers", scope=scope, symbol=symbol_id_or_name)
        return await self.call(
            "findCallers",
            {"scope": scope, "symbol": symbol_id_or_name},
        )

    async def find_callees(
        self,
        scope: str,
        symbol_id_or_name: str,
    ) -> dict[str, Any] | None:
        """Find what a function or method calls internally.

        Args:
            scope:              Workspace / repo scope identifier.
            symbol_id_or_name:  Symbol UUID or qualified name.
        """
        log.debug("tRPC find_callees", scope=scope, symbol=symbol_id_or_name)
        return await self.call(
            "findCallees",
            {"scope": scope, "symbol": symbol_id_or_name},
        )

    async def get_function_signature(
        self,
        scope: str,
        symbol_id_or_name: str,
    ) -> dict[str, Any] | None:
        """Get the full signature and parameter list of a function.

        Args:
            scope:              Workspace / repo scope identifier.
            symbol_id_or_name:  Symbol UUID or qualified name.
        """
        log.debug("tRPC get_function_signature", scope=scope, symbol=symbol_id_or_name)
        return await self.call(
            "getFunctionSignature",
            {"scope": scope, "symbol": symbol_id_or_name},
        )

    # ── File queries ──────────────────────────────────────────────────────────

    async def get_file_summary(
        self,
        scope: str,
        file_path: str,
    ) -> dict[str, Any] | None:
        """Get a structural summary for a single source file.

        Args:
            scope:     Workspace / repo scope identifier.
            file_path: Repository-relative file path.
        """
        log.debug("tRPC get_file_summary", scope=scope, file_path=file_path)
        return await self.call(
            "getFileSummary",
            {"scope": scope, "filePath": file_path},
        )

    async def list_files_in(
        self,
        scope: str,
        directory: str,
        recursive: bool = False,
        limit: int = 50,
    ) -> dict[str, Any] | None:
        """List files within a directory.

        Args:
            scope:      Workspace / repo scope identifier.
            directory:  Repository-relative directory path.
            recursive:  Whether to recurse into subdirectories.
            limit:      Maximum number of results (default 50).
        """
        log.debug("tRPC list_files_in", scope=scope, directory=directory)
        return await self.call(
            "listFilesIn",
            {"scope": scope, "directory": directory, "recursive": recursive, "limit": limit},
        )

    async def get_repo_map(
        self,
        scope: str,
        token_budget: int = 4000,
    ) -> dict[str, Any] | None:
        """Get a token-budgeted overview of the repository structure.

        Args:
            scope:        Workspace / repo scope identifier.
            token_budget: Approximate token budget for the response (default 4000).
        """
        log.debug("tRPC get_repo_map", scope=scope, token_budget=token_budget)
        return await self.call(
            "getRepoMap",
            {"scope": scope, "tokenBudget": token_budget},
        )

    # ── Contract / drift queries ──────────────────────────────────────────────

    async def get_contract_for_endpoint(
        self,
        scope: str,
        path: str,
        method: str,
    ) -> dict[str, Any] | None:
        """Look up the API contract (OpenAPI spec) for a specific endpoint.

        Args:
            scope:  Workspace / repo scope identifier.
            path:   URL path template, e.g. ``"/api/users/{id}"``.
            method: HTTP method, e.g. ``"GET"``, ``"POST"``.
        """
        log.debug("tRPC get_contract_for_endpoint", scope=scope, path=path, method=method)
        return await self.call(
            "getContractForEndpoint",
            {"scope": scope, "path": path, "method": method.upper()},
        )

    async def list_endpoints_implementing_contract(
        self,
        scope: str,
        contract_id: str,
        limit: int = 20,
    ) -> dict[str, Any] | None:
        """List endpoints that implement a given contract.

        Args:
            scope:       Workspace / repo scope identifier.
            contract_id: Contract / OpenAPI operation ID.
            limit:       Maximum number of results (default 20).
        """
        log.debug(
            "tRPC list_endpoints_implementing_contract",
            scope=scope,
            contract_id=contract_id,
        )
        return await self.call(
            "listEndpointsImplementingContract",
            {"scope": scope, "contractId": contract_id, "limit": limit},
        )

    async def get_drift_signals(
        self,
        scope: str,
        severity: Optional[str] = None,
        limit: int = 20,
    ) -> dict[str, Any] | None:
        """Get contract-to-implementation divergence signals.

        Args:
            scope:    Workspace / repo scope identifier.
            severity: Optional filter: ``"low"``, ``"medium"``, or ``"high"``.
            limit:    Maximum number of results (default 20).
        """
        inp: dict[str, Any] = {"scope": scope, "limit": limit}
        if severity is not None:
            inp["severity"] = severity
        log.debug("tRPC get_drift_signals", scope=scope, severity=severity)
        return await self.call("getDriftSignals", inp)

    # ── Database schema queries ───────────────────────────────────────────────

    async def get_table_for_entity(
        self,
        scope: str,
        entity_name: str,
    ) -> dict[str, Any] | None:
        """Resolve a domain entity name to its backing database table.

        Args:
            scope:       Workspace / repo scope identifier.
            entity_name: Domain entity name (e.g. ``"User"``, ``"Order"``).
        """
        log.debug("tRPC get_table_for_entity", scope=scope, entity_name=entity_name)
        return await self.call(
            "getTableForEntity",
            {"scope": scope, "entityName": entity_name},
        )

    async def find_columns_with_pattern(
        self,
        scope: str,
        pattern: str,
        limit: int = 20,
    ) -> dict[str, Any] | None:
        """Find database columns matching a name pattern.

        Args:
            scope:   Workspace / repo scope identifier.
            pattern: Substring or glob pattern for column names.
            limit:   Maximum number of results (default 20).
        """
        log.debug("tRPC find_columns_with_pattern", scope=scope, pattern=pattern)
        return await self.call(
            "findColumnsWithPattern",
            {"scope": scope, "pattern": pattern, "limit": limit},
        )

    async def get_foreign_keys(
        self,
        scope: str,
        table_name: str,
    ) -> dict[str, Any] | None:
        """Get foreign key relationships for a database table.

        Args:
            scope:      Workspace / repo scope identifier.
            table_name: Database table name.
        """
        log.debug("tRPC get_foreign_keys", scope=scope, table_name=table_name)
        return await self.call(
            "getForeignKeys",
            {"scope": scope, "tableName": table_name},
        )

    # ── Health ────────────────────────────────────────────────────────────────

    async def health(self, scope: Optional[str] = None) -> dict[str, Any] | None:
        """Check tRPC API health and Neo4j node count.

        Args:
            scope: Optional scope to report node count for.
        """
        inp: dict[str, Any] = {}
        if scope is not None:
            inp["scope"] = scope
        log.debug("tRPC health check", scope=scope)
        return await self.call("health", inp)
