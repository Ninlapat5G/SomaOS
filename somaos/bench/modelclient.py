"""Talking to a local model, for experiments only.

This lives in ``bench/`` and not in ``broker/`` deliberately. The runtime
must stay transport-free: it takes a ``complete(prompt) -> str`` callable
and knows nothing about how the text is produced. Putting a client in the
runtime would make every application that embeds SomaOS inherit an
opinion about HTTP, and would make ``somaos/modelbus/`` real a phase
early (CLAUDE.md scope).

Speaks OpenAI-compatible ``/v1/chat/completions``, which ollama,
llama.cpp's server, vLLM and LM Studio all serve, so one client covers
every way the model is likely to be hosted. Stdlib ``urllib`` only -- no
new dependency (CLAUDE.md).

Two things beyond a bare POST, both because of what an experiment needs
rather than what a demo needs:

``RecordingModel`` writes every prompt and reply to a file, and
``ReplayModel`` reads them back. A model-in-the-loop run costs real time
and is not reproducible -- a local model's output drifts with sampling,
with a version bump, with load. Being able to replay the exact
transcript is what makes a result checkable later by someone who does
not have the endpoint, which is the same reason the rest of the bench is
seeded rather than random.

``StubModel`` answers from a fixed script. It is what the tests use, and
it is why the whole harness can be finished and proven before an
endpoint exists.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_TIMEOUT = 60.0
DEFAULT_RETRIES = 2


class ModelError(RuntimeError):
    """The endpoint could not be reached, or answered with nothing usable."""


class ChatModel:
    """An OpenAI-compatible chat endpoint.

    ``temperature`` defaults to 0. A walk driven at a higher temperature
    is not reproducible even against the same server, and the first
    question being asked of a model here is whether it navigates better
    than a deterministic searcher -- not whether it can be lucky.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        model: str,
        api_key: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 16,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        if not self.endpoint.endswith("/chat/completions"):
            self.endpoint = f"{self.endpoint}/v1/chat/completions"
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        # Small: the reply is a number. Capping it stops a chatty model
        # from spending a second per step explaining itself, and the
        # parser does not need the explanation.
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.retries = retries
        #: Wall-clock seconds spent waiting on the model. Reported so the
        #: cost of putting it in the loop sits beside what it bought.
        self.seconds = 0.0
        self.calls = 0

    def complete(self, prompt: str) -> str:
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last: Exception | None = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(
                self.endpoint, data=body, headers=headers, method="POST"
            )
            started = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.seconds += time.monotonic() - started
                self.calls += 1
                return self._extract(payload)
            except (urllib.error.URLError, TimeoutError, OSError,
                    json.JSONDecodeError) as exc:
                self.seconds += time.monotonic() - started
                last = exc
                if attempt < self.retries:
                    time.sleep(0.5 * (attempt + 1))
        raise ModelError(f"{self.endpoint} did not answer: {last}")

    @staticmethod
    def _extract(payload: dict) -> str:
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError(
                f"endpoint replied with no message content: "
                f"{json.dumps(payload)[:200]}"
            ) from exc

    def __repr__(self) -> str:
        return f"ChatModel({self.model!r} at {self.endpoint!r})"


class StubModel:
    """Answers from a script. What the tests and the offline harness use.

    Exists so the whole comparison can be built and proven before any
    endpoint does. ``answers`` may be a list consumed in order, or a
    callable taking the prompt -- the second is how a stand-in that
    actually reads the menu is written.
    """

    def __init__(self, answers) -> None:
        self._answers = answers
        self._index = 0
        self.calls = 0
        self.seconds = 0.0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        if callable(self._answers):
            return self._answers(prompt)
        if self._index >= len(self._answers):
            return "stop"
        reply = self._answers[self._index]
        self._index += 1
        return reply

    def __repr__(self) -> str:
        return f"StubModel(calls={self.calls})"


class RecordingModel:
    """Wraps a model and writes every exchange to a JSONL file."""

    def __init__(self, inner, path: str | Path) -> None:
        self.inner = inner
        self.path = Path(path)
        self._handle = self.path.open("w", encoding="utf-8")
        self.calls = 0

    @property
    def seconds(self) -> float:
        return getattr(self.inner, "seconds", 0.0)

    def complete(self, prompt: str) -> str:
        reply = self.inner.complete(prompt)
        self.calls += 1
        self._handle.write(json.dumps(
            {"prompt": prompt, "reply": reply}, ensure_ascii=False
        ) + "\n")
        self._handle.flush()
        return reply

    def close(self) -> None:
        self._handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class ReplayModel:
    """Replays a recorded transcript, checking it still lines up.

    Prompts are matched rather than merely counted. A replay that had
    drifted -- because the tree, the prompt template or the seed changed
    -- would otherwise answer the wrong question with the right-looking
    text, and the run would look reproducible while being nothing of the
    kind.
    """

    def __init__(self, path: str | Path, *, strict: bool = True) -> None:
        self.path = Path(path)
        self.strict = strict
        self._rows: list[dict] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    self._rows.append(json.loads(line))
        self._index = 0
        self.calls = 0
        self.seconds = 0.0
        self.mismatches = 0

    def complete(self, prompt: str) -> str:
        if self._index >= len(self._rows):
            raise ModelError(
                f"{self.path} holds {len(self._rows)} exchanges; the run asked "
                f"for {self._index + 1}. The recording is from a different run."
            )
        row = self._rows[self._index]
        self._index += 1
        self.calls += 1
        if row["prompt"] != prompt:
            self.mismatches += 1
            if self.strict:
                raise ModelError(
                    f"replay diverged at exchange {self._index}: the recorded "
                    "prompt is not the one this run produced, so the reply "
                    "answers a different question"
                )
        return row["reply"]

    def __repr__(self) -> str:
        return f"ReplayModel({self.path.name!r}, {len(self._rows)} exchanges)"
