"""The provider layer.

The app supports Anthropic natively and anything speaking the OpenAI
chat-completions dialect (Groq, Gemini, OpenRouter, a local Ollama). The risk in
that abstraction is silent drift: a translation that looks right but sends a
malformed tool schema, or a response shape that parses into the wrong thing.

These tests drive the adapter against fake clients, so they run offline and
unbilled, and they assert on the *exact payload* sent to the provider rather
than only on what comes back.
"""

import json
from types import SimpleNamespace

import pytest

from app import providers
from app.providers import (
    OPENAI_COMPATIBLE_PRESETS,
    AnthropicProvider,
    OpenAICompatibleProvider,
    ProviderOutputError,
    ProviderUnavailable,
    build_provider,
)

TOOLS = [
    {
        "name": "ask_clarification",
        "description": "Ask one question.",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "submit_meal_analysis",
        "description": "Submit the analysis.",
        "input_schema": {
            "type": "object",
            "properties": {"total_calories": {"type": "integer"}},
            "required": ["total_calories"],
        },
    },
]

PROVIDER_ENV = [
    "MACRO_TRACKER_PROVIDER",
    "MACRO_TRACKER_MODEL",
    "MACRO_TRACKER_BASE_URL",
    "MACRO_TRACKER_API_KEY",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "OLLAMA_API_KEY",
]


@pytest.fixture(autouse=True)
def clean_provider_env(monkeypatch):
    """Each test configures the provider from scratch."""
    for name in PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    providers.reset_provider()
    yield
    providers.reset_provider()


class FakeCompletions:
    """Stands in for `openai.OpenAI().chat.completions`."""

    def __init__(self, response, fail_on_required=False):
        self.response = response
        self.fail_on_required = fail_on_required
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_on_required and kwargs.get("tool_choice") == "required":
            import openai

            raise openai.BadRequestError(
                "tool_choice 'required' is not supported",
                response=SimpleNamespace(status_code=400, headers={}, request=None),
                body=None,
            )
        return self.response


def openai_response(
    name="submit_meal_analysis",
    arguments='{"total_calories": 640}',
    model="some-model",
    finish_reason="tool_calls",
    with_usage=True,
):
    tool_calls = None
    if name is not None:
        tool_calls = [SimpleNamespace(function=SimpleNamespace(name=name, arguments=arguments))]
    usage = SimpleNamespace(prompt_tokens=500, completion_tokens=200) if with_usage else None
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(tool_calls=tool_calls, content=None),
                finish_reason=finish_reason,
            )
        ],
        model=model,
        usage=usage,
    )


def make_openai_provider(response, fail_on_required=False):
    provider = OpenAICompatibleProvider("groq", "some-model", "http://x/v1", "key")
    completions = FakeCompletions(response, fail_on_required)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return provider, completions


class TestProviderSelection:
    def test_anthropic_is_the_default(self):
        assert isinstance(build_provider(), AnthropicProvider)

    def test_anthropic_has_a_default_model(self):
        assert build_provider().model == providers.ANTHROPIC_DEFAULT_MODEL

    def test_the_model_can_be_overridden(self, monkeypatch):
        monkeypatch.setenv("MACRO_TRACKER_MODEL", "claude-haiku-4-5")
        assert build_provider().model == "claude-haiku-4-5"

    @pytest.mark.parametrize("preset", sorted(OPENAI_COMPATIBLE_PRESETS))
    def test_every_preset_builds_when_fully_configured(self, monkeypatch, preset):
        monkeypatch.setenv("MACRO_TRACKER_PROVIDER", preset)
        monkeypatch.setenv("MACRO_TRACKER_MODEL", "a-model")
        monkeypatch.setenv("MACRO_TRACKER_API_KEY", "a-key")
        if OPENAI_COMPATIBLE_PRESETS[preset]["base_url"] is None:
            monkeypatch.setenv("MACRO_TRACKER_BASE_URL", "http://example/v1")
        provider = build_provider()
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.model == "a-model"

    def test_a_preset_supplies_its_base_url(self, monkeypatch):
        monkeypatch.setenv("MACRO_TRACKER_PROVIDER", "groq")
        monkeypatch.setenv("MACRO_TRACKER_MODEL", "a-model")
        monkeypatch.setenv("GROQ_API_KEY", "k")
        assert build_provider().base_url == OPENAI_COMPATIBLE_PRESETS["groq"]["base_url"]

    def test_the_base_url_can_be_overridden(self, monkeypatch):
        monkeypatch.setenv("MACRO_TRACKER_PROVIDER", "groq")
        monkeypatch.setenv("MACRO_TRACKER_MODEL", "a-model")
        monkeypatch.setenv("GROQ_API_KEY", "k")
        monkeypatch.setenv("MACRO_TRACKER_BASE_URL", "http://proxy/v1")
        assert build_provider().base_url == "http://proxy/v1"

    def test_ollama_needs_no_key(self, monkeypatch):
        monkeypatch.setenv("MACRO_TRACKER_PROVIDER", "ollama")
        monkeypatch.setenv("MACRO_TRACKER_MODEL", "llama3.1")
        assert isinstance(build_provider(), OpenAICompatibleProvider)

    def test_an_unknown_provider_lists_the_valid_ones(self, monkeypatch):
        monkeypatch.setenv("MACRO_TRACKER_PROVIDER", "hal9000")
        with pytest.raises(ProviderUnavailable, match="groq"):
            build_provider()

    def test_a_missing_model_names_where_to_find_one(self, monkeypatch):
        """A hardcoded default would silently 404 once a catalogue changes."""
        monkeypatch.setenv("MACRO_TRACKER_PROVIDER", "groq")
        monkeypatch.setenv("GROQ_API_KEY", "k")
        with pytest.raises(ProviderUnavailable, match="MACRO_TRACKER_MODEL"):
            build_provider()

    def test_a_missing_key_names_the_variable(self, monkeypatch):
        monkeypatch.setenv("MACRO_TRACKER_PROVIDER", "groq")
        monkeypatch.setenv("MACRO_TRACKER_MODEL", "a-model")
        with pytest.raises(ProviderUnavailable, match="GROQ_API_KEY"):
            build_provider()

    def test_the_provider_name_is_case_and_space_insensitive(self, monkeypatch):
        monkeypatch.setenv("MACRO_TRACKER_PROVIDER", "  GROQ ")
        monkeypatch.setenv("MACRO_TRACKER_MODEL", "a-model")
        monkeypatch.setenv("GROQ_API_KEY", "k")
        assert build_provider().preset == "groq"

    def test_the_provider_is_cached(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        assert providers.get_provider() is providers.get_provider()

    def test_reset_rebuilds_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        first = providers.get_provider()
        providers.reset_provider()
        assert providers.get_provider() is not first


class TestIsConfigured:
    def test_reports_unavailable_without_a_key(self):
        available, detail = providers.is_configured()
        assert available is False
        assert "ANTHROPIC_API_KEY" in detail

    def test_reports_available_with_a_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        available, detail = providers.is_configured()
        assert available is True
        assert "anthropic" in detail

    def test_describes_the_active_provider_and_model(self, monkeypatch):
        monkeypatch.setenv("MACRO_TRACKER_PROVIDER", "groq")
        monkeypatch.setenv("MACRO_TRACKER_MODEL", "a-model")
        monkeypatch.setenv("GROQ_API_KEY", "k")
        available, detail = providers.is_configured()
        assert available is True
        assert detail == "groq / a-model"

    def test_misconfiguration_is_reported_not_raised(self, monkeypatch):
        """`/api/health` runs on every page load; it must never 500."""
        monkeypatch.setenv("MACRO_TRACKER_PROVIDER", "groq")
        available, detail = providers.is_configured()
        assert available is False
        assert "MACRO_TRACKER_MODEL" in detail


class TestToolTranslation:
    def test_tools_become_openai_functions(self):
        translated = OpenAICompatibleProvider._translate_tools(TOOLS)
        assert [t["type"] for t in translated] == ["function", "function"]
        first = translated[0]["function"]
        assert first["name"] == "ask_clarification"
        assert first["description"] == "Ask one question."
        # `input_schema` becomes `parameters`, unchanged in content.
        assert first["parameters"] == TOOLS[0]["input_schema"]

    def test_input_schema_is_not_left_behind(self):
        translated = OpenAICompatibleProvider._translate_tools(TOOLS)
        assert "input_schema" not in translated[0]["function"]

    def test_a_tool_without_a_description_still_translates(self):
        tools = [{"name": "t", "input_schema": {"type": "object", "properties": {}}}]
        assert (
            OpenAICompatibleProvider._translate_tools(tools)[0]["function"]["description"] == ""
        )

    def test_any_becomes_required(self):
        assert OpenAICompatibleProvider._translate_tool_choice({"type": "any"}) == "required"

    def test_a_named_tool_becomes_a_function_choice(self):
        choice = OpenAICompatibleProvider._translate_tool_choice(
            {"type": "tool", "name": "submit_day_rating"}
        )
        assert choice == {"type": "function", "function": {"name": "submit_day_rating"}}

    def test_anything_else_becomes_auto(self):
        assert OpenAICompatibleProvider._translate_tool_choice({"type": "auto"}) == "auto"


class TestOpenAICompatibleCalls:
    def test_a_tool_call_is_normalised(self):
        provider, _ = make_openai_provider(openai_response())
        result = provider.complete(
            "sys", [{"role": "user", "content": "hi"}], TOOLS, {"type": "any"}
        )
        assert result.name == "submit_meal_analysis"
        assert result.arguments == {"total_calories": 640}
        assert result.model == "some-model"

    def test_arguments_are_parsed_from_the_json_string(self):
        """The difference that bites: OpenAI returns a string, Anthropic a dict."""
        provider, _ = make_openai_provider(
            openai_response(arguments='{"total_calories": 1, "items": [{"name": "x"}]}')
        )
        result = provider.complete("sys", [], TOOLS, {"type": "any"})
        assert result.arguments["items"] == [{"name": "x"}]

    def test_the_system_prompt_becomes_the_first_message(self):
        provider, completions = make_openai_provider(openai_response())
        provider.complete(
            "YOU ARE A NUTRITIONIST",
            [{"role": "user", "content": "a wrap"}],
            TOOLS,
            {"type": "any"},
        )
        sent = completions.calls[0]["messages"]
        assert sent[0] == {"role": "system", "content": "YOU ARE A NUTRITIONIST"}
        assert sent[1] == {"role": "user", "content": "a wrap"}

    def test_the_conversation_order_is_preserved(self):
        provider, completions = make_openai_provider(openai_response())
        history = [
            {"role": "user", "content": "pizza"},
            {"role": "assistant", "content": "What size?"},
            {"role": "user", "content": "4 slices"},
        ]
        provider.complete("sys", history, TOOLS, {"type": "any"})
        assert completions.calls[0]["messages"][1:] == history

    def test_usage_is_mapped_to_the_common_shape(self):
        provider, _ = make_openai_provider(openai_response())
        result = provider.complete("sys", [], TOOLS, {"type": "any"})
        assert result.usage["input_tokens"] == 500
        assert result.usage["output_tokens"] == 200

    def test_a_response_without_usage_is_not_fatal(self):
        """Some compatible endpoints omit the usage block entirely."""
        provider, _ = make_openai_provider(openai_response(with_usage=False))
        assert provider.complete("sys", [], TOOLS, {"type": "any"}).usage == {}

    def test_the_served_model_is_taken_from_the_response(self):
        provider, _ = make_openai_provider(openai_response(model="actually-served"))
        assert provider.complete("sys", [], TOOLS, {"type": "any"}).model == "actually-served"

    def test_text_instead_of_a_tool_call_is_an_output_error(self):
        provider, _ = make_openai_provider(openai_response(name=None))
        with pytest.raises(ProviderOutputError, match="text instead"):
            provider.complete("sys", [], TOOLS, {"type": "any"})

    def test_malformed_json_arguments_are_an_output_error(self):
        """A weaker model can emit this; the Anthropic path cannot."""
        provider, _ = make_openai_provider(openai_response(arguments='{"total_calories": '))
        with pytest.raises(ProviderOutputError, match="malformed JSON"):
            provider.complete("sys", [], TOOLS, {"type": "any"})

    def test_non_object_arguments_are_an_output_error(self):
        provider, _ = make_openai_provider(openai_response(arguments="[1, 2, 3]"))
        with pytest.raises(ProviderOutputError, match="not an object"):
            provider.complete("sys", [], TOOLS, {"type": "any"})

    def test_already_parsed_arguments_are_accepted(self):
        provider, _ = make_openai_provider(openai_response(arguments={"total_calories": 5}))
        assert provider.complete("sys", [], TOOLS, {"type": "any"}).arguments == {
            "total_calories": 5
        }


class TestForcedChoiceFallback:
    """Not every compatible endpoint implements `tool_choice: "required"`."""

    def test_it_retries_with_auto_when_required_is_rejected(self):
        provider, completions = make_openai_provider(openai_response(), fail_on_required=True)
        result = provider.complete("sys", [], TOOLS, {"type": "any"})
        assert result.name == "submit_meal_analysis"
        assert [c["tool_choice"] for c in completions.calls] == ["required", "auto"]

    def test_the_fallback_is_remembered(self):
        """A second call should not pay for the failed attempt again."""
        provider, completions = make_openai_provider(openai_response(), fail_on_required=True)
        provider.complete("sys", [], TOOLS, {"type": "any"})
        completions.calls.clear()
        provider.complete("sys", [], TOOLS, {"type": "any"})
        assert [c["tool_choice"] for c in completions.calls] == ["auto"]

    def test_a_bad_request_on_auto_does_not_trigger_a_retry(self):
        """Only a rejected *forced* choice triggers the fallback.

        Any other 400 -- an oversized context, a malformed schema -- surfaces as
        a provider error on the first attempt rather than being retried into the
        bill.
        """
        import openai

        provider, completions = make_openai_provider(openai_response())

        def always_fail(**kwargs):
            completions.calls.append(kwargs)
            raise openai.BadRequestError(
                "context length exceeded",
                response=SimpleNamespace(status_code=400, headers={}, request=None),
                body=None,
            )

        provider._client.chat.completions.create = always_fail
        with pytest.raises(ProviderUnavailable, match="400"):
            provider.complete("sys", [], TOOLS, {"type": "auto"})
        assert len(completions.calls) == 1

    def test_a_non_tool_choice_bad_request_is_not_retried_either(self):
        """A 400 under a forced choice retries once, and only once."""
        import openai

        provider, completions = make_openai_provider(openai_response())

        def always_fail(**kwargs):
            completions.calls.append(kwargs)
            raise openai.BadRequestError(
                "schema rejected",
                response=SimpleNamespace(status_code=400, headers={}, request=None),
                body=None,
            )

        provider._client.chat.completions.create = always_fail
        with pytest.raises(ProviderUnavailable):
            provider.complete("sys", [], TOOLS, {"type": "any"})
        assert [c["tool_choice"] for c in completions.calls] == ["required", "auto"]


class TestErrorMapping:
    """Transport failures become a message the user can act on."""

    def _provider_raising(self, exc):
        provider, _ = make_openai_provider(openai_response())

        def fail(**kwargs):
            raise exc

        provider._client.chat.completions.create = fail
        return provider

    def test_a_missing_model_points_at_the_catalogue(self):
        import openai

        provider = self._provider_raising(
            openai.NotFoundError(
                "model not found",
                response=SimpleNamespace(status_code=404, headers={}, request=None),
                body=None,
            )
        )
        with pytest.raises(ProviderUnavailable, match="MACRO_TRACKER_MODEL"):
            provider.complete("sys", [], TOOLS, {"type": "any"})

    def test_a_rate_limit_mentions_free_tier_limits(self):
        import openai

        provider = self._provider_raising(
            openai.RateLimitError(
                "slow down",
                response=SimpleNamespace(status_code=429, headers={}, request=None),
                body=None,
            )
        )
        with pytest.raises(ProviderUnavailable, match="Rate limited"):
            provider.complete("sys", [], TOOLS, {"type": "any"})

    def test_a_bad_key_says_so(self):
        import openai

        provider = self._provider_raising(
            openai.AuthenticationError(
                "bad key",
                response=SimpleNamespace(status_code=401, headers={}, request=None),
                body=None,
            )
        )
        with pytest.raises(ProviderUnavailable, match="Invalid API key"):
            provider.complete("sys", [], TOOLS, {"type": "any"})

    def test_a_connection_failure_to_ollama_suggests_ollama_serve(self):
        import openai

        provider, _ = make_openai_provider(openai_response())
        provider.preset = "ollama"

        def fail(**kwargs):
            raise openai.APIConnectionError(request=None)

        provider._client.chat.completions.create = fail
        with pytest.raises(ProviderUnavailable, match="ollama serve"):
            provider.complete("sys", [], TOOLS, {"type": "any"})


class TestToolSchemasSurviveTranslation:
    """The real tool definitions, not a toy: a schema the provider rejects is a
    failure no unit test of the translator alone would catch."""

    def test_the_apps_own_tools_translate_to_valid_json(self):
        from app import ai

        for tool in (
            ai.ASK_CLARIFICATION_TOOL,
            ai.SUBMIT_MEAL_TOOL,
            ai.SUBMIT_ACTIVITY_TOOL,
            ai.SUBMIT_DAY_RATING_TOOL,
        ):
            translated = OpenAICompatibleProvider._translate_tools([tool])[0]
            # Must round-trip through JSON, since that is how it reaches the wire.
            encoded = json.dumps(translated)
            decoded = json.loads(encoded)
            assert decoded["function"]["name"] == tool["name"]
            assert decoded["function"]["parameters"]["type"] == "object"
            assert "required" in decoded["function"]["parameters"]

    def test_every_submit_tool_has_a_description(self):
        """OpenAI-compatible endpoints lean on the description to pick a tool,
        which matters once the forced-choice fallback kicks in."""
        from app import ai

        for tool in (ai.ASK_CLARIFICATION_TOOL, ai.SUBMIT_MEAL_TOOL, ai.SUBMIT_ACTIVITY_TOOL):
            assert tool.get("description"), tool["name"]
