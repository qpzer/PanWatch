"""ResearchModelGateway 思考开关与 research_graph 配置校验。"""

import asyncio

from src.modules.research.orchestration.gateway import ResearchModelGateway
from src.modules.research.orchestration.policies import validate_config


class _FakeMessage:
    content = "{}"
    tool_calls = None


class _FakeChoice:
    message = _FakeMessage()
    finish_reason = "stop"


class _FakeUsage:
    prompt_tokens = 1
    completion_tokens = 1


class _FakeReply:
    choices = [_FakeChoice()]
    usage = _FakeUsage()
    model = "fake"


class _FakeCompletions:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return _FakeReply()


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeRawClient:
    def __init__(self, completions):
        self.chat = _FakeChat(completions)

    def with_options(self, **kwargs):
        return self


class _FakeClient:
    def __init__(self, model):
        self.model = model
        self.completions = _FakeCompletions()
        self.client = _FakeRawClient(self.completions)


async def _noop():
    return None


def _complete(gateway):
    return asyncio.run(gateway.complete(
        role="technical", messages=[{"role": "user", "content": "hi"}], tools=None,
        schema={}, max_output_tokens=1536, timeout=30, before_attempt=_noop))


def test_disable_thinking_injects_extra_body_for_qwen():
    client = _FakeClient("qwen3.7-flash")
    gateway = ResearchModelGateway(client)
    gateway.disable_thinking = True
    _complete(gateway)
    assert client.completions.kwargs["extra_body"] == {"enable_thinking": False}


def test_disable_thinking_ignores_non_qwen_models():
    client = _FakeClient("deepseek-flash")
    gateway = ResearchModelGateway(client)
    gateway.disable_thinking = True
    _complete(gateway)
    assert "extra_body" not in client.completions.kwargs


def test_thinking_kept_by_default():
    client = _FakeClient("qwen3.7-flash")
    _complete(ResearchModelGateway(client))
    assert "extra_body" not in client.completions.kwargs


def test_validate_config_tolerates_disable_thinking_and_attempts_override():
    _, policy = validate_config(
        {"template": "intraday_fast", "disable_thinking": True, "max_attempts": 2},
        "intraday_monitor")
    assert policy.max_attempts == 2
    assert policy.graph == "intraday_fast"
