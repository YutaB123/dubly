"""Tests for the brain's tool loop (with a scripted fake Claude client)."""

from __future__ import annotations

from types import SimpleNamespace

from app.brain import Brain


# --- Fakes -------------------------------------------------------------------

def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_use_block(block_id, name, tool_input):
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=tool_input)


def message(stop_reason, content):
    return SimpleNamespace(stop_reason=stop_reason, content=content)


class FakeAnthropic:
    """Returns scripted responses in order; records the requests it received."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._scripted.pop(0)


class FakeToolBox:
    def __init__(self):
        self.dispatched = []

    def schemas(self):
        return [{"name": "get_upcoming", "input_schema": {"type": "object", "properties": {}}}]

    def dispatch(self, name, tool_input):
        self.dispatched.append((name, tool_input))
        return "CSE 163 A — Homework 4 — due Tue 11:59pm [1:55]"


def make_brain(scripted):
    client = FakeAnthropic(scripted)
    box = FakeToolBox()
    brain = Brain(client=client, model="claude-opus-4-8", toolbox=box)
    return brain, client, box


# --- Tests -------------------------------------------------------------------

def test_plain_reply_without_tools():
    brain, client, box = make_brain([
        message("end_turn", [text_block("you've got nothing due, relax")]),
    ])
    reply = brain.respond("anything due?")
    assert reply == "you've got nothing due, relax"
    assert box.dispatched == []
    # The model was given our tools and system prompt.
    assert client.calls[0]["tools"] == box.schemas()
    assert "system" in client.calls[0]


def test_runs_a_tool_then_replies():
    brain, client, box = make_brain([
        message("tool_use", [tool_use_block("tu_1", "get_upcoming", {"days": 7})]),
        message("end_turn", [text_block("hw4 for 163 is due tue 11:59pm")]),
    ])
    reply = brain.respond("what's due this week?")

    assert box.dispatched == [("get_upcoming", {"days": 7})]
    assert reply == "hw4 for 163 is due tue 11:59pm"

    # Second call carried the tool result back as a user message.
    second = client.calls[1]["messages"]
    tool_result_msg = second[-1]
    assert tool_result_msg["role"] == "user"
    block = tool_result_msg["content"][0]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "tu_1"
    assert "Homework 4" in block["content"]


def test_history_is_included():
    brain, client, box = make_brain([
        message("end_turn", [text_block("that one's the merge sort hw")]),
    ])
    history = [
        {"role": "user", "content": "what's due?"},
        {"role": "assistant", "content": "hw4 for 163"},
    ]
    brain.respond("what's that asking for?", history=history)
    sent = client.calls[0]["messages"]
    assert sent[0] == history[0]
    assert sent[1] == history[1]
    assert sent[-1] == {"role": "user", "content": "what's that asking for?"}


def test_attachments_make_a_multimodal_user_message():
    brain, client, box = make_brain([
        message("end_turn", [text_block("that's a related-rates problem")]),
    ])
    files = [("photo.png", "image/png", b"img-bytes")]
    brain.respond("what is this?", attachments=files)

    user_msg = client.calls[0]["messages"][-1]
    assert user_msg["role"] == "user"
    assert isinstance(user_msg["content"], list)
    assert user_msg["content"][0]["type"] == "image"            # the picture
    assert user_msg["content"][-1]["type"] == "text"            # the question
    assert "what is this?" in user_msg["content"][-1]["text"]


def test_no_attachments_keeps_a_plain_string_message():
    brain, client, box = make_brain([
        message("end_turn", [text_block("ok")]),
    ])
    brain.respond("hi")
    assert client.calls[0]["messages"][-1]["content"] == "hi"


def test_tool_loop_has_a_safety_limit():
    # Model keeps asking for tools forever; brain must stop and still answer.
    always_tool = message("tool_use", [tool_use_block("x", "get_upcoming", {})])
    scripted = [always_tool] * 20
    brain, client, box = make_brain(scripted)
    reply = brain.respond("loop?")
    assert isinstance(reply, str) and reply  # returns *something*, doesn't hang
    assert len(client.calls) <= 8  # bounded
