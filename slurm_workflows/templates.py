#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Filling of the placeholders of an sbatch template.
"""
import re

PLACEHOLDER_PATTERN = re.compile(r"<([^>]+)>")


def placeholders_in(
    text: str,
) -> list:
    """Return the placeholder names of a string.

    Args:
        text: Text with placeholders in the format `<name>`.

    Returns:
        list: The names without `<` and `>`.
    """
    return PLACEHOLDER_PATTERN.findall(text)


def fill_placeholders(
    lines: list,
    placeholder_values: dict,
) -> tuple:
    """Fill the placeholders of the lines.

    Args:
        lines: Lines of the template.
        placeholder_values: Dictionary mapping placeholder names to their values.

    Returns:
        tuple of:
            list - the filled lines
            set  - the placeholders without a value
    """
    processed = []
    missing = set()

    for line in lines:
        for name in placeholders_in(line):
            if name in placeholder_values:
                line = line.replace(f"<{name}>", str(placeholder_values[name]))
            else:
                missing.add(name)
        processed.append(line)

    return processed, missing


def render_template(
    template_file: str,
    sbatch_file: str,
    placeholder_values: dict,
    strict: bool = True,
) -> None:
    """Fill the placeholders of a template file and write the result to an sbatch script.

    The sbatch script is written only after all lines are processed. An incomplete sbatch script must
    never reach the disk, because a later call could submit it.

    Args:
        template_file: Path of the template.
        sbatch_file: Path of the sbatch script.
        placeholder_values: Dictionary mapping placeholder names to their values.
        strict: Raise an error instead of a warning if a placeholder has no value.
    """
    with open(template_file, "r") as myfile:
        lines = myfile.readlines()

    processed, missing = fill_placeholders(lines, placeholder_values)

    if missing:
        if strict:
            raise ValueError(f"Unresolved placeholders in template {template_file}: {sorted(missing)}. "
                             "Add them to the parameter file or to the settings file.")
        for name in sorted(missing):
            print(f"Warning: no value for placeholder <{name}>.")

    with open(sbatch_file, "w") as myfile:
        myfile.writelines(processed)

    print(f"Successfully processed {template_file} and saved output to {sbatch_file}")
