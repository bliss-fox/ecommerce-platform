from pathlib import Path

from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, event, inspect
from sqlmodel import create_engine

from xhs_seller.models import ModelBase

DEMO_DB = Path(__file__).resolve().parents[2] / "demo.sqlite3.db"
REAL_DB = Path(__file__).resolve().parents[2] / "local.sqlite3.db"


class SchemaMismatchError(RuntimeError):
    pass


def make_engine(path: Path = DEMO_DB):
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    return engine


def init_db(engine):
    with engine.begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        inspector = inspect(connection)
        existing = set(inspector.get_table_names())
        missing = []
        incompatible = []
        for name, table in sorted(ModelBase.metadata.tables.items()):
            if name in existing:
                columns = {column["name"]: column for column in inspector.get_columns(name)}
                absent = sorted({column.name for column in table.columns} - columns.keys())
                if absent:
                    missing.append(f"{name}：{', '.join(absent)}")
                    continue
                nullable = [
                    column.name
                    for column in table.columns
                    if not column.nullable and columns[column.name]["nullable"]
                ]
                if nullable:
                    incompatible.append(f"{name} 必填约束：{', '.join(nullable)}")
                primary = tuple(column.name for column in table.primary_key.columns)
                if tuple(inspector.get_pk_constraint(name)["constrained_columns"]) != primary:
                    incompatible.append(f"{name} 主键")
                expected_unique = {
                    tuple(column.name for column in constraint.columns)
                    for constraint in table.constraints
                    if isinstance(constraint, UniqueConstraint)
                }
                if expected_unique:
                    actual_unique = {
                        tuple(item["column_names"])
                        for item in inspector.get_unique_constraints(name)
                    }
                    actual_unique.update(
                        tuple(item["column_names"])
                        for item in inspector.get_indexes(name)
                        if item["unique"]
                        and item.get("dialect_options", {}).get("sqlite_where") is None
                    )
                    if expected_unique - actual_unique:
                        incompatible.append(f"{name} 唯一约束")
                expected_fk = {
                    (
                        tuple(element.parent.name for element in constraint.elements),
                        constraint.elements[0].column.table.name,
                        tuple(element.column.name for element in constraint.elements),
                    )
                    for constraint in table.constraints
                    if isinstance(constraint, ForeignKeyConstraint)
                }
                if expected_fk:
                    actual_fk = {
                        (
                            tuple(item["constrained_columns"]),
                            item["referred_table"],
                            tuple(item["referred_columns"]),
                        )
                        for item in inspector.get_foreign_keys(name)
                    }
                    if expected_fk - actual_fk:
                        incompatible.append(f"{name} 外键")
        if missing or incompatible:
            reasons = []
            if missing:
                reasons.append("缺少必要字段：" + "；".join(missing))
            if incompatible:
                reasons.append("缺少或不匹配必要约束：" + "；".join(incompatible))
            raise SchemaMismatchError("数据库结构" + "；".join(reasons))
        ModelBase.metadata.create_all(connection)
