# Rich UI Guidelines

This file defines the default progress-bar UI standard for scripts in this repository.

## When To Use Rich

- Use `rich.progress` for any script that processes more than one file.

## Progress Layout

- Use left-justified, non-expanding layout: `expand=False`.
- Keep task descriptions at a fixed width (for example `.ljust(25)`) so the bar does not shift horizontally.

## Standard Progress Columns

- `SpinnerColumn()`
- `TextColumn("[progress.description]{task.description}")`
- `BarColumn(bar_width=40)`
- `MofNCompleteColumn()`
- `TaskProgressColumn()`
- `TimeElapsedColumn()`

## Minimal Reference Pattern

```python
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

console = Console()
progress = Progress(
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(bar_width=40),
    MofNCompleteColumn(),
    TaskProgressColumn(),
    TimeElapsedColumn(),
    console=console,
    expand=False,
)

with progress:
    task = progress.add_task("Processing files".ljust(25), total=100)
    for _ in range(100):
        progress.advance(task)
```
