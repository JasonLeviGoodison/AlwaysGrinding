from __future__ import annotations

import contextlib
import os
import sys
import termios
import tty
from dataclasses import dataclass
from typing import Any, Callable, Sequence, TextIO


@dataclass(slots=True)
class MenuOption:
    label: str
    value: Any
    detail: str = ""


class PromptSetupUI:
    def __init__(
        self,
        input_func: Callable[[str], str] = input,
        output_func: Callable[[str], None] = print,
    ) -> None:
        self._input = input_func
        self._output = output_func

    def message(self, text: str = "") -> None:
        self._output(text)

    def text(
        self,
        title: str,
        prompt: str,
        default: str = "",
        allow_empty: bool = False,
    ) -> str:
        self._output("")
        self._output(title)
        while True:
            suffix = f" [{default}]" if default else ""
            answer = self._input(f"{prompt}{suffix}: ").strip()
            if answer:
                return answer
            if default:
                return default
            if allow_empty:
                return ""
            self._output("Enter a value.")

    def confirm(
        self,
        title: str,
        default: bool,
        yes_label: str = "Yes",
        no_label: str = "No",
    ) -> bool:
        self._output("")
        self._output(title)
        suffix = "Y/n" if default else "y/N"
        while True:
            answer = self._input(f"{yes_label}/{no_label} [{suffix}]: ").strip().lower()
            if not answer:
                return default
            if answer in {"y", "yes"}:
                return True
            if answer in {"n", "no"}:
                return False
            self._output("Please enter y or n.")

    def select(
        self,
        title: str,
        options: Sequence[MenuOption],
        default_index: int = 0,
    ) -> Any:
        self._output("")
        self._output(title)
        for index, option in enumerate(options, start=1):
            detail = f" - {option.detail}" if option.detail else ""
            self._output(f"  {index}. {option.label}{detail}")

        default_choice = options[default_index]
        while True:
            answer = self._input(f"Choice [{default_index + 1}]: ").strip()
            if not answer:
                return default_choice.value
            if answer.isdigit():
                choice = int(answer) - 1
                if 0 <= choice < len(options):
                    return options[choice].value
            for option in options:
                if answer.lower() == option.label.lower():
                    return option.value
            self._output(f"Enter a number between 1 and {len(options)}.")

    def multi_select(
        self,
        title: str,
        options: Sequence[MenuOption],
        selected_values: Sequence[Any] | None = None,
        min_selected: int = 1,
    ) -> list[Any]:
        defaults = list(selected_values or [])
        self._output("")
        self._output(title)
        for index, option in enumerate(options, start=1):
            marker = "[x]" if option.value in defaults else "[ ]"
            detail = f" - {option.detail}" if option.detail else ""
            self._output(f"  {index}. {marker} {option.label}{detail}")

        default_labels = ", ".join(str(value) for value in defaults)
        prompt = "Enter comma-separated numbers or names"
        if default_labels:
            prompt += f" [{default_labels}]"
        prompt += ": "

        while True:
            answer = self._input(prompt).strip()
            if not answer:
                if len(defaults) >= min_selected:
                    return defaults
                self._output(f"Pick at least {min_selected} option(s).")
                continue

            values: list[Any] = []
            for part in answer.split(","):
                token = part.strip()
                if not token:
                    continue
                resolved = _resolve_choice_token(token, options)
                if resolved is None:
                    self._output(f"Unknown option: {token}")
                    values = []
                    break
                if resolved not in values:
                    values.append(resolved)
            if len(values) >= min_selected:
                return values
            self._output(f"Pick at least {min_selected} option(s).")


class TerminalSetupUI:
    def __init__(
        self,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
    ) -> None:
        self._input = input_stream or sys.stdin
        self._output = output_stream or sys.stdout

    @classmethod
    def available(cls) -> bool:
        return bool(
            os.name == "posix"
            and getattr(sys.stdin, "isatty", lambda: False)()
            and getattr(sys.stdout, "isatty", lambda: False)()
        )

    def message(self, text: str = "") -> None:
        self._output.write(f"{text}\n")
        self._output.flush()

    def text(
        self,
        title: str,
        prompt: str,
        default: str = "",
        allow_empty: bool = False,
    ) -> str:
        while True:
            self._render(
                title,
                [],
                "Type your answer and press Enter.",
            )
            suffix = f" [{default}]" if default else ""
            self._output.write(f"{prompt}{suffix}: ")
            self._output.flush()
            answer = self._input.readline()
            if answer == "":
                raise KeyboardInterrupt
            value = answer.strip()
            if value:
                return value
            if default:
                return default
            if allow_empty:
                return ""
            self._render(title, [], "Enter a value and press Enter.")

    def confirm(
        self,
        title: str,
        default: bool,
        yes_label: str = "Yes",
        no_label: str = "No",
    ) -> bool:
        options = [
            MenuOption(yes_label, True),
            MenuOption(no_label, False),
        ]
        default_index = 0 if default else 1
        return bool(self.select(title, options, default_index=default_index))

    def select(
        self,
        title: str,
        options: Sequence[MenuOption],
        default_index: int = 0,
    ) -> Any:
        current = max(0, min(default_index, len(options) - 1))
        with self._terminal_session():
            while True:
                lines = []
                for index, option in enumerate(options):
                    prefix = ">" if index == current else " "
                    lines.append(f"{prefix} {option.label}")
                    if option.detail:
                        lines.append(f"    {option.detail}")
                self._render(title, lines, "Use Up/Down and Enter.")
                key = self._read_key()
                if key == "up":
                    current = (current - 1) % len(options)
                elif key == "down":
                    current = (current + 1) % len(options)
                elif key == "enter":
                    return options[current].value

    def multi_select(
        self,
        title: str,
        options: Sequence[MenuOption],
        selected_values: Sequence[Any] | None = None,
        min_selected: int = 1,
    ) -> list[Any]:
        current = 0
        selected = {option.value for option in options if option.value in set(selected_values or [])}
        footer = "Use Up/Down to move, Space to toggle, Enter to continue."
        with self._terminal_session():
            while True:
                lines = []
                for index, option in enumerate(options):
                    cursor = ">" if index == current else " "
                    marker = "[x]" if option.value in selected else "[ ]"
                    lines.append(f"{cursor} {marker} {option.label}")
                    if option.detail:
                        lines.append(f"    {option.detail}")
                if len(selected) < min_selected:
                    footer = f"Pick at least {min_selected} option(s). Space toggles, Enter confirms."
                else:
                    footer = "Use Up/Down to move, Space to toggle, Enter to continue."
                self._render(title, lines, footer)
                key = self._read_key()
                if key == "up":
                    current = (current - 1) % len(options)
                elif key == "down":
                    current = (current + 1) % len(options)
                elif key == "space":
                    value = options[current].value
                    if value in selected:
                        selected.remove(value)
                    else:
                        selected.add(value)
                elif key == "enter" and len(selected) >= min_selected:
                    return [option.value for option in options if option.value in selected]

    @contextlib.contextmanager
    def _terminal_session(self):
        self._output.write("\x1b[?25l")
        self._output.flush()
        fd = self._input.fileno()
        original = termios.tcgetattr(fd)
        tty.setraw(fd)
        try:
            yield
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, original)
            self._output.write("\x1b[?25h\x1b[2J\x1b[H")
            self._output.flush()

    def _render(self, title: str, lines: Sequence[str], footer: str) -> None:
        self._output.write("\x1b[2J\x1b[H")
        self._output.write("lid-guard setup\n\n")
        self._output.write(f"{title}\n\n")
        for line in lines:
            self._output.write(f"{line}\n")
        self._output.write(f"\n{footer}\n")
        self._output.flush()

    def _read_key(self) -> str:
        char = self._input.read(1)
        if char == "":
            raise KeyboardInterrupt
        if char in {"\r", "\n"}:
            return "enter"
        if char == " ":
            return "space"
        if char in {"k", "K"}:
            return "up"
        if char in {"j", "J"}:
            return "down"
        if char == "\x03":
            raise KeyboardInterrupt
        if char != "\x1b":
            return char

        prefix = self._input.read(1)
        if prefix not in {"[", "O"}:
            return "escape"
        suffix = self._input.read(1)
        return {
            "A": "up",
            "B": "down",
            "C": "right",
            "D": "left",
        }.get(suffix, "escape")


def _resolve_choice_token(token: str, options: Sequence[MenuOption]) -> Any | None:
    if token.isdigit():
        index = int(token) - 1
        if 0 <= index < len(options):
            return options[index].value

    lowered = token.lower()
    for option in options:
        if lowered == option.label.lower():
            return option.value
    return None
