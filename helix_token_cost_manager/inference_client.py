"""
Railway client for communicating with Kubernetes LLM services.

This module provides a client that enables Railway applications to make requests
to LLM services running in Kubernetes clusters, with fallback to OpenAI when
K8s services are unavailable.
"""

import asyncio
import logging
import random
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urljoin

import aiohttp
import openai
import requests  # type: ignore[import-untyped]

from .core.exceptions import LLMProviderUnavailable

logger = logging.getLogger(__name__)


class LLMProvider(Enum):
    """LLM provider types"""

    K8S = "kubernetes"
    OPENAI = "openai"
    CLAUDE = "anthropic"
    LOCAL = "local"


@dataclass
class LLMRequest:
    """LLM request parameters"""

    prompt: str
    model: str
    max_tokens: int = 1000
    temperature: float = 0.7
    top_p: float = 1.0
    stop: list[str] | None = None
    stream: bool = False
    user: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API calls"""
        return {
            "prompt": self.prompt,
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "stop": self.stop,
            "stream": self.stream,
            "user": self.user,
        }


@dataclass
class LLMResponse:
    """LLM response data"""

    content: str
    usage: dict[str, int]
    model: str
    provider: LLMProvider
    request_id: str
    latency: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            "content": self.content,
            "usage": self.usage,
            "model": self.model,
            "provider": self.provider.value,
            "request_id": self.request_id,
            "latency": self.latency,
        }


class LLMClient:
    """Railway client for communicating with K8s LLM services"""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize the LLM client with configuration.

        Args:
            config: Configuration dictionary containing:
                - K8S_LLM_SERVICE_URL: URL of K8s LLM service
                - K8S_LLM_TIMEOUT: Request timeout in seconds
                - K8S_LLM_RETRIES: Number of retry attempts
                - FALLBACK_TO_OPENAI: Whether to fallback to OpenAI
                - OPENAI_API_KEY: OpenAI API key for fallback
                - CIRCUIT_BREAKER_FAILURE_THRESHOLD: Circuit breaker threshold
                - CIRCUIT_BREAKER_RECOVERY_TIMEOUT: Circuit breaker recovery timeout
        """
        self.config = config
        self.logger = logging.getLogger(__name__)

        # Configuration
        self.k8s_service_url = config.get("K8S_LLM_SERVICE_URL")
        self.timeout = config.get("K8S_LLM_TIMEOUT", 30)
        self.retries = config.get("K8S_LLM_RETRIES", 3)
        self.fallback_enabled = config.get("FALLBACK_TO_OPENAI", False)
        self.openai_api_key = config.get("OPENAI_API_KEY")

        # Circuit breaker
        self.failure_threshold = config.get("CIRCUIT_BREAKER_FAILURE_THRESHOLD", 5)
        self.recovery_timeout = config.get("CIRCUIT_BREAKER_RECOVERY_TIMEOUT", 60)
        self.circuit_state = "CLOSED"
        self.failure_count = 0
        self.last_failure_time: float | None = None

        # HTTP session
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", "User-Agent": "Helix-LLM-Client/1.0"})

    def call_llm(self, request: LLMRequest) -> LLMResponse | None:
        """
        Make a request to the LLM service.

        Args:
            request: LLM request parameters

        Returns:
            LLMResponse if successful, None if failed

        Raises:
            Exception: If no LLM service is available
        """
        start_time = time.time()
        request_id = self._generate_request_id()

        try:
            if self.circuit_state != "OPEN":
                response = self._call_k8s_llm(request, request_id)
                if response:
                    latency = time.time() - start_time
                    self.logger.info("K8s LLM request successful: %s", request_id)
                    return LLMResponse(
                        content=response["content"],
                        usage=response["usage"],
                        model=response["model"],
                        provider=LLMProvider.K8S,
                        request_id=request_id,
                        latency=latency,
                    )

            # Fall back to OpenAI if configured
            if self.fallback_enabled and self.openai_api_key:
                response = self._call_openai_llm(request, request_id)
                latency = time.time() - start_time
                self.logger.warning("Using OpenAI fallback: %s", request_id)
                return LLMResponse(
                    content=response["content"],
                    usage=response["usage"],
                    model=response["model"],
                    provider=LLMProvider.OPENAI,
                    request_id=request_id,
                    latency=latency,
                )

            raise LLMProviderUnavailable("No LLM service available")

        except Exception as e:
            self.logger.error("LLM request failed: %s - %s", request_id, str(e))
            raise

    def _call_k8s_llm(self, request: LLMRequest, request_id: str) -> dict[str, Any] | None:
        """
        Call K8s LLM service.

        Uses asyncio.to_thread to avoid blocking the event loop when called
        from async context. Prefer AsyncLLMClient for new code.

        Args:
            request: LLM request parameters
            request_id: Unique request ID

        Returns:
            Response data if successful, None if failed
        """
        if not self.k8s_service_url:
            return None

        payload = {
            "prompt": request.prompt,
            "model": request.model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "top_p": request.top_p,
            "stop": request.stop,
            "user": request.user,
            "request_id": request_id,
        }

        for attempt in range(self.retries):
            try:
                response = self.session.post(
                    urljoin(self.k8s_service_url, "/v1/chat/completions"),
                    json=payload,
                    timeout=self.timeout,
                )

                if response.status_code == 200:
                    data = response.json()
                    self._on_success()
                    return {
                        "content": data["choices"][0]["message"]["content"],
                        "usage": data["usage"],
                        "model": data["model"],
                    }
                else:
                    self.logger.warning("K8s LLM service error %s: %s", response.status_code, response.text)
                    self._on_failure()

            except requests.exceptions.RequestException as e:
                self.logger.error("K8s LLM request failed (attempt %s): %s", attempt + 1, e)
                self._on_failure()

            if attempt < self.retries - 1:
                # Exponential backoff with jitter to avoid thundering herd
                delay = (2**attempt) + random.uniform(0, 1)
                # WARNING: This is a synchronous sleep.  This entire class is sync
                # (uses requests.Session).  Callers from async handlers MUST wrap
                # calls in asyncio.to_thread() to avoid blocking the event loop.
                # Prefer AsyncLLMClient for new code.
                # Confirmed sync-only: safe to use time.sleep here.
                time.sleep(delay)

        return None

    def _call_openai_llm(self, request: LLMRequest, request_id: str) -> dict[str, Any]:
        """
        Call OpenAI API as fallback.

        Args:
            request: LLM request parameters
            request_id: Unique request ID

        Returns:
            Response data from OpenAI

        Raises:
            Exception: If OpenAI API call fails
        """
        try:
            client = openai.OpenAI(api_key=self.openai_api_key)

            response = client.chat.completions.create(
                model=request.model,
                messages=[{"role": "user", "content": request.prompt}],
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                top_p=request.top_p,
                stop=request.stop,
            )

            return {
                "content": response.choices[0].message.content,
                "usage": response.usage.model_dump(),
                "model": response.model,
            }

        except Exception as e:
            self.logger.error("OpenAI fallback failed: %s", e)
            raise

    def _on_success(self):
        """Handle successful request"""
        if self.circuit_state == "HALF_OPEN":
            self.circuit_state = "CLOSED"
            self.failure_count = 0
            self.logger.info("Circuit breaker CLOSED after successful request")

    def _on_failure(self):
        """Handle failed request"""
        self.failure_count += 1

        if self.failure_count >= self.failure_threshold:
            self.circuit_state = "OPEN"
            self.last_failure_time = time.time()
            self.logger.warning("Circuit breaker OPEN after %s failures", self.failure_count)

    def _should_attempt_reset(self) -> bool:
        """Check if circuit breaker should attempt reset"""
        if self.circuit_state != "OPEN":
            return False

        if self.last_failure_time is None:
            return True

        return (time.time() - self.last_failure_time) >= self.recovery_timeout

    def _generate_request_id(self) -> str:
        """Generate unique request ID"""
        return f"req_{uuid.uuid4().hex[:16]}"

    async def call_llm_async(self, request: LLMRequest) -> LLMResponse | None:
        """Event-loop-safe wrapper around the sync call_llm method.

        Offloads the blocking HTTP calls to a thread pool so the asyncio
        event loop is never blocked. Prefer AsyncLLMClient for new code.
        """
        return await asyncio.to_thread(self.call_llm, request)

    def health_check(self) -> dict[str, Any]:
        """
        Check service health.

        Returns:
            Health status dictionary
        """
        k8s_health = False
        if self.k8s_service_url:
            try:
                response = self.session.get(urljoin(self.k8s_service_url, "/health"), timeout=5)
                k8s_health = response.status_code == 200
            except Exception as exc:
                logger.debug("K8s health check failed: %s", exc)
        return {
            "k8s_service": k8s_health,
            "circuit_breaker": {
                "state": self.circuit_state,
                "failure_count": self.failure_count,
                "last_failure_time": self.last_failure_time,
            },
            "fallback_enabled": self.fallback_enabled,
            "openai_available": bool(self.openai_api_key),
        }

    async def health_check_async(self) -> dict[str, Any]:
        """Event-loop-safe health check."""
        return await asyncio.to_thread(self.health_check)


class AsyncLLMClient:
    """Async Railway client for K8s LLM services"""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize the async LLM client with configuration.

        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.logger = logging.getLogger(__name__)

        self.k8s_service_url = config.get("K8S_LLM_SERVICE_URL")
        self.timeout = config.get("K8S_LLM_TIMEOUT", 30)
        self.retries = config.get("K8S_LLM_RETRIES", 3)
        self.fallback_enabled = config.get("FALLBACK_TO_OPENAI", False)
        self.openai_api_key = config.get("OPENAI_API_KEY")

        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout),
            headers={"Content-Type": "application/json"},
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()

    async def call_llm(self, request: LLMRequest) -> dict[str, Any] | None:
        """
        Make async request to LLM service.

        Args:
            request: LLM request parameters

        Returns:
            Response data if successful, None if failed
        """
        # Try K8s first
        if self.k8s_service_url:
            response = await self._call_k8s_llm(request)
            if response:
                return response

        # Fall back to OpenAI
        if self.fallback_enabled and self.openai_api_key:
            response = await self._call_openai_llm(request)
            return response

        return None

    async def _call_k8s_llm(self, request: LLMRequest) -> dict[str, Any] | None:
        """
        Async call to K8s LLM service.

        Args:
            request: LLM request parameters

        Returns:
            Response data if successful, None if failed
        """
        payload = {
            "prompt": request.prompt,
            "model": request.model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "top_p": request.top_p,
            "stop": request.stop,
            "stream": request.stream,
        }

        for attempt in range(self.retries):
            try:
                session = self.session or aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                    headers={"Content-Type": "application/json"},
                )
                try:
                    async with session.post(f"{self.k8s_service_url}/v1/chat/completions", json=payload) as response:
                        if response.status == 200:
                            data = await response.json()
                            return {
                                "content": data["choices"][0]["message"]["content"],
                                "usage": data["usage"],
                                "model": data["model"],
                            }
                        else:
                            self.logger.warning("K8s LLM error: %s", response.status)
                finally:
                    # Only close the session if we created a one-off
                    if self.session is None:
                        await session.close()

            except Exception as e:
                self.logger.error("K8s LLM request failed: %s", e)

            if attempt < self.retries - 1:
                await asyncio.sleep(2**attempt + random.uniform(0, 1))

        return None

    async def _call_openai_llm(self, request: LLMRequest) -> dict[str, Any]:
        """
        Async call to OpenAI API.

        Args:
            request: LLM request parameters

        Returns:
            Response data from OpenAI
        """
        try:
            client = openai.AsyncOpenAI(api_key=self.openai_api_key)

            response = await client.chat.completions.create(
                model=request.model,
                messages=[{"role": "user", "content": request.prompt}],
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                top_p=request.top_p,
                stop=request.stop,
            )

            return {
                "content": response.choices[0].message.content,
                "usage": response.usage.model_dump(),
                "model": response.model,
            }

        except Exception as e:
            self.logger.error("OpenAI async fallback failed: %s", e)
            raise


class ServiceDiscovery:
    """Service discovery for K8s LLM services"""

    def __init__(self, consul_url: str = "http://consul:8500"):
        """
        Initialize service discovery.

        Args:
            consul_url: Consul service discovery URL
        """
        self.consul_url = consul_url
        self.logger = logging.getLogger(__name__)
        self.session = requests.Session()

    def get_llm_services(self) -> list[dict[str, Any]]:
        """
        Get list of available LLM services.

        Returns:
            List of service information dictionaries
        """
        try:
            response = self.session.get(f"{self.consul_url}/v1/catalog/services", timeout=10)

            if response.status_code == 200:
                services = response.json()
                return [
                    {
                        "id": service["ServiceID"],
                        "name": service["ServiceName"],
                        "address": service["ServiceAddress"],
                        "port": service["ServicePort"],
                        "tags": service["ServiceTags"],
                        "healthy": self._check_service_health(service),
                    }
                    for service in services
                ]

        except Exception as e:
            self.logger.error("Service discovery failed: %s", e)

        return []

    async def get_llm_services_async(self) -> list[dict[str, Any]]:
        """Event-loop-safe wrapper for service discovery."""
        return await asyncio.to_thread(self.get_llm_services)

    def _check_service_health(self, service: dict[str, Any]) -> bool:
        """
        Check if service is healthy.

        Args:
            service: Service information dictionary

        Returns:
            True if service is healthy, False otherwise
        """
        try:
            health_url = f"http://{service['ServiceAddress']}:{service['ServicePort']}/health"
            response = self.session.get(health_url, timeout=5)
            return response.status_code == 200
        except Exception as e:
            logger.warning("Health check failed for service %s: %s", service.get("ServiceID", "unknown"), e)
            return False

    def register_service(
        self,
        service_name: str,
        service_id: str,
        address: str,
        port: int,
        tags: list[str] | None = None,
    ) -> bool:
        """
        Register a service with Consul.

        Args:
            service_name: Name of the service
            service_id: Unique service ID
            address: Service address
            port: Service port
            tags: Service tags

        Returns:
            True if registration successful, False otherwise
        """
        service_definition = {
            "ID": service_id,
            "Name": service_name,
            "Address": address,
            "Port": port,
            "Tags": tags or [],
            "Check": {
                "HTTP": f"http://{address}:{port}/health",
                "Interval": "10s",
                "Timeout": "5s",
            },
        }

        try:
            response = self.session.put(
                f"{self.consul_url}/v1/agent/service/register", json=service_definition, timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            self.logger.error("Service registration failed: %s", e)
            return False
