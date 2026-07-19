"""Central deterministic resource limits for the local MVP evidence boundary."""

from typing import Final


MAX_SOURCE_TEXT_CHARACTERS: Final = 100_000
MAX_NORMALIZATION_SEGMENT_CHARACTERS: Final = 2_048
MAX_QUOTATIONS_PER_AUDIT: Final = 70
MAX_INDIVIDUAL_QUOTATION_CHARACTERS: Final = 2_000
MAX_TOTAL_QUOTATION_CHARACTERS_PER_AUDIT: Final = 20_000
MAX_OCCURRENCES_PER_QUOTATION: Final = 256
# Raw normalized match positions are not evidence occurrences. This independent
# ceiling permits up to twenty candidates per maximum-length source character,
# including ordinary NFKC expansions, while bounding per-quotation scan work.
MAX_NORMALIZED_CANDIDATE_POSITIONS_PER_QUOTATION: Final = 2_000_000
MAX_NORMALIZATION_WORK_UNITS: Final = 4_000_000
