"""Mistral Embed API client for 1024-dimensional text embeddings."""

from __future__ import annotations

import logging

import httpx

from soulservice.core.config import settings

logger = logging.getLogger("soulservice.embeddings")

MISTRAL_EMBED_URL = "https://api.mistral.ai/v1/embeddings"
MISTRAL_EMBED_MODEL = "mistral-embed"
EMBEDDING_DIM = 1024


async def embed_text(text: str) -> list[float]:
    """Embed a single text string via Mistral Embed API.

    Returns a 1024-dimensional float vector.
    """
    if not settings.mistral_api_key:
        msg = "MISTRAL_API_KEY not set"
        raise ValueError(msg)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            MISTRAL_EMBED_URL,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.mistral_api_key}",
            },
            json={
                "model": MISTRAL_EMBED_MODEL,
                "input": [text],
                "encoding_format": "float",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

    embedding = data["data"][0]["embedding"]
    if len(embedding) != EMBEDDING_DIM:
        logger.warning(
            "Expected %d dimensions, got %d", EMBEDDING_DIM, len(embedding)
        )
    return embedding
