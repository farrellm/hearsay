"""The pipeline's stages and the order work is done in (design.md §3, §5.3).

Each stage of an episode is a row in `tasks`. M1.2 queues downloads; their runner and the
later stages arrive with M1.3 and Phase 2.
"""

from enum import IntEnum

DOWNLOAD = "download"


class Priority(IntEnum):
    """Higher runs first: Transcribe now > Up Next > new episodes > backfill."""

    BACKFILL = 0
    NEW = 10  # a new episode of an automatic show, or one you picked
    UP_NEXT = 20
    NOW = 30
