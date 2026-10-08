from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator


def _to_utc(value: datetime) -> datetime:
    # SQLite hands back naive datetimes; DriveSync only ever stores UTC.
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


# Use for every datetime the API returns, so clients always get an explicit UTC offset.
UtcDatetime = Annotated[datetime, AfterValidator(_to_utc)]
