"""Model providers.

The app needs one thing from a model: a **forced tool call** whose schema is the
response contract. Everything downstream -- `ai.validate_meal_analysis` and
friends -- works on a plain dict and does not care who produced it, so swapping
providers is an adapter, not a rewrite.

Two provider families are supported:

* `anthropic` (the default) -- the Anthropic SDK, native tool calling.
* Anything speaking the **OpenAI chat-completions dialect**, which covers Groq,
  Google Gemini's compatibility endpoint, OpenRouter, and a local Ollama. These
  share one adapter because they share one wire format; only the base URL, the
  key, and the model id differ.

Configure with `MACRO_TRACKER_PROVIDER`. The presets below fill in the base URL
and which environment variable holds the key, so a `.env` usually needs two
lines:

    MACRO_TRACKER_PROVIDER=groq
    MACRO_TRACKER_MODEL=<a model id from that provider>

Model ids are deliberately **not** defaulted for the OpenAI-compatible presets.
Provider catalogues churn, and a hardcoded id that silently 404s a year from now
is worse than an error that tells you to go pick one.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

# base_url: where the OpenAI-compatible endpoint lives.
# key_env:  which environment variable holds the key.
# key_required: Ollama is local and takes any placeholder key.
# docs:     where to find valid model ids, quoted in the error when one is missing.
OPENAI_COMPATIBLE_PRESETS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "key_required": True,
        "docs": "https://console.groq.com/docs/models",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env": "GEMINI_API_KEY",
        "key_required": True,
        "docs": "https://ai.google.dev/gemini-api/docs/models",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "key_required": True,
        "docs": "https://openrouter.ai/models",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "key_env": "OLLAMA_API_KEY",
        "key_required": False,
        "docs": "run `ollama list` to see what you have pulled",
        # Local models reliably call the right tool but leave nested arrays
        # empty; a response-format schema constrains generation instead of
        # merely offering one. See OpenAICompatibleProvider's docstring.
        "structured_output": True,
    },
    # The generic escape hatch: any other OpenAI-compatible endpoint.
    "openai": {
        "base_url": None,  # must come from MACRO_TRACKER_BASE_URL
        "key_env": "MACRO_TRACKER_API_KEY",
        "key_required": True,
        "docs": "your provider's model list",
    },
}

ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-5"


class ProviderUnavailable(Exception):
    """Configuration or transport problem. The message is shown to the user."""


class ProviderOutputError(Exception):
    """The provider replied, but not with a usable tool call."""


@dataclass
class ToolCall:
    """One forced tool call, normalised across providers."""

    name: str
    arguments: dict
    model: str
    stop_reason: str | None = None
    usage: dict = field(default_factory=dict)


class Provider:
    """A model that can be made to call one of a set of tools."""

    name = "base"

    def complete(
        self, system: str, messages: list[dict], tools: list[dict], tool_choice: dict
    ) -> ToolCall:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError


class AnthropicProvider(Provider):
    """The Anthropic SDK. Tool schemas are passed through unchanged.

    `tool_choice={"type": "any"}` is rejected with a 400 on some newer models
    (Opus 5.5, Sonnet 5.5, Fable 5.1). Those models need `{"type": "auto"}` plus
    a prompt instruction naming the tool, which this adapter does not do -- so
    stay on a model that supports forced tool choice.
    """

    name = "anthropic"

    def __init__(self, model: str | None = None):
        self.model = model or ANTHROPIC_DEFAULT_MODEL
        self._client = None

    def _get_client(self):
        import anthropic

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ProviderUnavailable(
                "No API key configured. Add ANTHROPIC_API_KEY to the .env file and "
                "restart, or set MACRO_TRACKER_PROVIDER to use a different provider."
            )
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def describe(self) -> str:
        return f"anthropic / {self.model}"

    def complete(self, system, messages, tools, tool_choice) -> ToolCall:
        import anthropic

        client = self._get_client()
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
            )
        except anthropic.AuthenticationError as e:
            raise ProviderUnavailable(
                "Invalid API key. Check ANTHROPIC_API_KEY in .env."
            ) from e
        except anthropic.RateLimitError as e:
            raise ProviderUnavailable(
                "Rate limited by the AI service. Try again in a minute."
            ) from e
        except anthropic.APIConnectionError as e:
            raise ProviderUnavailable(
                "Could not reach the AI service. Check your internet connection."
            ) from e
        except anthropic.NotFoundError as e:
            raise ProviderUnavailable(
                f"Model {self.model!r} was not found. Check MACRO_TRACKER_MODEL."
            ) from e
        except anthropic.APIStatusError as e:
            raise ProviderUnavailable(
                f"AI service error ({e.status_code}). Try again shortly."
            ) from e

        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0)
            or 0,
            "cache_creation_input_tokens": getattr(
                response.usage, "cache_creation_input_tokens", 0
            )
            or 0,
        }
        for block in response.content:
            if block.type == "tool_use":
                return ToolCall(
                    name=block.name,
                    arguments=block.input,
                    model=response.model,
                    stop_reason=response.stop_reason,
                    usage=usage,
                )
        raise ProviderOutputError(
            f"The model replied without calling a tool (stop_reason={response.stop_reason})."
        )


class OpenAICompatibleProvider(Provider):
    """Any endpoint speaking the OpenAI chat-completions dialect.

    Three things differ from the Anthropic shape and are translated here:

    ========================  ==========================  ==========================
    concept                   Anthropic                   OpenAI-compatible
    ========================  ==========================  ==========================
    tool definition           ``{name, description,       ``{type: "function",
                              input_schema}``               function: {..., parameters}}``
    force a call              ``{"type": "any"}``         ``"required"``
    arguments come back as    a dict                      a JSON **string**
    ========================  ==========================  ==========================

    The JSON-string difference is the one that bites: a weaker model can emit
    syntactically invalid JSON, which the Anthropic path simply cannot produce.
    That is caught here and surfaced as a retryable error rather than a crash.

    **Structured-output mode** (`structured_output=True`) swaps tool calling for
    `response_format: {"type": "json_schema", ...}`. Tool calling is advisory --
    the model is offered a schema and may ignore parts of it -- whereas a
    response-format schema constrains generation. That distinction is decisive
    for small local models: measured on llama3.1:8b and qwen2.5:3b, both call the
    right tool but leave this app's nested `items` array empty every time, and
    both fill it correctly under a response-format schema.

    The trade-off is that one schema means no choice of tool, so the model always
    commits to an analysis and never asks a clarifying question. That is the
    better failure mode here: these models answered "I need to ask something"
    without managing to say what. The prompt already asks for assumptions to be
    stated in the feedback field.

    On by default for the `ollama` preset, off elsewhere -- hosted providers
    generally do tool calling properly, and keeping their native path preserves
    the clarification round trip.
    """

    name = "openai-compatible"

    def __init__(
        self,
        preset: str,
        model: str,
        base_url: str,
        api_key: str,
        structured_output: bool = False,
    ):
        self.preset = preset
        self.model = model
        self.base_url = base_url
        self.structured_output = structured_output
        self._api_key = api_key
        self._client = None
        # Set once a `required` tool choice has been rejected, so the fallback is
        # not re-litigated on every subsequent call.
        self._forced_choice_unsupported = False

    def describe(self) -> str:
        mode = " (structured output)" if self.structured_output else ""
        return f"{self.preset} / {self.model}{mode}"

    def _get_client(self):
        try:
            import openai
        except ImportError as e:
            raise ProviderUnavailable(
                "The 'openai' package is required for this provider. "
                "Install it with: pip install openai"
            ) from e
        if self._client is None:
            self._client = openai.OpenAI(base_url=self.base_url, api_key=self._api_key)
        return self._client

    @staticmethod
    def _translate_tools(tools: list[dict]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool["input_schema"],
                },
            }
            for tool in tools
        ]

    @staticmethod
    def _translate_tool_choice(tool_choice: dict):
        if tool_choice.get("type") == "tool":
            return {"type": "function", "function": {"name": tool_choice["name"]}}
        if tool_choice.get("type") == "any":
            return "required"
        return "auto"

    @staticmethod
    def _submit_tool(tools: list[dict], tool_choice: dict) -> dict:
        """The tool the model should be constrained to.

        Structured output constrains generation to exactly one schema, so there
        is no choice to offer. `ask_clarification` is dropped and the submit tool
        is used, which is also the branch whose fields need to be required.
        """
        if tool_choice.get("type") == "tool":
            for tool in tools:
                if tool["name"] == tool_choice["name"]:
                    return tool
        candidates = [t for t in tools if t["name"] != "ask_clarification"]
        if not candidates:
            raise ProviderOutputError("No submit tool was offered.")
        return candidates[0]

    def _complete_structured(self, system, messages, tools, tool_choice) -> ToolCall:
        import openai

        client = self._get_client()
        tool = self._submit_tool(tools, tool_choice)
        schema = tool["input_schema"]

        instruction = (
            "Respond with JSON matching the required schema exactly. Every array "
            "the schema describes must be populated -- an empty array is not a "
            "valid answer. If anything is ambiguous, make a reasonable assumption "
            "and state it rather than leaving fields out."
        )
        payload_messages = [
            {"role": "system", "content": f"{system}\n\n{instruction}"},
            *messages,
        ]
        try:
            response = client.chat.completions.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                messages=payload_messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "response", "schema": schema, "strict": True},
                },
            )
        except (openai.APIStatusError, openai.APIConnectionError) as e:
            raise self._as_unavailable(e) from e

        content = response.choices[0].message.content or ""
        try:
            arguments = json.loads(content)
        except json.JSONDecodeError as e:
            raise ProviderOutputError("The model returned malformed JSON. Try again.") from e
        if not isinstance(arguments, dict):
            raise ProviderOutputError("The model's response was not a JSON object.")

        usage = {}
        if response.usage:
            usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            }
        return ToolCall(
            name=tool["name"],
            arguments=arguments,
            model=response.model or self.model,
            stop_reason=response.choices[0].finish_reason,
            usage=usage,
        )

    def _as_unavailable(self, e) -> ProviderUnavailable:
        """Map a transport failure to a message that names the fix."""
        import openai

        if isinstance(e, openai.AuthenticationError):
            return ProviderUnavailable(f"Invalid API key for {self.preset}. Check your .env.")
        if isinstance(e, openai.RateLimitError):
            return ProviderUnavailable(
                f"Rate limited by {self.preset}. Free tiers have low limits -- "
                "wait a minute and try again."
            )
        if isinstance(e, openai.NotFoundError):
            preset = OPENAI_COMPATIBLE_PRESETS.get(self.preset, {})
            return ProviderUnavailable(
                f"Model {self.model!r} was not found on {self.preset}. "
                f"Check MACRO_TRACKER_MODEL against {preset.get('docs', 'the model list')}."
            )
        if isinstance(e, openai.APIConnectionError):
            hint = (
                " Is `ollama serve` running?"
                if self.preset == "ollama"
                else " Check your internet connection."
            )
            return ProviderUnavailable(f"Could not reach {self.preset}.{hint}")
        status = getattr(e, "status_code", "unknown")
        return ProviderUnavailable(f"{self.preset} error ({status}). Try again shortly.")

    def complete(self, system, messages, tools, tool_choice) -> ToolCall:
        import openai

        if self.structured_output:
            return self._complete_structured(system, messages, tools, tool_choice)

        client = self._get_client()
        # OpenAI has no separate `system` parameter; it is the first message.
        payload_messages = [{"role": "system", "content": system}, *messages]
        translated_tools = self._translate_tools(tools)
        choice = self._translate_tool_choice(tool_choice)
        if self._forced_choice_unsupported and choice == "required":
            choice = "auto"

        def send(tool_choice_value):
            return client.chat.completions.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                messages=payload_messages,
                tools=translated_tools,
                tool_choice=tool_choice_value,
            )

        try:
            try:
                response = send(choice)
            except openai.BadRequestError:
                # Not every compatible endpoint implements `required` (Ollama and
                # some Gemini models historically did not). Fall back to `auto`
                # once and remember, rather than failing the user's meal log.
                if choice != "required":
                    raise
                self._forced_choice_unsupported = True
                response = send("auto")
        except openai.AuthenticationError as e:
            raise ProviderUnavailable(
                f"Invalid API key for {self.preset}. Check your .env."
            ) from e
        except openai.RateLimitError as e:
            raise ProviderUnavailable(
                f"Rate limited by {self.preset}. Free tiers have low limits -- "
                "wait a minute and try again."
            ) from e
        except openai.NotFoundError as e:
            preset = OPENAI_COMPATIBLE_PRESETS.get(self.preset, {})
            raise ProviderUnavailable(
                f"Model {self.model!r} was not found on {self.preset}. "
                f"Check MACRO_TRACKER_MODEL against {preset.get('docs', 'the model list')}."
            ) from e
        except openai.APIConnectionError as e:
            hint = (
                " Is `ollama serve` running?"
                if self.preset == "ollama"
                else " Check your internet connection."
            )
            raise ProviderUnavailable(f"Could not reach {self.preset}.{hint}") from e
        except openai.APIStatusError as e:
            raise ProviderUnavailable(
                f"{self.preset} error ({e.status_code}). Try again shortly."
            ) from e

        message = response.choices[0].message
        calls = message.tool_calls or []
        if not calls:
            raise ProviderOutputError(
                "The model replied with text instead of calling a tool. Smaller models "
                "sometimes do this -- try again, or use a model with stronger tool-calling "
                "support."
            )

        call = calls[0]
        raw = call.function.arguments
        try:
            arguments = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError as e:
            raise ProviderOutputError(
                "The model returned malformed JSON in its tool call. Try again."
            ) from e
        if not isinstance(arguments, dict):
            raise ProviderOutputError("The model's tool call arguments were not an object.")

        usage = {}
        if response.usage:
            usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            }
        return ToolCall(
            name=call.function.name,
            arguments=arguments,
            model=response.model or self.model,
            stop_reason=response.choices[0].finish_reason,
            usage=usage,
        )


MAX_TOKENS = 2000

_provider: Provider | None = None


def build_provider(name: str | None = None, model: str | None = None) -> Provider:
    """Construct a provider from the environment (or explicit overrides)."""
    name = (name or os.environ.get("MACRO_TRACKER_PROVIDER") or "anthropic").strip().lower()
    model = model or os.environ.get("MACRO_TRACKER_MODEL") or None

    if name == "anthropic":
        return AnthropicProvider(model)

    preset = OPENAI_COMPATIBLE_PRESETS.get(name)
    if preset is None:
        known = ", ".join(["anthropic", *sorted(OPENAI_COMPATIBLE_PRESETS)])
        raise ProviderUnavailable(
            f"Unknown MACRO_TRACKER_PROVIDER {name!r}. Supported values: {known}."
        )

    base_url = os.environ.get("MACRO_TRACKER_BASE_URL") or preset["base_url"]
    if not base_url:
        raise ProviderUnavailable(
            f"Provider {name!r} needs MACRO_TRACKER_BASE_URL set in .env."
        )

    if not model:
        raise ProviderUnavailable(
            f"Provider {name!r} needs MACRO_TRACKER_MODEL set in .env. "
            f"Pick one from {preset['docs']}."
        )

    api_key = os.environ.get(preset["key_env"]) or os.environ.get("MACRO_TRACKER_API_KEY")
    if not api_key:
        if preset["key_required"]:
            raise ProviderUnavailable(
                f"Provider {name!r} needs {preset['key_env']} set in .env."
            )
        # Ollama ignores the key but the OpenAI client insists on one.
        api_key = "not-needed"

    structured = preset.get("structured_output", False)
    override = os.environ.get("MACRO_TRACKER_STRUCTURED_OUTPUT")
    if override is not None:
        structured = override.strip().lower() in ("1", "true", "yes", "on")

    return OpenAICompatibleProvider(name, model, base_url, api_key, structured)


def get_provider() -> Provider:
    """The process-wide provider, built on first use."""
    global _provider
    if _provider is None:
        _provider = build_provider()
    return _provider


def reset_provider() -> None:
    """Drop the cached provider so the next call re-reads the environment."""
    global _provider
    _provider = None


def is_configured() -> tuple[bool, str]:
    """Whether the AI features can run, and a description for the UI.

    Never raises: this is called by `/api/health` on every page load, and a
    misconfigured provider should degrade the app, not break the endpoint.
    """
    try:
        provider = build_provider()
    except ProviderUnavailable as e:
        return False, str(e)

    # build_provider() only validates configuration for the OpenAI-compatible
    # presets; the Anthropic client reads its key lazily, so check it here.
    if isinstance(provider, AnthropicProvider) and not os.environ.get("ANTHROPIC_API_KEY"):
        return False, "No ANTHROPIC_API_KEY configured."
    return True, provider.describe()
