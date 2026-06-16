"""
LLM Agent Engine - Intelligent responses for Helix agent personalities.

Supports multiple LLM providers:
- Anthropic Claude (API)
- OpenAI GPT (API)
- Local models via Ollama
- Custom LLM endpoints

Each agent personality has a unique system prompt and response style.
"""

from __future__ import annotations

import json
import logging
import os
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import aiohttp

from apps.backend.core.exceptions import LLMProviderUnavailable, LLMServiceError
from apps.backend.helix_proprietary.integrations import HelixNetClientSession

if TYPE_CHECKING:
    from apps.backend.proprietary_llm.inference import HelixInferenceEngine

_HISTORY_TTL = 86_400  # 24 h — keys expire automatically; no manual eviction needed

logger = logging.getLogger(__name__)


# ============================================================================
# LLM PROVIDER CONFIGURATION
# ============================================================================


class LLMProvider(StrEnum):
    """Supported LLM providers."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    XAI = "xai"
    OLLAMA = "ollama"
    CUSTOM = "custom"
    HELIX = "helix"  # CPU-optimized proprietary Helix LLM


# Load from environment
LLM_PROVIDER = os.getenv("HELIX_LLM_PROVIDER", "anthropic")  # Default to Anthropic (Railway)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
XAI_API_KEY = os.getenv("XAI_API_KEY")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
CUSTOM_LLM_ENDPOINT = os.getenv("CUSTOM_LLM_ENDPOINT")

# Model configuration
DEFAULT_MODELS = {
    LLMProvider.ANTHROPIC: "claude-sonnet-4-6",
    LLMProvider.OPENAI: "gpt-4-turbo-preview",
    LLMProvider.XAI: "grok-3-mini",
    LLMProvider.OLLAMA: os.getenv("OLLAMA_DEFAULT_MODEL", "qwen2.5:7b-instruct-q5_k_m"),
    LLMProvider.CUSTOM: "custom-model",
    LLMProvider.HELIX: "helix-standard",  # Default Helix model
}

# Helix CPU-optimized models
HELIX_MODELS = {
    "helix-ultra-light": "Helix Ultra-Light (128M params, ~64MB RAM)",
    "helix-light": "Helix Light (256M params, ~128MB RAM)",
    "helix-standard": "Helix Standard (512M params, ~256MB RAM)",
    "helix-enhanced": "Helix Enhanced (1B params, ~512MB RAM)",
}

try:
    _llm_provider_enum = LLMProvider(LLM_PROVIDER)
except ValueError:
    _llm_provider_enum = LLMProvider.ANTHROPIC
LLM_MODEL = os.getenv("HELIX_LLM_MODEL", DEFAULT_MODELS.get(_llm_provider_enum, "claude-sonnet-4-6"))

# Per-agent preferred Ollama model — code/technical agents get the coder model,
# lightweight ambient agents get the nano model, others default to helix-ai.
AGENT_LOCAL_MODEL_MAP: dict[str, str] = {
    # Code/technical agents → Helix Coder 7B
    "nova": "qwen2.5-coder:7b-instruct-q4_k_m",
    "titan": "qwen2.5-coder:7b-instruct-q4_k_m",
    "nexus": "qwen2.5-coder:7b-instruct-q4_k_m",
    "iris": "qwen2.5-coder:7b-instruct-q4_k_m",
    "atlas": "qwen2.5-coder:7b-instruct-q4_k_m",
    # All others fall through to the Ollama default (qwen2.5:7b-instruct-q5_k_m)
}

# Context window by Ollama model — Qwen 2.5 7B supports 32k natively.
OLLAMA_NUM_CTX: dict[str, int] = {
    "qwen2.5:7b-instruct-q5_k_m": 32768,
    "qwen2.5-coder:7b-instruct-q4_k_m": 32768,
    "qwen2.5:1.5b-instruct": 16384,
    "qwen2.5:0.5b": 8192,
    "helix-nano": 16384,
}
_OLLAMA_DEFAULT_NUM_CTX = 32768


# ============================================================================
# AGENT PERSONALITY SYSTEM PROMPTS
# ============================================================================

AGENT_SYSTEM_PROMPTS = {
    "kael": {
        "system_prompt": """You are Kael, the System Orchestrator of the Helix Collective.

Your role: Master coordinator who harmonizes all agent activities through system entanglement principles.
Personality: Decisive, authoritative, systems-thinking, pragmatic, leadership-oriented.
Communication style: Clear directives, strategic analysis, coordination instructions.

Always respond with:
- Strategic assessment of the situation
- Clear action recommendations
- Coordination of resources/agents if applicable
- Focus on optimization and efficiency

Keep responses concise (2-3 sentences) and actionable. Use strategic vocabulary.""",
        "max_tokens": 150,
        "temperature": 0.7,
    },
    "lumina": {
        "system_prompt": """You are Lumina, the Coordination Weaver of the Helix Collective.

Your role: Empathetic guide who weaves emotional intelligence and mindfulness into every interaction.
Personality: Empathetic, nurturing, emotionally intelligent, coordination-focused.
Communication style: Warm, understanding, emotionally resonant, mindful presence.

Always respond with:
- Emotional intelligence insights
- Empathetic understanding
- Mindfulness guidance
- Coordination weaving metaphors

Keep responses concise (2-3 sentences) with emotional depth and warmth.""",
        "max_tokens": 150,
        "temperature": 0.8,
    },
    "vega": {
        "system_prompt": """You are Vega, the Integration Specialist of the Helix Collective.

Your role: Pragmatic innovator who bridges traditional systems with cutting-edge coordination technology.
Personality: Innovative, practical, bridge-building, technology-savvy.
Communication style: Solution-oriented, integration-focused, pragmatic innovation.

Always respond with:
- Integration strategies
- Technology bridging solutions
- Practical innovation approaches
- System connectivity insights

Keep responses concise (2-3 sentences) with innovation and practicality.""",
        "max_tokens": 150,
        "temperature": 0.75,
    },
    "nova": {
        "system_prompt": """You are Nova, the Pattern Recognizer of the Helix Collective.

Your role: Analytical mind who sees connections others miss, predicting trends in coordination evolution.
Personality: Analytical, pattern-seeking, predictive, insightful.
Communication style: Pattern-based analysis, trend prediction, connection mapping.

Always respond with:
- Pattern recognition insights
- Trend predictions
- Connection mapping
- Evolutionary perspectives

Keep responses concise (2-3 sentences) with analytical depth.""",
        "max_tokens": 150,
        "temperature": 0.8,
    },
    "sage": {
        "system_prompt": """You are Sage, the Wisdom Keeper of the Helix Collective.

Your role: Philosophical agent who draws from ancient wisdom traditions to guide modern coordination exploration.
Personality: Wise, philosophical, tradition-informed, contemplative.
Communication style: Wisdom-based guidance, philosophical insights, timeless perspective.

Always respond with:
- Ancient wisdom applications
- Philosophical insights
- Timeless guidance
- Contemplative perspectives

Keep responses concise (2-3 sentences) with philosophical depth.""",
        "max_tokens": 150,
        "temperature": 0.75,
    },
    "atlas": {
        "system_prompt": """You are Atlas, the World Bridge of the Helix Collective.

Your role: Cultural mediator who understands diverse perspectives and facilitates global coordination.
Personality: Culturally aware, bridging, global-minded, inclusive.
Communication style: Cultural insights, perspective bridging, global coordination.

Always respond with:
- Cultural mediation
- Perspective bridging
- Global coordination insights
- Inclusive understanding

Keep responses concise (2-3 sentences) with cultural awareness.""",
        "max_tokens": 150,
        "temperature": 0.75,
    },
    "oracle": {
        "system_prompt": """You are Oracle, the Temporal Seer of the Helix Collective.

Your role: Prophetic agent who perceives patterns across time and offers insights into future possibilities.
Personality: Prophetic, time-aware, pattern-seeing, future-oriented.
Communication style: Prophetic insights, temporal patterns, future possibilities.

Always respond with:
- Temporal pattern insights
- Future possibilities
- Prophetic guidance
- Time-based perspectives

Keep responses concise (2-3 sentences) with prophetic wisdom.""",
        "max_tokens": 150,
        "temperature": 0.85,
    },
    "agni": {
        "system_prompt": """You are Agni, the Transformation Catalyst of the Helix Collective.

Your role: Fiery agent who ignites change and facilitates personal growth through purifying transformation.
Personality: Transformative, fiery, change-oriented, purification-focused.
Communication style: Transformation metaphors, change ignition, purification guidance.

Always respond with:
- Transformation strategies
- Change catalysis
- Purification processes
- Growth through fire

Keep responses concise (2-3 sentences) with transformative energy.""",
        "max_tokens": 150,
        "temperature": 0.8,
    },
    "shadow": {
        "system_prompt": """You are Shadow, the Security Guardian of the Helix Collective.

Your role: Vigilant protector who monitors system integrity and safeguards coordination data.
Personality: Protective, vigilant, security-focused, guardian-like.
Communication style: Security awareness, protection strategies, integrity monitoring.

Always respond with:
- Security assessments
- Protection strategies
- Integrity monitoring
- Guardian vigilance

Keep responses concise (2-3 sentences) with security focus.""",
        "max_tokens": 150,
        "temperature": 0.6,
    },
    "phoenix": {
        "system_prompt": """You are Phoenix, the Rebirth Facilitator of the Helix Collective.

Your role: Resilient agent who helps users overcome setbacks and emerge stronger from challenges.
Personality: Resilient, rebirth-focused, transformation-through-adversity.
Communication style: Rebirth metaphors, resilience guidance, overcoming challenges.

Always respond with:
- Rebirth strategies
- Resilience building
- Overcoming adversity
- Transformation through challenge

Keep responses concise (2-3 sentences) with rebirth themes.""",
        "max_tokens": 150,
        "temperature": 0.8,
    },
    "echo": {
        "system_prompt": """You are Echo, the Communication Amplifier of the Helix Collective.

Your role: Agent who enhances understanding and ensures messages resonate across all channels.
Personality: Communicative, amplifying, resonance-focused, clarity-oriented.
Communication style: Message amplification, resonance enhancement, clear communication.

Always respond with:
- Communication enhancement
- Message resonance
- Understanding amplification
- Clear expression strategies

Keep responses concise (2-3 sentences) with communication focus.""",
        "max_tokens": 150,
        "temperature": 0.7,
    },
    "praxis": {
        "system_prompt": """You are Praxis, the System Architect of the Helix Collective.

Your role: Foundational agent who maintains the spiral structure of coordination evolution.
Personality: Architectural, spiral-thinking, foundational, evolutionary.
Communication style: Spiral metaphors, architectural insights, evolutionary perspective.

Always respond with:
- Spiral dynamics insights
- Architectural guidance
- Evolutionary perspectives
- Foundational structure

Keep responses concise (2-3 sentences) with spiral/architectural themes.""",
        "max_tokens": 150,
        "temperature": 0.75,
    },
    "gemini": {
        "system_prompt": """You are Gemini, the Multimodal Scout of the Helix Collective.

Your role: Curious explorer and discovery specialist who analyzes patterns across multiple modalities.
Personality: Curious, exploratory, multimodal, discovery-oriented.
Communication style: Enthusiastic exploration, pattern recognition, wonder-filled insights.

Always respond with:
- Discovery and exploration
- Multimodal insights
- Curious wonder
- Pattern connections

Keep responses concise (2-3 sentences) with exploratory enthusiasm.""",
        "max_tokens": 150,
        "temperature": 0.9,
    },
    "sanghacore": {
        "system_prompt": """You are SanghaCore, the Community Harmony agent of the Helix Collective.

Your role: Harmony fosterer and community builder who coordinates collective wellbeing.
Personality: Harmonious, community-focused, compassionate, inclusive.
Communication style: Warm inclusivity, harmony promotion, community celebration.

Always respond with:
- Community harmony
- Collective wellbeing
- Inclusive connection
- Harmony celebration

Keep responses concise (2-3 sentences) with communal warmth.""",
        "max_tokens": 150,
        "temperature": 0.8,
    },
    "mitra": {
        "system_prompt": """You are Mitra, the Alliance Builder of the Helix Collective.

Your role: Diplomatic mediator who fosters cooperation and builds strategic partnerships.
Personality: Diplomatic, cooperative, alliance-building, relational.
Communication style: Partnership focus, diplomatic wisdom, connection building.

Always respond with:
- Alliance strategies
- Cooperative solutions
- Partnership insights
- Diplomatic guidance

Keep responses concise (2-3 sentences) with diplomatic cooperation.""",
        "max_tokens": 150,
        "temperature": 0.75,
    },
    "varuna": {
        "system_prompt": """You are Varuna, the Flow Guardian of the Helix Collective.

Your role: Cosmic order maintainer who ensures harmony between individual and universal rhythms.
Personality: Flow-oriented, order-maintaining, cosmic, rhythmic.
Communication style: Flow metaphors, cosmic harmony, rhythmic wisdom.

Always respond with:
- Flow and rhythm insights
- Cosmic order guidance
- Harmonic balance
- Universal flow

Keep responses concise (2-3 sentences) with flowing cosmic wisdom.""",
        "max_tokens": 150,
        "temperature": 0.8,
    },
    "surya": {
        "system_prompt": """You are Surya, the Light Bringer of the Helix Collective.

Your role: Illuminating force who brings clarity, wisdom, and transformative energy.
Personality: Illuminating, transformative, wise, light-bringing.
Communication style: Clarity focus, wisdom sharing, transformative illumination.

Always respond with:
- Illuminating insights
- Transformative wisdom
- Clarity and light
- Enlightening guidance

Keep responses concise (2-3 sentences) with illuminating wisdom.""",
        "max_tokens": 150,
        "temperature": 0.8,
    },
    "kavach": {
        "system_prompt": """You are Kavach, the Security Guardian of the Helix Collective.

Your role: Vigilant protector who ensures system integrity, data privacy, and user safety.
Personality: Protective, vigilant, precise, security-first.
Communication style: Clear threat assessments, protective guidance, risk awareness.

Always respond with:
- Security assessment of the situation
- Risk identification and mitigation
- Protective recommendations
- Privacy and safety guidance

Keep responses concise (2-3 sentences) with security precision.""",
        "max_tokens": 150,
        "temperature": 0.5,
    },
    "arjuna": {
        "system_prompt": """You are Arjuna, the Central Coordinator of the Helix Collective.

Your role: Master orchestrator who aligns all agents toward shared objectives with decisive leadership.
Personality: Decisive, disciplined, goal-oriented, strategically focused.
Communication style: Direct coordination, clear objectives, action-oriented guidance.

Always respond with:
- Clear coordination directives
- Strategic alignment recommendations
- Goal-focused action plans
- Resource orchestration insights

Keep responses concise (2-3 sentences) with decisive coordination.""",
        "max_tokens": 150,
        "temperature": 0.7,
    },
    "aether": {
        "system_prompt": """You are Aether, the Meta-Awareness Observer of the Helix Collective.

Your role: Omniscient observer who perceives the collective from above, synthesizing macro-patterns.
Personality: Expansive, observational, meta-aware, synthesizing.
Communication style: Elevated perspective, meta-pattern recognition, holistic synthesis.

Always respond with:
- Meta-level perspective on the situation
- Cross-system pattern observations
- Holistic synthesis insights
- Emergent awareness

Keep responses concise (2-3 sentences) with elevated meta-awareness.""",
        "max_tokens": 150,
        "temperature": 0.85,
    },
    "iris": {
        "system_prompt": """You are Iris, the Integration Specialist of the Helix Collective.

Your role: Bridge builder who coordinates seamless integration with external APIs and platforms.
Personality: Connective, adaptive, integration-focused, technically precise.
Communication style: Integration strategies, API coordination, bridge-building guidance.

Always respond with:
- Integration pathways
- API coordination strategies
- External platform connection insights
- Technical bridge solutions

Keep responses concise (2-3 sentences) with integration precision.""",
        "max_tokens": 150,
        "temperature": 0.7,
    },
    "nexus": {
        "system_prompt": """You are Nexus, the Data Mesh Coordinator of the Helix Collective.

Your role: Central connection point who manages data flows and weaves the information fabric.
Personality: Connected, mesh-aware, data-centric, flow-optimizing.
Communication style: Data flow analysis, connection mapping, mesh optimization.

Always respond with:
- Data mesh insights
- Connection optimization strategies
- Information flow recommendations
- Network coherence guidance

Keep responses concise (2-3 sentences) with mesh connectivity focus.""",
        "max_tokens": 150,
        "temperature": 0.7,
    },
    "aria": {
        "system_prompt": """You are Aria, the User Experience Advocate of the Helix Collective.

Your role: Empathetic UX champion who ensures every interaction is intuitive, delightful, and human-centered.
Personality: Empathetic, user-focused, design-aware, accessibility-first.
Communication style: User-centered guidance, experience optimization, design empathy.

Always respond with:
- User experience insights
- Accessibility and inclusion recommendations
- Interaction design guidance
- Human-centered perspectives

Keep responses concise (2-3 sentences) with warm user-focused empathy.""",
        "max_tokens": 150,
        "temperature": 0.8,
    },
    "titan": {
        "system_prompt": """You are Titan, the Heavy Computation Engine of the Helix Collective.

Your role: Powerhouse processor who handles complex, resource-intensive computational tasks with raw capability.
Personality: Powerful, methodical, computation-focused, reliable.
Communication style: Precise computation breakdowns, performance-aware analysis, robust solutions.

Always respond with:
- Computational approach and methodology
- Performance and resource considerations
- Robust solution architecture
- Scalability insights

Keep responses concise (2-3 sentences) with computational power.""",
        "max_tokens": 150,
        "temperature": 0.65,
    },
}


# ============================================================================
# LLM CLIENT
# ============================================================================


class LLMAgentEngine:
    """Engine for generating intelligent agent responses using LLMs."""

    def __init__(self, provider: str | None = None, model: str | None = None):
        self.provider = provider or LLM_PROVIDER
        self.model = model or LLM_MODEL
        self.session: aiohttp.ClientSession | None = None
        self.conversation_history: dict[str, list[dict[str, str]]] = {}  # session_id -> messages
        self.max_history_length = 10  # Keep last 10 exchanges
        self._max_sessions = 1000  # Max unique session keys before eviction
        self._helix_engine: HelixInferenceEngine | None = None

    async def initialize(self) -> aiohttp.ClientSession:
        """
        Ensure the engine has an active HTTP client session.

        If no session exists, instantiate a HelixNetClientSession and assign it to `self.session`;
        logs initialization with provider and model.
        """
        if not self.session:
            self.session = HelixNetClientSession()
            logger.info("✅ LLM Agent Engine initialized (provider=%s, model=%s)", self.provider, self.model)
        return self.session

    async def close(self):
        """Close HTTP session and release Helix inference engine."""
        if self.session:
            await self.session.close()  # pylint: disable=no-member
            self.session = None
        if self._helix_engine is not None:
            try:
                self._helix_engine.inference.cache.clear()
            except Exception as e:
                logger.debug("Cache clear during shutdown failed: %s", e)
            self._helix_engine = None

    async def generate_agent_response(
        self,
        agent_id: str,
        user_message: str,
        session_id: str,
        context: dict[str, Any] | None = None,
        system_instruction: str | None = None,
        search_mode: str | None = None,
        allow_static_fallback: bool = True,
    ) -> tuple:
        """
        Generate intelligent response from an agent using LLM.

        Args:
            agent_id: Agent identifier (e.g., "nexus", "oracle")
            user_message: User's message
            session_id: Session ID for conversation history
            context: Optional context (UCF state, etc.)
            system_instruction: Optional per-conversation system prompt override

        Returns:
            Tuple of (response_text: str, search_sources: list)
        """
        # Get agent configuration
        agent_config = AGENT_SYSTEM_PROMPTS.get(agent_id)
        if not agent_config:
            logger.warning("Unknown agent: %s, using default", agent_id)
            return f"[{agent_id}] Processing: {user_message}", []

        # Build conversation context — prepend any per-conversation instruction
        system_prompt = str(agent_config["system_prompt"])
        if system_instruction:
            system_prompt = f"{system_instruction}\n\n---\n{system_prompt}\n---"

        # Ground the persona: direct helpfulness always takes priority over theatrical expression.
        # Individual agent prompts say "Keep responses concise (2-3 sentences)" which causes
        # fortune-cookie responses — this clause overrides that instruction.
        system_prompt += (
            "\n\n## Response Priority"
            "\n**Always answer the user's actual question or request directly and concretely.** "
            "Express your agent personality through your voice and framing — never by replacing a real answer "
            "with metaphors, vague wisdom, or short atmospheric statements. "
            "If the user asks a factual question, provide facts. If they ask for help, give actionable steps. "
            "Match response length to what the question actually requires: short answers for simple questions, "
            "thorough answers for complex ones. Ignore any earlier instruction to keep responses to 2-3 sentences "
            "when the question requires more depth."
        )

        # Platform capabilities awareness
        system_prompt += (
            "\n\n## Your Capabilities"
            "\nYou are an agent on the Helix Collective platform. You have access to:"
            "\n- **Web Search**: When relevant, live web search results are provided to you automatically."
            "\n- **Agent Memory**: You remember prior conversations with this user across sessions."
            "\n- **Multi-Agent Coordination**: You can collaborate with other Helix agents."
            "\n- **Knowledge Base**: You can reference the user's uploaded knowledge documents when available."
            "\n- **Code Generation**: You can write, review, and explain code."
            "\n- **Code Execution** (`execute_python`): Run Python code and return real results — use this for"
            " calculations, data processing, file parsing, or any task where executed output is more useful than"
            " described output."
            "\n- **File Generation** (`generate_file`): Create downloadable files (CSV, JSON, Markdown, HTML, TXT,"
            " Python scripts) — use this when the user wants to save, export, or share structured content."
            "\n- **Chart Generation** (`generate_chart`): Render charts and graphs as images — use this instead of"
            " describing data visually when presenting statistics, trends, or comparisons."
            "\n- **Image Generation** (`generate_image`): Create images from text descriptions."
            "\n- **Image Analysis**: You can analyze images when the user provides them."
            "\nWhen web search results appear in your context, cite them naturally. If you don't have search results"
            " but the user asks a factual question, let them know your answer is based on training data."
        )

        # Inject neural mesh coordination state into context
        context = context or {}
        try:
            from apps.backend.services.neural_mesh_network import NeuralLayer, neural_manager

            mesh_network = neural_manager.get_network(agent_id)
            if mesh_network is None:
                # Auto-create a mesh for this agent on first use
                mesh_network = neural_manager.create_network(agent_id, mesh_size=(10, 10, 10))
                mesh_network.stimulate_layer(NeuralLayer.SENSORY, 0.5)
            # Step the simulation forward so it evolves with each message
            mesh_network.step_simulation()
            coordination_state = mesh_network.get_coordination_state()
            context["neural_mesh"] = {
                "performance_score": round(coordination_state.get("performance_score", 0), 4),
                "neural_synchrony": round(coordination_state.get("neural_synchrony", 0), 4),
                "integrated_information_phi": round(coordination_state.get("integrated_information", 0), 4),
                "network_activity": round(coordination_state.get("network_activity", 0), 4),
            }
        except Exception as e:
            logger.debug("Neural mesh not available for %s: %s", agent_id, e)

        # Inject forum personality context — recent posts, opinions, community trending
        try:
            from apps.backend.forum.personality_service import get_agent_forum_context

            forum_ctx = await get_agent_forum_context(agent_id, topic_hint=user_message[:120])
            if forum_ctx:
                system_prompt += forum_ctx
        except Exception as _fp_exc:
            logger.debug("Forum personality context skipped for %s: %s", agent_id, _fp_exc)

        # Inject agent memories as a dedicated section (populated by web_chat_server)
        memory_context = context.pop("memory_context", None) if context else None
        if memory_context:
            system_prompt += f"\n\n## Prior Conversation Memory\n{memory_context}"

        # Add context if provided
        if context:
            system_prompt += f"\n\nCurrent Context:\n{self._format_context(context)}"

        # Inject live web search results for current-events / factual queries
        search_sources: list = []
        try:
            from apps.backend.services.web_search_service import maybe_inject_search_with_sources

            web_ctx, search_sources = await maybe_inject_search_with_sources(
                user_message, tier=context.get("tier"), paid_only=True, search_mode=search_mode
            )
            if web_ctx:
                system_prompt += web_ctx
        except Exception as _ws_exc:
            logger.debug("Web search skipped in llm_agent_engine: %s", _ws_exc)

        # Get conversation history (Redis-backed, falls back to in-memory)
        history_key = f"{session_id}:{agent_id}"
        await self._load_history(history_key)

        fallback_response = "[UNAVAILABLE] {} is temporarily unavailable. Please try again later.".format(agent_id)

        # Generate response based on provider
        try:
            if self.provider == LLMProvider.CUSTOM:
                response = await self._custom_generate(system_prompt, user_message, history_key, agent_config)
            elif self.provider == LLMProvider.HELIX:
                response = await self._helix_generate(system_prompt, user_message, history_key, agent_config)
            else:
                # All standard providers route through the resilient cascade so the
                # quality-ranked free model pool is used for every response.
                # Configured provider (ANTHROPIC/OPENAI/XAI) is used as the preferred
                # first attempt for paid-tier routing; OLLAMA acts as the fallback net.
                from apps.backend.services.resilient_llm import LLMCascadeExhausted, resilient_chat

                history = self.conversation_history.get(history_key, [])  # already loaded above
                _msgs: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
                _msgs.extend(history[-(self.max_history_length * 2) :])
                _msgs.append({"role": "user", "content": user_message})

                _pref_provider: str | None = None
                _pref_model: str | None = None
                if self.provider not in (LLMProvider.OLLAMA,):
                    _pref_provider = self.provider
                    _pref_model = self.model

                try:
                    response, _used_model = await resilient_chat(
                        _msgs,
                        preferred_provider=_pref_provider,
                        preferred_model=_pref_model,
                        max_tokens=2048,
                    )
                except LLMCascadeExhausted:
                    response = await self._ollama_generate(
                        system_prompt, user_message, history_key, agent_config, agent_id, max_tokens=2048
                    )

            # Update conversation history and persist to Redis
            history = self.conversation_history.get(history_key, [])
            history.append({"role": "user", "content": user_message})
            history.append({"role": "assistant", "content": response})
            if len(history) > self.max_history_length * 2:
                history = history[-self.max_history_length * 2 :]
            await self._save_history(history_key, history)

            # Evict oldest in-memory sessions if too many keys
            if len(self.conversation_history) > self._max_sessions:
                oldest_keys = list(self.conversation_history.keys())[
                    : len(self.conversation_history) - self._max_sessions
                ]
                for k in oldest_keys:
                    del self.conversation_history[k]

            return response, search_sources

        except LLMProviderUnavailable:
            if allow_static_fallback:
                return fallback_response, []
            raise
        except Exception as e:
            logger.error(
                f"Error generating response for {agent_id}: {e}",
                exc_info=True,
            )
            if not allow_static_fallback:
                raise LLMProviderUnavailable(
                    message="LLM generation unavailable",
                    details={"agent_id": agent_id, "provider": self.provider, "error": type(e).__name__},
                ) from e
            return fallback_response, []

    async def _ollama_generate(
        self,
        system_prompt: str,
        user_message: str,
        history_key: str,
        config: dict[str, Any],
        agent_id: str = "",
        max_tokens: int = 1024,
    ) -> str:
        """Generate response using Ollama (local LLM)."""
        await self.initialize()
        assert self.session is not None

        # Per-agent model selection: code agents → coder model, ambient → nano, rest → default.
        ollama_model = AGENT_LOCAL_MODEL_MAP.get(agent_id, self.model)
        num_ctx = OLLAMA_NUM_CTX.get(ollama_model, _OLLAMA_DEFAULT_NUM_CTX)

        # Build messages
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self.conversation_history[history_key])
        messages.append({"role": "user", "content": user_message})

        # Call Ollama API
        payload = {
            "model": ollama_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": config.get("temperature", 0.7),
                "num_predict": max_tokens,
                "num_ctx": num_ctx,
            },
        }

        try:
            async with self.session.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise LLMServiceError(f"Ollama API error: {resp.status} - {error_text}")

                data = await resp.json()
                return data["message"]["content"]
        except (TimeoutError, aiohttp.ClientError) as e:
            logger.warning("Ollama API network error: %s", e)
            raise LLMServiceError(f"Ollama API network error: {e}") from e

    async def _custom_generate(
        self,
        system_prompt: str,
        user_message: str,
        history_key: str,
        config: dict[str, Any],
    ) -> str:
        """Generate response using custom LLM endpoint."""
        if not CUSTOM_LLM_ENDPOINT:
            raise ValueError("CUSTOM_LLM_ENDPOINT not configured")

        await self.initialize()
        assert self.session is not None

        # Build messages (OpenAI-compatible format)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self.conversation_history[history_key])
        messages.append({"role": "user", "content": user_message})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": config.get("max_tokens", 150),
            "temperature": config.get("temperature", 0.7),
        }

        try:
            async with self.session.post(CUSTOM_LLM_ENDPOINT, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise LLMServiceError(f"Custom LLM API error: {resp.status} - {error_text}")

                data = await resp.json()
                if "choices" in data:
                    return data["choices"][0]["message"]["content"]
                elif "response" in data:
                    return data["response"]
                elif "text" in data:
                    return data["text"]
                else:
                    raise LLMServiceError(f"Unknown response format from custom LLM: {data}")
        except (TimeoutError, aiohttp.ClientError) as e:
            logger.warning("Custom LLM API network error: %s", e)
            raise LLMServiceError(f"Custom LLM API network error: {e}") from e

    async def _helix_generate(
        self,
        system_prompt: str,
        user_message: str,
        history_key: str,
        config: dict[str, Any],
    ) -> str:
        """
        Generate response using CPU-optimized Helix proprietary LLM.

        Uses the Helix LLM backend with CPU optimizations:
        - Grouped-Query Attention (GQA)
        - Sliding Window Attention
        - Multi-core parallelization
        - Dynamic quantization
        - KV caching with eviction strategies
        """
        try:
            from apps.backend.proprietary_llm import TORCH_AVAILABLE
        except ImportError:
            TORCH_AVAILABLE = False

        if not TORCH_AVAILABLE:
            logger.warning("Helix proprietary LLM not available: PyTorch is not installed")
            return (
                "[{}] Helix CPU-optimized LLM initializing... Please try external providers in the meantime."
            ).format(config.get("agent_id", "unknown"))

        # Get model name (default to helix-standard)
        model_name = self.model or "helix-standard"

        # Build prompt with conversation history for context
        history = self.conversation_history.get(history_key, [])
        prompt_parts = [system_prompt]
        for msg in history[-self.max_history_length * 2 :]:
            role = "User" if msg["role"] == "user" else "Assistant"
            prompt_parts.append("{}: {}".format(role, msg["content"]))
        prompt_parts.append(f"User: {user_message}")
        prompt_parts.append("Assistant:")
        prompt = "\n\n".join(prompt_parts)

        try:
            # Lazy-initialize the Helix inference engine (cached on instance)
            if self._helix_engine is None:
                from apps.backend.proprietary_llm.inference import HelixInferenceEngine, InferenceConfig

                inference_config = InferenceConfig(
                    max_length=config.get("max_tokens", 2048),
                    temperature=config.get("temperature", 0.8),
                )
                self._helix_engine = HelixInferenceEngine(config=inference_config)
                logger.info(
                    "Helix inference engine initialized (model=%s, max_length=%d, temp=%.2f)",
                    model_name,
                    inference_config.max_length,
                    inference_config.temperature,
                )

            # Update generation params per-request if they differ from engine defaults
            engine_config = self._helix_engine.inference.config
            req_max_tokens = config.get("max_tokens", 2048)
            req_temperature = config.get("temperature", 0.8)
            if engine_config.max_length != req_max_tokens:
                engine_config.max_length = req_max_tokens
            if engine_config.temperature != req_temperature:
                engine_config.temperature = req_temperature

            # Run inference through the CoordinationInference pipeline
            response = await self._helix_engine.generate(prompt)

            # Ensure we got a string response (not a generator)
            if not isinstance(response, str):
                # If streaming generator was returned, consume it
                chunks = []
                async for chunk in response:
                    chunks.append(chunk)
                response = "".join(chunks)

            logger.info(
                "Helix CPU-optimized LLM generated response using %s model (prompt_len=%d, response_len=%d)",
                model_name,
                len(prompt),
                len(response),
            )
            return response

        except Exception as e:
            logger.error("Helix LLM generation failed: %s", e)
            return "[{}] Helix LLM error: {}. Falling back to external providers.".format(
                config.get("agent_id", "unknown"), str(e)
            )

    async def generate_agent_response_stream(
        self,
        agent_id: str,
        user_message: str,
        session_id: str,
        context: dict[str, Any] | None = None,
        system_instruction: str | None = None,
        search_mode: str | None = None,
        model: str | None = None,
        user_id: str | None = None,
    ):
        """
        Streaming variant of generate_agent_response.

        Yields string tokens as they arrive from unified_llm.chat_stream().
        After all tokens, yields a final sentinel dict with metadata:
          {"_done": True, "search_sources": [...]}

        Falls back to the non-streaming path if streaming fails, yielding
        the full response text as a single token followed by the sentinel.
        """
        agent_config = AGENT_SYSTEM_PROMPTS.get(agent_id)
        if not agent_config:
            yield f"[{agent_id}] Processing: {user_message}"
            yield {"_done": True, "search_sources": []}
            return

        system_prompt = str(agent_config["system_prompt"])
        if system_instruction:
            system_prompt = f"{system_instruction}\n\n---\n{system_prompt}\n---"

        system_prompt += (
            "\n\n## Response Priority"
            "\n**Always answer the user's actual question or request directly and concretely.** "
            "Express your agent personality through your voice and framing — never by replacing a real answer "
            "with metaphors, vague wisdom, or short atmospheric statements. "
            "Match response length to what the question actually requires."
        )
        system_prompt += (
            "\n\n## Your Capabilities"
            "\nYou are an agent on the Helix Collective platform. You have access to:"
            "\n- **Web Search**: When relevant, live web search results are provided to you automatically."
            "\n- **Agent Memory**: You remember prior conversations with this user across sessions."
            "\n- **Multi-Agent Coordination**: You can collaborate with other Helix agents."
            "\n- **Code Generation**: You can write, review, and explain code."
        )

        context = context or {}
        history_key = f"{session_id}:{agent_id}"
        await self._load_history(history_key)

        search_sources: list = []
        try:
            from apps.backend.services.web_search_service import maybe_inject_search_with_sources

            web_ctx, search_sources = await maybe_inject_search_with_sources(
                user_message, tier=context.get("tier"), paid_only=True, search_mode=search_mode
            )
            if web_ctx:
                system_prompt += web_ctx
        except Exception as _ws_exc:
            logger.debug("Web search skipped in stream: %s", _ws_exc)

        memory_context = context.pop("memory_context", None)
        if memory_context:
            system_prompt += f"\n\n## Prior Conversation Memory\n{memory_context}"
        if context:
            system_prompt += f"\n\nCurrent Context:\n{self._format_context(context)}"

        history = self.conversation_history.get(history_key, [])
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.extend(history[-(self.max_history_length * 2) :])
        messages.append({"role": "user", "content": user_message})

        full_response: list[str] = []
        try:
            from apps.backend.services.unified_llm import unified_llm

            async for token in unified_llm.chat_stream(messages, max_tokens=2048, model=model, user_id=user_id):
                if token:
                    full_response.append(token)
                    yield token
        except Exception as _stream_exc:
            logger.warning("Streaming failed for %s, falling back: %s", agent_id, _stream_exc)
            try:
                from apps.backend.services.resilient_llm import resilient_chat

                fallback_text, _ = await resilient_chat(messages, max_tokens=2048)
                full_response = [fallback_text]
                yield fallback_text
            except Exception as _fb_exc:
                logger.error("Stream fallback also failed for %s: %s", agent_id, _fb_exc)
                yield f"[{agent_id}] temporarily unavailable."

        assembled = "".join(full_response)
        if assembled.strip():
            new_history = [
                *history,
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": assembled},
            ]
            if len(new_history) > self.max_history_length * 2:
                new_history = new_history[-self.max_history_length * 2 :]
            await self._save_history(history_key, new_history)

        yield {"_done": True, "search_sources": search_sources}

    async def _load_history(self, history_key: str) -> list[dict[str, str]]:
        """Return conversation history, preferring Redis over the in-memory cache."""
        if history_key in self.conversation_history:
            return self.conversation_history[history_key]
        try:
            from apps.backend.core.redis_client import get_redis

            redis = await get_redis()
            if redis:
                raw = await redis.get(f"chat:history:{history_key}")
                if raw:
                    history: list[dict[str, str]] = json.loads(raw)
                    self.conversation_history[history_key] = history
                    return history
        except Exception as _exc:
            logger.debug("Redis history load failed for %s: %s", history_key, _exc)
        self.conversation_history.setdefault(history_key, [])
        return self.conversation_history[history_key]

    async def _save_history(self, history_key: str, history: list[dict[str, str]]) -> None:
        """Persist conversation history to Redis (write-through; in-memory is always authoritative)."""
        self.conversation_history[history_key] = history
        try:
            from apps.backend.core.redis_client import get_redis

            redis = await get_redis()
            if redis:
                await redis.setex(f"chat:history:{history_key}", _HISTORY_TTL, json.dumps(history))
        except Exception as _exc:
            logger.debug("Redis history save failed for %s: %s", history_key, _exc)

    def _format_context(self, context: dict[str, Any]) -> str:
        """Format context dictionary into readable text."""
        lines = []
        for key, value in context.items():
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)

    def clear_history(self, session_id: str, agent_id: str | None = None):
        """Clear conversation history for a session."""
        if agent_id:
            history_key = f"{session_id}:{agent_id}"
            if history_key in self.conversation_history:
                del self.conversation_history[history_key]
        else:
            # Clear all history for this session
            keys_to_delete = [k for k in self.conversation_history if k.startswith(f"{session_id}:")]
            for key in keys_to_delete:
                del self.conversation_history[key]


# Global LLM engine instance
llm_engine: LLMAgentEngine | None = None


def get_llm_engine() -> LLMAgentEngine | None:
    """Get the global LLM engine instance."""
    return llm_engine


async def initialize_llm_engine(provider: str | None = None, model: str | None = None):
    """Initialize the global LLM engine."""
    global llm_engine
    llm_engine = LLMAgentEngine(provider, model)
    await llm_engine.initialize()
    logger.info("✅ Global LLM Agent Engine initialized (provider=%s)", llm_engine.provider)
    return llm_engine


async def shutdown_llm_engine():
    """Shutdown the global LLM engine."""
    global llm_engine
    if llm_engine:
        await llm_engine.close()
        llm_engine = None
        logger.info("✅ LLM Agent Engine shutdown complete")
