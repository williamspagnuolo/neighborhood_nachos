"""Text-to-SQL agent for the SF Livability Dashboard.

Given a plain-English question, uses Vertex AI Gemini to synthesize a
BigQuery `SELECT` statement against the gold dataset. The generated SQL is
validated and dry-run before it can be executed for real, and every real
execution is capped by `maximum_bytes_billed`.

Public surface:
    TextToSqlAgent(config, bq_client).generate(question) -> GeneratedQuery
    TextToSqlAgent(config, bq_client).execute(sql) -> QueryResult

The agent module never mutates BigQuery: the runtime service account
is expected to hold `roles/bigquery.dataViewer` only, and the validator
rejects anything that isn't a single `SELECT` / `WITH` statement.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import pathlib
import re
import textwrap
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

import sqlglot
import sqlglot.errors
import sqlglot.expressions as sqlglot_exp
from google.cloud import bigquery

from .config import AppConfig

LOGGER = logging.getLogger(__name__)

FORBIDDEN_STATEMENT_TYPES: tuple[type, ...] = (
    sqlglot_exp.Insert,
    sqlglot_exp.Update,
    sqlglot_exp.Delete,
    sqlglot_exp.Merge,
    sqlglot_exp.Create,
    sqlglot_exp.Drop,
    sqlglot_exp.Alter,
    sqlglot_exp.TruncateTable,
    sqlglot_exp.Command,  # catches DCL/DDL like GRANT, REVOKE, CALL, EXECUTE
)

FORBIDDEN_KEYWORD_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|DROP|ALTER|TRUNCATE|CALL|EXECUTE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)

STAGING_TABLE_SUFFIXES: tuple[str, ...] = ("__stage", "__staging")


class AgentError(Exception):
    """Base class for text-to-SQL agent errors. Message is user-safe."""


class QuestionTooShortError(AgentError):
    pass


class ModelResponseError(AgentError):
    pass


class SqlValidationError(AgentError):
    pass


class QueryTooExpensiveError(AgentError):
    pass


class SchemaUnavailableError(AgentError):
    """Raised when the target dataset exists but has no queryable tables."""


@dataclass(frozen=True)
class SchemaColumn:
    name: str
    data_type: str
    is_nullable: bool
    description: str | None = None


@dataclass(frozen=True)
class SchemaTable:
    name: str
    columns: tuple[SchemaColumn, ...]
    description: str | None = None
    row_count: int | None = None


@dataclass(frozen=True)
class DatasetMetadata:
    """Everything the prompt builder needs to describe the gold dataset."""

    tables: tuple[SchemaTable, ...]
    dataset_description: str | None = None
    semantics_notes: str | None = None


@dataclass(frozen=True)
class GeneratedQuery:
    """The output of the generate step: SQL ready to be executed."""

    question: str
    raw_sql: str
    executable_sql: str
    explanation: str
    estimated_bytes_processed: int
    model: str


@dataclass
class QueryResult:
    """The output of the execute step."""

    columns: list[str]
    rows: list[dict[str, Any]]
    total_bytes_billed: int
    duration_ms: int


# ---------------------------------------------------------------------------
# Analyst-authored semantics file
# ---------------------------------------------------------------------------


class SemanticsLoader:
    """Reads an optional analyst-editable markdown file included in the prompt.

    The file lives alongside the app code (default:
    `dashboard/app/gold_semantics.md`) so it ships in the container and can be
    edited by any teammate via a normal code review, without touching Python.
    """

    def __init__(self, file_path: pathlib.Path | None) -> None:
        self._path = file_path

    def load(self) -> str | None:
        if self._path is None:
            return None
        try:
            text = self._path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            LOGGER.info("Semantics file not present at %s; skipping.", self._path)
            return None
        except OSError as exc:
            LOGGER.warning("Could not read semantics file %s: %s", self._path, exc)
            return None
        return text or None


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------


class SchemaIntrospector:
    """Loads rich schema + metadata for the gold dataset (once, cached).

    Combines four cheap INFORMATION_SCHEMA / metadata calls:
      - `INFORMATION_SCHEMA.COLUMNS`            (name / type / nullable)
      - `INFORMATION_SCHEMA.COLUMN_FIELD_PATHS` (per-column descriptions)
      - `INFORMATION_SCHEMA.TABLE_OPTIONS`      (per-table descriptions)
      - `__TABLES__`                            (row counts)
    Plus a `get_dataset()` for the dataset-level description, and an
    optional analyst-authored markdown file for extra context.
    """

    def __init__(
        self,
        client: bigquery.Client,
        config: AppConfig,
        semantics_loader: SemanticsLoader | None = None,
    ) -> None:
        self._client = client
        self._config = config
        self._semantics_loader = semantics_loader
        self._lock = threading.Lock()
        self._cached: DatasetMetadata | None = None
        self._cached_at: float | None = None

    def load(self, refresh: bool = False) -> DatasetMetadata:
        with self._lock:
            if self._cached is not None and not refresh:
                return self._cached

            columns_by_table = self._load_columns()
            descriptions_by_col = self._load_column_descriptions()
            descriptions_by_table = self._load_table_descriptions()
            row_counts_by_table = self._load_row_counts()

            tables: list[SchemaTable] = []
            for table_name, cols in sorted(columns_by_table.items()):
                enriched_cols = tuple(
                    SchemaColumn(
                        name=col.name,
                        data_type=col.data_type,
                        is_nullable=col.is_nullable,
                        description=descriptions_by_col.get((table_name, col.name)),
                    )
                    for col in cols
                )
                tables.append(
                    SchemaTable(
                        name=table_name,
                        columns=enriched_cols,
                        description=descriptions_by_table.get(table_name),
                        row_count=row_counts_by_table.get(table_name),
                    )
                )

            metadata = DatasetMetadata(
                tables=tuple(tables),
                dataset_description=self._load_dataset_description(),
                semantics_notes=(
                    self._semantics_loader.load() if self._semantics_loader else None
                ),
            )
            self._cached = metadata
            self._cached_at = time.time()
            LOGGER.info(
                "Loaded agent schema: %d tables from %s.%s "
                "(dataset_desc=%s, semantics_notes=%s)",
                len(metadata.tables),
                self._config.bq_project,
                self._config.agent_dataset,
                bool(metadata.dataset_description),
                bool(metadata.semantics_notes),
            )
            return metadata

    def render_for_prompt(self) -> str:
        metadata = self.load()
        if not metadata.tables:
            return "(no tables found in gold dataset)"

        parts: list[str] = []
        parts.append(
            f"## Dataset: `{self._config.bq_project}.{self._config.agent_dataset}`"
        )
        if metadata.dataset_description:
            parts.append(f"Description: {metadata.dataset_description}")

        if metadata.semantics_notes:
            parts.append("")
            parts.append("## Analyst notes (edit `gold_semantics.md` to update)")
            parts.append(metadata.semantics_notes)

        parts.append("")
        parts.append("## Tables")
        for table in metadata.tables:
            row_count_suffix = (
                f" ({table.row_count:,} rows)"
                if table.row_count is not None
                else ""
            )
            parts.append("")
            parts.append(f"### `{table.name}`{row_count_suffix}")
            if table.description:
                parts.append(f"Description: {table.description}")
            parts.append("Columns:")
            for col in table.columns:
                nullable = "" if col.is_nullable else " NOT NULL"
                col_desc = f" — {col.description}" if col.description else ""
                parts.append(f"  - {col.name} {col.data_type}{nullable}{col_desc}")

        return "\n".join(parts)

    # -- individual metadata queries -----------------------------------------

    def _load_columns(self) -> dict[str, list[SchemaColumn]]:
        sql = f"""
SELECT
  table_name,
  column_name,
  data_type,
  is_nullable,
  ordinal_position
FROM `{self._config.bq_project}.{self._config.agent_dataset}.INFORMATION_SCHEMA.COLUMNS`
ORDER BY table_name, ordinal_position
"""
        by_table: dict[str, list[SchemaColumn]] = {}
        for row in self._client.query(sql).result():
            table = row["table_name"]
            if table.endswith(STAGING_TABLE_SUFFIXES):
                continue
            by_table.setdefault(table, []).append(
                SchemaColumn(
                    name=row["column_name"],
                    data_type=row["data_type"],
                    is_nullable=(row["is_nullable"] == "YES"),
                )
            )
        return by_table

    def _load_column_descriptions(self) -> dict[tuple[str, str], str]:
        """Return {(table_name, column_name): description} for top-level columns."""
        sql = f"""
SELECT table_name, column_name, description
FROM `{self._config.bq_project}.{self._config.agent_dataset}.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS`
WHERE field_path = column_name
  AND description IS NOT NULL
  AND description != ''
"""
        try:
            rows = list(self._client.query(sql).result())
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Column-description query failed, continuing without: %s", exc)
            return {}
        out: dict[tuple[str, str], str] = {}
        for row in rows:
            table = row["table_name"]
            if table.endswith(STAGING_TABLE_SUFFIXES):
                continue
            out[(table, row["column_name"])] = str(row["description"]).strip()
        return out

    def _load_table_descriptions(self) -> dict[str, str]:
        sql = f"""
SELECT table_name, option_value
FROM `{self._config.bq_project}.{self._config.agent_dataset}.INFORMATION_SCHEMA.TABLE_OPTIONS`
WHERE option_name = 'description'
"""
        try:
            rows = list(self._client.query(sql).result())
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Table-description query failed, continuing without: %s", exc)
            return {}
        out: dict[str, str] = {}
        for row in rows:
            table = row["table_name"]
            if table.endswith(STAGING_TABLE_SUFFIXES):
                continue
            raw = str(row["option_value"] or "").strip()
            # option_value is stored as a BigQuery string literal, e.g. `"hello"`.
            # Strip the surrounding quotes and unescape the classic BQ escapes.
            if len(raw) >= 2 and raw[0] == raw[-1] == '"':
                raw = raw[1:-1]
            raw = raw.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")
            if raw:
                out[table] = raw
        return out

    def _load_row_counts(self) -> dict[str, int]:
        sql = f"""
SELECT table_id AS table_name, row_count
FROM `{self._config.bq_project}.{self._config.agent_dataset}.__TABLES__`
"""
        try:
            rows = list(self._client.query(sql).result())
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Row-count query failed, continuing without: %s", exc)
            return {}
        out: dict[str, int] = {}
        for row in rows:
            table = row["table_name"]
            if table.endswith(STAGING_TABLE_SUFFIXES):
                continue
            count = row["row_count"]
            if count is None:
                continue
            out[table] = int(count)
        return out

    def _load_dataset_description(self) -> str | None:
        try:
            dataset = self._client.get_dataset(
                f"{self._config.bq_project}.{self._config.agent_dataset}"
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Could not fetch dataset description: %s", exc)
            return None
        desc = (dataset.description or "").strip()
        return desc or None


# ---------------------------------------------------------------------------
# SQL validation
# ---------------------------------------------------------------------------


class SqlValidator:
    """Static analysis of model-generated SQL before it touches BigQuery."""

    def __init__(self, row_limit: int) -> None:
        self._row_limit = row_limit

    def validate_and_cap(self, raw_sql: str) -> str:
        """Return an executable SQL string, or raise SqlValidationError."""
        cleaned = _strip_code_fences(raw_sql).strip().rstrip(";").strip()
        if not cleaned:
            raise SqlValidationError("Model returned empty SQL.")

        try:
            statements = sqlglot.parse(cleaned, read="bigquery")
        except sqlglot.errors.ParseError as exc:
            raise SqlValidationError(f"Generated SQL failed to parse: {exc}") from exc

        real_statements = [s for s in statements if s is not None]
        if len(real_statements) != 1:
            raise SqlValidationError(
                f"Expected exactly one SQL statement, got {len(real_statements)}."
            )

        statement = real_statements[0]

        if isinstance(statement, FORBIDDEN_STATEMENT_TYPES):
            raise SqlValidationError(
                f"Only SELECT / WITH statements are allowed (got {type(statement).__name__})."
            )

        if not isinstance(statement, (sqlglot_exp.Select, sqlglot_exp.Union, sqlglot_exp.Subquery)) \
                and not _is_select_with_ctes(statement):
            raise SqlValidationError(
                "Only SELECT / WITH statements are allowed."
            )

        for node in statement.walk():
            if isinstance(node, FORBIDDEN_STATEMENT_TYPES):
                raise SqlValidationError(
                    f"Query contains a forbidden statement type: {type(node).__name__}."
                )

        # Belt-and-suspenders keyword scan (catches things sqlglot might silently
        # translate away, e.g. dialect-specific DML).
        if FORBIDDEN_KEYWORD_RE.search(cleaned):
            raise SqlValidationError(
                "Query contains a forbidden keyword (INSERT/UPDATE/DELETE/MERGE/...)."
            )

        return self._apply_row_cap(cleaned)

    def _apply_row_cap(self, sql: str) -> str:
        """Wrap sql in an outer LIMIT if it doesn't already have one."""
        try:
            parsed = sqlglot.parse_one(sql, read="bigquery")
        except sqlglot.errors.ParseError:
            return sql

        existing_limit = parsed.args.get("limit")
        if existing_limit is not None:
            # Trust the model / user - they picked a LIMIT already.
            return sql

        return f"SELECT * FROM (\n{sql}\n) LIMIT {self._row_limit}"


def _is_select_with_ctes(statement: sqlglot_exp.Expression) -> bool:
    """Return True if statement is `WITH ... SELECT ...`."""
    with_expr = statement.args.get("with")
    if with_expr is None:
        return False
    inner = statement
    if isinstance(inner, sqlglot_exp.Select):
        return True
    return False


def _strip_code_fences(text: str) -> str:
    """Remove ```lang ... ``` fences the model may wrap the SQL in."""
    fenced = re.match(r"^```[a-zA-Z0-9]*\n(.*?)\n```\s*$", text.strip(), re.DOTALL)
    if fenced:
        return fenced.group(1)
    return text


# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------


PROMPT_INSTRUCTIONS = textwrap.dedent(
    """
    You are a careful BigQuery SQL author for the "{project}.{dataset}"
    dataset. Convert the user's plain-English question into a single valid
    BigQuery Standard SQL SELECT (or WITH ... SELECT) statement.

    Hard rules:
    - Only SELECT / WITH. Never INSERT, UPDATE, DELETE, MERGE, CREATE,
      DROP, ALTER, TRUNCATE, CALL, EXECUTE, GRANT, or REVOKE.
    - Fully qualify every table as `{project}.{dataset}.<table_name>`.
    - Only reference columns that exist in the schema below. If a
      requested column doesn't exist, say so in `explanation` and still
      return your best-effort SQL that uses columns that DO exist.
    - Assume all TIMESTAMP columns are stored in UTC. The user thinks in
      Pacific Time ("America/Los_Angeles") — convert with
      TIMESTAMP(datetime_value, "America/Los_Angeles") and
      DATE(ts, "America/Los_Angeles") as appropriate.
    - Always include an outer LIMIT of at most {row_limit}.
    - Prefer aggregation and ORDER BY when the question implies "top",
      "most", "highest", "lowest", "trend", etc.
    - Never use SELECT * on tables you haven't first narrowed with a WHERE
      or aggregation — the row_count numbers shown per table are exact and
      some tables may be very large.

    Context priority (treat everything below as authoritative):
    1. The dataset description tells you what the dataset is for overall.
    2. The "Analyst notes" section captures conventions and gotchas that
       aren't obvious from the schema (join keys, business definitions,
       cross-table warnings). Follow these carefully.
    3. Per-table and per-column descriptions explain what each field
       actually means. Prefer them over guessing from column names.

    {schema}

    Return ONLY a JSON object with exactly two string fields:
      {{"sql": "<the SQL>", "explanation": "<1-3 sentences plain English>"}}
    Do NOT wrap the JSON in markdown code fences.
    """
).strip()


class GeminiClient:
    """Thin wrapper around google-genai in Vertex mode."""

    def __init__(self, config: AppConfig) -> None:
        # Imported lazily so that unit tests / local runs without the SDK
        # installed don't fail at module import time.
        from google import genai  # type: ignore[import-not-found]

        self._genai = genai
        self._client = genai.Client(
            vertexai=True,
            project=config.llm_project,
            location=config.llm_location,
        )
        self._model = config.llm_model
        self._timeout_seconds = config.llm_timeout_seconds

    def generate_json(self, system_instructions: str, user_prompt: str) -> dict[str, Any]:
        from google.genai import types  # type: ignore[import-not-found]

        response = self._client.models.generate_content(
            model=self._model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instructions,
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

        text = _extract_text(response)
        if not text:
            raise ModelResponseError("Model returned an empty response.")

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ModelResponseError(
                f"Model response was not valid JSON: {exc.msg}"
            ) from exc

        if not isinstance(payload, dict):
            raise ModelResponseError("Model response was not a JSON object.")
        return payload

    @property
    def model_name(self) -> str:
        return self._model


def _extract_text(response: Any) -> str:
    """Best-effort text extraction across google-genai response shapes."""
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text
    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text.strip():
                return part_text
    return ""


# ---------------------------------------------------------------------------
# SQL executor
# ---------------------------------------------------------------------------


class SqlExecutor:
    """Runs BigQuery jobs with cost and safety caps."""

    def __init__(self, client: bigquery.Client, config: AppConfig) -> None:
        self._client = client
        self._config = config

    def dry_run(self, sql: str) -> int:
        job = self._client.query(
            sql,
            job_config=bigquery.QueryJobConfig(
                dry_run=True,
                use_query_cache=False,
            ),
        )
        return int(job.total_bytes_processed or 0)

    def execute(self, sql: str) -> QueryResult:
        start = time.perf_counter()
        job = self._client.query(
            sql,
            job_config=bigquery.QueryJobConfig(
                maximum_bytes_billed=self._config.llm_max_bytes_billed,
                use_query_cache=True,
            ),
        )
        row_iter = job.result(timeout=self._config.llm_timeout_seconds)
        columns = [field.name for field in row_iter.schema]
        rows = [_row_to_dict(row) for row in row_iter]
        duration_ms = int((time.perf_counter() - start) * 1000)
        return QueryResult(
            columns=columns,
            rows=rows,
            total_bytes_billed=int(job.total_bytes_billed or 0),
            duration_ms=duration_ms,
        )


def _row_to_dict(row: bigquery.Row) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        out[key] = _jsonify(value)
    return out


def _jsonify(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


# ---------------------------------------------------------------------------
# Top-level agent
# ---------------------------------------------------------------------------


@dataclass
class AgentComponents:
    """Composable pieces of the agent, useful for testing."""

    introspector: SchemaIntrospector
    validator: SqlValidator
    executor: SqlExecutor
    gemini: GeminiClient
    config: AppConfig


class TextToSqlAgent:
    def __init__(self, components: AgentComponents) -> None:
        self._components = components

    @classmethod
    def create(cls, config: AppConfig, bq_client: bigquery.Client) -> "TextToSqlAgent":
        semantics_loader = SemanticsLoader(
            file_path=_resolve_semantics_path(config.agent_semantics_file)
        )
        introspector = SchemaIntrospector(
            client=bq_client,
            config=config,
            semantics_loader=semantics_loader,
        )
        # Eagerly load the schema at construction time so that we fail fast
        # (and clearly) if the target dataset is empty or unreadable, rather
        # than sending an empty schema to Gemini every time a user asks.
        metadata = introspector.load()
        if not metadata.tables:
            raise SchemaUnavailableError(
                f"Dataset `{config.bq_project}.{config.agent_dataset}` has no "
                "queryable tables yet. The agent will be available once the "
                "gold-layer tables are created."
            )
        return cls(
            AgentComponents(
                introspector=introspector,
                validator=SqlValidator(row_limit=config.llm_row_limit),
                executor=SqlExecutor(client=bq_client, config=config),
                gemini=GeminiClient(config),
                config=config,
            )
        )

    def generate(self, question: str) -> GeneratedQuery:
        question = (question or "").strip()
        if len(question) < 5:
            raise QuestionTooShortError(
                "Please ask a longer question (at least a few words)."
            )

        schema_text = self._components.introspector.render_for_prompt()
        system_instructions = PROMPT_INSTRUCTIONS.format(
            project=self._components.config.bq_project,
            dataset=self._components.config.agent_dataset,
            schema=schema_text,
            row_limit=self._components.config.llm_row_limit,
        )
        payload = self._components.gemini.generate_json(
            system_instructions=system_instructions,
            user_prompt=question,
        )

        raw_sql = str(payload.get("sql", "")).strip()
        explanation = str(payload.get("explanation", "")).strip()
        if not raw_sql:
            raise ModelResponseError("Model did not return a `sql` field.")

        executable_sql = self._components.validator.validate_and_cap(raw_sql)

        estimated = self._components.executor.dry_run(executable_sql)
        if estimated > self._components.config.llm_max_bytes_billed:
            raise QueryTooExpensiveError(
                f"Query would scan ~{_format_bytes(estimated)}, over the "
                f"{_format_bytes(self._components.config.llm_max_bytes_billed)} cap. "
                "Try narrowing the question (e.g. add a date range or a boundary)."
            )

        return GeneratedQuery(
            question=question,
            raw_sql=raw_sql,
            executable_sql=executable_sql,
            explanation=explanation,
            estimated_bytes_processed=estimated,
            model=self._components.gemini.model_name,
        )

    def execute(self, sql: str) -> QueryResult:
        # Re-validate in case the SQL was passed in via a client-side round-trip
        # and could have been tampered with.
        executable = self._components.validator.validate_and_cap(sql)
        return self._components.executor.execute(executable)


def format_bytes(num_bytes: int) -> str:
    """Public helper used by the UI."""
    return _format_bytes(num_bytes)


def _format_bytes(num_bytes: int) -> str:
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(num_bytes) < step:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= step  # type: ignore[assignment]
    return f"{num_bytes:.1f} EB"


def _resolve_semantics_path(raw: str | None) -> pathlib.Path | None:
    """Resolve the analyst-authored semantics file path.

    Search order:
      1. Explicit override (`AppConfig.agent_semantics_file`, i.e. the
         DASH_AGENT_SEMANTICS_FILE env var).
      2. `<repo>/dashboard/app/gold_semantics.md`, i.e. next to this file.
      3. None (no semantics file — introspector skips silently).
    """
    if raw:
        path = pathlib.Path(raw).expanduser()
        return path if path.is_absolute() else path.resolve()
    default = pathlib.Path(__file__).with_name("gold_semantics.md")
    return default if default.exists() else None
