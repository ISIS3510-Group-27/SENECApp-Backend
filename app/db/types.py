import enum

from sqlalchemy import CheckConstraint, Enum


def str_enum(enum_cls: type[enum.StrEnum], length: int = 20) -> Enum:
    """Store a StrEnum as VARCHAR (by value), not a native PG enum.

    Avoids ALTER TYPE migrations whenever a new value is added. Pair it with
    :func:`enum_check` in ``__table_args__`` to enforce valid values in the DB.
    """
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=length,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


def enum_check(column: str, enum_cls: type[enum.StrEnum]) -> CheckConstraint:
    """CHECK constraint restricting ``column`` to the values of ``enum_cls``."""
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return CheckConstraint(f"{column} IN ({values})", name=f"{column}_values")
