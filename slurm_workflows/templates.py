#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Filling of the placeholders of an sbatch template.
"""
import re

PLACEHOLDER_PATTERN = re.compile(r"<([^>]+)>")


def extract_substrings(
    input_string: str,
) -> list:
    """Return the placeholder names of a string.

    Args:
        input_string: Text with placeholders in the format `<name>`.

    Returns:
        list: The names without `<` and `>`.
    """
    return PLACEHOLDER_PATTERN.findall(input_string)


def replace_substrings(
    lines: list,
    replacement_dict: dict,
) -> tuple:
    """Fill the placeholders of the lines.

    Args:
        lines: Lines of the template.
        replacement_dict: Dictionary mapping placeholder names to their replacements.

    Returns:
        tuple of:
            list - the filled lines
            set  - the placeholders without a replacement
    """
    processed = []
    missing = set()

    for line in lines:
        for substr in extract_substrings(line):
            if substr in replacement_dict:
                line = line.replace(f"<{substr}>", str(replacement_dict[substr]))
            else:
                missing.add(substr)
        processed.append(line)

    return processed, missing


def replace_substrings_in_file(
    input_filename: str,
    output_filename: str,
    replacement_dict: dict,
    strict: bool = True,
) -> None:
    """Fill the placeholders of a template file and write the result to a new file.

    The output file is written only after all lines are processed. An incomplete sbatch script must
    never reach the disk, because a later call could submit it.

    Args:
        input_filename: Path of the template.
        output_filename: Path of the sbatch script.
        replacement_dict: Dictionary mapping placeholder names to their replacements.
        strict: Raise an error instead of a warning if a placeholder has no replacement.
    """
    with open(input_filename, "r") as input_file:
        lines = input_file.readlines()

    processed, missing = replace_substrings(lines, replacement_dict)

    if missing:
        if strict:
            raise ValueError(f"Unresolved placeholders in template {input_filename}: {sorted(missing)}. "
                             "Add them to the parameter file or to the settings file.")
        for substr in sorted(missing):
            print(f"Warning: no replacement for placeholder <{substr}>.")

    with open(output_filename, "w") as output_file:
        output_file.writelines(processed)

    print(f"Successfully processed {input_filename} and saved output to {output_filename}")
