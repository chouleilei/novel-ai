"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-03-26 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("genre", sa.String(length=50)),
        sa.Column("style", sa.String(length=50)),
        sa.Column("global_prompt", sa.Text(), nullable=False),
        sa.Column("total_chapters", sa.Integer(), nullable=False),
        sa.Column("current_chapter", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("auto_mode", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "project_model_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("temperature", sa.Numeric(3, 2), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("extra_config", postgresql.JSONB(astext_type=sa.Text())),
        sa.UniqueConstraint("project_id", "role", name="uq_project_role"),
    )
    op.create_table(
        "chapter_outlines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_number", sa.Integer(), nullable=False),
        sa.Column("outline_text", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "chapter_number", name="uq_outline_project_chapter"),
    )
    op.create_table(
        "chapters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("final_content", sa.Text()),
        sa.Column("final_score", sa.Numeric(4, 2)),
        sa.Column("accepted_attempt_id", postgresql.UUID(as_uuid=True)),
        sa.Column("improvement_notes", sa.Text()),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "chapter_number", name="uq_chapter_project_chapter"),
    )
    op.create_table(
        "chapter_prompts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_number", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("generated_system_prompt", sa.Text(), nullable=False),
        sa.Column("user_edited_prompt", sa.Text()),
        sa.Column("effective_system_prompt", sa.Text(), nullable=False),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="generated"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "chapter_number", "version_no", name="uq_prompt_project_chapter_version"),
    )
    op.create_table(
        "chapter_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_number", sa.Integer(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("key_events", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("character_changes", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("world_changes", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("unresolved_threads", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("emotional_tone", sa.String(length=50)),
        sa.Column("time_location", sa.String(length=200)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "chapter_number", name="uq_summary_project_chapter"),
    )
    op.create_table(
        "characters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=50)),
        sa.Column("profile_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "name", name="uq_character_project_name"),
    )
    op.create_table(
        "world_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("setting_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "category", "name", name="uq_world_project_category_name"),
    )
    op.create_table(
        "character_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_number", sa.Integer(), nullable=False),
        sa.Column("character_name", sa.String(length=100), nullable=False),
        sa.Column("change_type", sa.String(length=30), nullable=False),
        sa.Column("patch_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 2)),
        sa.Column("apply_mode", sa.String(length=30), nullable=False, server_default="auto_safe"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "world_setting_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_number", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("change_type", sa.String(length=30), nullable=False),
        sa.Column("patch_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 2)),
        sa.Column("apply_mode", sa.String(length=30), nullable=False, server_default="auto_safe"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "generation_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_number", sa.Integer()),
        sa.Column("job_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("lease_owner", sa.String(length=100)),
        sa.Column("lease_expires_at", sa.DateTime()),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "project_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_number", sa.Integer()),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("event_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "chapter_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("prompt_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("content", sa.Text()),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()),
        sa.UniqueConstraint("chapter_id", "attempt_no", name="uq_attempt_chapter_no"),
    )
    op.create_table(
        "chapter_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chapter_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("overall_score", sa.Numeric(4, 2), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("outline_score", sa.Numeric(4, 2), nullable=False),
        sa.Column("instruction_score", sa.Numeric(4, 2), nullable=False),
        sa.Column("continuity_score", sa.Numeric(4, 2), nullable=False),
        sa.Column("character_score", sa.Numeric(4, 2), nullable=False),
        sa.Column("writing_score", sa.Numeric(4, 2), nullable=False),
        sa.Column("blocking_issues", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("uncovered_outline_points", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("violated_instructions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("improvement_suggestions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("non_scoring_notes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("attempt_id", name="uq_review_attempt"),
    )


def downgrade() -> None:
    for table in [
        "chapter_reviews",
        "chapter_attempts",
        "project_events",
        "generation_jobs",
        "world_setting_revisions",
        "character_revisions",
        "world_settings",
        "characters",
        "chapter_summaries",
        "chapter_prompts",
        "chapters",
        "chapter_outlines",
        "project_model_configs",
        "projects",
    ]:
        op.drop_table(table)
