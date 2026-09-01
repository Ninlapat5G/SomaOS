"""Turning a walk into a question a model can answer, and back again.

``describe()`` produces the data a chooser needs. This turns that into
text and turns a reply back into a move. It is kept separate from the
navigator because the two fail differently: a navigator bug loses a
memory, a prompting bug loses only this turn, and mixing them would make
a model that answers badly indistinguishable from a walk that is broken.

Nothing here touches a network. The runtime stays transport-free; a
caller supplies ``complete(prompt) -> str`` and owns the connection.

Three decisions worth stating, because they are what make this work with
a small local model rather than only with a large hosted one:

**Options are numbered, not addressed.** ``describe()`` hands out
71-character content addresses. Asking a 4B-parameter model to copy one
back verbatim is asking it to fail: it will drop a character, or
hallucinate a plausible-looking one, and every such answer arrives as an
off-menu move. So the prompt shows ``1``, ``2``, ``3`` and this module
maps the number back to the real address. The model never sees a hash.

**The reply is parsed permissively and resolved strictly.** A small model
will wrap its answer in prose, in markdown fences, or in a sentence. All
of that is fine and is stripped. What is *not* negotiable is that the
answer has to land on an option that was actually offered -- being
generous about form while staying strict about outcome is what keeps
"the model chose" meaningful.

**The prompt shows only where the walk stands.** Same discipline as
``describe()``: the current memory and its neighbours, never the store.
A prompt that listed everything would not be navigation, and the model
would be doing retrieval by reading rather than by remembering.
"""
from __future__ import annotations

import json
import re

from somaos.broker.recall.navigator import NavigationError

#: What the model is told it is doing. Short on purpose: a small model
#: given a long preamble spends its attention on the preamble.
SYSTEM_PROMPT = (
    "You are recalling a memory by walking a tree of memories.\n"
    "You are standing at one memory. You can move to a related one, "
    "step back to a broader one, bring the current one to mind, or stop.\n"
    "Moving uses effort. Bringing a memory to mind uses none, so bring "
    "anything useful to mind as you pass it.\n"
    "Answer with the number of one option and nothing else."
)

#: The same instructions in Thai. Not a translation exercise: the models
#: this is aimed at are Thai-first, and asking one to reason in its
#: second language is a variable in an experiment about navigation. Kept
#: beside the English rather than replacing it so the two can be compared
#: on the same seeds, which is the only way to know whether it mattered.
SYSTEM_PROMPT_TH = (
    "คุณกำลังนึกถึงความทรงจำ โดยการเดินไปบนต้นไม้ของความทรงจำ\n"
    "ตอนนี้คุณยืนอยู่ที่ความทรงจำหนึ่ง คุณเดินไปยังความทรงจำที่เกี่ยวข้องได้ "
    "ถอยกลับไปยังความทรงจำที่กว้างกว่าได้ หยิบความทรงจำตรงนี้ขึ้นมาได้ หรือหยุดก็ได้\n"
    "การเดินต้องใช้แรง การหยิบความทรงจำขึ้นมาไม่ใช้แรงเลย "
    "ดังนั้นเจออะไรที่มีประโยชน์ระหว่างทาง ให้หยิบขึ้นมาด้วย\n"
    "ตอบเป็นหมายเลขของตัวเลือกเดียว ห้ามตอบอย่างอื่น"
)

#: Every user-visible string, in both languages. A dict rather than two
#: render functions: the shape of the prompt is what is being tested, and
#: two copies of the layout would drift apart the first time either is
#: edited, turning a language comparison into a layout comparison.
_PHRASES = {
    "en": {
        "system": SYSTEM_PROMPT,
        "here": "You are at this memory:",
        "about": "about", "note": "note", "when": "when", "to": "to",
        "nothing": "(nothing recorded)",
        "not_started": "You have not started walking yet.",
        "effort": "Effort left: {ops} moves.",
        "brought_plain": "  Brought to mind so far: {n}.",
        "brought_room": "  Brought to mind: {n} (room for {room} more, "
                        "at no cost in effort).",
        "rejected": "Your last answer could not be used: {why}",
        "use_number": "Answer with one of the numbers below.",
        "options": "Options:",
        "which": "Which number?",
        "stop": "Stop -- I have what I need.",
        "materialize": "Bring this memory to mind.",
        "gather": "Bring the most promising memories here to mind, all at "
                  "once, and finish.",
        "ascend": "Step back to the broader memory this belongs to.",
        "lateral": "Go to a related memory",
        "descend": "Go into a memory within this one",
        "answer_text": "Answer with the number of one option and nothing else.",
        "answer_tool": "Answer by calling the choose function with the number "
                       "of one option.",
        "which_tool": "Call choose with the number.",
    },
    "th": {
        "system": SYSTEM_PROMPT_TH,
        "here": "ตอนนี้คุณอยู่ที่ความทรงจำนี้:",
        "about": "เกี่ยวกับ", "note": "บันทึก", "when": "ช่วงเวลา", "to": "ถึง",
        "nothing": "(ไม่มีบันทึกไว้)",
        "not_started": "คุณยังไม่ได้เริ่มเดิน",
        "effort": "แรงที่เหลือ: เดินได้อีก {ops} ก้าว",
        "brought_plain": "  หยิบขึ้นมาแล้ว: {n}",
        "brought_room": "  หยิบขึ้นมาแล้ว: {n} (หยิบได้อีก {room} "
                        "โดยไม่เสียแรง)",
        "rejected": "คำตอบล่าสุดของคุณใช้ไม่ได้: {why}",
        "use_number": "กรุณาตอบเป็นหมายเลขจากรายการข้างล่างนี้",
        "options": "ตัวเลือก:",
        "which": "หมายเลขไหน?",
        "stop": "หยุด -- ได้สิ่งที่ต้องการแล้ว",
        "materialize": "หยิบความทรงจำนี้ขึ้นมา",
        "gather": "หยิบความทรงจำที่น่าสนใจที่สุดตรงนี้ขึ้นมาทั้งหมดในครั้งเดียว แล้วจบ",
        "ascend": "ถอยกลับไปยังความทรงจำที่กว้างกว่าซึ่งเรื่องนี้อยู่ในนั้น",
        "lateral": "ไปยังความทรงจำที่เกี่ยวข้อง",
        "descend": "เข้าไปในความทรงจำย่อยของเรื่องนี้",
        "answer_text": "ตอบเป็นหมายเลขของตัวเลือกเดียว ห้ามตอบอย่างอื่น",
        "answer_tool": "ตอบโดยเรียกฟังก์ชัน choose พร้อมหมายเลขของตัวเลือกเดียว",
        "which_tool": "เรียก choose พร้อมหมายเลข",
    },
}

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_NUMBER = re.compile(r"\b(\d{1,3})\b")
_LABELLED = re.compile(r"(?:choice|answer|option|move)\s*[:=]\s*(\d{1,3}|[a-z]+)", re.I)

#: Words that mean "finish" wherever they appear in a reply. Every one is
#: a verb a model only uses when it has decided to stop, so finding one
#: mid-sentence ("I think we should stop here") is safe.
_STOP_VERBS = frozenset({"stop", "done", "finish", "finished"})

#: Words that mean "finish" only when they are the entire reply. "no" and
#: "none" are here rather than above because scanning for them anywhere
#: turns confusion into a decision: "no idea" contains a standalone "no",
#: and a model that has just told you it is lost would be recorded as
#: having chosen to stop. An experiment measuring whether a model
#: navigates well cannot afford to read its confusion as decisiveness.
_STOP_ALONE = _STOP_VERBS | {"none", "no", "nothing"}

#: Thai words for finishing, matched only as an entire reply -- never as a
#: substring. Thai is written without spaces, so ``\b`` does not apply and
#: a substring search cannot tell "หยุด" (stop) from "ไม่หยุด" (do not
#: stop) or "ยังไม่หยุด" (not stopping yet). That is the "no idea" bug in
#: a language where the safe form of the check does not exist, so the
#: unsafe form is not offered at all. A model that means to stop while
#: being asked for a number nearly always answers with the number.
_STOP_ALONE_TH = frozenset({
    "หยุด", "พอ", "พอแล้ว", "จบ", "เสร็จ", "เสร็จแล้ว", "ไม่มี",
})

#: Thai digits. A Thai-first model prompted in Thai occasionally answers
#: with them, and every such answer would otherwise arrive as off-menu.
_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def render_prompt(view: dict, *, include_system: bool = True,
                  lang: str = "en", tools: bool = False) -> str:
    """Render one decision point as text.

    Deterministic: the same view always renders the same string, so a run
    against a temperature-zero model is replayable and a recorded
    transcript can be matched back to the position that produced it.

    ``lang`` selects the wording only. The layout, the option order and
    the numbering are identical in every language, so a run that differs
    differs because of the language and not because of the shape.
    """
    say = _phrases(lang)
    lines: list[str] = []
    if include_system:
        # The last line of the preamble tells the model how to answer, and
        # it has to match the channel actually being offered. Left as
        # "answer with the number and nothing else" while a tool was
        # attached, the model obeyed the sentence: Typhoon returned plain
        # numbers and not one tool call in 3,019 exchanges, and the
        # experiment read that as a model that cannot call tools.
        system = say["system"]
        if tools:
            system = system.replace(say["answer_text"], say["answer_tool"])
        lines.append(system)
        lines.append("")

    here = view.get("here")
    if here:
        lines.append(say["here"])
        keys = ", ".join(here.get("keys", ())) or say["nothing"]
        lines.append(f"  {say['about']}: {keys}")
        if here.get("text_ref"):
            lines.append(f"  {say['note']}: {here['text_ref']}")
        span = here.get("span") or ()
        if len(span) == 2:
            lines.append(f"  {say['when']}: {span[0]} {say['to']} {span[1]}")
    else:
        lines.append(say["not_started"])

    lines.append("")
    budget = say["effort"].format(ops=view.get("ops_left", 0))
    room = view.get("can_bring_to_mind")
    if room is None:
        budget += say["brought_plain"].format(n=view.get("materialized", 0))
    else:
        budget += say["brought_room"].format(
            n=view.get("materialized", 0), room=room)
    lines.append(budget)

    error = view.get("error")
    if error:
        lines.append("")
        lines.append(say["rejected"].format(why=error))
        lines.append(say["use_number"])

    lines.append("")
    lines.append(say["options"])
    for index, option, label in _numbered(view, lang=lang):
        lines.append(f"  {index}. {label}")

    lines.append("")
    lines.append(say["which_tool"] if tools else say["which"])
    return "\n".join(lines)


def _phrases(lang: str) -> dict:
    try:
        return _PHRASES[lang]
    except KeyError:
        raise ValueError(
            f"no prompt wording for {lang!r}; have {sorted(_PHRASES)}"
        ) from None


def _describe_option(option: dict, *, lang: str = "en") -> str:
    """One option as a phrase, with no address in it."""
    say = _phrases(lang)
    move = option.get("move")
    if move in ("stop", "materialize", "gather", "ascend"):
        return say[move]

    keys = ", ".join(option.get("keys", ())) or say["nothing"]
    note = option.get("text_ref")
    where = say["lateral"] if move == "lateral" else say["descend"]
    described = f"{where}: {keys}"
    if note:
        described += f" -- {note}"
    return described


def _numbered(view: dict, *, lang: str = "en") -> list[tuple[int, dict, str]]:
    """Options paired with the numbers the model will answer with.

    One-based because a model asked to choose from a list starting at
    zero picks 1 anyway, and then means the second item.
    """
    return [
        (index, option, _describe_option(option, lang=lang))
        for index, option in enumerate(view.get("options", ()), start=1)
    ]


def parse_choice(reply: str, view: dict) -> dict:
    """Turn a model's reply into one of the options it was shown.

    Raises :class:`NavigationError` if the reply cannot be resolved to an
    offered option. That is the right failure: the navigator retries it
    by showing the menu again with the reason attached, which is how a
    model that fumbles once still gets to finish the walk.
    """
    options = list(view.get("options", ()))
    if not options:
        raise NavigationError("nothing was offered, so nothing can be chosen")

    text = (reply or "").strip().translate(_THAI_DIGITS)
    if not text:
        raise NavigationError("the model replied with nothing")

    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()

    # A model that answers in JSON is answering well; take it first.
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        for key in ("choice", "option", "answer", "number", "move"):
            if key in parsed:
                return _resolve(parsed[key], options, reply)
    if isinstance(parsed, int):
        return _resolve(parsed, options, reply)

    labelled = _LABELLED.search(text)
    if labelled:
        return _resolve(labelled.group(1), options, reply)

    lowered = text.lower()
    bare = lowered.strip().strip(".!,;:'\"*` ")
    if bare in _STOP_ALONE or bare in _STOP_ALONE_TH or any(
        re.search(rf"\b{word}\b", lowered) for word in _STOP_VERBS
    ):
        stop = next((o for o in options if o.get("move") == "stop"), None)
        if stop is not None:
            return stop

    number = _NUMBER.search(text)
    if number:
        return _resolve(number.group(1), options, reply)

    raise NavigationError(
        f"could not find a choice in {reply.strip()[:80]!r}; "
        f"expected a number from 1 to {len(options)}"
    )


def _resolve(raw, options: list[dict], reply: str) -> dict:
    if isinstance(raw, str) and raw.strip().lower() in _STOP_ALONE:
        stop = next((o for o in options if o.get("move") == "stop"), None)
        if stop is not None:
            return stop
    if isinstance(raw, str) and not raw.strip().lstrip("+-").isdigit():
        # A bare move name, e.g. {"move": "descend"}. Honoured when exactly
        # one option carries it; ambiguous otherwise, and guessing which of
        # four children it meant would be inventing the model's decision.
        named = [o for o in options if o.get("move") == raw.strip().lower()]
        if len(named) == 1:
            return named[0]
        if len(named) > 1:
            raise NavigationError(
                f"{raw!r} matches {len(named)} options; answer with a number"
            )
        raise NavigationError(f"{raw!r} is not one of the options")

    try:
        index = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise NavigationError(f"{raw!r} is not a number") from exc

    if not 1 <= index <= len(options):
        raise NavigationError(
            f"option {index} does not exist; there are {len(options)}"
        )
    return options[index - 1]


def build_tools(view: dict, *, lang: str = "en") -> list[dict]:
    """The same menu as an OpenAI-style function schema.

    One function with one enum-constrained argument. The enum is the
    point: a server that validates arguments against the schema cannot
    return an option that was not offered, so "did the model format its
    answer correctly" stops being a variable and what is left is whether
    it navigates. That is a cleaner instrument for the question being
    asked -- though only for models that support tool calling at all,
    which is why the text path stays the default rather than the fallback.
    """
    numbered = _numbered(view, lang=lang)
    return [{
        "type": "function",
        "function": {
            "name": "choose",
            "description": _phrases(lang)["which"],
            "parameters": {
                "type": "object",
                "properties": {
                    "option": {
                        "type": "integer",
                        "enum": [index for index, _, _ in numbered],
                        "description": "\n".join(
                            f"{index} = {label}" for index, _, label in numbered
                        ),
                    },
                },
                "required": ["option"],
                "additionalProperties": False,
            },
        },
    }]


def parse_tool_call(call: dict | None, view: dict) -> dict:
    """Turn one tool call into the option it names.

    Falls back to :func:`parse_choice` on the message text when the model
    answered in prose instead of calling the tool, which small models do
    even with ``tool_choice`` set. Failing outright there would score a
    model that answered correctly in the wrong envelope as one that could
    not answer.
    """
    if not call:
        raise NavigationError("the model returned no tool call")
    function = call.get("function") or {}
    if function.get("name") not in (None, "choose"):
        raise NavigationError(f"{function.get('name')!r} is not a tool it was given")

    raw = function.get("arguments")
    if isinstance(raw, str):
        try:
            arguments = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise NavigationError(
                f"tool arguments were not JSON: {raw[:80]!r}"
            ) from exc
    else:
        arguments = raw or {}
    if not isinstance(arguments, dict):
        raise NavigationError(f"tool arguments were not an object: {arguments!r}")

    options = list(view.get("options", ()))
    for key in ("option", "choice", "number", "move"):
        if key in arguments:
            return _resolve(arguments[key], options, json.dumps(arguments))
    raise NavigationError(f"tool call named no option: {arguments!r}")


class ToolCallingChooser:
    """A chooser that asks the model to call a function instead of typing.

    ``call_tool(prompt, tools) -> (tool_call, text)`` is supplied by the
    caller, the same way :class:`PromptedChooser` takes ``complete``. The
    runtime still touches no network.
    """

    def __init__(self, call_tool, *, keep_transcript: bool = True,
                 lang: str = "en") -> None:
        self._call_tool = call_tool
        self.keep_transcript = keep_transcript
        self.lang = lang
        _phrases(lang)
        self.transcript: list[tuple[str, str]] = []
        self.prompt_chars = 0
        #: Times the model answered in prose rather than calling the tool.
        #: Reported rather than hidden: a model that ignores the tool it
        #: was given is telling you something about how it will behave.
        self.text_instead_of_call = 0
        #: Replies with neither a tool call nor any text. Counted apart
        #: from the rest because an empty answer is the endpoint failing,
        #: not the model navigating badly, and folding it into off_menu
        #: charges the model for the server's behaviour.
        self.empty_replies = 0

    def reset(self) -> None:
        self.transcript = []
        self.prompt_chars = 0
        self.text_instead_of_call = 0
        self.empty_replies = 0

    def __call__(self, view: dict) -> dict:
        prompt = render_prompt(view, lang=self.lang, tools=True)
        tools = build_tools(view, lang=self.lang)
        self.prompt_chars += len(prompt)
        call, text = self._call_tool(prompt, tools)
        if self.keep_transcript:
            self.transcript.append((prompt, json.dumps(call) if call else (text or "")))
        if not call:
            if not (text or "").strip():
                self.empty_replies += 1
            self.text_instead_of_call += 1
            return parse_choice(text or "", view)
        return parse_tool_call(call, view)


class PromptedChooser:
    """A chooser backed by a text-completion function.

    Pass one to :class:`~somaos.broker.recall.navigator.CallableNavigator`::

        nav = CallableNavigator(PromptedChooser(my_model.complete))

    ``complete`` takes a prompt and returns the model's text. Everything
    about how that text is produced -- endpoint, sampling, batching,
    retries at the transport layer -- belongs to the caller.

    The transcript is kept so a run can be read back afterwards. An
    experiment comparing model-driven recall against the fast path needs
    to be able to answer "what did it actually see, and what did it say",
    and reconstructing that from logs later never works.
    """

    def __init__(self, complete, *, keep_transcript: bool = True,
                 lang: str = "en") -> None:
        self._complete = complete
        self.keep_transcript = keep_transcript
        self.lang = lang
        _phrases(lang)  # fail now, not on the first call of a long run
        #: (prompt, reply) per call, oldest first.
        self.transcript: list[tuple[str, str]] = []
        #: Prompt characters sent. A rough stand-in for tokens, and the
        #: honest cost of putting a model in the loop -- reported so it
        #: sits beside the quality it bought rather than out of sight.
        self.prompt_chars = 0

    def reset(self) -> None:
        self.transcript = []
        self.prompt_chars = 0

    def __call__(self, view: dict) -> dict:
        prompt = render_prompt(view, lang=self.lang)
        self.prompt_chars += len(prompt)
        reply = self._complete(prompt)
        if self.keep_transcript:
            self.transcript.append((prompt, reply))
        return parse_choice(reply, view)
