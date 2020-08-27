from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
from cyvcf2 import VCF

from sgkit.typing import PathType
from sgkit_vcf.csi import read_csi
from sgkit_vcf.tbi import read_tabix
from sgkit_vcf.utils import ceildiv, get_file_length


def region_string(contig: str, start: int, end: Optional[int] = None) -> str:
    if end is not None:
        return f"{contig}:{start}-{end}"
    else:
        return f"{contig}:{start}-"


def get_tabix_path(vcf_path: PathType) -> Optional[Path]:
    tbi_path = Path(vcf_path).parent / (Path(vcf_path).name + ".tbi")
    if tbi_path.exists():
        return tbi_path
    else:
        return None


def get_csi_path(vcf_path: PathType) -> Optional[Path]:
    csi_path = Path(vcf_path).parent / (Path(vcf_path).name + ".csi")
    if csi_path.exists():
        return csi_path
    else:
        return None


def read_index(index_path: Path) -> Any:
    if index_path.suffix == ".tbi":
        return read_tabix(index_path)
    elif index_path.suffix == ".csi":
        return read_csi(index_path)
    else:
        raise ValueError("Only .tbi or .csi indexes are supported.")


def get_sequence_names(vcf_path: Path, index: Any) -> Any:
    try:
        # tbi stores sequence names
        return index.sequence_names
    except AttributeError:
        # ... but csi doesn't, so fall back to the VCF header
        return VCF(vcf_path).seqnames


def partition_into_regions(
    vcf_path: PathType,
    *,
    index_path: Optional[PathType] = None,
    num_parts: Optional[int] = None,
    target_part_size: Optional[int] = None,
) -> Optional[Sequence[str]]:
    """
    Calculate genomic region strings to partition a compressed VCF or BCF file into roughly equal parts.

    A .tbi or .csi file is used to find BGZF boundaries in the compressed VCF file, which are then
    used to divide the file into parts.

    The number of parts can specified directly by providing `num_parts`, or by specifying the
    desired size of each part by providing `target_part_size`. Exactly one of `num_parts` or
    `target_part_size` must be provided.

    Both `num_parts` and `target_part_size` serve as hints: the number of parts and their sizes
    may be more or less than these parameters.
    """
    if num_parts is None and target_part_size is None:
        raise ValueError("One of num_parts or target_part_size must be specified")

    if num_parts is not None and target_part_size is not None:
        raise ValueError("Only one of num_parts or target_part_size may be specified")

    if num_parts is not None and num_parts < 1:
        raise ValueError("num_parts must be positive")

    if target_part_size is not None and target_part_size < 1:
        raise ValueError("target_part_size must be positive")

    if index_path is None:
        index_path = get_tabix_path(vcf_path)
        if index_path is None:
            index_path = get_csi_path(vcf_path)
            if index_path is None:
                raise ValueError("Cannot find .tbi or .csi file.")

    # Calculate the desired part file boundaries
    file_length = get_file_length(vcf_path)
    if num_parts is not None:
        target_part_size = file_length // num_parts
    elif target_part_size is not None:
        num_parts = ceildiv(file_length, target_part_size)
    if num_parts == 1:
        return None
    part_lengths = np.array([i * target_part_size for i in range(num_parts)])  # type: ignore

    # Get the file offsets from .tbi/.csi
    index = read_index(index_path)
    sequence_names = get_sequence_names(vcf_path, index)
    file_offsets, region_contig_indexes, region_positions = index.offsets()

    # Search the file offsets to find which indexes the part lengths fall at
    ind = np.searchsorted(file_offsets, part_lengths)

    # Drop any parts that are greater than the file offsets (these will be covered by a region with no end)
    ind = np.delete(ind, ind >= len(file_offsets))

    # Drop any duplicates
    ind = np.unique(ind)

    # Calculate region contig and start for each index
    region_contigs = region_contig_indexes[ind]
    region_starts = region_positions[ind]

    # Build region query strings
    regions = []
    for i in range(len(region_starts)):
        contig = sequence_names[region_contigs[i]]
        start = region_starts[i]

        if i == len(region_starts) - 1:  # final region
            regions.append(region_string(contig, start))
        else:
            next_contig = sequence_names[region_contigs[i + 1]]
            next_start = region_starts[i + 1]
            end = next_start - 1  # subtract one since positions are inclusive
            if next_contig == contig:  # contig doesn't change
                regions.append(region_string(contig, start, end))
            else:  # contig changes, so need two regions (or possibly more if any sequences were skipped)
                regions.append(region_string(contig, start))
                for ri in range(region_contigs[i] + 1, region_contigs[i + 1]):
                    regions.append(sequence_names[ri])  # pragma: no cover
                regions.append(region_string(next_contig, 1, end))
    # Add any sequences at the end that were not skipped
    for ri in range(region_contigs[-1] + 1, len(sequence_names)):
        regions.append(sequence_names[ri])  # pragma: no cover

    return regions
