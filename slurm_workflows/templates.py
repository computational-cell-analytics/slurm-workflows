#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Access to the templates of the projects, and filling of their placeholders.
"""
import os
import re

TEMPLATE_SUFFIX = ".template"

# A placeholder name holds no whitespace, '<' or '>'. A backslash before it gives the literal text,
# e.g. '\<name>' gives '<name>'.
PLACEHOLDER_PATTERN = re.compile(r"(\\?)<([^<>\s]+)>")


def project_of_template(
    template_file: str,
) -> str:
    """Return the project of a template, which is the name of its folder.

    Args:
        template_file: Path of the template.

    Returns:
        str: Name of the project.
    """
    return os.path.basename(os.path.dirname(os.path.realpath(template_file)))


def step_template(
    template_dir: str,
    step: str,
) -> str:
    """Return the template path of a pipeline step.

    Args:
        template_dir: Template folder of the project.
        step: Name of the step, which is the template name without the suffix.

    Returns:
        str: Path of the template file.
    """
    return os.path.join(template_dir, f"{step}{TEMPLATE_SUFFIX}")


def placeholders_in(
    text: str,
) -> list:
    """Return the placeholder names of a string.

    Args:
        text: Text with placeholders in the format `<name>`.

    Returns:
        list: The names without `<` and `>`. An escaped placeholder is left out.
    """
    return [name for escape, name in PLACEHOLDER_PATTERN.findall(text) if not escape]


def fill_placeholders(
    lines: list,
    placeholder_values: dict,
) -> tuple:
    """Fill the placeholders of the lines, and remove the backslash of an escaped placeholder.

    A value is not searched for placeholders, so a value which holds `<name>` stays as it is.

    Args:
        lines: Lines of the template.
        placeholder_values: Dictionary mapping placeholder names to their values.

    Returns:
        tuple of:
            list - the filled lines
            set  - the placeholders without a value
    """
    missing = set()

    def fill(match):
        escape, name = match.groups()
        if escape:
            return f"<{name}>"
        if name in placeholder_values:
            return str(placeholder_values[name])
        missing.add(name)
        return match.group(0)

    return [PLACEHOLDER_PATTERN.sub(fill, line) for line in lines], missing


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
